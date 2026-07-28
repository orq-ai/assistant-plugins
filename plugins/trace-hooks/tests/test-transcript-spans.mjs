#!/usr/bin/env node
// Verify the spans built from a Claude Code transcript (BOPS-1008).
//
// Runs hooks/stop.js and hooks/session-end.js as child processes against a
// synthetic transcript, with OTEL_EXPORTER_OTLP_ENDPOINT pointed at a local
// capture server, so the assertions run on the exact OTLP payload the plugin
// would send to orq.
//
// Usage: node test-transcript-spans.mjs

import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import assert from "node:assert/strict";

const repoRoot = path.resolve(import.meta.dirname, "..");
const stopHook = path.join(repoRoot, "hooks/stop.js");
const sessionEndHook = path.join(repoRoot, "hooks/session-end.js");
const sessionStartHook = path.join(repoRoot, "hooks/session-start.js");

let passed = 0;
let failed = 0;
let captured = [];

const server = http.createServer((req, res) => {
  const chunks = [];
  req.on("data", (chunk) => chunks.push(chunk));
  req.on("end", () => {
    try {
      captured.push(JSON.parse(Buffer.concat(chunks).toString("utf8")));
    } catch {
      // A malformed body fails the assertions below, not the server.
    }
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end("{}");
  });
});

await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const endpoint = `http://127.0.0.1:${server.address().port}/v1/traces`;

function makeTempDir() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "orq-trace-spans-"));
  fs.mkdirSync(path.join(dir, "orq_sessions"), { recursive: true });
  return dir;
}

function seedSession(stateDir, sessionId, overrides = {}) {
  const state = {
    session_id: sessionId,
    trace_id: "a".repeat(32),
    root_span_id: "a".repeat(16),
    session_started_at_ns: "1700000000000000000",
    turn_count: 1,
    total_tool_calls: 0,
    current_turn_span_id: "b".repeat(16),
    current_turn_started_at_ns: "1700000001000000000",
    current_turn_input: "list the plugin files",
    model: "claude-opus-4-8",
    last_processed_line: 0,
    subagents: {},
    ...overrides,
  };
  const file = path.join(stateDir, "orq_sessions", `${sessionId}.json`);
  fs.writeFileSync(file, JSON.stringify(state, null, 2));
  return file;
}

// One assistant turn: prose plus a tool call, then the tool result. Written the
// way Claude Code writes it, every line terminated with a newline.
//
// The assistant turn is split across two lines sharing one message id, which is
// how a streamed response lands in the transcript. The tool_use block is only
// on the first chunk while the merged message takes the timestamp of the last,
// so anything ordering tools by timestamp puts the result before its own call.
function transcriptLines() {
  return [
    {
      type: "user",
      timestamp: "2026-07-23T12:00:00.000Z",
      message: { role: "user", content: "list the plugin files" },
    },
    {
      type: "assistant",
      timestamp: "2026-07-23T12:00:01.000Z",
      message: {
        id: "msg_01",
        model: "claude-opus-4-8",
        stop_reason: "tool_use",
        usage: { input_tokens: 10, output_tokens: 3 },
        content: [
          { type: "text", text: "Listing them now." },
          { type: "tool_use", id: "toolu_01", name: "Bash", input: { command: "ls" } },
        ],
      },
    },
    {
      type: "assistant",
      timestamp: "2026-07-23T12:00:02.000Z",
      message: {
        id: "msg_01",
        model: "claude-opus-4-8",
        stop_reason: "tool_use",
        usage: { input_tokens: 10, output_tokens: 5 },
        content: [{ type: "text", text: "Listing them now." }],
      },
    },
    {
      type: "user",
      timestamp: "2026-07-23T12:00:03.000Z",
      message: {
        role: "user",
        content: [
          {
            type: "tool_result",
            tool_use_id: "toolu_01",
            content: [{ type: "text", text: "src\nhooks\n" }],
          },
        ],
      },
    },
    {
      type: "assistant",
      timestamp: "2026-07-23T12:00:04.000Z",
      message: {
        id: "msg_02",
        model: "claude-opus-4-8",
        stop_reason: "end_turn",
        usage: { input_tokens: 20, output_tokens: 8 },
        content: [{ type: "text", text: "Two directories: src and hooks." }],
      },
    },
  ];
}

function writeTranscript(dir, entries) {
  const file = path.join(dir, "transcript.jsonl");
  fs.writeFileSync(file, entries.map((entry) => `${JSON.stringify(entry)}\n`).join(""));
  return file;
}

function appendTranscript(file, entries) {
  fs.appendFileSync(file, entries.map((entry) => `${JSON.stringify(entry)}\n`).join(""));
}

// Async on purpose: the capture server runs in this process, so a synchronous
// spawn would block the event loop and deadlock against the hook's own POST.
function runHook(hookScript, payload, stateDir) {
  return new Promise((resolve) => {
    const child = spawn("node", [hookScript], {
      env: {
        ...process.env,
        ORQ_CLAUDE_STATE_DIR: stateDir,
        ORQ_API_KEY: "test-key",
        OTEL_EXPORTER_OTLP_ENDPOINT: endpoint,
      },
    });
    let stderr = "";
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
    });
    child.on("close", (status) => resolve({ status, stderr }));
    child.stdin.end(JSON.stringify(payload));
  });
}

function sentSpans() {
  return captured.flatMap((payload) =>
    (payload.resourceSpans ?? []).flatMap((resourceSpan) =>
      (resourceSpan.scopeSpans ?? []).flatMap((scopeSpan) => scopeSpan.spans ?? []),
    ),
  );
}

function attrValue(span, key) {
  const found = (span.attributes ?? []).find((attribute) => attribute.key === key);
  if (!found) return undefined;
  return found.value?.stringValue ?? found.value?.intValue ?? found.value?.boolValue;
}

function jsonAttr(span, key) {
  const raw = attrValue(span, key);
  return raw === undefined ? undefined : JSON.parse(raw);
}

async function test(name, fn) {
  captured = [];
  try {
    await fn();
    console.log(`PASS: ${name}`);
    passed++;
  } catch (err) {
    console.error(`FAIL: ${name}`);
    console.error(`  ${err?.message || err}`);
    if (err?.stack) console.error(err.stack.split("\n").slice(1, 4).join("\n"));
    failed++;
  }
}

// --- Test 1: tool call and result share an id, in semconv parts shape ---
await test("chat span: tool_call and tool_call_response pair on the tool_use id", async () => {
  const dir = makeTempDir();
  seedSession(dir, "sess-parts");
  const transcript = writeTranscript(dir, transcriptLines());

  const res = await runHook(stopHook, { session_id: "sess-parts", transcript_path: transcript }, dir);
  assert.equal(res.status, 0, `hook exit ${res.status}: ${res.stderr}`);

  const chatSpans = sentSpans().filter((span) => span.name.startsWith("chat "));
  assert.equal(chatSpans.length, 2, "expected one chat span per assistant message");

  const firstOutput = jsonAttr(chatSpans[0], "gen_ai.output.messages");
  const toolCallPart = firstOutput[0].parts.find((part) => part.type === "tool_call");
  assert.ok(toolCallPart, "first assistant message should carry a tool_call part");
  assert.equal(toolCallPart.id, "toolu_01");
  assert.equal(toolCallPart.name, "Bash");

  const secondInput = jsonAttr(chatSpans[1], "gen_ai.input.messages");
  const responsePart = secondInput
    .flatMap((message) => message.parts ?? [])
    .find((part) => part.type === "tool_call_response");
  assert.ok(responsePart, "the tool result should be a tool_call_response part");
  assert.equal(responsePart.id, "toolu_01", "result must carry the id of the call it answers");
  assert.match(responsePart.response, /src/);

  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 2: the call precedes the result in the replayed conversation ---
await test("chat span: the tool_call is replayed before its tool_call_response", async () => {
  const dir = makeTempDir();
  seedSession(dir, "sess-order");
  const transcript = writeTranscript(dir, transcriptLines());

  await runHook(stopHook, { session_id: "sess-order", transcript_path: transcript }, dir);

  const chatSpans = sentSpans().filter((span) => span.name.startsWith("chat "));
  const messages = jsonAttr(chatSpans[1], "gen_ai.input.messages");
  const callIndex = messages.findIndex((message) =>
    (message.parts ?? []).some((part) => part.type === "tool_call" && part.id === "toolu_01"),
  );
  const responseIndex = messages.findIndex((message) =>
    (message.parts ?? []).some((part) => part.type === "tool_call_response" && part.id === "toolu_01"),
  );
  assert.notEqual(callIndex, -1, "call missing from the replayed conversation");
  assert.notEqual(responseIndex, -1, "result missing from the replayed conversation");
  assert.ok(callIndex < responseIndex, `call at ${callIndex} must precede result at ${responseIndex}`);

  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 3: only the semconv content attributes are emitted ---
await test("chat span: no legacy input/output/orq.*.value attributes", async () => {
  const dir = makeTempDir();
  seedSession(dir, "sess-attrs");
  const transcript = writeTranscript(dir, transcriptLines());

  await runHook(stopHook, { session_id: "sess-attrs", transcript_path: transcript }, dir);

  const legacyOnChat = ["gen_ai.input", "gen_ai.output", "orq.input.value", "orq.output.value", "input", "output"];
  for (const span of sentSpans().filter((s) => s.name.startsWith("chat "))) {
    for (const key of legacyOnChat) {
      assert.equal(attrValue(span, key), undefined, `${key} should not be emitted on chat spans`);
    }
    assert.ok(attrValue(span, "gen_ai.input.messages"), "gen_ai.input.messages missing");
    assert.ok(attrValue(span, "gen_ai.output.messages"), "gen_ai.output.messages missing");
  }

  for (const span of sentSpans().filter((s) => s.name.startsWith("execute_tool "))) {
    for (const key of ["orq.input.value", "orq.output.value"]) {
      assert.equal(attrValue(span, key), undefined, `${key} should not be emitted on tool spans`);
    }
    assert.ok(attrValue(span, "gen_ai.tool.call.result"), "gen_ai.tool.call.result missing");
  }

  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 4: no transcript entry is skipped between parse windows ---
await test("cursor: a second parse window loses no assistant message", async () => {
  const dir = makeTempDir();
  seedSession(dir, "sess-cursor");
  const transcript = writeTranscript(dir, transcriptLines());

  await runHook(stopHook, { session_id: "sess-cursor", transcript_path: transcript }, dir);

  // The first entry of the new window is the one an off-by-one cursor eats, so
  // put an assistant message there: its chat span is the probe.
  appendTranscript(transcript, [
    {
      type: "assistant",
      timestamp: "2026-07-23T12:00:04.000Z",
      message: {
        id: "msg_03",
        model: "claude-opus-4-8",
        stop_reason: "end_turn",
        usage: { input_tokens: 30, output_tokens: 4 },
        content: [{ type: "text", text: "Six files under tests." }],
      },
    },
    {
      type: "user",
      timestamp: "2026-07-23T12:00:05.000Z",
      message: { role: "user", content: "thanks" },
    },
  ]);

  await runHook(stopHook, { session_id: "sess-cursor", transcript_path: transcript }, dir);

  const chatSpans = sentSpans().filter((span) => span.name.startsWith("chat "));
  assert.equal(chatSpans.length, 3, "every assistant message must produce exactly one chat span");

  const lastOutput = jsonAttr(chatSpans[2], "gen_ai.output.messages");
  assert.equal(lastOutput[0].parts[0].content, "Six files under tests.");

  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 5: the root session span reports the session's input and output ---
await test("root span: carries first prompt and closing assistant text", async () => {
  const dir = makeTempDir();
  seedSession(dir, "sess-root");
  const transcript = writeTranscript(dir, transcriptLines());

  const res = await runHook(
    sessionEndHook,
    { session_id: "sess-root", transcript_path: transcript, reason: "clear" },
    dir,
  );
  assert.equal(res.status, 0, `hook exit ${res.status}: ${res.stderr}`);

  const rootSpan = sentSpans().find((span) => span.name === "orq.claude_code.session");
  assert.ok(rootSpan, "root span not sent");

  const input = jsonAttr(rootSpan, "gen_ai.input.messages");
  assert.equal(input[0].parts[0].content, "list the plugin files");

  const output = jsonAttr(rootSpan, "gen_ai.output.messages");
  assert.equal(output[0].parts[0].content, "Two directories: src and hooks.");

  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 6: no per-turn span, everything hangs off the root ---
await test("hierarchy: no claude_code.turn spans, every span parents to the root", async () => {
  const dir = makeTempDir();
  seedSession(dir, "sess-flat");
  const transcript = writeTranscript(dir, transcriptLines());

  await runHook(sessionEndHook, { session_id: "sess-flat", transcript_path: transcript }, dir);

  const spans = sentSpans();
  const turnSpans = spans.filter((span) => span.name.startsWith("claude_code.turn"));
  assert.equal(turnSpans.length, 0, "turn spans should no longer be emitted");

  const root = spans.find((span) => span.name === "orq.claude_code.session");
  assert.ok(root, "root span not sent");

  for (const span of spans.filter((s) => s !== root)) {
    assert.equal(
      span.parentSpanId,
      root.spanId,
      `${span.name} should parent to the root, got ${span.parentSpanId}`,
    );
  }

  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 7: thread grouping ---
await test("threads: every span carries orq.thread_id set to the session id", async () => {
  const dir = makeTempDir();
  seedSession(dir, "sess-thread");
  const transcript = writeTranscript(dir, transcriptLines());

  await runHook(sessionEndHook, { session_id: "sess-thread", transcript_path: transcript }, dir);

  const spans = sentSpans();
  assert.ok(spans.length > 1, "expected the root plus transcript spans");
  for (const span of spans) {
    assert.equal(
      attrValue(span, "orq.thread_id"),
      "sess-thread",
      `${span.name} is missing orq.thread_id`,
    );
  }

  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 8: the root replays the whole session, not just the last turn ---
await test("root span: conversation spans every turn, including later user prompts", async () => {
  const dir = makeTempDir();
  seedSession(dir, "sess-full");
  const transcript = writeTranscript(dir, transcriptLines());

  // Turn 1 flushes through Stop, exactly as a live session would.
  await runHook(stopHook, { session_id: "sess-full", transcript_path: transcript }, dir);

  appendTranscript(transcript, [
    {
      type: "user",
      timestamp: "2026-07-23T12:00:05.000Z",
      message: { role: "user", content: "now count them" },
    },
    {
      type: "assistant",
      timestamp: "2026-07-23T12:00:06.000Z",
      message: {
        id: "msg_03",
        model: "claude-opus-4-8",
        stop_reason: "end_turn",
        usage: { input_tokens: 30, output_tokens: 4 },
        content: [{ type: "text", text: "Two." }],
      },
    },
  ]);

  await runHook(
    sessionEndHook,
    { session_id: "sess-full", transcript_path: transcript, reason: "clear" },
    dir,
  );

  const root = sentSpans().find((span) => span.name === "orq.claude_code.session");
  const conversation = jsonAttr(root, "gen_ai.input.messages");
  const roles = conversation.map((message) => message.role);

  // Both user prompts, the tool call, its result, and the closing prose.
  assert.deepEqual(roles, ["user", "assistant", "assistant", "tool", "assistant", "user", "assistant"], `got ${roles.join(",")}`);
  assert.equal(conversation[0].parts[0].content, "list the plugin files");
  assert.equal(conversation[5].parts[0].content, "now count them");
  assert.ok(
    conversation.some((m) => (m.parts ?? []).some((p) => p.type === "tool_call_response" && p.id === "toolu_01")),
    "the tool result should survive into the root conversation",
  );

  const output = jsonAttr(root, "gen_ai.output.messages");
  assert.equal(output[0].parts[0].content, "Two.", "root output should be the closing message");

  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 9: the full replay must not double-count tools or rewind the cursor ---
await test("full replay: leaves the incremental cursor and tool count untouched", async () => {
  const dir = makeTempDir();
  const stateFile = seedSession(dir, "sess-idem");
  const transcript = writeTranscript(dir, transcriptLines());

  await runHook(
    sessionEndHook,
    { session_id: "sess-idem", transcript_path: transcript, reason: "clear" },
    dir,
  );

  // State is deleted at SessionEnd, so assert on what was emitted. Span ids are
  // derived from the transcript entry, so the count of DISTINCT ids is what
  // says whether anything was double-emitted: the final chat span is
  // deliberately re-sent under the same id, carrying the thread conversation.
  const spans = sentSpans();
  const chatSpans = spans.filter((span) => span.name.startsWith("chat "));
  const toolSpans = spans.filter((span) => span.name.startsWith("execute_tool "));
  assert.equal(
    new Set(chatSpans.map((span) => span.spanId)).size,
    2,
    `expected 2 distinct chat spans, got ${new Set(chatSpans.map((s) => s.spanId)).size}`,
  );
  assert.equal(toolSpans.length, 1, `expected 1 tool span, got ${toolSpans.length}`);

  const root = spans.find((span) => span.name === "orq.claude_code.session");
  assert.equal(attrValue(root, "metadata.total_tool_calls"), "1", "tool count double-counted");
  assert.ok(!fs.existsSync(stateFile), "session state should be deleted at SessionEnd");

  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 10: a resumed session does not re-emit its existing transcript ---
await test("resume: SessionStart seeds the cursor past the existing transcript", async () => {
  const dir = makeTempDir();
  const transcript = writeTranscript(dir, transcriptLines());

  // /model and /clear delete the session state and fire SessionStart again
  // against a transcript that already holds the whole session.
  const res = await runHook(
    sessionStartHook,
    { session_id: "sess-resume", transcript_path: transcript, source: "startup" },
    dir,
  );
  assert.equal(res.status, 0, `hook exit ${res.status}: ${res.stderr}`);

  const state = JSON.parse(
    fs.readFileSync(path.join(dir, "orq_sessions", "sess-resume.json"), "utf8"),
  );
  assert.equal(state.last_processed_line, 5, "cursor should start past the existing entries");

  // The next Stop must therefore emit nothing: it is all old history.
  await runHook(stopHook, { session_id: "sess-resume", transcript_path: transcript }, dir);
  assert.equal(sentSpans().length, 0, "a resumed session must not re-send old transcript entries");

  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 11: one window cannot produce an unsendable payload ---
await test("bounds: a long window keeps every span's conversation under the cap", async () => {
  const dir = makeTempDir();
  seedSession(dir, "sess-big");

  // 60 assistant/tool pairs in a single parse window, each result ~20 KB.
  // Unbounded replay is quadratic: this window alone would exceed 30 MB.
  const bulk = [
    { type: "user", timestamp: "2026-07-23T12:00:00.000Z", message: { role: "user", content: "go" } },
  ];
  for (let i = 0; i < 60; i += 1) {
    bulk.push({
      type: "assistant",
      timestamp: `2026-07-23T12:${String(i).padStart(2, "0")}:01.000Z`,
      message: {
        id: `msg_${i}`,
        model: "claude-opus-4-8",
        stop_reason: "tool_use",
        usage: { input_tokens: 1, output_tokens: 1 },
        content: [{ type: "tool_use", id: `toolu_${i}`, name: "Bash", input: { command: "ls" } }],
      },
    });
    bulk.push({
      type: "user",
      timestamp: `2026-07-23T12:${String(i).padStart(2, "0")}:02.000Z`,
      message: {
        role: "user",
        content: [
          { type: "tool_result", tool_use_id: `toolu_${i}`, content: [{ type: "text", text: "y".repeat(20_000) }] },
        ],
      },
    });
  }
  const transcript = writeTranscript(dir, bulk);

  await runHook(stopHook, { session_id: "sess-big", transcript_path: transcript }, dir);

  const chatSpans = sentSpans().filter((span) => span.name.startsWith("chat "));
  assert.ok(chatSpans.length > 20, `expected a long window, got ${chatSpans.length} chat spans`);

  const cap = 256 * 1024;
  let total = 0;
  for (const span of chatSpans) {
    const size = Buffer.byteLength(attrValue(span, "gen_ai.input.messages") ?? "", "utf8");
    assert.ok(size <= cap * 1.1, `${span.name} carries ${size} bytes, over the ${cap} cap`);
    total += size;
  }
  // The point of the cap: total stays linear in span count, not quadratic.
  assert.ok(total < 20 * 1024 * 1024, `window total ${(total / 1024 / 1024).toFixed(1)} MB is too large`);

  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 12: an unsendable payload does not block the queue behind it ---
await test("queue: an oversized payload is split and still lands as one trace", async () => {
  const dir = makeTempDir();
  const queueDir = path.join(dir, "orq_queue");
  fs.mkdirSync(queueDir, { recursive: true });

  const TRACE = "a".repeat(32);
  const makeSpan = (name, padBytes, index) => ({
    traceId: TRACE,
    spanId: String(index).padStart(16, "0"),
    name,
    kind: 1,
    startTimeUnixNano: "1700000000000000000",
    endTimeUnixNano: "1700000001000000000",
    attributes: padBytes ? [{ key: "pad", value: { stringValue: "z".repeat(padBytes) } }] : [],
  });
  const wrap = (spans) => ({
    resourceSpans: [{
      resource: { attributes: [] },
      scopeSpans: [{ scope: { name: "orq-claude-code", version: "0.1.0" }, spans }],
    }],
  });

  // 6 spans of 3 MB: ~18 MB total, over the single-request limit, but every
  // individual span is sendable. Nothing here may be lost.
  const big = wrap(Array.from({ length: 6 }, (_, i) => makeSpan(`SPLIT-${i}`, 3 * 1024 * 1024, i)));
  fs.writeFileSync(path.join(queueDir, "1000-oversized.json"), JSON.stringify(big));
  fs.writeFileSync(path.join(queueDir, "2000-good.json"), JSON.stringify(wrap([makeSpan("BEHIND-IT", 0, 99)])));

  // state.js resolves the queue directory at module load, so the environment
  // has to be in place before the first import of it.
  process.env.ORQ_CLAUDE_STATE_DIR = dir;
  process.env.ORQ_API_KEY = "test-key";
  process.env.OTEL_EXPORTER_OTLP_ENDPOINT = endpoint;
  const { drainQueue } = await import(
    `file://${path.join(repoRoot, "src/otlp.js").replaceAll("\\", "/")}`
  );
  await drainQueue();

  assert.equal(fs.readdirSync(queueDir).length, 0, "queue should be fully drained");

  const names = sentSpans().map((span) => span.name);
  for (let i = 0; i < 6; i += 1) {
    assert.ok(names.includes(`SPLIT-${i}`), `SPLIT-${i} was lost instead of being split out`);
  }
  assert.ok(names.includes("BEHIND-IT"), "the payload behind the oversized one never sent");

  // Split across requests, but every span still carries the one trace id, so
  // it renders as a single trace.
  assert.ok(captured.length > 1, "an 18 MB payload should have gone out as several requests");
  const traceIds = new Set(sentSpans().map((span) => span.traceId));
  assert.deepEqual([...traceIds], [TRACE], "splitting must not change how many traces this is");

  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 13: endpoint resolution per environment ---
await test("endpoint: profile otlp_endpoint overrides the subdomain swap, prod unchanged", async () => {
  const dir = makeTempDir();
  const configPath = path.join(dir, "orq-config.json");
  fs.writeFileSync(configPath, JSON.stringify({
    current: "prod",
    profiles: {
      // Production derives api.orq.ai from my.orq.ai and must keep doing so.
      prod: { api_key: "sk-orq-prod", base_url: "https://my.orq.ai" },
      // Staging cannot: api.staging.orq.ai answers 526, so it names its URL.
      staging: {
        api_key: "sk-orq-staging",
        base_url: "https://my.staging.orq.ai",
        otlp_endpoint: "https://my.staging.orq.ai/v2/otel/v1/traces",
      },
      local: { api_key: "sk-orq-local", base_url: "http://localhost:5300" },
    },
  }));

  // Exercise otlp.js's own resolution, not a reimplementation of it: the bug
  // this guards against was the wiring between the two modules going missing.
  const resolve = (profile) => {
    const out = spawnSync("node", ["--input-type=module", "-e", `
      import { getEndpoint } from ${JSON.stringify(`file://${path.join(repoRoot, "src/otlp.js").replaceAll("\\", "/")}`)};
      console.log(getEndpoint());
    `], {
      encoding: "utf8",
      env: {
        ...process.env,
        ORQ_CONFIG_PATH: configPath,
        ORQ_TRACE_PROFILE: profile,
        OTEL_EXPORTER_OTLP_ENDPOINT: "",
      },
    });
    return out.stdout.trim();
  };

  assert.equal(resolve("prod"), "https://api.orq.ai/v2/otel/v1/traces", "prod must keep the subdomain swap");
  assert.equal(resolve("staging"), "https://my.staging.orq.ai/v2/otel/v1/traces", "staging must use its declared endpoint");
  assert.equal(resolve("local"), "http://localhost:5300/v2/otel/v1/traces", "local has no my. prefix to swap");

  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 14: a later window's spans start where the previous one ended ---
await test("timing: window 2 starts at window 1's end, not at session start", async () => {
  const dir = makeTempDir();
  seedSession(dir, "sess-time");
  const transcript = writeTranscript(dir, transcriptLines());

  await runHook(stopHook, { session_id: "sess-time", transcript_path: transcript }, dir);
  const sessionStart = BigInt("1700000000000000000");

  // The user sits idle for five minutes, then prompts and gets a 7s answer.
  appendTranscript(transcript, [
    {
      type: "user",
      timestamp: "2026-07-23T12:05:00.000Z",
      message: { role: "user", content: "and again" },
    },
    {
      type: "assistant",
      timestamp: "2026-07-23T12:05:07.000Z",
      message: {
        id: "msg_09",
        model: "claude-opus-4-8",
        stop_reason: "end_turn",
        usage: { input_tokens: 3, output_tokens: 2 },
        content: [{ type: "text", text: "Again." }],
      },
    },
  ]);

  const before = sentSpans().length;
  await runHook(stopHook, { session_id: "sess-time", transcript_path: transcript }, dir);
  const newSpans = sentSpans().slice(before);
  assert.equal(newSpans.length, 1, `second window should emit exactly 1 span, got ${newSpans.length}`);

  const start = BigInt(newSpans[0].startTimeUnixNano);
  assert.notEqual(start, sessionStart, "the span must not restart the clock at session start");
  assert.equal(
    start,
    BigInt(new Date("2026-07-23T12:05:00.000Z").getTime()) * 1000000n,
    "a turn-opening span should start at the prompt, not at the previous turn's end",
  );
  // 7s of model latency, not the 5 minutes the user spent typing.
  const durationMs = Number((BigInt(newSpans[0].endTimeUnixNano) - start) / 1000000n);
  assert.equal(durationMs, 7000, `duration ${durationMs}ms should be the model's latency alone`);

  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 15: the prompt is not pushed twice ---
await test("conversation: a user prompt appears exactly once per chat span", async () => {
  const dir = makeTempDir();
  seedSession(dir, "sess-dupe");
  const transcript = writeTranscript(dir, transcriptLines());

  await runHook(stopHook, { session_id: "sess-dupe", transcript_path: transcript }, dir);

  for (const span of sentSpans().filter((s) => s.name.startsWith("chat "))) {
    const messages = jsonAttr(span, "gen_ai.input.messages");
    const prompts = messages.filter(
      (m) => m.role === "user" && (m.parts ?? []).some((p) => p.content === "list the plugin files"),
    );
    assert.equal(prompts.length, 1, `prompt repeated ${prompts.length}x in ${span.name}`);
  }

  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 16: the last chat span carries the whole session (thread view) ---
await test("thread: the final chat span's history covers every turn", async () => {
  const dir = makeTempDir();
  seedSession(dir, "sess-thread-full");
  const transcript = writeTranscript(dir, transcriptLines());

  // Turn 1 flushes, then a second turn arrives in its own window.
  await runHook(stopHook, { session_id: "sess-thread-full", transcript_path: transcript }, dir);
  appendTranscript(transcript, [
    {
      type: "user",
      timestamp: "2026-07-23T12:06:00.000Z",
      message: { role: "user", content: "second question" },
    },
    {
      type: "assistant",
      timestamp: "2026-07-23T12:06:02.000Z",
      message: {
        id: "msg_10",
        model: "claude-opus-4-8",
        stop_reason: "end_turn",
        usage: { input_tokens: 3, output_tokens: 2 },
        content: [{ type: "text", text: "Second answer." }],
      },
    },
  ]);
  await runHook(stopHook, { session_id: "sess-thread-full", transcript_path: transcript }, dir);

  // The thread view renders exactly one span: the last LLM-content span.
  const chatSpans = sentSpans().filter((s) => s.name.startsWith("chat "));
  const last = chatSpans[chatSpans.length - 1];
  const messages = jsonAttr(last, "gen_ai.input.messages");
  const prompts = messages
    .filter((m) => m.role === "user")
    .flatMap((m) => (m.parts ?? []).map((p) => p.content));

  assert.deepEqual(
    prompts,
    ["list the plugin files", "second question"],
    "the final span must carry both turns' prompts for the thread view to render them",
  );
  assert.ok(
    messages.some((m) => (m.parts ?? []).some((p) => p.type === "tool_call_response")),
    "turn 1's tool result should still be present in the final span",
  );

  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 17: reasoning is replayed into later spans' history ---
await test("conversation: an earlier turn's reasoning is replayed forward", async () => {
  const dir = makeTempDir();
  seedSession(dir, "sess-reason");
  const transcript = writeTranscript(dir, [
    {
      type: "user",
      timestamp: "2026-07-23T12:00:00.000Z",
      message: { role: "user", content: "think about it" },
    },
    {
      type: "assistant",
      timestamp: "2026-07-23T12:00:01.000Z",
      message: {
        id: "msg_r1",
        model: "claude-opus-4-8",
        stop_reason: "end_turn",
        usage: { input_tokens: 5, output_tokens: 5 },
        content: [
          { type: "thinking", thinking: "Weighing the options carefully." },
          { type: "text", text: "Here is my answer." },
        ],
      },
    },
    {
      type: "user",
      timestamp: "2026-07-23T12:00:02.000Z",
      message: { role: "user", content: "and now?" },
    },
    {
      type: "assistant",
      timestamp: "2026-07-23T12:00:03.000Z",
      message: {
        id: "msg_r2",
        model: "claude-opus-4-8",
        stop_reason: "end_turn",
        usage: { input_tokens: 6, output_tokens: 3 },
        content: [{ type: "text", text: "Follow-up." }],
      },
    },
  ]);

  await runHook(stopHook, { session_id: "sess-reason", transcript_path: transcript }, dir);

  const chatSpans = sentSpans().filter((s) => s.name.startsWith("chat "));
  const last = chatSpans[chatSpans.length - 1];
  const messages = jsonAttr(last, "gen_ai.input.messages");
  const reasoning = messages
    .flatMap((m) => m.parts ?? [])
    .filter((p) => p.type === "reasoning");

  assert.equal(reasoning.length, 1, "the earlier turn's reasoning should be in the replayed history");
  assert.equal(reasoning[0].content, "Weighing the options carefully.");

  // And it is still on the span that produced it.
  const firstOutput = jsonAttr(chatSpans[0], "gen_ai.output.messages");
  assert.ok(
    firstOutput[0].parts.some((p) => p.type === "reasoning"),
    "reasoning must remain on the span that produced it",
  );

  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 18: withheld thinking is marked, never blank ---
await test("reasoning: encrypted thinking is marked [encrypted reasoning], plaintext is kept", async () => {
  const dir = makeTempDir();
  seedSession(dir, "sess-encrypted");
  const transcript = writeTranscript(dir, [
    {
      type: "user",
      timestamp: "2026-07-23T12:00:00.000Z",
      message: { role: "user", content: "think" },
    },
    {
      type: "assistant",
      timestamp: "2026-07-23T12:00:01.000Z",
      message: {
        id: "msg_e1",
        model: "claude-opus-4-8",
        stop_reason: "end_turn",
        usage: { input_tokens: 5, output_tokens: 5 },
        content: [
          // How Claude Code almost always stores it: ciphertext in signature,
          // plaintext blank.
          { type: "thinking", thinking: "", signature: "CAISwAYKkQEIEBgCKkCLX" },
          { type: "text", text: "Answer one." },
        ],
      },
    },
    {
      type: "user",
      timestamp: "2026-07-23T12:00:02.000Z",
      message: { role: "user", content: "again" },
    },
    {
      type: "assistant",
      timestamp: "2026-07-23T12:00:03.000Z",
      message: {
        id: "msg_e2",
        model: "claude-opus-4-8",
        stop_reason: "end_turn",
        usage: { input_tokens: 6, output_tokens: 4 },
        content: [
          { type: "thinking", thinking: "Actual visible reasoning.", signature: "sig" },
          { type: "text", text: "Answer two." },
        ],
      },
    },
  ]);

  await runHook(stopHook, { session_id: "sess-encrypted", transcript_path: transcript }, dir);

  const chatSpans = sentSpans().filter((s) => s.name.startsWith("chat "));
  assert.equal(chatSpans.length, 2);

  // The withheld one is still reported, marked the way the gateway marks it,
  // so the trace shows that the model reasoned rather than hiding it.
  const firstOut = jsonAttr(chatSpans[0], "gen_ai.output.messages");
  assert.deepEqual(
    firstOut[0].parts.map((p) => p.type),
    ["reasoning", "text"],
    "a withheld thinking block should still produce a reasoning part",
  );
  assert.equal(
    firstOut[0].parts[0].content,
    "[encrypted reasoning]",
    "withheld reasoning must be marked, never blank",
  );

  // The plaintext one still does.
  const secondOut = jsonAttr(chatSpans[1], "gen_ai.output.messages");
  const reasoning = secondOut[0].parts.filter((p) => p.type === "reasoning");
  assert.equal(reasoning.length, 1);
  assert.equal(reasoning[0].content, "Actual visible reasoning.");

  // No part anywhere may carry empty content.
  for (const span of chatSpans) {
    for (const key of ["gen_ai.input.messages", "gen_ai.output.messages"]) {
      for (const message of jsonAttr(span, key) ?? []) {
        for (const part of message.parts ?? []) {
          if (part.type === "text" || part.type === "reasoning") {
            assert.ok(part.content, `empty ${part.type} part in ${key} of ${span.name}`);
          }
        }
      }
    }
  }

  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 19: replaying history must not cost per-message serialization ---
await test("perf: a long transcript with one new entry stays well under budget", async () => {
  const dir = makeTempDir();
  const entries = [
    { type: "user", timestamp: "2026-07-23T12:00:00.000Z", message: { role: "user", content: "go" } },
  ];
  for (let i = 0; i < 1000; i += 1) {
    entries.push({
      type: "assistant",
      timestamp: `2026-07-23T12:00:${String(i % 60).padStart(2, "0")}.000Z`,
      message: {
        id: `msg_${i}`,
        model: "claude-opus-4-8",
        stop_reason: "end_turn",
        usage: { input_tokens: 1, output_tokens: 1 },
        content: [{ type: "text", text: "y".repeat(2000) }],
      },
    });
  }
  const transcript = writeTranscript(dir, entries);

  // Cursor sits at the end: the whole transcript is history, replayed for
  // context, and only the one appended entry is emitted.
  seedSession(dir, "sess-perf", { last_processed_line: entries.length });
  appendTranscript(transcript, [
    {
      type: "assistant",
      timestamp: "2026-07-23T13:00:00.000Z",
      message: {
        id: "msg_new",
        model: "claude-opus-4-8",
        stop_reason: "end_turn",
        usage: { input_tokens: 1, output_tokens: 1 },
        content: [{ type: "text", text: "the only new one" }],
      },
    },
  ]);

  const started = Date.now();
  const res = await runHook(stopHook, { session_id: "sess-perf", transcript_path: transcript }, dir);
  const elapsed = Date.now() - started;
  assert.equal(res.status, 0, `hook exit ${res.status}: ${res.stderr}`);

  const chatSpans = sentSpans().filter((span) => span.name.startsWith("chat "));
  assert.equal(chatSpans.length, 1, `only the new entry should be emitted, got ${chatSpans.length}`);

  // Serializing the conversation once per historical message costs ~7 s here.
  // Doing it only for emitted spans costs tens of ms. 3 s sits far below the
  // broken version and far above the fixed one, so it will not flake, and it
  // catches a whole-conversation stringify reintroduced anywhere in the loop.
  assert.ok(
    elapsed < 3000,
    `hook took ${elapsed}ms; history is being serialized per message again`,
  );

  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 20: compaction drops context, so the replay must drop it too ---
await test("compaction: messages before a compact boundary leave the conversation", async () => {
  const dir = makeTempDir();
  seedSession(dir, "sess-compact");
  const transcript = writeTranscript(dir, [
    {
      type: "user",
      timestamp: "2026-07-23T12:00:00.000Z",
      message: { role: "user", content: "the forgotten question" },
    },
    {
      type: "assistant",
      timestamp: "2026-07-23T12:00:01.000Z",
      message: {
        id: "msg_pre",
        model: "claude-opus-4-8",
        stop_reason: "end_turn",
        usage: { input_tokens: 5, output_tokens: 5 },
        content: [{ type: "text", text: "the forgotten answer" }],
      },
    },
    // Claude Code drops the context here and injects a summary as a user turn.
    {
      type: "system",
      subtype: "compact_boundary",
      timestamp: "2026-07-23T12:00:02.000Z",
      content: "Conversation compacted",
      compactMetadata: { trigger: "auto", preTokens: 1000000, postTokens: 12000 },
    },
    {
      type: "user",
      timestamp: "2026-07-23T12:00:03.000Z",
      message: {
        role: "user",
        content: "This session is being continued from a previous conversation...",
      },
    },
    {
      type: "assistant",
      timestamp: "2026-07-23T12:00:04.000Z",
      message: {
        id: "msg_post",
        model: "claude-opus-4-8",
        stop_reason: "end_turn",
        usage: { input_tokens: 6, output_tokens: 6 },
        content: [{ type: "text", text: "carrying on" }],
      },
    },
  ]);

  await runHook(stopHook, { session_id: "sess-compact", transcript_path: transcript }, dir);

  const chatSpans = sentSpans().filter((span) => span.name.startsWith("chat "));
  const post = chatSpans[chatSpans.length - 1];
  const text = JSON.stringify(jsonAttr(post, "gen_ai.input.messages"));

  assert.ok(
    !text.includes("the forgotten question") && !text.includes("the forgotten answer"),
    "pre-compaction messages are no longer in the model's context and must not be replayed",
  );
  assert.ok(
    text.includes("This session is being continued"),
    "the summary that replaced them should be the new start of the conversation",
  );

  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 21: a transcript that cannot be read must not rewind the cursor ---
await test("cursor: an unreadable transcript leaves the cursor where it was", async () => {
  const dir = makeTempDir();
  const stateFile = seedSession(dir, "sess-rewind");
  const transcript = writeTranscript(dir, transcriptLines());

  await runHook(stopHook, { session_id: "sess-rewind", transcript_path: transcript }, dir);
  const cursor = JSON.parse(fs.readFileSync(stateFile, "utf8")).last_processed_line;
  assert.ok(cursor > 0, "first Stop should have advanced the cursor");

  // A hook payload with no transcript path: one transient read failure.
  await runHook(stopHook, { session_id: "sess-rewind" }, dir);
  assert.equal(
    JSON.parse(fs.readFileSync(stateFile, "utf8")).last_processed_line,
    cursor,
    "a failed read must not rewind the cursor, or the next Stop re-sends the session",
  );

  // Proof of the consequence: nothing is re-emitted afterwards.
  captured = [];
  await runHook(stopHook, { session_id: "sess-rewind", transcript_path: transcript }, dir);
  assert.equal(sentSpans().length, 0, "the whole session was re-sent");

  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 22: an incomplete tool call is emitted even from before the cursor ---
await test("pending tools: an unfinished call still emits at session end", async () => {
  const dir = makeTempDir();
  const transcript = writeTranscript(dir, [
    { type: "user", timestamp: "2026-07-23T12:00:00.000Z", message: { role: "user", content: "run it" } },
    {
      type: "assistant",
      timestamp: "2026-07-23T12:00:01.000Z",
      message: {
        id: "msg_hang",
        model: "claude-opus-4-8",
        stop_reason: "tool_use",
        usage: { input_tokens: 1, output_tokens: 1 },
        // No tool_result ever arrives: the session ended mid-call.
        content: [{ type: "tool_use", id: "toolu_hang", name: "Bash", input: { command: "sleep 900" } }],
      },
    },
  ]);
  // Cursor already past the call, which is where it lands once the assistant
  // message has been emitted by an earlier Stop.
  seedSession(dir, "sess-pending", { last_processed_line: 3 });

  await runHook(
    sessionEndHook,
    { session_id: "sess-pending", transcript_path: transcript, reason: "clear" },
    dir,
  );

  const toolSpans = sentSpans().filter((span) => span.name.startsWith("execute_tool "));
  assert.equal(toolSpans.length, 1, "an incomplete call has never been sent, so it is always new");
  assert.equal(attrValue(toolSpans[0], "claude_code.tool.incomplete"), true);

  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 23: a response whose chunks straddle a window boundary ---
await test("streaming: a message split across two windows emits once", async () => {
  const dir = makeTempDir();
  seedSession(dir, "sess-straddle");

  // Window 1 sees only the first chunk of a streamed response.
  const firstChunk = {
    type: "assistant",
    timestamp: "2026-07-23T12:00:01.000Z",
    message: {
      id: "msg_split",
      model: "claude-opus-4-8",
      stop_reason: null,
      usage: { input_tokens: 10, output_tokens: 2 },
      content: [{ type: "text", text: "Half an ans" }],
    },
  };
  const transcript = writeTranscript(dir, [
    { type: "user", timestamp: "2026-07-23T12:00:00.000Z", message: { role: "user", content: "stream it" } },
    firstChunk,
  ]);
  await runHook(stopHook, { session_id: "sess-straddle", transcript_path: transcript }, dir);
  const afterFirst = sentSpans().filter((s) => s.name.startsWith("chat ")).length;

  // Window 2 carries the rest of the same message id.
  appendTranscript(transcript, [
    {
      type: "assistant",
      timestamp: "2026-07-23T12:00:02.000Z",
      message: {
        id: "msg_split",
        model: "claude-opus-4-8",
        stop_reason: "end_turn",
        usage: { input_tokens: 10, output_tokens: 6 },
        content: [{ type: "text", text: "Half an answer, then the rest." }],
      },
    },
  ]);
  captured = [];
  await runHook(stopHook, { session_id: "sess-straddle", transcript_path: transcript }, dir);
  const afterSecond = sentSpans().filter((s) => s.name.startsWith("chat ")).length;

  // Merging keys on message id and the merged entry keeps the first chunk's
  // line index, so the completed message must not be emitted a second time.
  assert.equal(
    afterFirst + afterSecond,
    1,
    `a straddling message produced ${afterFirst + afterSecond} chat spans, expected exactly 1`,
  );

  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 24: truncation must honour the cap and keep tool pairs intact ---
await test("truncation: the cap holds and a tool result keeps its call", async () => {
  const { fitConversation, SPAN_CONVERSATION_MAX_BYTES: CAP } = await import(
    `file://${path.join(repoRoot, "src/messages.js").replaceAll("\\", "/")}`
  );
  const sizeOf = (value) => Buffer.byteLength(JSON.stringify(value), "utf8");

  // One message far larger than the cap: dropping whole messages cannot fix
  // this, only shrinking the part content can.
  const single = [
    { role: "tool", parts: [{ type: "tool_call_response", id: "t1", response: "x".repeat(600 * 1024) }] },
  ];
  const a = fitConversation(single, CAP);
  assert.ok(sizeOf(a.messages) <= CAP, `single oversized message left at ${sizeOf(a.messages)} bytes`);
  assert.equal(a.truncated, true);

  // A call and its response, where the response alone busts the cap. Dropping
  // oldest-first would strand the response with no call to pair to, which is
  // exactly the bug the parts shape exists to fix.
  const paired = [
    { role: "user", parts: [{ type: "text", content: "go" }] },
    { role: "assistant", parts: [{ type: "tool_call", id: "toolu_1", name: "Bash", arguments: {} }] },
    { role: "tool", parts: [{ type: "tool_call_response", id: "toolu_1", response: "y".repeat(300 * 1024) }] },
  ];
  const b = fitConversation(paired, CAP);
  assert.ok(sizeOf(b.messages) <= CAP, `paired conversation left at ${sizeOf(b.messages)} bytes`);

  const parts = b.messages.flatMap((message) => message.parts ?? []);
  const calls = parts.filter((part) => part.type === "tool_call").map((part) => part.id);
  const responses = parts.filter((part) => part.type === "tool_call_response").map((part) => part.id);
  const orphans = responses.filter((id) => !calls.includes(id));
  assert.deepEqual(orphans, [], "a tool response outlived the call it answers");

  // A conversation already under the cap is returned untouched and unflagged.
  const small = [{ role: "user", parts: [{ type: "text", content: "hi" }] }];
  assert.equal(fitConversation(small, CAP).truncated, false);
});

// --- Test 25: a partial drain must not re-send what already landed ---
await test("queue: a drain that fails halfway does not re-send delivered spans", async () => {
  const dir = makeTempDir();
  const queueDir = path.join(dir, "orq_queue");
  fs.mkdirSync(queueDir, { recursive: true });

  const span = (name, pad, index) => ({
    traceId: "a".repeat(32),
    spanId: String(index).padStart(16, "0"),
    name,
    kind: 1,
    startTimeUnixNano: "1700000000000000000",
    endTimeUnixNano: "1700000001000000000",
    attributes: [{ key: "pad", value: { stringValue: "z".repeat(pad) } }],
  });
  // 3 spans of 3 MB: one legacy-style file that re-chunks into three requests.
  fs.writeFileSync(path.join(queueDir, "1.json"), JSON.stringify({
    resourceSpans: [{
      resource: { attributes: [] },
      scopeSpans: [{
        scope: { name: "orq-claude-code", version: "0.1.0" },
        spans: [span("A", 3 * 1024 * 1024, 0), span("B", 3 * 1024 * 1024, 1), span("C", 3 * 1024 * 1024, 2)],
      }],
    }],
  }));

  // Fail every request after the first, so exactly one batch lands.
  let requests = 0;
  const flaky = http.createServer((req, res) => {
    req.on("data", () => {});
    req.on("end", () => {
      requests += 1;
      if (requests > 1) {
        res.writeHead(503).end("later");
        return;
      }
      res.writeHead(200).end("{}");
    });
  });
  await new Promise((resolve) => flaky.listen(0, "127.0.0.1", resolve));

  // A fresh process, spawned asynchronously: state.js resolves the queue
  // directory at module load, so an already-imported copy would drain a
  // different one, and a synchronous spawn would block this process's event
  // loop, leaving the server above unable to answer the child.
  await new Promise((resolve) => {
    const child = spawn("node", ["--input-type=module", "-e", `
      const { drainQueue } = await import(${JSON.stringify(`file://${path.join(repoRoot, "src/otlp.js").replaceAll("\\", "/")}`)});
      await drainQueue();
    `], {
      env: {
        ...process.env,
        ORQ_CLAUDE_STATE_DIR: dir,
        ORQ_API_KEY: "test-key",
        OTEL_EXPORTER_OTLP_ENDPOINT: `http://127.0.0.1:${flaky.address().port}/v1/traces`,
      },
    });
    child.stderr.on("data", () => {});
    child.on("close", resolve);
  });

  const left = fs.readdirSync(queueDir);
  assert.equal(left.length, 1, "the file should be kept while the endpoint is down");

  const remaining = JSON.parse(fs.readFileSync(path.join(queueDir, left[0]), "utf8"));
  const names = remaining.resourceSpans
    .flatMap((r) => r.scopeSpans.flatMap((s) => s.spans))
    .map((s) => s.name);
  assert.deepEqual(names, ["B", "C"], "the delivered span is still queued and would be re-sent");

  flaky.close();
  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 26: send while offline, then drain, spans must survive ---
// The round trip, not either half: sendSpans and drainQueue have to agree on
// the envelope, and nothing else in the suite checks that they do.
await test("queue round trip: spans queued while offline are delivered later", async () => {
  const dir = makeTempDir();
  fs.mkdirSync(path.join(dir, "orq_queue"), { recursive: true });

  let reachable = false;
  const delivered = [];
  const flaky = http.createServer((req, res) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      if (!reachable) {
        res.writeHead(503).end("down");
        return;
      }
      const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      delivered.push(
        ...(body.resourceSpans ?? [])
          .flatMap((r) => r.scopeSpans.flatMap((s) => s.spans ?? []))
          .map((s) => s.name),
      );
      res.writeHead(200).end("{}");
    });
  });
  await new Promise((resolve) => flaky.listen(0, "127.0.0.1", resolve));

  const runInChild = (source) => new Promise((resolve) => {
    const child = spawn("node", ["--input-type=module", "-e", source], {
      env: {
        ...process.env,
        ORQ_CLAUDE_STATE_DIR: dir,
        ORQ_API_KEY: "test-key",
        OTEL_EXPORTER_OTLP_ENDPOINT: `http://127.0.0.1:${flaky.address().port}/v1/traces`,
      },
    });
    child.stderr.on("data", () => {});
    child.on("close", resolve);
  });
  const otlpUrl = JSON.stringify(`file://${path.join(repoRoot, "src/otlp.js").replaceAll("\\", "/")}`);

  // Endpoint is down: the span must end up queued.
  await runInChild(`
    const { sendSpans } = await import(${otlpUrl});
    await sendSpans([{ traceId: "a".repeat(32), spanId: "b".repeat(16), name: "chat offline",
      kind: 1, startTimeUnixNano: "1700000000000000000", endTimeUnixNano: "1700000001000000000", attributes: [] }]);
  `);
  assert.equal(fs.readdirSync(path.join(dir, "orq_queue")).length, 1, "a failed send should be queued");

  // Endpoint recovers: the queued span must actually arrive.
  reachable = true;
  await runInChild(`
    const { drainQueue } = await import(${otlpUrl});
    await drainQueue();
  `);

  assert.deepEqual(delivered, ["chat offline"], "the queued span never reached the endpoint");
  assert.equal(fs.readdirSync(path.join(dir, "orq_queue")).length, 0, "a delivered file should be removed");

  flaky.close();
  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 27: the root span must have no parent ---
// The traces list only returns rows whose parent_id is empty. A root that
// parents to itself is stored fine and read back fine by trace id, but never
// appears in the list, so the whole session looks like it vanished.
await test("root span: carries no parent, or the traces list hides the session", async () => {
  const dir = makeTempDir();
  seedSession(dir, "sess-rootparent");
  const transcript = writeTranscript(dir, transcriptLines());

  await runHook(
    sessionEndHook,
    { session_id: "sess-rootparent", transcript_path: transcript, reason: "clear" },
    dir,
  );

  const root = sentSpans().find((span) => span.name === "orq.claude_code.session");
  assert.ok(root, "root span not sent");
  assert.ok(
    root.parentSpanId === undefined || root.parentSpanId === null || root.parentSpanId === "",
    `root span parents to ${root.parentSpanId}; it must be parentless`,
  );
  assert.notEqual(root.parentSpanId, root.spanId, "the root span is its own parent");

  // And every other span still hangs off it.
  for (const span of sentSpans().filter((s) => s !== root)) {
    assert.equal(span.parentSpanId, root.spanId, `${span.name} should parent to the root`);
  }

  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 28: session counters must survive ingest ---
// Ingest drops the whole claude_code.* namespace on a trace row while keeping
// metadata.*. Writing the counters under claude_code.* put them nowhere, which
// also invalidated the reason given for removing the per-turn spans.
await test("root span: session counters are keyed where ingest keeps them", async () => {
  const dir = makeTempDir();
  seedSession(dir, "sess-meta", { turn_count: 3 });
  const transcript = writeTranscript(dir, transcriptLines());

  await runHook(
    sessionEndHook,
    { session_id: "sess-meta", transcript_path: transcript, reason: "clear" },
    dir,
  );

  const root = sentSpans().find((span) => span.name === "orq.claude_code.session");
  assert.ok(root, "root span not sent");

  for (const key of ["metadata.total_turns", "metadata.total_tool_calls", "metadata.end_reason"]) {
    assert.notEqual(attrValue(root, key), undefined, `${key} missing from the root span`);
  }
  const dropped = (root.attributes ?? [])
    .map((a) => a.key)
    .filter((key) => key.startsWith("claude_code."));
  assert.deepEqual(dropped, [], `these would be discarded by ingest: ${dropped.join(", ")}`);
});

// --- Test 29: span ids are derived, so a repeat is an upsert ---
await test("span ids: the same transcript entry always yields the same span id", async () => {
  const dir = makeTempDir();
  seedSession(dir, "sess-stable");
  const transcript = writeTranscript(dir, transcriptLines());

  await runHook(stopHook, { session_id: "sess-stable", transcript_path: transcript }, dir);
  const first = sentSpans().map((span) => `${span.name}#${span.spanId}`).sort();

  // Same transcript, cursor rewound: the replay must reproduce the same ids
  // rather than minting a second span per message.
  captured = [];
  seedSession(dir, "sess-stable");
  await runHook(stopHook, { session_id: "sess-stable", transcript_path: transcript }, dir);
  const second = sentSpans().map((span) => `${span.name}#${span.spanId}`).sort();

  assert.deepEqual(second, first, "re-emitting the same entries produced different span ids");
});

// --- Test 30: a span is sent once ---
// Storage keeps every publish and no read path collapses duplicates, so a
// re-send is either swallowed by JetStream's 120 s window or stored twice, and
// a second row draws the span and its subtree twice in the waterfall.
await test("session end: does not re-send a span an earlier Stop already emitted", async () => {
  const dir = makeTempDir();
  seedSession(dir, "sess-thread-cap");

  // A conversation far past the 256 KB per-span budget.
  const entries = [
    { type: "user", timestamp: "2026-07-23T12:00:00.000Z", message: { role: "user", content: "start" } },
  ];
  for (let i = 0; i < 40; i += 1) {
    entries.push({
      type: "assistant",
      timestamp: `2026-07-23T12:00:${String(i + 1).padStart(2, "0")}.000Z`,
      message: {
        id: `msg_${i}`,
        model: "claude-opus-4-8",
        stop_reason: "end_turn",
        usage: { input_tokens: 1, output_tokens: 1 },
        content: [{ type: "text", text: `answer ${i} ` + "z".repeat(20_000) }],
      },
    });
  }
  const transcript = writeTranscript(dir, entries);

  await runHook(stopHook, { session_id: "sess-thread-cap", transcript_path: transcript }, dir);
  const chatSpans = sentSpans().filter((s) => s.name.startsWith("chat "));
  const lastFromStop = chatSpans[chatSpans.length - 1];
  const truncatedAtStop = jsonAttr(lastFromStop, "gen_ai.input.messages").length;

  captured = [];
  await runHook(
    sessionEndHook,
    { session_id: "sess-thread-cap", transcript_path: transcript, reason: "clear" },
    dir,
  );

  const reSent = sentSpans().filter((s) => s.spanId === lastFromStop.spanId);
  assert.equal(reSent.length, 0, "a span already sent must not be sent again");
  assert.ok(truncatedAtStop > 0, "the span Stop emitted should carry a conversation");

  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 31: an oversized tool_call must not strand its response ---
await test("truncation: a huge tool_call argument keeps its pair", async () => {
  const { fitConversation, SPAN_CONVERSATION_MAX_BYTES: CAP } = await import(
    `file://${path.join(repoRoot, "src/messages.js").replaceAll("\\", "/")}`
  );
  const conversation = [
    { role: "user", parts: [{ type: "text", content: "write it" }] },
    { role: "assistant", parts: [{ type: "tool_call", id: "tu_1", name: "Write", arguments: { content: "x".repeat(400 * 1024) } }] },
    { role: "tool", parts: [{ type: "tool_call_response", id: "tu_1", response: "File written" }] },
  ];
  const { messages } = fitConversation(conversation, CAP);
  const size = Buffer.byteLength(JSON.stringify(messages), "utf8");
  assert.ok(size <= CAP, `left at ${size} bytes, over the ${CAP} cap`);

  const parts = messages.flatMap((m) => m.parts ?? []);
  const calls = parts.filter((p) => p.type === "tool_call").map((p) => p.id);
  const responses = parts.filter((p) => p.type === "tool_call_response").map((p) => p.id);
  assert.deepEqual(responses.filter((id) => !calls.includes(id)), [], "response outlived its call");
});

// --- Test 32: a Skill with no body must not eat the next prompt ---
await test("skill: an unanswered Skill call does not swallow the following prompt", async () => {
  const dir = makeTempDir();
  seedSession(dir, "sess-skill");
  const transcript = writeTranscript(dir, [
    { type: "user", timestamp: "2026-07-23T12:00:00.000Z", message: { role: "user", content: "/review" } },
    {
      type: "assistant",
      timestamp: "2026-07-23T12:00:01.000Z",
      message: {
        id: "msg_s1", model: "claude-opus-4-8", stop_reason: "tool_use",
        usage: { input_tokens: 1, output_tokens: 1 },
        content: [{ type: "tool_use", id: "toolu_s", name: "Skill", input: { skill: "review" } }],
      },
    },
    {
      type: "user",
      timestamp: "2026-07-23T12:00:02.000Z",
      message: { role: "user", content: [{ type: "tool_result", tool_use_id: "toolu_s", content: [{ type: "text", text: "Launching skill: review" }] }] },
    },
    // The body never arrives; this is the user's next typed prompt.
    { type: "user", timestamp: "2026-07-23T12:00:03.000Z", message: { role: "user", content: "now fix the login bug" } },
    {
      type: "assistant",
      timestamp: "2026-07-23T12:00:04.000Z",
      message: {
        id: "msg_s2", model: "claude-opus-4-8", stop_reason: "end_turn",
        usage: { input_tokens: 2, output_tokens: 2 },
        content: [{ type: "text", text: "on it" }],
      },
    },
  ]);

  await runHook(stopHook, { session_id: "sess-skill", transcript_path: transcript }, dir);

  const chatSpans = sentSpans().filter((s) => s.name.startsWith("chat "));
  const conversation = jsonAttr(chatSpans[chatSpans.length - 1], "gen_ai.input.messages");
  const prompts = conversation
    .filter((m) => m.role === "user")
    .flatMap((m) => (m.parts ?? []).map((p) => p.content));
  assert.ok(prompts.includes("now fix the login bug"), `typed prompt was swallowed; saw ${JSON.stringify(prompts)}`);

  const skillSpan = sentSpans().find((s) => s.name === "execute_tool Skill");
  assert.ok(
    !String(attrValue(skillSpan, "gen_ai.tool.call.result") ?? "").includes("now fix the login bug"),
    "the typed prompt was glued onto the Skill span",
  );

  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 33: an endpoint that never answers must not cost the window ---
// undici waits 300 s by default while Claude Code kills the hook at 30 s. The
// send has to give up inside that budget so the spans still reach the queue,
// and the window must stay unconsumed if the hook is killed before it does.
await test("send: a black-hole endpoint gives up in time, and a killed hook keeps its window", async () => {
  // Accepts the connection, reads the body, and never writes a response.
  const blackHole = http.createServer(() => {});
  await new Promise((resolve) => blackHole.listen(0, "127.0.0.1", resolve));
  const deadEndpoint = `http://127.0.0.1:${blackHole.address().port}/v1/traces`;

  const runAgainstBlackHole = (stateDir, killAfterMs) => new Promise((resolve) => {
    const child = spawn("node", [stopHook], {
      env: {
        ...process.env,
        ORQ_CLAUDE_STATE_DIR: stateDir,
        ORQ_API_KEY: "test-key",
        OTEL_EXPORTER_OTLP_ENDPOINT: deadEndpoint,
      },
    });
    child.stderr.on("data", () => {});
    // Claude Code kills a hook that overruns; this is that kill, early.
    const killer = killAfterMs ? setTimeout(() => child.kill("SIGKILL"), killAfterMs) : null;
    child.on("close", () => {
      if (killer) clearTimeout(killer);
      resolve();
    });
    child.stdin.end(JSON.stringify({ session_id: path.basename(stateDir), transcript_path: path.join(stateDir, "t.jsonl") }));
  });

  const cursorOf = (stateDir, sessionId) =>
    JSON.parse(fs.readFileSync(path.join(stateDir, "orq_sessions", `${sessionId}.json`), "utf8"))
      .last_processed_line;

  // It gives up on its own: the spans land in the queue and the window is
  // consumed, because the queue now owns delivery.
  const dir = makeTempDir();
  const sessionId = path.basename(dir);
  seedSession(dir, sessionId);
  fs.writeFileSync(path.join(dir, "t.jsonl"), transcriptLines().map((e) => `${JSON.stringify(e)}\n`).join(""));

  const startedAt = process.hrtime.bigint();
  await runAgainstBlackHole(dir, null);
  const elapsedMs = Number(process.hrtime.bigint() - startedAt) / 1e6;

  assert.ok(elapsedMs < 25_000, `the send ran ${Math.round(elapsedMs / 1000)} s, past the hook budget`);
  assert.equal(fs.readdirSync(path.join(dir, "orq_queue")).length, 1, "the spans should have been queued");
  assert.ok(cursorOf(dir, sessionId) > 0, "queued spans are delivered, so the window is consumed");

  // Killed before it gives up: nothing was sent or queued, so the window must
  // still be there for the next Stop to re-emit.
  const killedDir = makeTempDir();
  const killedSession = path.basename(killedDir);
  seedSession(killedDir, killedSession);
  fs.writeFileSync(path.join(killedDir, "t.jsonl"), transcriptLines().map((e) => `${JSON.stringify(e)}\n`).join(""));

  await runAgainstBlackHole(killedDir, 2000);

  assert.equal(cursorOf(killedDir, killedSession), 0, "a killed send must not consume the window");
  assert.ok(
    !fs.existsSync(path.join(killedDir, "orq_queue")) ||
      fs.readdirSync(path.join(killedDir, "orq_queue")).length === 0,
    "nothing was queued, which is why the window has to be kept",
  );

  blackHole.close();
  fs.rmSync(dir, { recursive: true, force: true });
  fs.rmSync(killedDir, { recursive: true, force: true });
});

// --- Test 35: the tool output must survive whole ---
// The state file caps what it stores, because it is rewritten on every hook
// fire. That cap is bookkeeping and must never be the reason a trace shows less
// than the tool produced: the transcript still holds the whole thing, so the
// span takes it from there. History replays it whole too, and is only
// shortened once a conversation has actually blown its span budget.
await test("tool output: neither the span nor its history loses what the tool produced", async () => {
  const dir = makeTempDir();
  seedSession(dir, "sess-whole");

  const body = "OUTPUT LINE ".repeat(3_000); // ~36 KB, past the 10 KB state cap
  const transcript = writeTranscript(dir, [
    { type: "user", timestamp: "2026-07-23T12:00:00.000Z", message: { role: "user", content: "read it" } },
    {
      type: "assistant",
      timestamp: "2026-07-23T12:00:01.000Z",
      message: {
        id: "msg_w1", model: "claude-opus-4-8", stop_reason: "tool_use",
        usage: { input_tokens: 1, output_tokens: 1 },
        content: [{ type: "tool_use", id: "toolu_w", name: "Bash", input: { command: "cat big.txt" } }],
      },
    },
    {
      type: "user",
      timestamp: "2026-07-23T12:00:02.000Z",
      message: { role: "user", content: [{ type: "tool_result", tool_use_id: "toolu_w", content: body }] },
    },
    {
      type: "assistant",
      timestamp: "2026-07-23T12:00:03.000Z",
      message: {
        id: "msg_w2", model: "claude-opus-4-8", stop_reason: "end_turn",
        usage: { input_tokens: 1, output_tokens: 1 },
        content: [{ type: "text", text: "done" }],
      },
    },
  ]);

  // The state copy is capped, exactly as PostToolUse would have left it.
  const stateFile = path.join(dir, "orq_sessions", "sess-whole.json");
  const state = JSON.parse(fs.readFileSync(stateFile, "utf8"));
  state.successful_tool_calls = [
    {
      tool_use_id: "toolu_w",
      tool_name: "Bash",
      tool_input: { command: "cat big.txt" },
      tool_input_size_bytes: 30,
      tool_response: `${body.slice(0, 10_240)}... [truncated]`,
      tool_response_size_bytes: Buffer.byteLength(body, "utf8"),
      timestamp: "1700000002000000000",
    },
  ];
  fs.writeFileSync(stateFile, JSON.stringify(state));

  await runHook(stopHook, { session_id: "sess-whole", transcript_path: transcript }, dir);

  const toolSpan = sentSpans().find((s) => s.name === "execute_tool Bash");
  assert.ok(toolSpan, "the tool span should have been emitted");
  const result = String(attrValue(toolSpan, "gen_ai.tool.call.result") ?? "");
  assert.equal(result, body, "the tool span must carry the payload the tool actually produced");
  assert.ok(!result.includes("[truncated]"), "the tool span must not carry an elided copy");

  const chat = sentSpans().filter((s) => s.name.startsWith("chat ")).at(-1);
  const response = jsonAttr(chat, "gen_ai.input.messages")
    .flatMap((m) => m.parts ?? [])
    .find((p) => p.type === "tool_call_response");
  assert.ok(response, "history must still pair the response to its call");
  assert.equal(
    String(response.response),
    body,
    "history is well under its span budget here, so it must carry the payload whole",
  );

  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 36: a cap means the size that was asked for ---
// Three of the four truncation sites used to append the marker after cutting at
// the cap, so they returned cap + 15 bytes. Now they share one helper.
await test("truncation: shortening to a cap includes the marker in the cap", async () => {
  const { ELISION, truncateBytes } = await import(
    `file://${path.join(repoRoot, "src/common.js").replaceAll("\\", "/")}`
  );

  for (const [label, text, cap] of [
    ["plain", "x".repeat(20_000), 10_240],
    ["multibyte", "é".repeat(6_000), 10_240],
    ["cap under the marker", "z".repeat(100), 5],
  ]) {
    const out = truncateBytes(text, cap);
    assert.ok(
      Buffer.byteLength(out, "utf8") <= cap,
      `${label}: ${Buffer.byteLength(out, "utf8")} bytes against a ${cap} cap`,
    );
    assert.ok(!out.includes("�"), `${label}: a byte cut left a replacement character`);
  }

  assert.equal(truncateBytes("short", 10_240), "short", "a value under the cap is untouched");
  assert.ok(truncateBytes("x".repeat(20_000), 10_240).endsWith(ELISION), "a cut value says so");
});

server.close();
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
