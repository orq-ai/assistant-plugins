# orq-skills — Maintainer Notes

## Versioning

This repo follows [Semantic Versioning](https://semver.org/). Version is tracked in **4 plugin manifests that must stay in sync**:

- `plugin.json` — portable [Agent Plugins 1.0.0](https://github.com/agentplugins/agent-plugins-spec) manifest
- `.claude-plugin/plugin.json`
- `.codex-plugin/plugin.json`
- `.cursor-plugin/plugin.json`

### When to bump

| Change | Bump |
|--------|------|
| Bug fix, typo, small doc tweak | **PATCH** (0.0.x) |
| New skill, new command, backward-compatible capability | **MINOR** (0.x.0) |
| Skill removed/renamed, breaking frontmatter change, MCP server URL change | **MAJOR** (x.0.0) |

Exception: removing shipped content that was never the canonical surface (e.g. stale duplicates tracked outside `skills/`) is a **PATCH**, not MAJOR — cleanup is not a breaking change.

### How to bump

1. Update `version` in all 4 plugin.json files (same value).
2. Add an entry to `CHANGELOG.md` under a new `## [X.Y.Z] - YYYY-MM-DD` heading. Use `### Added / Changed / Fixed / Removed` sections.
3. Run `tests/scripts/validate-plugin-manifests.sh` — passes.
4. Commit with message: `chore: bump version to X.Y.Z`.

## Plugin manifest rules

- All 4 plugin.json `version` fields must match — `validate-skills.mjs` enforces this in CI.
- Root `plugin.json` is a **closed** Agent Plugins 1.0.0 manifest: only `$schema`, `name`, `version`, `description`, `author`, `homepage`, `repository`, `license`, `keywords`, `extensions` are permitted. Never add `skills`/`mcpServers` to it — the spec fixes those locations (`skills/`, `mcp.json`). CI validates it with `ajv` against `tests/schemas/agent-plugins-1.0.0.plugin.schema.json`, a vendored copy of the published schema; re-vendor it if the spec revs.
- **The per-harness manifests cannot be removed yet, and this is not just a scheduling matter.** The Codex `interface` blob (displayName, logo, brandColor, category, privacy/ToS URLs) and Cursor's `displayName` have nowhere to go in the portable manifest: they belong under `extensions["<reverse.domain>"]`, and no client has published a namespace — the spec defines no registry. Until one does, dropping `.codex-plugin/` or `.cursor-plugin/` loses store-listing metadata. Do not "finish the migration" without checking that first.
- Only the repo root may be an Agent Plugins root. A nested `plugin.json` carrying the 1.0.0 `$schema` is rejected by CI — it would re-create the `plugins/orq` escape (symlinks that stayed inside the repo but left their own plugin root). Client-specific manifests (`.claude-plugin/`, `.codex-plugin/`, `.cursor-plugin/`, `plugins/trace-hooks/`) are unaffected; they are matched by `$schema`, not filename.
- `mcp.json`, `.claude-plugin/.mcp.json`, `.claude-plugin/skills` are symlinks. Do not replace with copies; every tracked symlink must resolve inside the repo root (CI enforces).
- New skill = add to: `skills/<name>/SKILL.md`, `agents/AGENTS.md` (path list + `<available_skills>` block), `README.md` skills table, `tests/skills.md` (smoke tests + Critical Files), and `skills-lock.json` (see below).

## SKILL.md frontmatter is a closed field set

Only `name`, `description`, `license`, `compatibility`, `metadata`, `allowed-tools` are permitted — that is the [Agent Skills](https://agentskills.io/specification) field set, and `validate-skills.mjs` enforces it. Do **not** add a field a harness happens to read: Agent Plugins §7.1 requires a conformant client to *skip the whole skill* if it fails the Agent Skills spec, so one extra frontmatter line silently costs the skill everywhere. This is not theoretical — `disallowed-tools` shipped in 2.2.3 and made 14 of 15 skills skippable until 2.3.0 removed it. Harness-specific data belongs in `metadata` (a string→string map), which the spec reserves for exactly that. Cross-check with the reference validator when in doubt:

```bash
uvx --from "git+https://github.com/agentskills/agentskills.git#subdirectory=skills-ref" skills-ref validate skills/<name>
```

A typo'd field name is the realistic version of this mistake, so the validator reports any unindented frontmatter line it cannot read as a key — `allowed_tools:` is not a harmless annotation, it is `allowed-tools` costing the skill.

**Skill names** additionally must not contain `anthropic` or `claude` as a whole segment. That is Anthropic's naming rule rather than a spec constraint, so `skills-ref` will not tell you; `validate-skills.mjs` does.

**SKILL.md length:** the spec *recommends* under 500 lines and the validator warns above it, deliberately short of an error — several skills here are over it because their procedures don't survive being split across `resources/`, and §7.1 does not make a long skill skippable. Other suites (e.g. `opper-ai/opper-skills`) do treat 500 as a hard limit; that is a real divergence, not an oversight.

## skills-lock.json

Lock file for the [`vercel-labs/skills`](https://github.com/vercel-labs/skills) CLI (`npx skills`) — the tool people use to install this suite into Claude Code, Cursor, Codex, Copilot, Gemini, etc. It is **not** an npm/Claude-Code-core standard; only `npx skills` reads it.

**What the hash is for.** Each entry stores a `computedHash` of the skill folder. `npx skills sync` / `npx skills install` recompute the folder hash and compare: match → skill is up-to-date, skip; differ → reinstall. It is a **skip-cache key, not an integrity or security check** — a wrong hash only causes an unnecessary reinstall, never a failure. So keeping it correct is a courtesy to consumers, not a hard gate.

**Invariant:** every `skills/<dir>` must have exactly one entry in `skills-lock.json`, keyed by the dir name, with `source: "orq-ai/assistant-plugins"`, `sourceType: "github"`, and a current `computedHash`. Keys sorted alphabetically.

**To install / use the CLI:** Node ≥ 18. No global install needed — `npx skills …` downloads the `skills` package (npm) on demand.

**How `computedHash` is computed** (after upstream `computeSkillFolderHash`, deterministic): SHA-256 over every **tracked** file in the skill folder, read from the **git index** — sort by `/`-normalized relative path, then for each file `update(relativePath)` then `update(blobBytes)`, output hex. Relative path is relative to the skill folder (the folder name itself is not hashed), so editing `SKILL.md` or any resource changes the hash; renaming the folder does not.

Reading the index rather than the disk is a deliberate divergence from upstream, and the reason the hashes are reproducible at all:

- **Line endings.** Under `core.autocrlf=true` the checkout is CRLF while the blobs are LF. Hashing the disk made all 15 skills report stale on Windows — the validator could not pass there — and `--fix` wrote CRLF hashes that CI then rejected. (`.gitattributes` now pins the working tree to LF as well, so the two agree.)
- **Ignored artifacts.** The disk walk picked up `.pytest_cache/`, `__pycache__/` and `runs/`, so running a skill's own pytest suite changed its hash. This is what forced the hand-corrected hash in `23406d3`.
- **Cost of the divergence.** `npx skills add <local path>` still hashes the folder on disk, so installing from a dirty or CRLF checkout sees a different hash than the lock records. That costs one needless reinstall and nothing else — the hash is a skip-cache key, never an integrity check. Installing from GitHub, which is how consumers get this repo, matches exactly.

**How to update after changing skills:**
- **Added a skill** → add an entry. **Removed** → delete its entry (`npx skills remove` does *not* clean the lock — do it by hand). **Edited/renamed** → recompute that skill's `computedHash`.
- Regenerate every entry with the validator, which is the only implementation of the algorithm — there is deliberately no second copy here to drift out of sync with it:

```bash
git add skills/                                  # hashes come from the index
node tests/scripts/validate-skills.mjs --fix
```

> `--fix` hashes **staged** content. An unstaged edit is not reflected; the validator warns when it finds one.

## Sub-plugin versioning

`plugins/trace-hooks` has its own independent version in `plugins/trace-hooks/.claude-plugin/plugin.json`. Bump it whenever files under `plugins/trace-hooks/` are touched in a commit — same semver rules apply. Do **not** bump the root 4-manifest version for trace-hooks-only changes.
