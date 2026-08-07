#!/usr/bin/env node
// Repo-invariant checks for the skills suite, in one pass:
//   1. skills-lock.json <-> skills/ directories, both directions, with hash freshness
//      (recomputes every computedHash and fails on drift)
//   2. the four plugin manifests agree on one version
//   3. SKILL.md frontmatter is loadable and consistent with its directory,
//      including full length of folded (>-style) descriptions
//   4. content-pattern lint on public files
// Errors fail the run; warnings don't. Run from anywhere in the repo.

import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join, relative, dirname } from "node:path";
import { fileURLToPath } from "node:url";

// Optional positional arg points the checks at another repo-shaped tree, so
// the validator's own failure paths can be exercised against a fixture.
const root = process.argv.slice(2).find((a) => !a.startsWith("--"))
  ?? join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const fixMode = process.argv.includes("--fix");
let errors = 0;
const err = (msg) => { console.error(`ERROR: ${msg}`); errors++; };
const warn = (msg) => { console.error(`warn: ${msg}`); };

// JSON.parse with the filename in the error instead of a raw SyntaxError stack.
const readJson = (path) => {
  const text = readFileSync(path, "utf8");
  try { return JSON.parse(text); }
  catch (e) { err(`${relative(root, path)}: invalid JSON — ${e.message}`); return null; }
};

// ---------- helpers ----------
const walkFiles = (dir, base = dir, acc = []) => {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === ".git" || entry.name === "node_modules") continue;
    const full = join(dir, entry.name);
    if (entry.isDirectory()) walkFiles(full, base, acc);
    else acc.push({ rel: relative(base, full).split("\\").join("/"), full });
  }
  return acc;
};

// Mirrors the canonical lock-hash algorithm documented in CLAUDE.md
// ("How computedHash is computed") — keep the two in sync.
const folderHash = (dir) => {
  const files = walkFiles(dir).sort((a, b) => a.rel.localeCompare(b.rel));
  const h = createHash("sha256");
  for (const f of files) { h.update(f.rel); h.update(readFileSync(f.full)); }
  return h.digest("hex");
};

// Minimal frontmatter reader: returns { name, description } with folded
// scalars (">" continuation lines) joined to their full text.
const readFrontmatter = (text) => {
  const lines = text.replace(/^﻿/, "").split(/\r?\n/);
  if (lines[0] !== "---") return null;
  const out = {};
  let key = null;
  for (let i = 1; i < lines.length; i++) {
    const line = lines[i];
    if (line === "---") break;
    const m = line.match(/^([A-Za-z-]+):\s*(.*)$/);
    if (m) {
      key = m[1];
      out[key] = m[2] === ">" || m[2] === "|" ? "" : m[2];
    } else if (key && /^\s+\S/.test(line)) {
      out[key] = (out[key] ? out[key] + " " : "") + line.trim();
    }
  }
  return out;
};

// ---------- 1. lock <-> dirs + hash freshness ----------
const lockPath = join(root, "skills-lock.json");
const lock = readJson(lockPath) ?? { skills: {} };
const skillDirs = readdirSync(join(root, "skills"), { withFileTypes: true })
  .filter((e) => e.isDirectory()).map((e) => e.name).sort();

if (fixMode) {
  // Regenerate the lock in place from the directories on disk — same output
  // as the CLAUDE.md snippet — then continue validating everything else.
  const skills = {};
  for (const name of skillDirs)
    skills[name] = { source: "orq-ai/assistant-plugins", sourceType: "github",
      computedHash: folderHash(join(root, "skills", name)) };
  writeFileSync(lockPath, JSON.stringify({ version: 1, skills }, null, 2) + "\n");
  lock.skills = skills;
  console.error(`fixed: skills-lock.json regenerated for ${skillDirs.length} skills`);
}

const FIX_HINT = "run `node tests/scripts/validate-skills.mjs --fix` to regenerate";
for (const name of skillDirs) {
  const entry = lock.skills?.[name];
  if (!entry) { err(`skills/${name} has no entry in skills-lock.json — ${FIX_HINT}`); continue; }
  if (entry.source !== "orq-ai/assistant-plugins")
    err(`skills-lock.json '${name}': source is '${entry.source}', expected 'orq-ai/assistant-plugins'`);
  if (entry.sourceType !== "github")
    err(`skills-lock.json '${name}': sourceType is '${entry.sourceType}', expected 'github'`);
  const actual = folderHash(join(root, "skills", name));
  if (entry.computedHash !== actual)
    err(`skills-lock.json hash stale for '${name}' — ${FIX_HINT}`);
}
const lockKeys = Object.keys(lock.skills ?? {});
for (const key of lockKeys) {
  try { statSync(join(root, "skills", key)); }
  catch { err(`skills-lock.json entry '${key}' has no skills/ directory — ${FIX_HINT}`); }
}
const sorted = [...lockKeys].sort((a, b) => a.localeCompare(b));
if (JSON.stringify(lockKeys) !== JSON.stringify(sorted))
  err(`skills-lock.json keys are not sorted alphabetically (CLAUDE.md invariant) — ${FIX_HINT}`);

// ---------- 2. manifest version sync ----------
const manifests = [
  ".claude-plugin/plugin.json",
  ".codex-plugin/plugin.json",
  ".cursor-plugin/plugin.json",
  "plugins/orq/.codex-plugin/plugin.json",
];
const versions = manifests
  .map((m) => ({ m, json: readJson(join(root, m)) }))
  .filter(({ json }) => json !== null)
  .map(({ m, json }) => ({ m, v: json.version }));
const canonical = versions[0]?.v;
for (const { m, v } of versions.slice(1))
  if (v !== canonical) err(`manifest version drift: ${m} has ${v}, ${manifests[0]} has ${canonical}`);

// ---------- 3. frontmatter consistency ----------
for (const name of skillDirs) {
  const path = join(root, "skills", name, "SKILL.md");
  let text;
  try { text = readFileSync(path, "utf8"); }
  catch { err(`skills/${name}: missing SKILL.md`); continue; }
  const fm = readFrontmatter(text);
  if (!fm) { err(`skills/${name}: SKILL.md has no frontmatter block`); continue; }
  if (fm.name !== name) err(`skills/${name}: frontmatter name '${fm.name}' != directory name`);
  const desc = (fm.description ?? "").trim();
  if (!desc) err(`skills/${name}: missing or empty description`);
  else if (desc.length > 1024) err(`skills/${name}: description ${desc.length} chars (limit 1024)`);
  const lineCount = text.split("\n").length;
  if (lineCount > 500)
    warn(`skills/${name}: SKILL.md is ${lineCount} lines — consider moving content to resources/`);
  // A skill with valid frontmatter but no instructions passes every other
  // check; require a real body.
  const body = text.split(/^---$/m).slice(2).join("").replace(/\s/g, "");
  if (body.length < 200)
    err(`skills/${name}: SKILL.md body is ${body.length} non-whitespace chars (min 200) — a skill with no instructions ships as an empty capability`);
}

// ---------- 4. content-pattern lint ----------
const lintDirs = ["skills", "agents", "commands"];
const lintTargets = [
  ...lintDirs.flatMap((d) => {
    try { return walkFiles(join(root, d)).map((f) => f.full); }
    catch { return []; }
  }),
  join(root, "CHANGELOG.md"),
  join(root, "README.md"),
];
// Keep in sync with the PR-title lint in skills-ci.yml. The ticket-id pattern
// catches ANY uppercase JIRA-style id that is not RES- (so a new internal
// prefix can't slip through), minus known-benign technical prefixes.
const patterns = [
  [/owner decision/i, "decision-context language"],
  [/\b(?!RES-|UTF-|ISO-|GPT-|ADR-|SHA-|OWASP-|ASI-|A2A-)[A-Z]{2,5}-[0-9]+\b/, "non-project ticket id"],
];
for (const file of lintTargets) {
  let text;
  try { text = readFileSync(file, "utf8"); } catch { continue; }
  for (const [re, label] of patterns) {
    const m = text.match(re);
    if (m) err(`pattern lint: ${label} in ${relative(root, file)} ('${m[0]}')`);
  }
}

// ---------- 5. no stray tracked skills ----------
// Skill installers walk the whole repo for SKILL.md files, so a tracked copy
// anywhere outside skills/ ships to every consumer (this caught nine stale
// pre-rename skills accidentally tracked under .agents/skills/).
// Skipped rather than fatal when root isn't a git checkout — the other checks
// still apply to a plain directory (e.g. a test fixture).
let tracked = [];
try {
  tracked = execFileSync("git", ["ls-files"], { cwd: root, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] })
    .split("\n").filter(Boolean);
} catch {
  warn(`not a git checkout (${root}) — skipping the stray-tracked-skill check`);
}
for (const f of tracked) {
  if (!f.endsWith("/SKILL.md")) continue;
  if (!f.startsWith("skills/") && !f.startsWith("plugins/"))
    err(`stray tracked skill outside skills/: ${f} — installers will ship it`);
}

// ---------- result ----------
if (errors > 0) {
  console.error(`\nSkill validation failed with ${errors} error(s).`);
  process.exit(1);
}
console.log("Skill validation passed.");
