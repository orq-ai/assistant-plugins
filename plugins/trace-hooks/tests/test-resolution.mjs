#!/usr/bin/env node
// Verify credential resolution priority chain.
// Runs config.js getApiKey/getBaseUrl across 4 env-var permutations and
// asserts the JWT-decoded workspace_id matches what the priority chain
// promises.
//
// Usage: node test-resolution.mjs
// Env: requires the orq config (ORQ_CONFIG_PATH) to hold at least two profiles.
// By default it picks the CLI's current profile as the "general/CLI" profile
// and any other profile as the trace-specific one. Override with
// TEST_SECONDARY_PROFILE / TEST_TRACE_PROFILE env vars.

import fs from "node:fs";
import path from "node:path";
import { execSync } from "node:child_process";
import { ORQ_CONFIG_PATH } from "../src/config.js";

const repoRoot = path.resolve(import.meta.dirname, "..");
const configJs = path.join(repoRoot, "src/config.js");
const orqConfig = JSON.parse(fs.readFileSync(ORQ_CONFIG_PATH, "utf8"));

const profileNames = Object.keys(orqConfig.profiles || {});
const SECONDARY_PROFILE = process.env.TEST_SECONDARY_PROFILE || orqConfig.current;
const TRACE_PROFILE =
  process.env.TEST_TRACE_PROFILE ||
  profileNames.find((n) => n !== SECONDARY_PROFILE) ||
  SECONDARY_PROFILE;

for (const [label, name] of [["secondary", SECONDARY_PROFILE], ["trace", TRACE_PROFILE]]) {
  if (!orqConfig.profiles?.[name]?.api_key) {
    console.error(`ERROR: ${label} profile "${name}" missing or has no api_key in ${ORQ_CONFIG_PATH}`);
    console.error(`Available profiles: ${profileNames.join(", ") || "(none)"}`);
    process.exit(2);
  }
}

function workspaceFromKey(key) {
  const payload = key.split(".")[1];
  const pad = (4 - (payload.length % 4)) % 4;
  const buf = Buffer.from(payload + "=".repeat(pad), "base64");
  const decoded = JSON.parse(buf.toString());
  return decoded.workspace_id || decoded.workspaceId;
}

function resolveInChild(envOverrides) {
  // Run a fresh node so module-level cache in config.js doesn't leak between cases.
  const cmd = `node --input-type=module -e "import { getApiKey, getBaseUrl } from '${configJs}'; const k = getApiKey(); console.log(JSON.stringify({ key: k, base_url: getBaseUrl() }));"`;
  const env = { ...process.env, ...envOverrides };
  for (const k of ["ORQ_API_KEY", "ORQ_TRACE_PROFILE", "ORQ_PROFILE", "ORQ_BASE_URL"]) {
    if (envOverrides[k] === null) delete env[k];
  }
  const out = execSync(cmd, { env, encoding: "utf8" });
  const { key, base_url } = JSON.parse(out.trim());
  return { workspace_id: key ? workspaceFromKey(key) : null, base_url };
}

const expectedSecondary = workspaceFromKey(orqConfig.profiles[SECONDARY_PROFILE].api_key);
const expectedTrace = workspaceFromKey(orqConfig.profiles[TRACE_PROFILE].api_key);

const cases = [
  {
    name: "T1: ORQ_TRACE_PROFILE only",
    env: { ORQ_API_KEY: null, ORQ_TRACE_PROFILE: TRACE_PROFILE, ORQ_PROFILE: null },
    expected: expectedTrace,
  },
  {
    name: "T2: ORQ_API_KEY only",
    env: {
      ORQ_API_KEY: orqConfig.profiles[SECONDARY_PROFILE].api_key,
      ORQ_TRACE_PROFILE: null,
      ORQ_PROFILE: null,
    },
    expected: expectedSecondary,
  },
  {
    name: "T3: BOTH (ORQ_TRACE_PROFILE wins)",
    env: {
      ORQ_API_KEY: orqConfig.profiles[SECONDARY_PROFILE].api_key,
      ORQ_TRACE_PROFILE: TRACE_PROFILE,
      ORQ_PROFILE: null,
    },
    expected: expectedTrace,
  },
  {
    name: "T4: neither (CLI current profile)",
    env: { ORQ_API_KEY: null, ORQ_TRACE_PROFILE: null, ORQ_PROFILE: null },
    expected: workspaceFromKey(orqConfig.profiles[orqConfig.current].api_key),
  },
  {
    name: "T5: ORQ_PROFILE fallback",
    env: { ORQ_API_KEY: null, ORQ_TRACE_PROFILE: null, ORQ_PROFILE: TRACE_PROFILE },
    expected: expectedTrace,
  },
];

let failed = 0;
for (const c of cases) {
  const got = resolveInChild(c.env);
  const ok = got.workspace_id === c.expected;
  console.log(`${ok ? "PASS" : "FAIL"} ${c.name}`);
  console.log(`     expected workspace_id=${c.expected}`);
  console.log(`     got      workspace_id=${got.workspace_id} base_url=${got.base_url}`);
  if (!ok) failed++;
}

console.log(`\n${failed === 0 ? "ALL PASS" : `${failed} FAILED`} (${cases.length} cases)`);
process.exit(failed === 0 ? 0 : 1);
