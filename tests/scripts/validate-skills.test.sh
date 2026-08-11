#!/usr/bin/env bash
# Regression test: validate-skills.mjs must reject a stale skills-lock.json hash
# rather than silently passing. Plain bash, no framework — run with
# `bash tests/scripts/validate-skills.test.sh`.
#
# The fixture is built in a temp dir rather than committed: a tracked SKILL.md
# outside skills/ would trip the validator's own stray-skill check, and skill
# installers walk the whole repo, so it would ship to every consumer.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fixture="$(mktemp -d)"
trap 'rm -rf "$fixture"' EXIT

mkdir -p "$fixture/skills/example-skill"
cat > "$fixture/skills/example-skill/SKILL.md" <<'EOF'
---
name: example-skill
description: Fixture skill used to prove the lock-hash freshness check fails loudly.
---

Body text long enough to clear the minimum-length check that guards against a
skill shipping with frontmatter and no instructions at all. This fixture exists
only so the validator has a real skill folder to hash, and its recorded hash in
skills-lock.json is deliberately wrong so the freshness check must reject it.
EOF

cat > "$fixture/skills-lock.json" <<'EOF'
{
  "version": 1,
  "skills": {
    "example-skill": {
      "source": "orq-ai/assistant-plugins",
      "sourceType": "github",
      "computedHash": "0000000000000000000000000000000000000000000000000000000000000000"
    }
  }
}
EOF

for m in .claude-plugin .codex-plugin .cursor-plugin; do
  mkdir -p "$fixture/$m"
  printf '{\n  "name": "orq",\n  "version": "0.0.0"\n}\n' > "$fixture/$m/plugin.json"
done
# Root manifest in the spec-conformant 1.0.0 shape the validator checks.
printf '{\n  "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",\n  "name": "orq",\n  "version": "0.0.0"\n}\n' > "$fixture/plugin.json"

rc=0
out=$(node "$repo_root/tests/scripts/validate-skills.mjs" "$fixture" 2>&1) || rc=$?

if [ "$rc" -eq 0 ]; then
  echo "FAIL: expected a non-zero exit on the stale-hash fixture"
  echo "$out"
  exit 1
fi

if ! grep -q "hash stale for 'example-skill'" <<<"$out"; then
  echo "FAIL: expected a stale-hash error naming 'example-skill', got:"
  echo "$out"
  exit 1
fi

echo "PASS: validate-skills.mjs rejects a stale lock hash"
