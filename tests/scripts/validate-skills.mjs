#!/usr/bin/env node
// Repo-invariant checks for the skills suite, in one pass:
//   1. skills-lock.json <-> skills/ directories, both directions, with hash freshness
//      (recomputes every computedHash from the git index and fails on drift)
//   2. the four plugin manifests agree on one version, and the root
//      plugin.json is a usable object (its Agent Plugins 1.0.0 field rules
//      are ajv's job — see the vendored schema in tests/schemas/)
//   3. SKILL.md frontmatter is loadable, consistent with its directory, and
//      conforms to the Agent Skills spec — closed field set plus the name,
//      description and compatibility constraints, measured over the full
//      length of folded (>-style) values. Agent Plugins §7.1 lets a client
//      skip a skill that fails this, so it is not cosmetic.
//   4. content-pattern lint on public files
//   5. no tracked SKILL.md outside skills/
//   6. spec §4.1 — every tracked symlink resolves inside the plugin root
//   7. the repo root is the only Agent Plugins root
//   8. agents/AGENTS.md <-> skills/ directories — path list and
//      <available_skills> both cover every skill, bidirectionally
//   9. README.md skills table <-> skills/ directories — the table
//      between BEGIN/END_SKILLS_TABLE markers covers every skill
// Errors fail the run; warnings don't. Run from anywhere in the repo.

import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { existsSync, lstatSync, readdirSync, readFileSync, realpathSync, statSync, writeFileSync } from "node:fs";
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

// ---------- git probe ----------
// Done once, up front: the lock hashes (section 1) and the tracked-file checks
// (sections 5-7) both need it, and running `git ls-files` twice could report the
// same broken checkout twice.
//
// -z: without it git C-quotes any path with non-ASCII, quote, backslash or
// newline bytes, and every check below silently skips that file.
// A genuinely non-git root (a plain directory, a test fixture) legitimately
// skips sections 5-7 and hashes the working tree instead. But if .git is there
// and git still failed, something is wrong with the checkout (a corrupt index,
// `detected dubious ownership` under a containerised runner) and skipping would
// drop three sections from a run that then reports success. Fail instead.
let tracked = [];
let isCheckout = false;
try {
  tracked = execFileSync("git", ["ls-files", "-z"], { cwd: root, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] })
    .split("\0").filter(Boolean);
  isCheckout = true;
} catch (e) {
  const reason = String(e.stderr || e.message).trim().split("\n")[0];
  if (existsSync(join(root, ".git")))   // a worktree's .git is a file, not a dir
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

// Mirrors the canonical lock-hash algorithm documented in CLAUDE.md
// ("How computedHash is computed") — keep the two in sync.
//
// The bytes come from the git index, not from disk. Hashing the working tree
// made the lock non-reproducible in two ways, both of which have already cost
// us a hand-corrected hash (23406d3):
//   * line endings — under core.autocrlf=true the checkout is CRLF while the
//     blobs are LF, so every one of the 15 skills reported a stale hash and
//     the validator could not pass on a Windows machine at all. Worse, --fix
//     there writes CRLF-derived hashes that CI then rejects.
//   * gitignored artifacts — the walk picked up .pytest_cache/, __pycache__/
//     and runs/, so merely running a skill's own pytest suite changed its hash.
// Index blobs are LF on every platform and contain only tracked files, which is
// also what a consumer's `npx skills` sees when it fetches this repo from
// GitHub. It is not byte-identical to upstream computeSkillFolderHash for a
// *local-path* install off a CRLF checkout; that costs one needless reinstall,
// because the hash is a skip-cache key and never an integrity check.
const gitFiles = (dir) =>
  execFileSync("git", ["ls-files", "-z", "--", dir], { cwd: root, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] })
    .split("\0").filter(Boolean);

// One `git cat-file --batch` per folder rather than one spawn per file: a skill
// with a few dozen resources costs seconds on Windows otherwise.
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
    off = start + size + 1;   // git writes a newline after each object
  }
  return blobs;
};

const folderHashDisk = (dir) => {
  const files = walkFiles(dir).sort((a, b) => a.rel.localeCompare(b.rel));
  const h = createHash("sha256");
  for (const f of files) { h.update(f.rel); h.update(readFileSync(f.full)); }
  return h.digest("hex");
};

const folderHash = (dir) => {
  // Falls back to the working tree only when this isn't a git checkout — the
  // same policy sections 5-7 use, and what the non-git test fixture relies on.
  if (!isCheckout) return folderHashDisk(dir);
  const rel = relative(root, dir).split(sep).join("/");
  const files = gitFiles(rel).sort((a, b) => a.localeCompare(b));
  if (files.length === 0) {
    // An untracked skill folder would otherwise hash as if it were empty, and
    // --fix would write that hash into the lock for CI to reject.
    err(`${rel} has no tracked files — \`git add\` it before regenerating the lock`);
    return "";
  }
  const blobs = gitBlobs(files);
  const h = createHash("sha256");
  for (let i = 0; i < files.length; i++) {
    h.update(files[i].slice(rel.length + 1));   // relative to the skill folder
    h.update(blobs[i]);
  }
  return h.digest("hex");
};

// `name: "foo"` used to keep its quotes and then fail the directory-match check
// with a message about a name nobody wrote.
const unquote = (s) =>
  (s.length > 1 && ((s.startsWith('"') && s.endsWith('"')) || (s.startsWith("'") && s.endsWith("'"))))
    ? s.slice(1, -1) : s;

// Minimal frontmatter reader. Returns { fields, unparsed }: scalar values with
// block scalars (">"/"|", with any chomping indicator) joined to their full
// text, plus every unindented line it could not read as a key.
//
// `unparsed` is the point. The key pattern used to be /^([A-Za-z-]+):/, which
// made `allowed_tools:` and `model2:` invisible — the closed-field-set check
// below then passed on frontmatter that a conformant client rejects outright,
// which is the same failure `disallowed-tools` shipped in 2.2.3. Widening the
// pattern to identifier-ish keys catches the realistic typos; reporting
// anything still unreadable catches the rest, so nothing in a frontmatter
// block can be silently ignored again. Exotic YAML (quoted or non-ASCII keys)
// lands in `unparsed` rather than being validated as a field — an error either
// way, and the `skills-ref` cross-check in CLAUDE.md stays the backstop.
const readFrontmatter = (text) => {
  const lines = text.replace(/^﻿/, "").split(/\r?\n/);
  if (lines[0] !== "---") return null;
  const fields = {};
  const unparsed = [];
  let key = null;
  for (let i = 1; i < lines.length; i++) {
    const line = lines[i];
    if (line === "---") break;
    const m = line.match(/^([A-Za-z_][A-Za-z0-9_.-]*):(?:\s+(.*))?$/);
    if (m) {
      key = m[1];
      const raw = (m[2] ?? "").trim();
      // A block scalar opener carries no value of its own; the indented
      // continuation lines below supply it. `>-`, `|+`, `>2` all count.
      fields[key] = /^[>|][-+]?[0-9]*$/.test(raw) ? "" : unquote(raw);
    } else if (key && /^\s+\S/.test(line)) {
      // Not unquoted: inside a block scalar the quotes are content, and a line
      // that happens to open and close with one ("my judge keeps changing its
      // mind") would otherwise be silently shortened.
      fields[key] = (fields[key] ? fields[key] + " " : "") + line.trim();
    } else if (line.trim() !== "" && !line.trimStart().startsWith("#")) {
      unparsed.push(line);
    }
  }
  return { fields, unparsed };
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

// The hashes describe staged content, so an edit that hasn't been `git add`ed
// is invisible to them. Say so rather than letting a clean run imply the working
// tree is what got hashed.
if (isCheckout) {
  // Hashes come from the index, so only worktree-dirty entries matter: the
  // second character (Y) of `git status --porcelain` is the worktree status.
  // M = modified, D = deleted, ? = untracked.  Staged-only entries (Y=' ')
  // ARE reflected in the index and should not warn.
  const dirty = execFileSync("git", ["status", "--porcelain", "-z", "--", "skills"],
    { cwd: root, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] })
    .split("\0").filter(Boolean)
    .filter((e) => e.length >= 2 && e[1] !== " ");
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
// Parse once and keep the objects: sections 2b and 7 read these too, and a
// second readJson would report an unreadable file twice. Failures stay in the
// map as null so that holds for them as well — filtering here would send
// exactly the already-reported files back through readJson in section 7.
const manifestEntries = new Map(manifests.map((m) => [m, readJson(join(root, m))]));
const versions = [...manifestEntries]
  .filter(([, json]) => json !== null)
  .map(([m, json]) => ({ m, v: json.version }));
const [reference, ...rest] = versions;
for (const { m, v } of rest)
  // Name the manifest the reference version actually came from — with
  // plugin.json absent that is not manifests[0], and blaming a missing file
  // sends you hunting for it.
  if (v !== reference.v) err(`manifest version drift: ${m} has ${v}, ${reference.m} has ${reference.v}`);

// ---------- 2b. root plugin.json is a usable manifest ----------
// Its 1.0.0 field rules are ajv's job (vendored schema, run in CI); only the
// shape the version check above assumes is asserted here.
const rootManifest = manifestEntries.get("plugin.json") ?? null;
if (rootManifest !== null && (typeof rootManifest !== "object" || Array.isArray(rootManifest)))
  err(`plugin.json: top level must be a JSON object, got ${Array.isArray(rootManifest) ? "array" : typeof rootManifest}`);

// ---------- 3. frontmatter consistency + Agent Skills conformance ----------
// Agent Plugins §7.1 requires every skill to conform to the Agent Skills
// specification, and a client MUST skip one that doesn't. The frontmatter
// field set there is closed, so an extra field is not a harmless annotation —
// it costs the skill. This mirrors ALLOWED_FIELDS in `skills-ref`, the
// reference validator the spec links to, which reports an extra field as an
// error rather than a warning. Keep the two in sync if the spec adds a field.
const SKILL_FIELDS = new Set(["name", "description", "license", "compatibility",
  "metadata", "allowed-tools"]);
// 1-64 chars, lowercase alphanumeric and hyphens, no leading/trailing hyphen,
// no consecutive hyphens. Distinct from the plugin name pattern in §5.5 of
// Agent Plugins, which also permits periods.
const SKILL_NAME_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
// Anthropic's skill naming rules reserve these, so a name carrying one is
// rejected on publish however well it conforms to the Agent Skills spec.
// Matched whole-segment, so `orq-claude-bridge` trips and `clauded` doesn't.
const RESERVED_NAME_RE = /(^|-)(anthropic|claude)(-|$)/i;
for (const name of skillDirs) {
  const path = join(root, "skills", name, "SKILL.md");
  let text;
  try { text = readFileSync(path, "utf8"); }
  catch { err(`skills/${name}: missing SKILL.md`); continue; }
  const parsed = readFrontmatter(text);
  if (!parsed) { err(`skills/${name}: SKILL.md has no frontmatter block`); continue; }
  const { fields: fm, unparsed } = parsed;
  for (const line of unparsed)
    err(`skills/${name}: unreadable frontmatter line '${line.trim()}' — it is neither a key, a comment, nor a continuation, so no check can see it`);
  for (const key of Object.keys(fm))
    if (!SKILL_FIELDS.has(key))
      err(`skills/${name}: unknown frontmatter field '${key}' — the Agent Skills field set is closed, so a conformant client skips this skill entirely (Agent Plugins §7.1)`);
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
  // A warning, deliberately, where opper-ai/opper-skills makes 500 lines an
  // error: several skills here are over it on purpose, because their procedures
  // don't survive being split across resources/. The spec recommends the limit,
  // it doesn't impose it, and §7.1 doesn't make a long skill skippable — so this
  // stays advisory. See CLAUDE.md.
  // Counted the way `wc -l` does, so the number is comparable to the 500 the
  // spec and other validators quote; splitting alone counts a phantom last line.
  const lineCount = text.split("\n").length - (text.endsWith("\n") ? 1 : 0);
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
// Skipped rather than fatal when root isn't a git checkout — see the git probe
// at the top of the file, which is where `tracked` comes from.
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
// The cost of that choice: a nested plugin root in a *client-specific* format
// is invisible here, which is the exact shape plugins/orq had. Re-adding it
// would pass. Matching on filename instead would flag all four manifests
// above, so this is deliberate — the spec invariant is enforced, the
// Codex/Claude-shaped one is left to review and the CLAUDE.md rule.
const SPEC_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json";
for (const f of tracked) {
  if (!f.endsWith("/plugin.json")) continue;
  const m = manifestEntries.has(f) ? manifestEntries.get(f) : readJson(join(root, f));
  if (m && typeof m === "object" && m.$schema === SPEC_SCHEMA)
    err(`nested Agent Plugins manifest: ${f} — the repo root must be the only plugin root`);
}

// ---------- 8. AGENTS.md <-> skills/ ----------
// Claude Code, Codex and Gemini CLI read AGENTS.md at runtime. A skill
// missing from the path list is undiscoverable; one missing from the
// <available_skills> block is never routed to. Cursor doesn't read this
// file (npx skills copies SKILL.md into .cursor/rules/), so this check
// guards the three agents that do.
//
// The file is required, not opportunistic: a missing or unreadable
// AGENTS.md turns the drift check off while CI stays green — the same
// failure class the git-probe hardening above was written to close.
const agentsPath = join(root, "agents", "AGENTS.md");
let agentsText;
try { agentsText = readFileSync(agentsPath, "utf8"); } catch {
  err(`agents/AGENTS.md is missing or unreadable — the registration drift check cannot run`);
  agentsText = null;
}

if (agentsText !== null) {
  const pathListRe = /^\s*-\s+(\S+)\s+->\s+"skills\//gm;
  const pathListNames = new Set();
  let agM;
  while ((agM = pathListRe.exec(agentsText)) !== null) pathListNames.add(agM[1]);

  for (const name of skillDirs)
    if (!pathListNames.has(name))
      err(`agents/AGENTS.md path list is missing '${name}' — Claude Code/Codex/Gemini CLI won't find it`);
  for (const name of pathListNames)
    if (!skillDirs.includes(name))
      err(`agents/AGENTS.md path list references '${name}' but skills/${name} does not exist`);

  const availBlock = agentsText.match(/<available_skills>([\s\S]*?)<\/available_skills>/);
  if (availBlock) {
    const availRe = /^(\S+):\s*`/gm;
    const availNames = new Set();
    while ((agM = availRe.exec(availBlock[1])) !== null) availNames.add(agM[1]);

    for (const name of skillDirs)
      if (!availNames.has(name))
        err(`agents/AGENTS.md <available_skills> is missing '${name}' — the agent won't route to it`);
    for (const name of availNames)
      if (!skillDirs.includes(name))
        err(`agents/AGENTS.md <available_skills> references '${name}' but skills/${name} does not exist`);
  } else {
    err(`agents/AGENTS.md has no <available_skills> block — the agent won't route to any skill`);
  }
}

// ---------- 9. README.md skills table <-> skills/ ----------
// The table between BEGIN/END_SKILLS_TABLE markers is what humans see on
// GitHub. A missing row means the skill is invisible in documentation.
// Same policy as section 8: the file and markers are required inputs.
const readmePath = join(root, "README.md");
let readmeText;
try { readmeText = readFileSync(readmePath, "utf8"); } catch {
  err(`README.md is missing or unreadable — the skills-table drift check cannot run`);
  readmeText = null;
}

if (readmeText !== null) {
  const tableMatch = readmeText.match(/<!-- BEGIN_SKILLS_TABLE -->([\s\S]*?)<!-- END_SKILLS_TABLE -->/);
  if (tableMatch) {
    const tableRe = /\|\s*\*\*(\S+?)\*\*/g;
    const tableNames = new Set();
    let rdM;
    while ((rdM = tableRe.exec(tableMatch[1])) !== null) tableNames.add(rdM[1]);

    for (const name of skillDirs)
      if (!tableNames.has(name))
        err(`README.md skills table is missing '${name}'`);
    for (const name of tableNames)
      if (!skillDirs.includes(name))
        err(`README.md skills table references '${name}' but skills/${name} does not exist`);
  } else {
    err(`README.md has no <!-- BEGIN_SKILLS_TABLE --> / <!-- END_SKILLS_TABLE --> markers — the skills-table drift check cannot run`);
  }
}

// ---------- result ----------
if (errors > 0) {
  console.error(`\nSkill validation failed with ${errors} error(s).`);
  process.exit(1);
}
console.log("Skill validation passed.");
