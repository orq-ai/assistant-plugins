#!/usr/bin/env node
// Guard executable skill examples against regression to non-Responses model and agent execution.
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

function skillFiles(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
    const path = join(dir, entry.name);
    return entry.isDirectory() ? skillFiles(path) : (/\.(md|py|ts|js|mjs)$/.test(entry.name) && !path.includes('/tests/')) ? [path] : [];
  });
}
const legacy = /\/v2\/agents\/(?:[^\s"'`]+\/)?(?:responses|invoke|stream|run)\b|\/v2\/router\b|\/chat\/completions\b|\bchat\.completions\.create\s*\(|\borq\s+(?:chat|completions)\s+create\b|\bagents\.(?:responses\.create|invoke|stream|run)\s*\(|\borq\s+(?:agents\s+(?:invoke|stream|run|stream-run)|agents-responses\s+create)\b/;
let failures = 0;
for (const path of skillFiles('skills')) {
  const source = readFileSync(path, 'utf8');
  if (!path.endsWith('.md') && legacy.test(source)) {
    console.error(`${path}: runtime inference uses a non-Responses interface`);
    failures++;
  }
  for (const block of source.matchAll(/^```[^\n]*\n([\s\S]*?)^```/gm)) {
    if (!legacy.test(block[1])) continue;
    console.error(`${path}: executable example uses non-Responses model/agent execution; use general Responses`);
    failures++;
  }
}
for (const name of ['orq-invoke-deployment', 'orq-build-agent', 'orq-compare-agents', 'orq-run-experiment', 'orq-cli', 'orq-simulate-agent', 'evaluatorq', 'orq-red-team', 'orq-setup-observability', 'orq-evaluator-alignment']) {
  const path = `skills/${name}/SKILL.md`;
  const source = readFileSync(path, 'utf8');
  for (const required of ['/v3/router/responses', 'orq responses create', 'only when the user explicitly requests', 'Never fall back', 'provider/model-id', 'Prefer Responses over Chat Completions', 'do not use v2 inference routes']) {
    if (source.includes(required)) continue;
    console.error(`${path}: missing model/agent invocation policy: ${required}`);
    failures++;
  }
}
if (failures) process.exit(1);
console.log('Model and agent invocation guidance: general Responses policy and executable examples pass');
