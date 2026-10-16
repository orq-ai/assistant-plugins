#!/usr/bin/env node
// Verify the value-side redactor against the secret shapes a coding session
// actually carries, and against the prose it must leave alone. Tool input and
// output reach deepRedact as free-text strings, where the key-name pattern
// never fires, so this file is the only thing standing between a pasted
// provider key and the trace.
//
// Usage: node test-redact.mjs

import { deepRedact } from "../src/redact.js";

// Fixtures are assembled at runtime from fragments so no secret-shaped literal
// sits in the shipped source (directory scanners flag them). The joined strings
// are the same ones a real session would carry.
const j = (...parts) => parts.join("");
const b64 = (v) => Buffer.from(typeof v === "string" ? v : JSON.stringify(v)).toString("base64url");
const JWT_HEAD = b64({ alg: "HS256" });
const JWT_BODY = b64({ workspace_id: "ws-1" });
const JWT_SIG = j("c2", "ln");
const JWT_DASH_SIG = j("dBjftJeZ4CVPmB92", "K27uhbUJU1p1r");
const ALNUM = j("abcdefghijklmnopqrstuvwxyz", "0123456789");

const SECRETS = [
  ["anthropic key", j("sk", "-ant-", "api03-", "AbCdEf1234567890", "AbCdEf1234567890", "-AAAA")],
  ["openai project key", j("sk", "-proj-", "AbCdEf1234567890", "AbCdEf1234567890")],
  ["anthropic key with an underscore early in the body", j("sk", "-ant-", "api03-", "Ab_cdEfGhIjKlMn", "OpQrStUvWxYz0123456789AA")],
  ["openai project key with an underscore early in the body", j("sk", "-proj-", "A_bCdEf1234567890", "AbCdEf1234567890")],
  ["openai classic key", j("sk", "-abc123", "def456", "ghi789", "jkl012")],
  ["orq key with prefix", j("sk", "-orq-", JWT_HEAD, ".", JWT_BODY, ".", JWT_SIG)],
  ["orq key from the dashboard", j(JWT_HEAD, ".", JWT_BODY, ".", JWT_DASH_SIG)],
  ["bearer header in a shell command", j('curl -H "Authorization: Bear', 'er abcdef1234567890', 'abcdef" https://api.orq.ai')],
  ["github token", j("gh", "p_", ALNUM)],
  ["aws access key id", j("AK", "IA", "IOSFODNN7", "EXAMPLE")],
  ["private key block", j("-----BEGIN RSA ", "PRIVATE KEY-----\nMIIE...")],
  ["slack token", j("xo", "xb-", "123456789", "-abcdef")],
  ["stripe live key", j("sk", "_live_", "abcdef", "123456")],
  ["fine-grained github pat", j("github", "_pat_", "11ABCDEFG0", "abcdefghij_", "KLMNOPqrstuvwxyz0123456789")],
  ["google api key", j("AI", "zaSy", "A1bC2dE3fG4hI5jK6", "lM7nO8pQ9rS0tU1vW")],
  ["huggingface token", j("hf", "_", ALNUM)],
  ["dotenv path", "/Users/dev/project/.env"],
];

// Prose that reads like a secret to a pattern that is too eager. Redacting
// these costs the trace its content for nothing.
const KEEP = [
  ["the word bearer in prose", "Bearer authentication is required for this route"],
  ["a plain sentence", "the session ran ls and counted the files"],
  ["a source path", "/Users/dev/orq-cli/cli/custom/launch/telemetry.go"],
  ["a long word", "internationalization and localization"],
  ["a git sha", "415edd51ddba3b10d4e3091c6d91b0cbca57566b"],
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
