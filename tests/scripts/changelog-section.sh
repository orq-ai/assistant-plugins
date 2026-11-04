#!/usr/bin/env bash
# Print the CHANGELOG.md section for <version>; fail if missing or blank.
# Shared by the release workflow and PR CI so the check and the release agree.
set -euo pipefail
version=${1:?usage: changelog-section.sh <version>}
section=$(awk -v v="$version" '
  index($0, "## [" v "]") == 1 { on = 1; next }
  on && /^## / { exit }
  on
' "$(dirname "$0")/../../CHANGELOG.md")
if [ -z "${section//[[:space:]]/}" ]; then
  echo "::error::CHANGELOG.md has no entry for $version" >&2
  exit 1
fi
printf '%s\n' "$section"
