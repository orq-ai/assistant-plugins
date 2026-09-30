#!/usr/bin/env node
// Verify the value-side redactor against the secret shapes a coding session
// actually carries, and against the prose it must leave alone. Tool input and
// output reach deepRedact as free-text strings, where the key-name pattern
// never fires, so this file is the only thing standing between a pasted
// provider key and the trace.
//
// Usage: node test-redact.mjs

import { deepRedact } from "../src/redact.js";

const SECRETS = [
  ["anthropic key", "sk-ant-api03-AbCdEf1234567890AbCdEf1234567890-AAAA"],
  ["openai project key", "sk-proj-AbCdEf1234567890AbCdEf1234567890"],
  ["openai classic key", "sk-abc123def456ghi789jkl012"],
  ["orq key with prefix", "sk-orq-eyJhbGciOiJIUzI1NiJ9.eyJ3b3Jrc3BhY2VfaWQiOiJ3cy0xIn0.c2ln"],
  ["orq key from the dashboard", "eyJhbGciOiJIUzI1NiJ9.eyJ3b3Jrc3BhY2VfaWQiOiJ3cy0xIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r"],
  ["bearer header in a shell command", 'curl -H "Authorization: Bearer abcdef1234567890abcdef" https://api.orq.ai'],
  ["github token", "ghp_abcdefghijklmnopqrstuvwxyz0123456789"],
  ["aws access key id", "AKIAIOSFODNN7EXAMPLE"],
  ["private key block", "-----BEGIN RSA PRIVATE KEY-----\nMIIE..."],
  ["slack token", "xoxb-123456789-abcdef"],
  ["stripe live key", "sk_live_abcdef123456"],
  ["fine-grained github pat", "github_pat_11ABCDEFG0abcdefghij_KLMNOPqrstuvwxyz0123456789"],
  ["google api key", "AIzaSyA1bC2dE3fG4hI5jK6lM7nO8pQ9rS0tU1vW"],
  ["huggingface token", "hf_abcdefghijklmnopqrstuvwxyz0123456789"],
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

const total = SECRETS.length + KEEP.length;
console.log(`\n${failed === 0 ? "ALL PASS" : `${failed} FAILED`} (${total} cases)`);
process.exit(failed === 0 ? 0 : 1);
