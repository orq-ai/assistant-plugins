#!/usr/bin/env node
// Repo-invariant checks for the skills suite, in one pass:
//   1. skills-lock.json <-> skills/ directories, both directions, with hash freshness
//      (recomputes every computedHash and fails on drift)
//   2. the four plugin manifests agree on one version, and the root
//      plugin.json conforms to Agent Plugins 1.0.0 (closed field set,
//      name constraints, skills/ discovery matches the lock)
//   3. SKILL.md frontmatter is loadable and consistent with its directory,
//      including full length of folded (>-style) descriptions
//   4. content-pattern lint on public files
// Errors fail the run; warnings don't. Run from anywhere in the repo.

import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { lstatSync, readdirSync, readFileSync, realpathSync, statSync, writeFileSync } from "node:fs";
import { join, relative, dirname, sep } from "node:path";
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
// The read is inside the try too: a missing manifest is a reportable error, not
// a Node ENOENT dump.
const readJson = (path) => {
  let text;
  try { text = readFileSync(path, "utf8"); }
  catch (e) { err(`${relative(root, path)}: cannot read — ${e.code ?? e.message}`); return null; }
  let parsed;
  try { parsed = JSON.parse(text); }
  catch (e) { err(`${relative(root, path)}: invalid JSON — ${e.message}`); return null; }
  // A bare `null` body parses clean and is indistinguishable from the failure
  // return above, so every caller would silently skip its checks. Reject it here.
  if (parsed === null) { err(`${relative(root, path)}: contains a bare null, not a JSON object`); return null; }
  return parsed;
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
  "plugin.json",
  ".claude-plugin/plugin.json",
  ".codex-plugin/plugin.json",
  ".cursor-plugin/plugin.json",
];
// Parse once and keep the objects: section 2b reads the root manifest too, and
// a second readJson would report an unreadable file twice.
const parsed = manifests
  .map((m) => ({ m, json: readJson(join(root, m)) }))
  .filter(({ json }) => json !== null);
const versions = parsed.map(({ m, json }) => ({ m, v: json?.version }));
const canonical = versions[0];
for (const { m, v } of versions.slice(1))
  // Name the manifest `canonical` actually came from — with plugin.json absent
  // that is not manifests[0], and blaming a missing file sends you hunting.
  if (v !== canonical.v) err(`manifest version drift: ${m} has ${v}, ${canonical.m} has ${canonical.v}`);

// ---------- 2b. root plugin.json is a usable manifest ----------
// Full Agent Plugins 1.0.0 field validation is the vendored schema's job —
// `ajv` runs it in CI against tests/schemas/agent-plugins-1.0.0.plugin.schema.json.
// Duplicating its rules here produced a strictly weaker copy that accepted
// `"author": "orq.ai"` and a non-string `version`. What stays is the shape
// assumption the version-sync check above depends on: a non-null JSON object.
const SPEC_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json";
const rootManifest = parsed.find(({ m }) => m === "plugin.json")?.json ?? null;
if (rootManifest !== null && (typeof rootManifest !== "object" || Array.isArray(rootManifest)))
  err(`plugin.json: top level must be a JSON object, got ${Array.isArray(rootManifest) ? "array" : typeof rootManifest}`);

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
  if (!f.startsWith("skills/"))
    err(`stray tracked skill outside skills/: ${f} — installers will ship it`);
}

// ---------- 6. spec §4.1 path containment ----------
// Every path a client resolves must stay inside the plugin root. Symlinks are
// allowed to point within it (§4.1), so resolve each tracked link fully —
// realpathSync follows the whole chain, including a final component that is
// itself a link, and throws on a dangling target. Resolving only the parent
// directory and appending the last component would accept both `a/../..` and
// links to nowhere. Uses the same `tracked` list and the same skip-when-not-a-
// checkout policy as section 5.
const rootPhys = realpathSync(root);
for (const f of tracked) {
  let stat;
  try { stat = lstatSync(join(root, f)); } catch { continue; }
  if (!stat.isSymbolicLink()) continue;
  let resolved;
  try { resolved = realpathSync(join(root, f)); }
  catch (e) { err(`symlink ${f} does not resolve (${e.code ?? e.message})`); continue; }
  if (resolved !== rootPhys && !resolved.startsWith(rootPhys + sep))
    err(`symlink ${f} resolves outside the plugin root: ${resolved}`);
}

// ---------- 7. one Agent Plugins root ----------
// Section 6 measures containment against the repo root, which is the right
// yardstick only while the repo root is the sole Agent Plugins root. A nested
// one would re-create the plugins/orq escape this release removed: its symlinks
// stayed inside the repo while leaving their own plugin root.
// Identified by the manifest's own $schema, not by filename — .claude-plugin,
// .codex-plugin, .cursor-plugin and plugins/trace-hooks all ship a plugin.json
// in a client-specific format, and adding another harness must not trip this.
for (const f of tracked) {
  if (f === "plugin.json" || !f.endsWith("plugin.json")) continue;
  const m = readJson(join(root, f));
  if (m && typeof m === "object" && m.$schema === SPEC_SCHEMA)
    err(`nested Agent Plugins manifest: ${f} — the repo root must be the only plugin root`);
}

// ---------- result ----------
if (errors > 0) {
  console.error(`\nSkill validation failed with ${errors} error(s).`);
  process.exit(1);
}
console.log("Skill validation passed.");
