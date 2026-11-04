// Compile the actual SDK/HTTP snippets, so a documentation-only scope error is caught.
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const fixture = dirname(fileURLToPath(import.meta.url));
const root = resolve(fixture, '../../../..');
const sources = [
  'skills/orq-compare-agents/resources/job-patterns.md',
  'skills/orq-invoke-deployment/SKILL.md',
  'skills/orq-setup-observability/resources/framework-integrations.md',
];
const scratch = mkdtempSync(join(fixture, '.examples-'));
const files = [];
let hasOrq = false;
let hasOpenAI = false;
let hasCrewAI = false;
try {
  for (const source of sources) {
    for (const match of readFileSync(join(root, source), 'utf8').replace(/\r\n/g, '\n').matchAll(/^```typescript\n([\s\S]*?)^```/gm)) {
      const code = match[1];
      if (!code.includes('responses.create') && !code.includes('const crewJob')) continue;
      hasOrq ||= code.includes('from "@orq-ai/node"');
      hasOpenAI ||= code.includes('from "openai"');
      hasCrewAI ||= code.includes('const crewJob');
      const lines = code.split('\n');
      const imports = lines.filter(line => line.startsWith('import ')).join('\n');
      const body = lines.filter(line => !line.startsWith('import ')).join('\n');
      const path = join(scratch, `example-${files.length}.ts`);
      writeFileSync(path, `${imports}\nimport { job } from "@orq-ai/evaluatorq";\nasync function example() {\n${body}\n}\n`);
      files.push(path);
    }
  }
  if (!hasOrq || !hasOpenAI || !hasCrewAI) throw new Error('Expected both SDK variants and the CrewAI HTTP example');
  const result = spawnSync(process.execPath, [
    join(fixture, 'node_modules/typescript/bin/tsc'), '--noEmit', '--strict',
    '--target', 'ES2022', '--module', 'NodeNext', '--moduleResolution', 'NodeNext',
    '--skipLibCheck', ...files,
  ], { stdio: 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`TypeScript example check failed (${result.status})`);
  console.log(`${files.length} SDK/HTTP markdown examples type-check`);
} finally {
  rmSync(scratch, { recursive: true, force: true });
}
