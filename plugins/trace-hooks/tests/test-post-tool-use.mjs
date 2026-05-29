#!/usr/bin/env node
// Verify the PostToolUse hook: payload shape, redaction, size cap, edge cases.
//
// Runs the hook entry script (hooks/post-tool-use.js) as a child process with
// a synthetic CC PostToolUse payload on stdin and a temp ORQ_CLAUDE_STATE_DIR
// so the on-disk session state is isolated from the user's real sessions.
//
// Usage: node test-post-tool-use.mjs

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import assert from "node:assert/strict";

const repoRoot = path.resolve(import.meta.dirname, "..");
const hookScript = path.join(repoRoot, "hooks/post-tool-use.js");

let passed = 0;
let failed = 0;

function makeTempStateDir() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "orq-trace-test-"));
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
    current_turn_input: "test",
    model: "claude",
    subagents: {},
    ...overrides,
  };
  const file = path.join(stateDir, "orq_sessions", `${sessionId}.json`);
  fs.writeFileSync(file, JSON.stringify(state, null, 2));
  return file;
}

function runHook(payload, stateDir, extraEnv = {}) {
  const result = spawnSync("node", [hookScript], {
    input: JSON.stringify(payload),
    env: {
      ...process.env,
      ORQ_CLAUDE_STATE_DIR: stateDir,
      // Fake key so runSafely's enabledTracing() returns true and the handler runs.
      ORQ_API_KEY: "test-key",
      ...extraEnv,
    },
    encoding: "utf8",
  });
  return result;
}

function loadState(stateFile) {
  return JSON.parse(fs.readFileSync(stateFile, "utf8"));
}

function test(name, fn) {
  try {
    fn();
    console.log(`PASS: ${name}`);
    passed++;
  } catch (err) {
    console.error(`FAIL: ${name}`);
    console.error(`  ${err?.message || err}`);
    if (err?.stack) console.error(err.stack.split("\n").slice(1, 4).join("\n"));
    failed++;
  }
}

// --- Test 1: basic record shape ---
test("basic record: pushes entry with all expected fields", () => {
  const dir = makeTempStateDir();
  const stateFile = seedSession(dir, "sess1");
  const res = runHook({
    session_id: "sess1",
    tool_name: "Bash",
    tool_use_id: "toolu_01",
    tool_input: { command: "ls" },
    tool_response: "README.md\nplugin.json\n",
  }, dir);
  assert.equal(res.status, 0, `hook exit ${res.status}: ${res.stderr}`);
  const state = loadState(stateFile);
  assert.equal(state.successful_tool_calls.length, 1);
  const entry = state.successful_tool_calls[0];
  assert.equal(entry.tool_use_id, "toolu_01");
  assert.equal(entry.tool_name, "Bash");
  assert.deepEqual(entry.tool_input, { command: "ls" });
  assert.equal(entry.tool_response, "README.md\nplugin.json\n");
  assert.equal(typeof entry.tool_input_size_bytes, "number");
  assert.equal(typeof entry.tool_response_size_bytes, "number");
  assert.ok(entry.tool_response_size_bytes > 0);
  assert.equal(typeof entry.timestamp, "string");
  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 2: large response is truncated, size_bytes preserves original ---
test("size cap: tool_response truncated, size_bytes reflects original", () => {
  const dir = makeTempStateDir();
  const stateFile = seedSession(dir, "sess2");
  const big = "x".repeat(50_000);
  const res = runHook({
    session_id: "sess2",
    tool_name: "Read",
    tool_use_id: "toolu_02",
    tool_input: { file_path: "/big" },
    tool_response: big,
  }, dir);
  assert.equal(res.status, 0, res.stderr);
  const entry = loadState(stateFile).successful_tool_calls[0];
  assert.ok(entry.tool_response.length < big.length, "should be truncated");
  assert.ok(entry.tool_response.endsWith("[truncated]"), "should have truncation marker");
  assert.equal(entry.tool_response_size_bytes, 50_000, "size_bytes must reflect original");
  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 3: secrets in tool_response are redacted ---
test("redaction: API key in tool_response is redacted", () => {
  const dir = makeTempStateDir();
  const stateFile = seedSession(dir, "sess3");
  const res = runHook({
    session_id: "sess3",
    tool_name: "Bash",
    tool_use_id: "toolu_03",
    tool_input: { command: "echo secret" },
    tool_response: "leaked: sk-abc123def456ghi789jkl012",
  }, dir);
  assert.equal(res.status, 0, res.stderr);
  const entry = loadState(stateFile).successful_tool_calls[0];
  assert.ok(!entry.tool_response.includes("sk-abc123def456ghi789jkl012"),
    `raw key should not appear in stored response, got: ${entry.tool_response}`);
  assert.ok(entry.tool_response.includes("[REDACTED]"), "should contain redaction marker");
  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 4: missing session state → noop (no crash) ---
test("edge: nonexistent session exits cleanly without state", () => {
  const dir = makeTempStateDir();
  const res = runHook({
    session_id: "does-not-exist",
    tool_name: "Bash",
    tool_response: "x",
  }, dir);
  assert.equal(res.status, 0, res.stderr);
  // No session file should have been created
  const files = fs.readdirSync(path.join(dir, "orq_sessions"));
  assert.deepEqual(files, []);
  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 5: session without open turn → noop ---
test("edge: session without open turn does not mutate state", () => {
  const dir = makeTempStateDir();
  const stateFile = seedSession(dir, "sess5", { current_turn_span_id: null });
  const before = loadState(stateFile);
  const res = runHook({
    session_id: "sess5",
    tool_name: "Bash",
    tool_response: "x",
  }, dir);
  assert.equal(res.status, 0, res.stderr);
  const after = loadState(stateFile);
  assert.equal(after.successful_tool_calls, undefined,
    "should not have added successful_tool_calls when no turn is open");
  assert.deepEqual(after, before, "state should be unchanged");
  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 6: missing session_id in payload → silent noop ---
test("edge: payload without session_id returns without error", () => {
  const dir = makeTempStateDir();
  const res = runHook({
    tool_name: "Bash",
    tool_response: "x",
  }, dir);
  assert.equal(res.status, 0, res.stderr);
  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 7: multiple calls append in order ---
test("append: two PostToolUse calls produce two entries in order", () => {
  const dir = makeTempStateDir();
  const stateFile = seedSession(dir, "sess7");
  runHook({
    session_id: "sess7", tool_name: "Bash", tool_use_id: "t1", tool_response: "a",
  }, dir);
  runHook({
    session_id: "sess7", tool_name: "Read", tool_use_id: "t2", tool_response: "b",
  }, dir);
  const calls = loadState(stateFile).successful_tool_calls;
  assert.equal(calls.length, 2);
  assert.equal(calls[0].tool_use_id, "t1");
  assert.equal(calls[1].tool_use_id, "t2");
  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 8: tool_input redaction ---
test("redaction: API key in tool_input is redacted", () => {
  const dir = makeTempStateDir();
  const stateFile = seedSession(dir, "sess8");
  const res = runHook({
    session_id: "sess8",
    tool_name: "Bash",
    tool_use_id: "toolu_08",
    tool_input: { command: "curl -H 'Authorization: Bearer sk-abc123def456ghi789jkl012' https://api.example.com" },
    tool_response: "ok",
  }, dir);
  assert.equal(res.status, 0, res.stderr);
  const entry = loadState(stateFile).successful_tool_calls[0];
  const inputStr = JSON.stringify(entry.tool_input);
  assert.ok(!inputStr.includes("sk-abc123def456ghi789jkl012"),
    `raw key should not appear in stored input, got: ${inputStr}`);
  assert.ok(inputStr.includes("[REDACTED]"), "tool_input should contain redaction marker");
  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 9: ORQ_TRACE_STATE_MAX_FIELD_CHARS env override ---
test("env override: ORQ_TRACE_STATE_MAX_FIELD_CHARS caps at custom value", () => {
  const dir = makeTempStateDir();
  const stateFile = seedSession(dir, "sess9");
  const res = runHook({
    session_id: "sess9",
    tool_name: "Read",
    tool_use_id: "toolu_09",
    tool_input: { file_path: "/f" },
    tool_response: "a".repeat(200),
  }, dir, { ORQ_TRACE_STATE_MAX_FIELD_CHARS: "50" });
  assert.equal(res.status, 0, res.stderr);
  const entry = loadState(stateFile).successful_tool_calls[0];
  assert.ok(entry.tool_response.length < 200, "response should be truncated by custom cap");
  assert.ok(entry.tool_response.endsWith("[truncated]"), "should have truncation marker");
  assert.equal(entry.tool_response_size_bytes, 200, "size_bytes must reflect original");
  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 10: camelCase payload aliases (toolUseId / toolInput / toolResponse) ---
test("aliases: camelCase payload keys are accepted", () => {
  const dir = makeTempStateDir();
  const stateFile = seedSession(dir, "sess10");
  const res = runHook({
    session_id: "sess10",
    toolName: "Bash",
    toolUseId: "toolu_10",
    toolInput: { command: "pwd" },
    toolResponse: "/home/user",
  }, dir);
  assert.equal(res.status, 0, res.stderr);
  const entry = loadState(stateFile).successful_tool_calls[0];
  assert.equal(entry.tool_use_id, "toolu_10");
  assert.deepEqual(entry.tool_input, { command: "pwd" });
  assert.equal(entry.tool_response, "/home/user");
  fs.rmSync(dir, { recursive: true, force: true });
});

// --- Test 11: object tool_response is serialized without crashing ---
test("object tool_response: JSON-serializable object is stored correctly", () => {
  const dir = makeTempStateDir();
  const stateFile = seedSession(dir, "sess11");
  const res = runHook({
    session_id: "sess11",
    tool_name: "mcp__orq-workspace__list_skills",
    tool_use_id: "toolu_11",
    tool_input: {},
    tool_response: { data: [{ skill_id: "abc", display_name: "my-skill" }], has_more: false },
  }, dir);
  assert.equal(res.status, 0, res.stderr);
  const entry = loadState(stateFile).successful_tool_calls[0];
  // stored value may be the object itself or its JSON string — either is valid
  const stored = typeof entry.tool_response === "string"
    ? entry.tool_response
    : JSON.stringify(entry.tool_response);
  assert.ok(stored.includes("my-skill"), `object response should be stored; got: ${stored}`);
  fs.rmSync(dir, { recursive: true, force: true });
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
