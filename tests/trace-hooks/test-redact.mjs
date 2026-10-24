#!/usr/bin/env node
// Verify the value-side redactor against the secret shapes a coding session
// actually carries, and against the prose it must leave alone. Tool input and
// output reach deepRedact as free-text strings, where the key-name pattern
// never fires, so this file is the only thing standing between a pasted
// provider key and the trace.
//
// Usage: node test-redact.mjs

import { deepRedact } from "../../plugins/trace-hooks/src/redact.js";

// Fixtures are generated at runtime so the shipped source holds no key-shaped
// text at all (directory scanners flag it, even when split into fragments):
// prefixes are joined from short pieces and every body comes from a seeded
// generator. The redactor sees the same shapes a real session carries.
const j = (...parts) => parts.join("");
let seed = 20261016;
const next = () => (seed = (Math.imul(seed, 1103515245) + 12345) >>> 0);
const body = (n, alphabet) => {
  let out = "";
  while (out.length < n) out += alphabet[next() % alphabet.length];
  return out;
};
const B64 = j("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz", "0123456789", "-_");
const ALNUM = j("abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "0123456789");
const UPPER = j("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "234567");
const HEX = "0123456789abcdef";
const b64 = (v) => Buffer.from(typeof v === "string" ? v : JSON.stringify(v)).toString("base64url");
const JWT_HEAD = b64({ alg: "HS256" });
const JWT_BODY = b64({ workspace_id: "ws-1" });
const JWT_SIG = body(10, B64);
const JWT_DASH_SIG = body(43, B64);
const withDigit = (n, alphabet) => body(n - 1, alphabet) + "7";
const dash = j("-");

const SECRETS = [
  ["anthropic key", j("sk", dash, "ant", dash, "api03", dash, withDigit(40, B64), dash, "AAAA")],
  ["openai project key", j("sk", dash, "proj", dash, withDigit(32, B64))],
  ["anthropic key with an underscore early in the body", j("sk", dash, "ant", dash, "api03", dash, "Ab_", withDigit(37, ALNUM))],
  ["openai project key with an underscore early in the body", j("sk", dash, "proj", dash, "A_", withDigit(30, ALNUM))],
  ["openai classic key", j("sk", dash, withDigit(24, ALNUM))],
  ["orq key with prefix", j("sk", dash, "orq", dash, JWT_HEAD, ".", JWT_BODY, ".", JWT_SIG)],
  ["orq key from the dashboard", j(JWT_HEAD, ".", JWT_BODY, ".", JWT_DASH_SIG)],
  ["bearer header in a shell command", j('curl -H "Authorization: Bear', "er ", withDigit(24, ALNUM), '" https://api.orq.ai')],
  ["github token", j("gh", "p_", withDigit(36, ALNUM))],
  ["aws access key id", j("AK", "IA", withDigit(16, UPPER))],
  ["private key block", j(dash.repeat(5), "BEGIN RSA ", "PRIV", "ATE KEY", dash.repeat(5), "\n", body(24, B64))],
  ["slack token", j("xo", "xb", dash, body(11, "0123456789"), dash, body(8, ALNUM))],
  ["stripe live key", j("sk", "_live_", withDigit(20, ALNUM))],
  ["fine-grained github pat", j("github", "_pat_", "11", withDigit(20, ALNUM), "_", body(30, ALNUM))],
  ["google api key", j("AI", "za", "Sy", withDigit(33, B64.slice(0, 64)))],
  ["huggingface token", j("hf", "_", withDigit(34, ALNUM))],
  ["dotenv path", "/Users/dev/project/.env"],
];

// Prose that reads like a secret to a pattern that is too eager. Redacting
// these costs the trace its content for nothing.
const KEEP = [
  ["the word bearer in prose", "Bearer authentication is required for this route"],
  ["a plain sentence", "the session ran ls and counted the files"],
  ["a source path", "/Users/dev/orq-cli/cli/custom/launch/telemetry.go"],
  ["a long word", "internationalization and localization"],
  ["a git sha", body(40, HEX)],
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
