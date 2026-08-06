#!/usr/bin/env bash
# Validates skills/ frontmatter, skills-lock.json invariants, manifest version
# sync, and content patterns. Run from anywhere; operates on the repo root.
set -uo pipefail
cd "$(dirname "$0")/../.."
fail=0
err() { echo "ERROR: $*" >&2; fail=1; }
warn() { echo "warn: $*" >&2; }

# --- per-skill frontmatter ---------------------------------------------------
for dir in skills/*/; do
  name=$(basename "$dir")
  f="${dir}SKILL.md"
  [ -f "$f" ] || { err "$name: missing SKILL.md"; continue; }
  fmname=$(awk 'f==1 && /^name:/{sub(/^name:[[:space:]]*/,""); print; exit} /^---$/{f++}' "$f")
  [ "$fmname" = "$name" ] || err "$name: frontmatter name '$fmname' != directory name"
  # single-line description check; multi-line (folded '>') descriptions get a warn
  desc=$(awk 'f==1 && /^description:/{sub(/^description:[[:space:]]*/,""); print; exit} /^---$/{f++}' "$f")
  if [ -z "$desc" ] || [ "$desc" = ">" ]; then
    grep -q '^description:' "$f" && warn "$name: multi-line description — length not checked" \
      || err "$name: missing description"
  else
    [ "${#desc}" -le 1024 ] || err "$name: description ${#desc} chars (>1024)"
  fi
  lines=$(wc -l < "$f")
  [ "$lines" -le 500 ] || warn "$name: SKILL.md is $lines lines (>500) — consider moving content to resources/"
  jq -e --arg n "$name" '.skills[$n]' skills-lock.json >/dev/null 2>&1 \
    || err "$name: no entry in skills-lock.json"
done

# --- lock entries without a directory ---------------------------------------
for key in $(jq -r '.skills | keys[]' skills-lock.json); do
  [ -d "skills/$key" ] || err "skills-lock.json entry '$key' has no skills/ directory"
done

# --- manifest version sync (all 4 paths; one is a symlink, checked anyway) ---
v=$(jq -r .version .claude-plugin/plugin.json)
for m in .codex-plugin/plugin.json .cursor-plugin/plugin.json plugins/orq/.codex-plugin/plugin.json; do
  mv_=$(jq -r .version "$m")
  [ "$mv_" = "$v" ] || err "manifest version drift: $m has $mv_, .claude-plugin has $v"
done

# --- content pattern lint ----------------------------------------------------
# Commit/PR text is checked by CI separately; this covers committed files.
hits=$(grep -rniE "owner decision" skills/ CHANGELOG.md README.md 2>/dev/null || true)
[ -z "$hits" ] || { echo "$hits" >&2; err "pattern lint: decision-context language in public files"; }
hits=$(grep -rnE "\b(ENG|INN|BOPS|TOPS)-[0-9]+\b" skills/ CHANGELOG.md README.md 2>/dev/null || true)
[ -z "$hits" ] || { echo "$hits" >&2; err "pattern lint: non-RES internal ticket ids in public files"; }

[ "$fail" -eq 0 ] && echo "Skill validation passed."
exit "$fail"
