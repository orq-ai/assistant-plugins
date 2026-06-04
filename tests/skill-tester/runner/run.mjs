#!/usr/bin/env node
// run.mjs — CLI entrypoint for the deterministic skill-tester harness.
//
//   node run.mjs [--track behavioural|functional|all]
//                [--skills a,b,c|all] [--concurrency 4] [--repeat 1]
//                [--out <path>] [--timeout 180000] [--keep-temp] [--list] [--debug]
//
// Two tracks:
//   behavioural (default) — spawn one headless `claude -p` per case, parse the real
//                           tool-call stream, grade routing + tool calls + text. No writes.
//   functional            — call the orq MCP server directly (create→read-back→cleanup),
//                           verify results, detect drift. MUTATES the live workspace and
//                           cleans up after itself; requires ORQ_API_KEY.
//   all                   — run both; exit code is the worse of the two.
//
// Exit code is non-zero if any gating check failed (CI gate).

import { readFileSync, writeFileSync, existsSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import YAML from "yaml";

import { runCase, writeSystemPromptFile, casePrompt } from "./lib/capture.mjs";
import { gradeCase } from "./lib/grade.mjs";
import { markdownReport, consoleSummary } from "./lib/report.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "..", "..", "..");
const CATALOG_DIR = join(REPO_ROOT, "tests", "skill-tester", "catalog");
const AGENTS_MD = join(REPO_ROOT, "agents", "AGENTS.md");
const MCP_CONFIG = join(REPO_ROOT, ".mcp.json");

const TRACKS = ["behavioural", "functional", "all"];

function parseArgs(argv) {
  const a = { track: "behavioural", skills: "all", concurrency: 4, repeat: 1, out: null, timeout: 240000, keepTemp: false, list: false, debug: false };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i];
    const next = () => argv[++i];
    if (k === "--track") a.track = next();
    else if (k === "--skills") a.skills = next();
    else if (k === "--concurrency") a.concurrency = parseInt(next(), 10);
    else if (k === "--repeat") a.repeat = parseInt(next(), 10);
    else if (k === "--out") a.out = next();
    else if (k === "--timeout") a.timeout = parseInt(next(), 10);
    else if (k === "--keep-temp") a.keepTemp = true;
    else if (k === "--list") a.list = true;
    else if (k === "--debug") a.debug = true;
    else if (k === "-h" || k === "--help") a.help = true;
    else console.error(`warning: unknown arg ${k}`);
  }
  return a;
}

/** Catalog skills that have a spec with the given suffix (".cases.yaml" | ".functional.yaml"). */
function listCatalogSkills(suffix) {
  return readdirSync(CATALOG_DIR)
    .filter((f) => f.endsWith(suffix))
    .map((f) => f.replace(new RegExp(`${suffix.replace(/\./g, "\\.")}$`), ""))
    .sort();
}

function resolveSkills(args, suffix) {
  if (args.skills === "all") return listCatalogSkills(suffix);
  return args.skills.split(",").map((s) => s.trim()).filter(Boolean);
}

function loadDoc(file) {
  const doc = YAML.parse(readFileSync(file, "utf8"));
  if (!doc || !Array.isArray(doc.cases)) throw new Error(`${file}: missing 'cases' array`);
  return doc;
}

/** Run an array of async task factories with a fixed concurrency limit. */
async function pool(items, limit, worker) {
  const results = new Array(items.length);
  let next = 0;
  async function runner() {
    while (true) {
      const i = next++;
      if (i >= items.length) return;
      results[i] = await worker(items[i], i);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, runner));
  return results;
}

async function gradeWithRepeat(spec, skill, runOne, repeat) {
  let verdict = null;
  let sawPass = false;
  let sawFail = false;
  for (let r = 0; r < repeat; r++) {
    const captured = await runOne(spec);
    const v = gradeCase(spec, skill, captured);
    if (v.overall_pass) sawPass = true;
    else sawFail = true;
    // Keep the first failing verdict for detail; else the first verdict.
    if (!verdict || (!v.overall_pass && verdict.overall_pass)) verdict = v;
  }
  verdict.flaky = repeat > 1 && sawPass && sawFail;
  return verdict;
}

// ── behavioural track ───────────────────────────────────────────────────────
async function runBehavioural(args) {
  const skills = resolveSkills(args, ".cases.yaml");
  if (!skills.length) {
    console.error("error: no skills selected");
    return 2;
  }
  if (!process.env.ORQ_API_KEY) {
    console.error("warning: ORQ_API_KEY not set — orq MCP read tools will error; routing + text checks still grade.");
  }

  const sys = writeSystemPromptFile(readFileSync(AGENTS_MD, "utf8"));
  const runOne = (spec) =>
    runCase({
      prompt: casePrompt(spec),
      systemPromptFile: sys.file,
      settingsFile: sys.settingsFile,
      cwd: REPO_ROOT,
      mcpConfigPath: MCP_CONFIG,
      timeoutMs: args.timeout,
      keepTemp: args.keepTemp,
    });

  const allVerdicts = [];
  try {
    for (const skill of skills) {
      const file = join(CATALOG_DIR, `${skill}.cases.yaml`);
      let doc;
      try {
        doc = loadDoc(file);
      } catch (e) {
        console.error(`✗ ${skill}: ${e.message}`);
        continue;
      }
      process.stderr.write(`\n▶ ${skill} (${doc.cases.length} cases, concurrency ${args.concurrency})\n`);
      const verdicts = await pool(doc.cases, args.concurrency, async (spec) => {
        const v = await gradeWithRepeat(spec, skill, runOne, args.repeat);
        process.stderr.write(`   ${v.overall_pass ? "✓" : "✗"} ${v.scenario_id} [${v.invocation_type}]\n`);
        if (args.debug) {
          const sep = "─".repeat(72);
          process.stderr.write(
            `\n${sep}\n[debug] ${v.scenario_id} [${v.invocation_type}] → ${v.overall_pass ? "PASS" : "FAIL"}\n${sep}\n` +
              `PROMPT:\n${casePrompt(spec)}\n\nRESPONSE (${v.response_text.length} chars):\n${v.response_text || "(empty)"}\n${sep}\n`
          );
        }
        return v;
      });
      allVerdicts.push(...verdicts);
    }
  } finally {
    if (!args.keepTemp) sys.cleanup();
  }

  console.log("\n" + consoleSummary(allVerdicts));
  const outPath = args.track === "behavioural" && args.out ? args.out : join(__dirname, "last-report.md");
  writeFileSync(outPath, markdownReport(allVerdicts, { repeat: args.repeat }), "utf8");
  console.log(`\nBehavioural report written to ${outPath}`);
  return allVerdicts.some((v) => !v.overall_pass) ? 1 : 0;
}

// ── functional track ──────────────────────────────────────────────────────────
async function runFunctional(args) {
  if (!process.env.ORQ_API_KEY) {
    console.error("error: functional track requires ORQ_API_KEY (it calls the live orq MCP server).");
    return 2;
  }
  const skills = resolveSkills(args, ".functional.yaml");
  if (!skills.length) {
    console.error("error: no skills selected");
    return 2;
  }

  const { McpClient } = await import("./lib/mcp-client.mjs");
  const { preflight, runFunctionalCase, teardown } = await import("./lib/functional.mjs");
  const { markdownReportFunctional, consoleSummaryFunctional } = await import("./lib/functional-report.mjs");

  const onDebug = args.debug
    ? ({ scenario_id, call, args: a, response }) => {
        const sep = "─".repeat(72);
        const resp = String(response ?? "");
        process.stderr.write(
          `\n${sep}\n[debug] ${scenario_id} → ${call}\n${sep}\nARGS: ${JSON.stringify(a)}\n` +
            `RESPONSE: ${resp.length > 1500 ? resp.slice(0, 1500) + "…" : resp}\n${sep}\n`
        );
      }
    : null;

  const client = new McpClient(MCP_CONFIG);
  const verdicts = [];
  let ctx = null;
  let cleanup = [];
  try {
    await client.connect();
    ctx = await preflight(client);
    process.stderr.write(`\n▶ functional — test path: ${ctx.testPath}\n   seed: ${ctx.seedNotes.join("; ")}\n`);
    for (const skill of skills) {
      const file = join(CATALOG_DIR, `${skill}.functional.yaml`);
      if (!existsSync(file)) {
        console.error(`✗ ${skill}: no functional spec (${skill}.functional.yaml)`);
        continue;
      }
      let doc;
      try {
        doc = loadDoc(file);
      } catch (e) {
        console.error(`✗ ${skill}: ${e.message}`);
        continue;
      }
      process.stderr.write(`\n▶ ${skill} (${doc.cases.length} functional cases)\n`);
      for (const spec of doc.cases) {
        const v = await runFunctionalCase(spec, skill, ctx, client, { onDebug });
        const mark = { PASS: "✓", FAIL: "✗", DRIFT: "≠", SKIP: "–" }[v.status] || "?";
        process.stderr.write(`   ${mark} ${v.scenario_id} [${v.status}]\n`);
        verdicts.push(v);
      }
    }
  } finally {
    if (ctx) cleanup = await teardown(ctx, client);
    await client.close();
  }

  console.log("\n" + consoleSummaryFunctional(verdicts));
  const outPath = args.track === "functional" && args.out ? args.out : join(__dirname, "functional-report.md");
  writeFileSync(
    outPath,
    markdownReportFunctional(verdicts, cleanup, { testPath: ctx?.testPath, seedNotes: ctx?.seedNotes }),
    "utf8"
  );
  console.log(`\nFunctional report written to ${outPath}`);
  return verdicts.some((v) => v.status === "FAIL" || v.status === "DRIFT") ? 1 : 0;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));

  if (args.help) {
    console.log(
      "Usage: node run.mjs [--track behavioural|functional|all] [--skills a,b,c|all] [--concurrency N] [--repeat N] [--out path] [--timeout ms] [--keep-temp] [--list] [--debug]"
    );
    return 0;
  }
  if (!TRACKS.includes(args.track)) {
    console.error(`error: --track must be one of ${TRACKS.join(" | ")}`);
    return 2;
  }
  if (args.list) {
    console.log(listCatalogSkills(".cases.yaml").join("\n"));
    return 0;
  }

  if (!existsSync(AGENTS_MD)) {
    console.error(`error: ${AGENTS_MD} not found`);
    return 2;
  }
  if (!existsSync(MCP_CONFIG)) {
    console.error(`error: ${MCP_CONFIG} not found`);
    return 2;
  }

  let code = 0;
  if (args.track === "behavioural" || args.track === "all") code = Math.max(code, await runBehavioural(args));
  if (args.track === "functional" || args.track === "all") code = Math.max(code, await runFunctional(args));
  return code;
}

main()
  .then((code) => process.exit(code))
  .catch((err) => {
    console.error(err);
    process.exit(2);
  });
