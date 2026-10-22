#!/usr/bin/env node
// Guard executable skill examples against regression to deprecated agent execution.
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

function markdownFiles(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
    const path = join(dir, entry.name);
    return entry.isDirectory() ? markdownFiles(path) : entry.name.endsWith('.md') ? [path] : [];
  });
}
const legacy = /\/v2\/agents\/(?:[^\s"'`]+\/)?(?:responses|invoke|stream|run)\b|\bagents\.(?:responses\.create|invoke|stream|run)\s*\(|\borq\s+(?:agents\s+(?:invoke|stream|run|stream-run)|agents-responses\s+create)\b/;
let failures = 0;
for (const path of markdownFiles('skills')) {
  const source = readFileSync(path, 'utf8');
  for (const block of source.matchAll(/^```[^\n]*\n([\s\S]*?)^```/gm)) {
    if (!legacy.test(block[1])) continue;
    console.error(`${path}: executable example uses deprecated agent execution; use general Responses`);
    failures++;
  }
}
for (const name of ['orq-invoke-deployment', 'orq-build-agent', 'orq-compare-agents', 'orq-run-experiment', 'orq-cli', 'orq-simulate-agent', 'evaluatorq', 'orq-red-team']) {
  const path = `skills/${name}/SKILL.md`;
  const source = readFileSync(path, 'utf8');
  for (const required of ['/v3/router/responses', 'orq responses create', 'only when the user explicitly requests', 'Never fall back']) {
    if (source.includes(required)) continue;
    console.error(`${path}: missing agent invocation policy: ${required}`);
    failures++;
  }
}
if (failures) process.exit(1);
console.log('Agent invocation guidance: general Responses policy and executable examples pass');
