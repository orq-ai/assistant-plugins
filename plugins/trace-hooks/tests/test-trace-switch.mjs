#!/usr/bin/env node
// ORQ_TRACE_DISABLED is the only way a session can decline tracing once the
// plugin is installed in the user's own config, so `orq launch claude
// --no-otel` stands or falls on this switch. Every hook goes through
// runSafely, which is why the gate is tested there rather than per hook.
//
// Each case runs in a child process, because the gate is read at hook entry
// from the environment.
//
// Usage: node test-trace-switch.mjs

import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const handlersUrl = JSON.stringify(
  `file://${path.join(here, "..", "src", "handlers.js").replaceAll("\\", "/")}`,
);

// Prints "ran" only if runSafely let the handler through.
const probe = `
  const { runSafely } = await import(${handlersUrl});
  await runSafely(async () => { process.stdout.write("ran"); });
`;

function run(env) {
  return new Promise((resolve) => {
    const child = spawn("node", ["--input-type=module", "-e", probe], {
      env: { ...process.env, ORQ_TRACE_DISABLED: "", ORQ_API_KEY: "", CLAUDE_PLUGIN_OPTION_ORQ_API_KEY: "", ...env },
    });
    let out = "";
    child.stdout.on("data", (chunk) => { out += chunk; });
    child.stderr.on("data", () => {});
    child.on("close", () => resolve(out.includes("ran")));
  });
}

const CASES = [
  ["runs on the plugin option alone", { CLAUDE_PLUGIN_OPTION_ORQ_API_KEY: "test-key" }, true],
  ["runs when the plugin option is blank but ORQ_API_KEY is set", { CLAUDE_PLUGIN_OPTION_ORQ_API_KEY: "", ORQ_API_KEY: "test-key" }, true],
  ["stays off with no key anywhere", {}, false],
  ["runs with a key and no switch", { ORQ_API_KEY: "test-key" }, true],
  ["stops when the switch is set", { ORQ_API_KEY: "test-key", ORQ_TRACE_DISABLED: "1" }, false],
  ["stops on the word true as well", { ORQ_API_KEY: "test-key", ORQ_TRACE_DISABLED: "true" }, false],
  ["stops on yes", { ORQ_API_KEY: "test-key", ORQ_TRACE_DISABLED: "yes" }, false],
  ["stops on TRUE, since an env var carries whatever case it was set in", { ORQ_API_KEY: "test-key", ORQ_TRACE_DISABLED: "TRUE" }, false],
  ["stops on on, with the trailing space a shell script leaves", { ORQ_API_KEY: "test-key", ORQ_TRACE_DISABLED: "on " }, false],
  ["keeps running when the switch is off", { ORQ_API_KEY: "test-key", ORQ_TRACE_DISABLED: "0" }, true],
  ["keeps running on a word that is not a yes", { ORQ_API_KEY: "test-key", ORQ_TRACE_DISABLED: "maybe" }, true],
];

let failed = 0;
for (const [name, env, expected] of CASES) {
  const ran = await run(env);
  const ok = ran === expected;
  console.log(`${ok ? "PASS" : "FAIL"} ${name}`);
  if (!ok) failed += 1;
}

console.log("");
console.log(failed === 0 ? `ALL PASS (${CASES.length} cases)` : `${failed} FAILED (${CASES.length} cases)`);
process.exit(failed === 0 ? 0 : 1);
