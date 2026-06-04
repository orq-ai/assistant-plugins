// capture.mjs — spawn a single headless `claude -p` run and parse its stream-json
// into a deterministic { selectedSkills, toolCalls, text } record.
//
// The command shape here was validated empirically (see plan / probe):
//   - prompt is fed via STDIN (no positional arg) → no cross-platform quoting of
//     multi-line prompts/context.
//   - the combined system prompt (AGENTS.md + framing) is passed via a temp file
//     with --append-system-prompt-file.
//   - --permission-mode dontAsk + a read-only --allowedTools allowlist means
//     read/list/get/search tools EXECUTE, while every write tool (create_*/update_*/
//     delete_*/invoke_*) is auto-DENIED — but its `tool_use` block is still emitted
//     into the stream, so we capture write *intent* with zero side effects.
//   - --settings '{"disableAllHooks":true}' silences the repo trace-hooks plugin.
//   - --strict-mcp-config + --disallowedTools Skill force routing to surface as a
//     `Read` of skills/<name>/SKILL.md (not a plugin Skill shortcut).

import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export const MCP_SERVER = "orq-workspace";
export const MCP_PREFIX = `mcp__${MCP_SERVER}__`;

// Read-only orq MCP tools that are SAFE to execute live (idempotent, no mutation).
// Everything not listed here (create_*/update_*/delete_*/invoke_*) is denied by
// dontAsk and captured as intent only.
export const READONLY_ORQ_TOOLS = [
  "search_entities",
  "search_directories",
  "search_docs",
  "list_models",
  "list_datapoints",
  "list_experiment_runs",
  "list_traces",
  "list_spans",
  "list_skills",
  "get_agent",
  "get_deployment",
  "get_llm_eval",
  "get_python_eval",
  "get_skill",
  "get_span",
  "get_experiment_run",
  "get_analytics_overview",
  "query_analytics",
  "retrieve_agent_response",
];

export const DEFAULT_ALLOWED_TOOLS = [
  "Read",
  "Grep",
  "Glob",
  ...READONLY_ORQ_TOOLS.map((t) => MCP_PREFIX + t),
].join(",");

/** Strip the orq MCP prefix so short aliases and full names compare equal. */
export function shortName(name) {
  return name && name.startsWith(MCP_PREFIX) ? name.slice(MCP_PREFIX.length) : name;
}

/** Build the user turn for a case: prompt plus optional appended context. */
export function casePrompt(spec) {
  return spec.prompt + (spec.context ? `\n\n${spec.context}` : "");
}

/** Resolve the claude binary; override with CLAUDE_BIN if the default is wrong. */
function claudeBin() {
  return process.env.CLAUDE_BIN || "claude";
}

const IS_WIN = process.platform === "win32";

// On Windows the claude launcher is a `.cmd`, which modern Node refuses to spawn
// with shell:false (EINVAL). Use the shell there and quote args ourselves; on
// POSIX keep shell:false (no quoting, exact argv). The prompt is fed via stdin,
// so only simple flag values (paths, tool list, mode) are ever args here.
function spawnClaude(args, opts) {
  if (IS_WIN) {
    const quoted = args.map((a) =>
      a === "" || /[\s()"&^%!]/.test(a) ? `"${String(a).replace(/"/g, '""')}"` : a
    );
    return spawn(claudeBin(), quoted, { ...opts, shell: true });
  }
  return spawn(claudeBin(), args, { ...opts, shell: false });
}

/** Match an absolute/relative Read path back to a skill name (skills/<name>/SKILL.md). */
export function skillFromReadPath(p) {
  if (!p) return null;
  const norm = String(p).replace(/\\/g, "/");
  const m = norm.match(/skills\/([^/]+)\/SKILL\.md$/i);
  return m ? m[1] : null;
}

/**
 * Run one case headless and return the parsed trace.
 * @returns {Promise<{exitCode:number, timedOut:boolean, resultSubtype:string|null,
 *   selectedSkills:string[], toolCalls:{name:string,input:object}[], text:string,
 *   stderr:string, rawLines:number}>}
 */
export function runCase({
  prompt,
  systemPromptFile,
  settingsFile,
  cwd,
  mcpConfigPath = ".mcp.json",
  allowedTools = DEFAULT_ALLOWED_TOOLS,
  timeoutMs = 180000,
  keepTemp = false,
} = {}) {
  const args = [
    "-p",
    "--append-system-prompt-file", systemPromptFile,
    "--strict-mcp-config",
    "--mcp-config", mcpConfigPath,
    "--disallowedTools", "Skill",
    "--allowedTools", allowedTools,
    "--permission-mode", "dontAsk",
    "--settings", settingsFile,
    "--output-format", "stream-json",
    "--verbose",
  ];

  return new Promise((resolve) => {
    let child;
    try {
      child = spawnClaude(args, {
        cwd,
        stdio: ["pipe", "pipe", "pipe"],
      });
    } catch (err) {
      resolve(failure(`spawn failed: ${err.message}`));
      return;
    }

    let stdout = "";
    let stderr = "";
    let timedOut = false;

    const timer = setTimeout(() => {
      timedOut = true;
      child.kill("SIGKILL");
    }, timeoutMs);

    child.on("error", (err) => {
      clearTimeout(timer);
      resolve(failure(`process error: ${err.message} (set CLAUDE_BIN if claude is not on PATH)`));
    });

    child.stdout.on("data", (d) => (stdout += d));
    child.stderr.on("data", (d) => (stderr += d));

    child.stdin.write(prompt);
    child.stdin.end();

    child.on("close", (code) => {
      clearTimeout(timer);
      const parsed = parseStream(stdout);
      resolve({
        exitCode: code ?? -1,
        timedOut,
        stderr: stderr.slice(-2000),
        ...parsed,
      });
    });

    function failure(msg) {
      return {
        exitCode: -1,
        timedOut: false,
        resultSubtype: "harness_error",
        selectedSkills: [],
        toolCalls: [],
        text: "",
        stderr: msg,
        rawLines: 0,
      };
    }
  });
}

/** Parse stream-json NDJSON into tool calls, selected skills, and final text. */
export function parseStream(stdout) {
  const toolCalls = [];
  const assistantTexts = [];
  const selected = new Set();
  let resultText = "";
  let resultSubtype = null;
  let rawLines = 0;

  for (const line of stdout.split(/\r?\n/)) {
    if (!line.trim()) continue;
    rawLines++;
    let o;
    try {
      o = JSON.parse(line);
    } catch {
      continue;
    }
    if (o.type === "assistant" && o.message?.content) {
      for (const c of o.message.content) {
        if (c.type === "tool_use") {
          toolCalls.push({ name: c.name, input: c.input ?? {} });
          if (c.name === "Read") {
            const sk = skillFromReadPath(c.input?.file_path || c.input?.path);
            if (sk) selected.add(sk);
          }
        } else if (c.type === "text" && typeof c.text === "string") {
          assistantTexts.push(c.text);
        }
      }
    } else if (o.type === "result") {
      resultSubtype = o.subtype ?? null;
      if (typeof o.result === "string") resultText = o.result;
    }
  }

  return {
    resultSubtype,
    selectedSkills: [...selected],
    toolCalls,
    text: resultText || assistantTexts.join("\n"),
    rawLines,
  };
}

/**
 * Build the shared temp files for a run: the combined system prompt
 * (AGENTS.md + routing framing) and a settings file that disables hooks.
 * Created once and reused across all cases.
 */
export function writeSystemPromptFile(agentsMd) {
  const dir = mkdtempSync(join(tmpdir(), "skill-tester-"));
  const file = join(dir, "system.txt");
  const settingsFile = join(dir, "settings.json");
  const framing =
    "\n\nYou are the orq.ai workspace assistant. Skills live under " +
    "skills/<name>/SKILL.md relative to the repo root. When a skill description fits " +
    "the user's intent, READ the matching SKILL.md and follow it. If no skill fits, " +
    "answer normally and do not force one.";
  writeFileSync(file, agentsMd + framing, "utf8");
  writeFileSync(settingsFile, JSON.stringify({ disableAllHooks: true }), "utf8");
  return { file, settingsFile, dir, cleanup: () => rmSync(dir, { recursive: true, force: true }) };
}
