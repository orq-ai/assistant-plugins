#!/usr/bin/env node
// Repo-invariant checks for the skills suite in one pass:
//   1. skills-lock.json <-> skills/ with hash freshness
//   2. four plugin manifests agree on one version, root plugin.json is usable
//   3. SKILL.md frontmatter conformance (Agent Skills closed field set)
//   4. content-pattern lint on public files
//   5. no tracked SKILL.md outside skills/
//   6. spec §4.1 — every tracked symlink resolves inside the plugin root
//   7. the repo root is the only Agent Plugins root
//   8. agents/AGENTS.md <-> skills/ (path list + <available_skills>)
//   9. README.md skills table <-> skills/
//  10. tests/skills.md <-> skills/
// Errors fail the run; warnings don't. Run from anywhere in the repo.

import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { existsSync, lstatSync, readdirSync, readFileSync, realpathSync, statSync, writeFileSync } from "node:fs";
import { join, relative, dirname, sep } from "node:path";
import { fileURLToPath } from "node:url";

// Optional positional arg targets another repo-shaped tree (for test fixtures).
const root = process.argv.slice(2).find((a) => !a.startsWith("--"))
  ?? join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const fixMode = process.argv.includes("--fix");
let errors = 0;
const err = (msg) => { console.error(`ERROR: ${msg}`); errors++; };
const warn = (msg) => { console.error(`warn: ${msg}`); };

// JSON.parse with the filename in the error instead of a raw SyntaxError stack.
const readJson = (path) => {
  let text;
  try { text = readFileSync(path, "utf8"); }
  catch (e) { err(`${relative(root, path)}: cannot read — ${e.code ?? e.message}`); return null; }
  let parsed;
  try { parsed = JSON.parse(text); }
  catch (e) { err(`${relative(root, path)}: invalid JSON — ${e.message}`); return null; }
  if (parsed === null) { err(`${relative(root, path)}: contains a bare null, not a JSON object`); return null; }
  return parsed;
};

// ---------- git probe ----------
// -z avoids C-quoting non-ASCII paths. A non-git root skips sections 5-7;
// a checkout where git fails is an error, not a silent skip.
let tracked = [];
let isCheckout = false;
try {
  tracked = execFileSync("git", ["ls-files", "-z"], { cwd: root, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] })
    .split("\0").filter(Boolean);
  isCheckout = true;
} catch (e) {
  const reason = String(e.stderr || e.message).trim().split("\n")[0];
  if (existsSync(join(root, ".git")))
    err(`git ls-files failed in a checkout that has .git — sections 5-7 did not run: ${reason}`);
  else
    warn(`not a git checkout (${root}) — skipping the tracked-file checks`);
}

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

// Byte-order comparison — deterministic across platforms, unlike localeCompare.
const byteSort = (a, b) => a < b ? -1 : a > b ? 1 : 0;

// Mirrors the lock-hash algorithm in CLAUDE.md. Bytes come from the git index
// (not disk) to avoid CRLF and gitignored-artifact divergence.
const gitFiles = (dir) =>
  execFileSync("git", ["ls-files", "-z", "--", dir], { cwd: root, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] })
    .split("\0").filter(Boolean);

// One `git cat-file --batch` per folder instead of one spawn per file.
const gitBlobs = (paths) => {
  const out = execFileSync("git", ["cat-file", "--batch"], {
    cwd: root, input: paths.map((p) => `:${p}`).join("\n") + "\n", maxBuffer: 1 << 28,
  });
  const blobs = [];
  let off = 0;
  for (const p of paths) {
    const nl = out.indexOf(0x0a, off);
    if (nl === -1) throw new Error(`git cat-file --batch: truncated response at ${p}`);
    const header = out.subarray(off, nl).toString("utf8");
    const m = header.match(/^[0-9a-f]{40,64} blob (\d+)$/);
    if (!m) throw new Error(`git cat-file --batch: ${p}: ${header}`);
    const start = nl + 1, size = Number(m[1]);
    blobs.push(out.subarray(start, start + size));
    off = start + size + 1;
  }
  return blobs;
};

const folderHashDisk = (dir) => {
  const files = walkFiles(dir).sort((a, b) => byteSort(a.rel, b.rel));
  const h = createHash("sha256");
  for (const f of files) { h.update(f.rel); h.update(readFileSync(f.full)); }
  return h.digest("hex");
};

const folderHash = (dir) => {
  if (!isCheckout) return folderHashDisk(dir);
  const rel = relative(root, dir).split(sep).join("/");
  const files = gitFiles(rel).sort(byteSort);
  if (files.length === 0) {
    err(`${rel} has no tracked files — \`git add\` it before regenerating the lock`);
    return "";
  }
  const blobs = gitBlobs(files);
  const h = createHash("sha256");
  for (let i = 0; i < files.length; i++) {
    h.update(files[i].slice(rel.length + 1));
    h.update(blobs[i]);
  }
  return h.digest("hex");
};

const unquote = (s) =>
  (s.length > 1 && ((s.startsWith('"') && s.endsWith('"')) || (s.startsWith("'") && s.endsWith("'"))))
    ? s.slice(1, -1) : s;

// One 'key: value' pair of a mapping. Deliberately wider than the frontmatter
// key pattern: a nested map key may be quoted or start with a digit, and
// rejecting those would report a valid map as a scalar.
const MAP_ENTRY_RE = /^(?:"[^"]*"|'[^']*'|[^:\s]+):(\s|$)/;

// Minimal frontmatter reader: { fields, shapes, blocks, unparsed } — scalars
// with block-scalar continuation, plus unindented lines no check can see.
// `shapes` records scalar/list/map so a value's type can be checked and not
// just its field name: `metadata: [a, b]` is a list where the spec requires a
// string->string map, and flattening every value to a string hides that.
const readFrontmatter = (text) => {
  const lines = text.replace(/^﻿/, "").split(/\r?\n/);
  if (lines[0] !== "---") return null;
  const fields = {};
  const shapes = {};
  const blocks = {};
  const unparsed = [];
  let key = null;
  for (let i = 1; i < lines.length; i++) {
    const line = lines[i];
    if (line === "---") break;
    const m = line.match(/^([A-Za-z_][A-Za-z0-9_.-]*):(?:\s+(.*))?$/);
    if (m) {
      key = m[1];
      const raw = (m[2] ?? "").trim();
      // Block scalar opener (>-, |+, >2, etc.) carries no value of its own.
      const blockScalar = /^[>|][-+]?[0-9]*$/.test(raw);
      fields[key] = blockScalar ? "" : unquote(raw);
      blocks[key] = [];
      // A flow collection is decided here, a block one by the indented lines
      // below — which is why an empty value stays undecided for now.
      if (blockScalar) shapes[key] = "scalar";
      else if (raw === "") shapes[key] = "empty";
      else if (raw.startsWith("[")) shapes[key] = "list";
      else if (raw.startsWith("{")) shapes[key] = "map";
      else shapes[key] = "scalar";
    } else if (key && /^\s+\S/.test(line)) {
      // Inside a block scalar the quotes are content, not delimiters.
      fields[key] = (fields[key] ? fields[key] + " " : "") + line.trim();
      blocks[key].push(line);
      // Only an undecided value is resolved from its indented lines: a block
      // scalar's body may legitimately open with '- ' or carry a colon. A
      // comment decides nothing, so the next real line still gets to.
      if (shapes[key] === "empty" && !line.trimStart().startsWith("#"))
        shapes[key] = /^\s+-(\s|$)/.test(line) ? "list"
          : MAP_ENTRY_RE.test(line.trim()) ? "map"
            : "scalar";
    } else if (line.trim() !== "" && !line.trimStart().startsWith("#")) {
      unparsed.push(line);
    }
  }
  // An empty value with no indented lines under it is an empty scalar.
  for (const k of Object.keys(shapes)) if (shapes[k] === "empty") shapes[k] = "scalar";
  return { fields, shapes, blocks, unparsed };
};

// ---------- 1. lock <-> dirs + hash freshness ----------
const lockPath = join(root, "skills-lock.json");
const lock = readJson(lockPath) ?? { skills: {} };
const skillDirs = readdirSync(join(root, "skills"), { withFileTypes: true })
  .filter((e) => e.isDirectory()).map((e) => e.name).sort();

if (fixMode) {
  const errorsBefore = errors;
  const skills = {};
  for (const name of skillDirs)
    skills[name] = { source: "orq-ai/assistant-plugins", sourceType: "github",
      computedHash: folderHash(join(root, "skills", name)) };
  if (errors === errorsBefore) {
    writeFileSync(lockPath, JSON.stringify({ version: 1, skills }, null, 2) + "\n");
    lock.skills = skills;
    console.error(`fixed: skills-lock.json regenerated for ${skillDirs.length} skills`);
  } else {
    console.error(`--fix: not writing skills-lock.json because ${errors - errorsBefore} error(s) occurred during hash computation`);
  }
}

// Warn about unstaged changes invisible to the index-based hashes.
if (isCheckout) {
  // With -z, R/C status codes emit a trailing old-path record with no XY prefix.
  const statusEntries = execFileSync("git", ["status", "--porcelain", "-z", "--", "skills"],
    { cwd: root, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] })
    .split("\0").filter(Boolean);
  const dirty = [];
  for (let i = 0; i < statusEntries.length; i++) {
    const e = statusEntries[i];
    if (e.length < 2) continue;
    if (e[1] !== " ") dirty.push(e);
    if (e[0] === "R" || e[0] === "C") i++;
  }
  if (dirty.length)
    warn(`${dirty.length} unstaged change(s) under skills/ are not reflected in the lock hashes — \`git add\` them first`);
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
const sorted = [...lockKeys].sort(byteSort);
if (JSON.stringify(lockKeys) !== JSON.stringify(sorted))
  err(`skills-lock.json keys are not sorted alphabetically (CLAUDE.md invariant) — ${FIX_HINT}`);

// ---------- 2. manifest version sync ----------
const manifests = [
  "plugin.json",
  ".claude-plugin/plugin.json",
  ".codex-plugin/plugin.json",
  ".cursor-plugin/plugin.json",
];
// Parse once; sections 2b and 7 reuse these. Failures stay as null to avoid re-reporting.
const manifestEntries = new Map(manifests.map((m) => [m, readJson(join(root, m))]));
const versions = [...manifestEntries]
  .filter(([, json]) => json !== null)
  .map(([m, json]) => ({ m, v: json.version }));
const [reference, ...rest] = versions;
if (reference && reference.v == null)
  err(`manifest version missing: ${reference.m} has no 'version' field`);
for (const { m, v } of rest)
  if (v !== reference.v) err(`manifest version drift: ${m} has ${v}, ${reference.m} has ${reference.v}`);

// ---------- 2b. root plugin.json is a usable manifest ----------
const rootManifest = manifestEntries.get("plugin.json") ?? null;
if (rootManifest !== null && (typeof rootManifest !== "object" || Array.isArray(rootManifest)))
  err(`plugin.json: top level must be a JSON object, got ${Array.isArray(rootManifest) ? "array" : typeof rootManifest}`);

// ---------- 3. frontmatter consistency + Agent Skills conformance ----------
// Agent Skills closed field set — an extra field costs the skill (Agent Plugins §7.1).
const SKILL_FIELDS = new Set(["name", "description", "license", "compatibility",
  "metadata", "allowed-tools"]);
// Every permitted field except `metadata` takes a string. `allowed-tools` is a
// comma-separated string in this suite and in Claude Code's own reader, not a
// YAML sequence.
const SCALAR_SKILL_FIELDS = ["name", "description", "license", "compatibility",
  "allowed-tools"];
// 1-64 chars, lowercase alphanumeric and hyphens, no leading/trailing/consecutive hyphens.
const SKILL_NAME_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
// Anthropic reserves these segment names; matched whole-segment.
const RESERVED_NAME_RE = /(^|-)(anthropic|claude)(-|$)/i;
for (const name of skillDirs) {
  const path = join(root, "skills", name, "SKILL.md");
  let text;
  try { text = readFileSync(path, "utf8"); }
  catch { err(`skills/${name}: missing SKILL.md`); continue; }
  const parsed = readFrontmatter(text);
  if (!parsed) { err(`skills/${name}: SKILL.md has no frontmatter block`); continue; }
  const { fields: fm, shapes, blocks, unparsed } = parsed;
  for (const line of unparsed)
    err(`skills/${name}: unreadable frontmatter line '${line.trim()}' — it is neither a key, a comment, nor a continuation, so no check can see it`);
  for (const key of Object.keys(fm))
    if (!SKILL_FIELDS.has(key))
      err(`skills/${name}: unknown frontmatter field '${key}' — the Agent Skills field set is closed, so a conformant client skips this skill entirely (Agent Plugins §7.1)`);
  // A permitted field name is only half the spec: each value's type is fixed
  // too, and a wrong one makes the skill skippable exactly like an unknown
  // field does. Without this the value is never inspected at all.
  for (const key of SCALAR_SKILL_FIELDS)
    if (key in fm && shapes[key] !== "scalar")
      err(`skills/${name}: frontmatter '${key}' is a ${shapes[key]}, but the spec defines it as a string`);
  if ("metadata" in fm) {
    const badEntry = (e) =>
      err(`skills/${name}: frontmatter 'metadata' entry '${e}' is not a 'key: value' pair — the spec defines metadata as a string->string map`);
    if (shapes.metadata !== "map") {
      err(`skills/${name}: frontmatter 'metadata' is a ${shapes.metadata}, but the spec defines it as a string->string map`);
    } else if (fm.metadata.startsWith("{")) {
      // Flow form. Without this it got a shape and no entry check at all.
      const flow = fm.metadata.trim();
      if (!flow.endsWith("}"))
        err(`skills/${name}: frontmatter 'metadata' opens a flow map that is never closed with '}'`);
      else {
        const inner = flow.slice(1, -1).trim();
        if (/[[\]{}]/.test(inner))
          err(`skills/${name}: frontmatter 'metadata' nests a collection — the spec defines it as a string->string map`);
        else for (const part of inner ? inner.split(",") : [])
          if (!MAP_ENTRY_RE.test(part.trim())) badEntry(part.trim());
      }
    } else {
      // Block form. Entries sit at the shallowest indent; anything deeper
      // belongs to an entry's own value (a nested block scalar), and a comment
      // is not an entry at all.
      const lines = blocks.metadata.filter((l) => !l.trimStart().startsWith("#"));
      const depth = Math.min(...lines.map((l) => l.length - l.trimStart().length));
      for (const line of lines)
        if (line.length - line.trimStart().length === depth && !MAP_ENTRY_RE.test(line.trim()))
          badEntry(line.trim());
    }
  }
  if (!("name" in fm)) err(`skills/${name}: missing required frontmatter field 'name'`);
  else if (fm.name !== name) err(`skills/${name}: frontmatter name '${fm.name}' != directory name`);
  else if (!SKILL_NAME_RE.test(name) || name.length > 64)
    err(`skills/${name}: name violates the Agent Skills constraints (1-64 chars, lowercase alphanumeric and single hyphens)`);
  else if (RESERVED_NAME_RE.test(name))
    err(`skills/${name}: name contains a reserved word (anthropic, claude)`);
  const desc = (fm.description ?? "").trim();
  if (!desc) err(`skills/${name}: missing or empty description`);
  else if (desc.length > 1024) err(`skills/${name}: description ${desc.length} chars (limit 1024)`);
  const compat = (fm.compatibility ?? "").trim();
  if (compat.length > 500) err(`skills/${name}: compatibility ${compat.length} chars (limit 500)`);
  // Advisory: several skills exceed 500 lines intentionally (see CLAUDE.md).
  const lineCount = text.split("\n").length - (text.endsWith("\n") ? 1 : 0);
  if (lineCount > 500)
    warn(`skills/${name}: SKILL.md is ${lineCount} lines — consider moving content to resources/`);
  // Require a real body beyond the frontmatter.
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
// Keep in sync with the PR-title lint in skills-ci.yml.
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
// Tracked SKILL.md outside skills/ ships to every consumer via skill installers.
for (const f of tracked) {
  if (!f.endsWith("/SKILL.md")) continue;
  if (!f.startsWith("skills/"))
    err(`stray tracked skill outside skills/: ${f} — installers will ship it`);
}

// ---------- 6. spec §4.1 path containment ----------
// Every tracked symlink must resolve inside the plugin root.
const rootPhys = realpathSync(root);
for (const f of tracked) {
  let stat;
  try { stat = lstatSync(join(root, f)); } catch { warn(`tracked path missing from disk: ${f}`); continue; }
  if (!stat.isSymbolicLink()) continue;
  let resolved;
  try { resolved = realpathSync(join(root, f)); }
  catch (e) { err(`symlink ${f} does not resolve (${e.code ?? e.message})`); continue; }
  if (resolved !== rootPhys && !resolved.startsWith(rootPhys + sep))
    err(`symlink ${f} resolves outside the plugin root: ${resolved}`);
}

// ---------- 7. one Agent Plugins root ----------
// A nested Agent Plugins manifest would break §4.1 containment assumptions.
const SPEC_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json";
for (const f of tracked) {
  if (!f.endsWith("/plugin.json")) continue;
  const m = manifestEntries.has(f) ? manifestEntries.get(f) : readJson(join(root, f));
  if (m && typeof m === "object" && m.$schema === SPEC_SCHEMA)
    err(`nested Agent Plugins manifest: ${f} — the repo root must be the only plugin root`);
}

// ---------- registration-surface helpers (sections 8-10) ----------
// A surface registers each skill exactly once. Collecting into a Set alone
// would collapse a repeat, so duplicates are reported before deduping.
const collectNames = (surface, names) => {
  const seen = new Set();
  for (const name of names) {
    if (seen.has(name))
      err(`${surface} lists '${name}' more than once — each skill must be registered exactly once`);
    seen.add(name);
  }
  return seen;
};

// Bidirectional: every skill is registered, every registration is a real skill.
const diffAgainstSkills = (surface, names, hint = "") => {
  for (const name of skillDirs)
    if (!names.has(name)) err(`${surface} is missing '${name}'${hint}`);
  for (const name of names)
    if (!skillDirs.includes(name)) err(`${surface} references '${name}' but skills/${name} does not exist`);
};

const allMatches = (re, text) => {
  const out = [];
  let m;
  while ((m = re.exec(text)) !== null) out.push(m);
  return out;
};

// ---------- 8. AGENTS.md <-> skills/ ----------
// Required input: a missing AGENTS.md silently disables the drift check.
const agentsPath = join(root, "agents", "AGENTS.md");
let agentsText;
try { agentsText = readFileSync(agentsPath, "utf8"); } catch {
  err(`agents/AGENTS.md is missing or unreadable — the registration drift check cannot run`);
  agentsText = null;
}

if (agentsText !== null) {
  // Both sides captured: an entry whose label and target disagree satisfies the
  // name diff in both directions while every harness loads the wrong file.
  const pathList = allMatches(/^\s*-\s+(\S+)\s+->\s+"skills\/([^/"]+)\/SKILL\.md"/gm, agentsText);
  for (const [, label, target] of pathList)
    if (label !== target)
      err(`agents/AGENTS.md path list: label '${label}' points to skills/${target}/ — they must match`);
  const pathListNames = collectNames("agents/AGENTS.md path list", pathList.map((m) => m[1]));
  diffAgainstSkills("agents/AGENTS.md path list", pathListNames,
    " — Claude Code/Codex/Gemini CLI won't find it");

  const availBlock = agentsText.match(/<available_skills>([\s\S]*?)<\/available_skills>/);
  if (availBlock) {
    const availNames = collectNames("agents/AGENTS.md <available_skills>",
      allMatches(/^(\S+):\s*`/gm, availBlock[1]).map((m) => m[1]));
    diffAgainstSkills("agents/AGENTS.md <available_skills>", availNames,
      " — the agent won't route to it");
  } else {
    err(`agents/AGENTS.md has no <available_skills> block — the agent won't route to any skill`);
  }
}

// ---------- 9. README.md skills table <-> skills/ ----------
// Required input: missing file or markers silently disables the drift check.
const readmePath = join(root, "README.md");
let readmeText;
try { readmeText = readFileSync(readmePath, "utf8"); } catch {
  err(`README.md is missing or unreadable — the skills-table drift check cannot run`);
  readmeText = null;
}

if (readmeText !== null) {
  const tableMatch = readmeText.match(/<!-- BEGIN_SKILLS_TABLE -->([\s\S]*?)<!-- END_SKILLS_TABLE -->/);
  if (tableMatch) {
    // Row shape: | **name** | what it does | [SKILL.md](skills/name/SKILL.md) |
    // The link is read per row rather than folded into the name regex, so a row
    // that has lost it reports that, instead of vanishing from the name diff.
    const rowNames = [];
    for (const line of tableMatch[1].split(/\r?\n/)) {
      const nameM = line.match(/\|\s*\*\*(\S+?)\*\*/);
      if (!nameM) continue;
      rowNames.push(nameM[1]);
      // Anchored on the [SKILL.md] label, not the first skills/ link on the
      // row: a description cell may cite another skill's file.
      const linkM = line.match(/\[SKILL\.md\]\(skills\/([^/)]+)\/SKILL\.md\)/);
      if (!linkM)
        err(`README.md skills table: row '${nameM[1]}' has no [SKILL.md](skills/<name>/SKILL.md) link — its target cannot be checked`);
      else if (linkM[1] !== nameM[1])
        err(`README.md skills table: row '${nameM[1]}' links to skills/${linkM[1]}/ — they must match`);
    }
    const tableNames = collectNames("README.md skills table", rowNames);
    diffAgainstSkills("README.md skills table", tableNames);
  } else {
    err(`README.md has no <!-- BEGIN_SKILLS_TABLE --> / <!-- END_SKILLS_TABLE --> markers — the skills-table drift check cannot run`);
  }
}

// ---------- 10. tests/skills.md <-> skills/ ----------
// The fifth registration surface CLAUDE.md names, and the only one nothing
// checked: a skill with no smoke-test entry ships with nothing exercising it
// and every CI job green.
const smokePath = join(root, "tests", "skills.md");
let smokeText;
try { smokeText = readFileSync(smokePath, "utf8"); } catch {
  err(`tests/skills.md is missing or unreadable — the smoke-test drift check cannot run`);
  smokeText = null;
}

if (smokeText !== null) {
  // One "## `skill-name`" heading per skill. A reformat that breaks the shape
  // empties the set, so the diff below reports every skill as missing rather
  // than passing on nothing.
  // [^`\n]: without the newline exclusion one unclosed backtick swallows the
  // next heading, which then reports as a missing skill and a phantom whose
  // name is several lines of the file.
  const smokeNames = collectNames("tests/skills.md",
    allMatches(/^##\s+`([^`\n]+)`/gm, smokeText).map((m) => m[1]));
  diffAgainstSkills("tests/skills.md", smokeNames, " — nothing smoke-tests it");
}

// ---------- result ----------
if (errors > 0) {
  console.error(`\nSkill validation failed with ${errors} error(s).`);
  process.exit(1);
}
console.log("Skill validation passed.");
