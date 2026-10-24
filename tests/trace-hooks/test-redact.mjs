#!/usr/bin/env node
// Verify the value-side redactor against the secret shapes a coding session
// actually carries, and against the prose it must leave alone. Tool input and
// output reach deepRedact as free-text strings, where the key-name pattern
// never fires, so this file is the only thing standing between a pasted
// provider key and the trace.
//
// Usage: node test-redact.mjs

import { deepRedact } from "../../plugins/trace-hooks/src/redact.js";

// These explicit example fixtures use obvious placeholders rather than
// fabricated credential-like bodies. The redactor only needs the provider prefix and
// minimum shape to exercise each matcher. Its `sk-` and bearer guards also
// require a digit, so those cases include one digit derived from a test value.
const digitForGuard = (testValue) => String(testValue);
const jwtExample = `eyJ${"x".repeat(8)}.eyJEXAMPLE.${"x".repeat(8)}`;

const SECRETS = [
  ["anthropic key", `sk-ant-api03-${"x".repeat(40)}-EXAMPLE`],
  ["openai project key", `sk-proj-${"x".repeat(20)}${digitForGuard(7)}`],
  ["anthropic key with an underscore early in the body", `sk-ant-api03-x_${"x".repeat(30)}`],
  ["openai project key with an underscore early in the body", `sk-proj-x_${"x".repeat(14)}${digitForGuard(7)}`],
  ["openai classic key", `sk-${"x".repeat(15)}${digitForGuard(7)}`],
  ["orq key with prefix", `sk-orq-${jwtExample}`],
  ["orq key from the dashboard", jwtExample],
  ["bearer header in a shell command", `curl -H "Authorization: Bearer ${"x".repeat(20)}${digitForGuard(7)}" https://api.orq.ai`],
  ["github token", `ghp_${"x".repeat(36)}`],
  ["aws access key id", `AKIA${"X".repeat(16)}`],
  ["private key block", `-----BEGIN RSA PRIVATE KEY----- EXAMPLE`],
  ["slack token", "xoxb-EXAMPLE"],
  ["stripe live key", "sk_live_EXAMPLE"],
  ["fine-grained github pat", `github_pat_${"x".repeat(30)}`],
  ["google api key", `AIza${"x".repeat(35)}`],
  ["huggingface token", `hf_${"x".repeat(30)}`],
  ["dotenv path", "/Users/dev/project/.env"],
];

// Prose that reads like a secret to a pattern that is too eager. Redacting
// these costs the trace its content for nothing.
const KEEP = [
  ["the word bearer in prose", "Bearer authentication is required for this route"],
  ["a plain sentence", "the session ran ls and counted the files"],
  ["a source path", "/Users/dev/orq-cli/cli/custom/launch/telemetry.go"],
  ["a long word", "internationalization and localization"],
  ["a git sha", "a".repeat(40)],
  ["a kebab-case script name", "npm run task-runner-configuration"],
  ["a kebab-case filename", "see risk-assessment-framework.md"],
  ["a kebab-case css class", '<li class="task-list-item-checkbox">'],
  ["a kebab-case branch name", "git checkout feature/ask-orq-assistant-refactor"],
  ["a kebab-case service name", "disk-usage-monitoring-service"],
  ["an upper-case ticket id", "TASK-1234567890123456"],
  ["a snake_case name that holds sk-", "my_task_sk-runner_config_v2_final_build"],
];

let failed = 0;
for (const [name, value] of SECRETS) {
  const redacted = deepRedact(value) === "[REDACTED]";
  console.log(`${redacted ? "PASS" : "FAIL"} redacts ${name}`);
  if (!redacted) failed++;
}
for (const [name, value] of KEEP) {
  const kept = deepRedact(value) === value;
  console.log(`${kept ? "PASS" : "FAIL"} keeps ${name}`);
  if (!kept) failed++;
}

// The `sk-` lookahead scans the body run at every `sk-` start, so an unbounded
// one costs time in the square of the run length: 240k characters of `sk-` took
// 16.8s, past half the 30s hook timeout, and nothing truncates a string before
// it reaches the pattern. This pins the bound rather than the measurement, so
// it fails if the `{0,64}` is ever dropped again.
const adversarial = "sk-".repeat(80_000);
const startedAt = process.hrtime.bigint();
deepRedact(adversarial);
const elapsedMs = Number(process.hrtime.bigint() - startedAt) / 1e6;
const fastEnough = elapsedMs < 500;
console.log(`${fastEnough ? "PASS" : "FAIL"} scans 240k characters of \`sk-\` in ${elapsedMs.toFixed(0)}ms`);
if (!fastEnough) failed++;

const total = SECRETS.length + KEEP.length + 1;
console.log(`\n${failed === 0 ? "ALL PASS" : `${failed} FAILED`} (${total} cases)`);
process.exit(failed === 0 ? 0 : 1);
