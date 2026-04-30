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
- New skill = add to: `skills/<name>/SKILL.md`, `agents/AGENTS.md` (path list + `<available_skills>` block), `README.md` skills table, `tests/skills.md` (smoke tests + Critical Files).

## Sub-plugin versioning

`plugins/trace-hooks` has its own independent version in `plugins/trace-hooks/.claude-plugin/plugin.json`. Bump it whenever files under `plugins/trace-hooks/` are touched in a commit — same semver rules apply. Do **not** bump the root 4-manifest version for trace-hooks-only changes.
