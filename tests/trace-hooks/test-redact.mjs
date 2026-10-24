#!/usr/bin/env node
// Verify redaction for safe vendor-prefixed placeholders and prose that must
// remain readable. Full format coverage can be supplied outside the repo via
// ORQ_REDACTION_TEST_CASES_JSON; no credential-shaped values are generated.
// Usage: node test-redact.mjs

import { deepRedact } from "../../plugins/trace-hooks/src/redact.js";

const SECRETS = [
  ["test vendor placeholder", "sk_test_EXAMPLE"],
  ["live vendor placeholder", "sk_live_EXAMPLE"],
  ["dotenv path", "/Users/dev/project/.env"],
];

// Short provider-like values below their detection threshold stay unchanged.
const KEEP_SHORT = [
  ["short GitHub vendor placeholder", "ghp_xxxx"],
];

// External callers may provide real-shaped test values without checking them
// into the repository or creating them in CI. JSON must be [[name, value], ...].
let optionalCases = [];
if (process.env.ORQ_REDACTION_TEST_CASES_JSON) {
  try {
    const parsed = JSON.parse(process.env.ORQ_REDACTION_TEST_CASES_JSON);
    if (!Array.isArray(parsed) || parsed.some((entry) =>
      !Array.isArray(entry) || entry.length !== 2 ||
      typeof entry[0] !== "string" || typeof entry[1] !== "string")) {
      throw new Error("expected an array of [name, value] string pairs");
    }
    optionalCases = parsed;
    console.log(`Running ${optionalCases.length} optional redaction cases from ORQ_REDACTION_TEST_CASES_JSON`);
  } catch {
    console.error("Invalid ORQ_REDACTION_TEST_CASES_JSON: expected an array of [name, value] string pairs");
    process.exit(1);
  }
} else {
  console.log("SKIP optional full-format redaction cases (ORQ_REDACTION_TEST_CASES_JSON is unset)");
}

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
  ["YOUR_API_KEY as a free-text value", "YOUR_API_KEY"],
];

let failed = 0;
for (const [name, value] of [...SECRETS, ...optionalCases]) {
  const redacted = deepRedact(value) === "[REDACTED]";
  console.log(`${redacted ? "PASS" : "FAIL"} redacts ${name}`);
  if (!redacted) failed++;
}
for (const [name, value] of [...KEEP_SHORT, ...KEEP]) {
  const kept = deepRedact(value) === value;
  console.log(`${kept ? "PASS" : "FAIL"} keeps ${name}`);
  if (!kept) failed++;
}

const keyedPlaceholder = deepRedact({ api_key: "YOUR_API_KEY" });
const keyedRedacted = keyedPlaceholder.api_key === "[REDACTED]";
console.log(`${keyedRedacted ? "PASS" : "FAIL"} redacts YOUR_API_KEY under api_key field`);
if (!keyedRedacted) failed++;

// The `sk-` lookahead scans the body run at every `sk-` start, so an unbounded
// one costs time in the square of the run length. This pins the bound without
// using credential-like content.
const adversarial = "sk-".repeat(80_000);
const startedAt = process.hrtime.bigint();
deepRedact(adversarial);
const elapsedMs = Number(process.hrtime.bigint() - startedAt) / 1e6;
const fastEnough = elapsedMs < 500;
console.log(`${fastEnough ? "PASS" : "FAIL"} scans 240k characters of \`sk-\` in ${elapsedMs.toFixed(0)}ms`);
if (!fastEnough) failed++;

const total = SECRETS.length + optionalCases.length + KEEP_SHORT.length + KEEP.length + 2;
console.log(`\n${failed === 0 ? "ALL PASS" : `${failed} FAILED`} (${total} cases)`);
process.exit(failed === 0 ? 0 : 1);
