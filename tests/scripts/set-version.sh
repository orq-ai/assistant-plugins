#!/usr/bin/env bash
# Set the version in all 4 plugin manifests, in place so formatting survives.
# Root plugin.json is what the release workflow tags from.
set -euo pipefail
v=${1:?usage: set-version.sh X.Y.Z}
[[ "$v" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "not semver: $v" >&2; exit 1; }
cd "$(dirname "$0")/../.."
for f in plugin.json .claude-plugin/plugin.json .codex-plugin/plugin.json .cursor-plugin/plugin.json; do
  sed -E -i.bak "s/^([[:space:]]*\"version\"[[:space:]]*:[[:space:]]*)\"[^\"]*\"/\1\"$v\"/" "$f" && rm "$f.bak"
done
echo "set $v; add a CHANGELOG.md '## [$v] - $(date +%F)' entry"
