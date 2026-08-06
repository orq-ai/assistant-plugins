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
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
let errors = 0;
const err = (msg) => { console.error(`ERROR: ${msg}`); errors++; };
const warn = (msg) => { console.error(`warn: ${msg}`); };

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
const lock = JSON.parse(readFileSync(join(root, "skills-lock.json"), "utf8"));
const skillDirs = readdirSync(join(root, "skills"), { withFileTypes: true })
  .filter((e) => e.isDirectory()).map((e) => e.name).sort();

for (const name of skillDirs) {
  const entry = lock.skills?.[name];
  if (!entry) { err(`skills/${name} has no entry in skills-lock.json`); continue; }
  if (entry.source !== "orq-ai/assistant-plugins")
    err(`skills-lock.json '${name}': source is '${entry.source}', expected 'orq-ai/assistant-plugins'`);
  if (entry.sourceType !== "github")
    err(`skills-lock.json '${name}': sourceType is '${entry.sourceType}', expected 'github'`);
  const actual = folderHash(join(root, "skills", name));
  if (entry.computedHash !== actual)
    err(`skills-lock.json hash stale for '${name}' — regenerate per CLAUDE.md`);
}
const lockKeys = Object.keys(lock.skills ?? {});
for (const key of lockKeys) {
  try { statSync(join(root, "skills", key)); }
  catch { err(`skills-lock.json entry '${key}' has no skills/ directory`); }
}
const sorted = [...lockKeys].sort((a, b) => a.localeCompare(b));
if (JSON.stringify(lockKeys) !== JSON.stringify(sorted))
  err("skills-lock.json keys are not sorted alphabetically (CLAUDE.md invariant)");

// ---------- 2. manifest version sync ----------
const manifests = [
  ".claude-plugin/plugin.json",
  ".codex-plugin/plugin.json",
  ".cursor-plugin/plugin.json",
  "plugins/orq/.codex-plugin/plugin.json",
];
const versions = manifests.map((m) => ({
  m, v: JSON.parse(readFileSync(join(root, m), "utf8")).version,
}));
const canonical = versions[0].v;
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
// Casing matches the PR-title lint in skills-ci.yml (both case-insensitive).
const patterns = [
  [/owner decision/i, "decision-context language"],
  [/\b(ENG|INN|BOPS|TOPS)-[0-9]+\b/i, "non-project ticket id"],
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
const tracked = execFileSync("git", ["ls-files"], { cwd: root, encoding: "utf8" })
  .split("\n").filter(Boolean);
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
