// dbg.mjs — capture and print one or more cases' raw trace for tuning.
// Usage: node dbg.mjs <skill/id> [<skill/id> ...]
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import YAML from "yaml";
import { runCase, writeSystemPromptFile, casePrompt } from "./lib/capture.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "..", "..", "..");
const CATALOG = join(REPO_ROOT, "tests", "skill-tester", "catalog");
const sys = writeSystemPromptFile(readFileSync(join(REPO_ROOT, "agents", "AGENTS.md"), "utf8"));

function findCase(ref) {
  const [skill, id] = ref.split("/");
  const doc = YAML.parse(readFileSync(join(CATALOG, `${skill}.cases.yaml`), "utf8"));
  return { skill, spec: doc.cases.find((c) => c.id === id) };
}

const refs = process.argv.slice(2);
const results = await Promise.all(
  refs.map(async (ref) => {
    const { skill, spec } = findCase(ref);
    const cap = await runCase({
      prompt: casePrompt(spec),
      systemPromptFile: sys.file,
      settingsFile: sys.settingsFile,
      cwd: REPO_ROOT,
      mcpConfigPath: join(REPO_ROOT, ".mcp.json"),
      timeoutMs: 220000,
    });
    return { ref, cap };
  })
);
sys.cleanup();

for (const { ref, cap } of results) {
  console.log(`\n${"=".repeat(70)}\n${ref}  exit=${cap.exitCode} timedOut=${cap.timedOut}`);
  console.log(`selectedSkills: ${JSON.stringify(cap.selectedSkills)}`);
  console.log(`toolCalls: ${cap.toolCalls.map((t) => t.name).join(", ")}`);
  console.log(`--- TEXT (${cap.text.length} chars) ---`);
  console.log(cap.text.slice(0, 2600));
}
