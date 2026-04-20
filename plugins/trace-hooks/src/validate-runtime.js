import fs from "node:fs/promises";
import path from "node:path";

const root = path.resolve(path.join(import.meta.dirname, ".."));
const required = [
  "hooks/session-start.js",
  "hooks/user-prompt-submit.js",
  "hooks/post-tool-use-failure.js",
  "hooks/pre-compact.js",
  "hooks/stop.js",
  "hooks/session-end.js",
  "hooks/subagent-start.js",
  "hooks/subagent-stop.js",
  "hooks/hooks.json",
  "src/handlers.js",
  "src/config.js",
  "src/otlp.js",
  "src/state.js",
  "src/transcript.js",
  "src/redact.js",
];

for (const rel of required) {
  const full = path.join(root, rel);
  await fs.access(full);
}

console.log("orq-claude-plugin runtime files verified");
