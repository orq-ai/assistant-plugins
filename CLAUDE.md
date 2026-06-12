# orq-skills — Maintainer Notes

## Versioning

This repo follows [Semantic Versioning](https://semver.org/). Version is tracked in **4 plugin manifests that must stay in sync**:

- `.claude-plugin/plugin.json`
- `.codex-plugin/plugin.json`
- `.cursor-plugin/plugin.json`
- `plugins/orq/.codex-plugin/plugin.json`

### When to bump

| Change | Bump |
|--------|------|
| Bug fix, typo, small doc tweak | **PATCH** (0.0.x) |
| New skill, new command, backward-compatible capability | **MINOR** (0.x.0) |
| Skill removed/renamed, breaking frontmatter change, MCP server URL change | **MAJOR** (x.0.0) |

### How to bump

1. Update `version` in all 4 plugin.json files (same value).
2. Add an entry to `CHANGELOG.md` under a new `## [X.Y.Z] - YYYY-MM-DD` heading. Use `### Added / Changed / Fixed / Removed` sections.
3. Run `tests/scripts/validate-plugin-manifests.sh` — passes.
4. Commit with message: `chore: bump version to X.Y.Z`.

## Plugin manifest rules

- All 4 plugin.json `version` fields must match — `validate-plugin-manifests.sh` does not enforce this yet, but drift is a bug.
- `plugins/orq/.mcp.json`, `plugins/orq/mcp.json`, `plugins/orq/skills` are symlinks. Do not replace with copies.
- New skill = add to: `skills/<name>/SKILL.md`, `agents/AGENTS.md` (path list + `<available_skills>` block), `README.md` skills table, `tests/skills.md` (smoke tests + Critical Files), and `skills-lock.json` (see below).

## skills-lock.json

Lock file for the [`vercel-labs/skills`](https://github.com/vercel-labs/skills) CLI (`npx skills`) — the tool people use to install this suite into Claude Code, Cursor, Codex, Copilot, Gemini, etc. It is **not** an npm/Claude-Code-core standard; only `npx skills` reads it.

**What the hash is for.** Each entry stores a `computedHash` of the skill folder. `npx skills sync` / `npx skills install` recompute the folder hash and compare: match → skill is up-to-date, skip; differ → reinstall. It is a **skip-cache key, not an integrity or security check** — a wrong hash only causes an unnecessary reinstall, never a failure. So keeping it correct is a courtesy to consumers, not a hard gate.

**Invariant:** every `skills/<dir>` must have exactly one entry in `skills-lock.json`, keyed by the dir name, with `source: "orq-ai/assistant-plugins"`, `sourceType: "github"`, and a current `computedHash`. Keys sorted alphabetically.

**To install / use the CLI:** Node ≥ 18. No global install needed — `npx skills …` downloads the `skills` package (npm) on demand.

**How `computedHash` is computed** (upstream `computeSkillFolderHash`, deterministic): SHA-256 over every file in the skill folder — walk recursively skipping `.git`/`node_modules`, sort files by `/`-normalized relative path, then for each file `update(relativePath)` then `update(fileContentBytes)`, output hex. Relative path is relative to the skill folder (the folder name itself is not hashed), so editing `SKILL.md` or any resource changes the hash; renaming the folder does not.

**How to update after changing skills:**
- **Added a skill** → add an entry. **Removed** → delete its entry (`npx skills remove` does *not* clean the lock — do it by hand). **Edited/renamed** → recompute that skill's `computedHash`.
- Regenerate all entries deterministically with Node (no install):

```bash
node --input-type=module -e '
import { createHash } from "node:crypto";
import { readdir, readFile, writeFile } from "node:fs/promises";
import { join, relative } from "node:path";
const walk = async (d, b, a=[]) => { for (const e of await readdir(d,{withFileTypes:true})) {
  if (e.name===".git"||e.name==="node_modules") continue; const f=join(d,e.name);
  e.isDirectory() ? await walk(f,b,a) : a.push({p:relative(b,f).split("\\").join("/"),f}); } return a; };
const hash = async dir => { const fs=(await walk(dir,dir)).sort((x,y)=>x.p.localeCompare(y.p));
  const h=createHash("sha256"); for (const x of fs){ h.update(x.p); h.update(await readFile(x.f)); } return h.digest("hex"); };
const dirs=(await readdir("skills",{withFileTypes:true})).filter(e=>e.isDirectory()).map(e=>e.name).sort((a,b)=>a.localeCompare(b));
const skills={}; for (const n of dirs) skills[n]={source:"orq-ai/assistant-plugins",sourceType:"github",computedHash:await hash(join("skills",n))};
await writeFile("skills-lock.json", JSON.stringify({version:1,skills},null,2)+"\n");
console.log("locked", dirs.length, "skills");'
```

> Hashes are computed from on-disk bytes, so line-ending differences change them — generate on LF (macOS/Linux), not Windows with `core.autocrlf=true`.

## Sub-plugin versioning

`plugins/trace-hooks` has its own independent version in `plugins/trace-hooks/.claude-plugin/plugin.json`. Bump it whenever files under `plugins/trace-hooks/` are touched in a commit — same semver rules apply. Do **not** bump the root 4-manifest version for trace-hooks-only changes.
