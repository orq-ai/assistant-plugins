#!/usr/bin/env node
// Verify credential resolution in src/config.js.
//
// Order under test: plugin option (CLAUDE_PLUGIN_OPTION_ORQ_API_KEY) first,
// then the ORQ_API_KEY env var, else null. No config file or profile is read:
// the "no file" case points HOME at an empty directory that holds a decoy
// ~/.orq/config.json, and asserts the decoy is ignored.
//
// Each case runs in a fresh node so nothing leaks between cases.
//
// Usage: node test-resolution.mjs

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";

const configJs = path.join(import.meta.dirname, "..", "src", "config.js");
const configUrl = `file://${configJs.replaceAll("\\", "/")}`;
const probe = `
  import { getApiKey, getBaseUrl, getOtlpEndpoint } from ${JSON.stringify(configUrl)};
  console.log(JSON.stringify({ key: getApiKey(), base_url: getBaseUrl(), otlp: getOtlpEndpoint() }));
`;

const home = fs.mkdtempSync(path.join(os.tmpdir(), "orq-resolution-"));
fs.mkdirSync(path.join(home, ".orq"));
fs.writeFileSync(
  path.join(home, ".orq", "config.json"),
  JSON.stringify({
    current: "decoy",
    profiles: { decoy: { api_key: "decoy-key", base_url: "https://decoy.example", otlp_endpoint: "https://decoy.example/v1/traces" } },
  }),
);

const MANAGED = [
  "CLAUDE_PLUGIN_OPTION_ORQ_API_KEY", "ORQ_API_KEY", "ORQ_BASE_URL", "ORQ_OTLP_ENDPOINT",
  "ORQ_TRACE_PROFILE", "ORQ_PROFILE", "ORQ_CONFIG_PATH",
];

function resolveInChild(overrides) {
  const env = { ...process.env, HOME: home, USERPROFILE: home };
  for (const k of MANAGED) delete env[k];
  Object.assign(env, overrides);
  const out = spawnSync("node", ["--input-type=module", "-e", probe], { env, encoding: "utf8" });
  if (out.status !== 0) throw new Error(`child failed: ${out.stderr}`);
  return JSON.parse(out.stdout.trim());
}

const DEFAULT_BASE = "https://my.orq.ai";
const cases = [
  ["T1: plugin option only", { CLAUDE_PLUGIN_OPTION_ORQ_API_KEY: "plugin-key" }, { key: "plugin-key" }],
  ["T2: ORQ_API_KEY only", { ORQ_API_KEY: "env-key" }, { key: "env-key" }],
  ["T3: both set, plugin option wins", { CLAUDE_PLUGIN_OPTION_ORQ_API_KEY: "plugin-key", ORQ_API_KEY: "env-key" }, { key: "plugin-key" }],
  ["T4: neither set, the CLI profile file is ignored", {}, { key: null }],
  ["T5: empty plugin option falls through to ORQ_API_KEY", { CLAUDE_PLUGIN_OPTION_ORQ_API_KEY: "", ORQ_API_KEY: "env-key" }, { key: "env-key" }],
  ["T6: empty values everywhere mean no key", { CLAUDE_PLUGIN_OPTION_ORQ_API_KEY: "", ORQ_API_KEY: "" }, { key: null }],
  ["T7: removed profile variables have no effect", { ORQ_TRACE_PROFILE: "decoy", ORQ_PROFILE: "decoy", ORQ_CONFIG_PATH: path.join(home, ".orq", "config.json") }, { key: null }],
  ["T8: ORQ_BASE_URL overrides the default", { ORQ_BASE_URL: "https://my.staging.orq.ai" }, { base_url: "https://my.staging.orq.ai" }],
  ["T9: empty ORQ_BASE_URL is unset", { ORQ_BASE_URL: "" }, { base_url: DEFAULT_BASE }],
  ["T10: ORQ_OTLP_ENDPOINT is returned, and is null when unset or empty", { ORQ_OTLP_ENDPOINT: "https://otlp.example/v1/traces" }, { otlp: "https://otlp.example/v1/traces" }],
  ["T11: empty ORQ_OTLP_ENDPOINT is unset", { ORQ_OTLP_ENDPOINT: "" }, { otlp: null }],
];

let failed = 0;
for (const [name, env, expected] of cases) {
  const got = resolveInChild(env);
  const want = { key: null, base_url: DEFAULT_BASE, otlp: null, ...expected };
  const ok = Object.keys(want).every((k) => got[k] === want[k]);
  console.log(`${ok ? "PASS" : "FAIL"} ${name}`);
  if (!ok) {
    console.log(`     expected ${JSON.stringify(want)}`);
    console.log(`     got      ${JSON.stringify(got)}`);
    failed++;
  }
}

fs.rmSync(home, { recursive: true, force: true });
console.log(`\n${failed === 0 ? "ALL PASS" : `${failed} FAILED`} (${cases.length} cases)`);
process.exit(failed === 0 ? 0 : 1);
