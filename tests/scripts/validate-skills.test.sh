#!/usr/bin/env bash
# Negative tests for validate-skills.mjs: every invariant it enforces must
# actually fail the run when violated. Plain bash, no framework — run with
# `bash tests/scripts/validate-skills.test.sh`.
#
# Why this exists: the checks in sections 2b and 5-7 were verified by hand
# during review, and two of them turned out to pass while not checking (a bare
# `null` manifest, and a git failure that skipped three sections silently).
# Hand-verification does not survive the next refactor; these do.
#
# Fixtures are built in a temp dir rather than committed: a tracked SKILL.md
# outside skills/ would trip the validator's own stray-skill check, and skill
# installers walk the whole repo, so it would ship to every consumer.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
validator="$repo_root/tests/scripts/validate-skills.mjs"
tmp_root="$(mktemp -d)"
trap 'rm -rf "$tmp_root"' EXIT
failures=0

# A fixture that passes clean: one real skill, a lock regenerated from it, the
# four manifests on one version, and a git checkout so sections 5-7 run. Each
# case starts from this and breaks exactly one thing.
build_fixture() {
  local d="$tmp_root/$1"
  mkdir -p "$d/skills/example-skill"
  cat > "$d/skills/example-skill/SKILL.md" <<'EOF'
---
name: example-skill
description: Fixture skill used to prove the validator's checks fail loudly when violated.
---

Body text long enough to clear the minimum-length check that guards against a
skill shipping with frontmatter and no instructions at all. This fixture exists
only so the validator has a real skill folder to hash and a real tree to walk,
and it has to clear two hundred non-whitespace characters to do that, which is
rather more prose than a fixture would otherwise need to carry.
EOF

  local m
  for m in .claude-plugin .codex-plugin .cursor-plugin; do
    mkdir -p "$d/$m"
    printf '{\n  "name": "orq",\n  "version": "0.0.0"\n}\n' > "$d/$m/plugin.json"
  done
  printf '{\n  "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",\n  "name": "orq",\n  "version": "0.0.0"\n}\n' > "$d/plugin.json"

  # --fix regenerates the lock from the directories on disk, so the fixture
  # carries a correct hash without this test reimplementing the hash function.
  node "$validator" "$d" --fix >/dev/null 2>&1

  git -C "$d" init -q
  git -C "$d" add -A
  echo "$d"
}

# expect_fail <label> <dir> <pattern> — non-zero exit AND a message matching pattern.
# Both halves matter: an exit code alone does not prove the intended check fired.
expect_fail() {
  local label="$1" dir="$2" pattern="$3" rc=0 out
  out=$(node "$validator" "$dir" 2>&1) || rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "FAIL [$label]: expected a non-zero exit, got 0"
    echo "$out" | sed 's/^/    /'
    failures=$((failures + 1))
    return
  fi
  if ! grep -q "$pattern" <<<"$out"; then
    echo "FAIL [$label]: expected a message matching '$pattern', got:"
    echo "$out" | sed 's/^/    /'
    failures=$((failures + 1))
    return
  fi
  echo "PASS [$label]"
}

expect_pass() {
  local label="$1" dir="$2" rc=0 out
  out=$(node "$validator" "$dir" 2>&1) || rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "FAIL [$label]: expected exit 0, got $rc"
    echo "$out" | sed 's/^/    /'
    failures=$((failures + 1))
    return
  fi
  echo "PASS [$label]"
}

# --- the clean fixture must pass, or every case below proves nothing ---
d=$(build_fixture baseline)
expect_pass "clean fixture passes" "$d"

# --- 1. lock <-> dirs ---
d=$(build_fixture stale-hash)
node -e 'const f=process.argv[1],j=JSON.parse(require("fs").readFileSync(f));
  j.skills["example-skill"].computedHash="0".repeat(64);
  require("fs").writeFileSync(f,JSON.stringify(j,null,2))' "$d/skills-lock.json"
expect_fail "stale lock hash" "$d" "hash stale for 'example-skill'"

d=$(build_fixture unlocked-skill)
mkdir -p "$d/skills/second-skill"
cp "$d/skills/example-skill/SKILL.md" "$d/skills/second-skill/SKILL.md"
sed -i.bak 's/^name: example-skill/name: second-skill/' "$d/skills/second-skill/SKILL.md"
rm -f "$d/skills/second-skill/SKILL.md.bak"
expect_fail "skill missing from the lock" "$d" "skills/second-skill has no entry"

# --- 2. manifest version sync ---
d=$(build_fixture version-drift)
printf '{\n  "name": "orq",\n  "version": "9.9.9"\n}\n' > "$d/.codex-plugin/plugin.json"
expect_fail "manifest version drift" "$d" "manifest version drift"

# --- 2b. root plugin.json is a usable manifest ---
# The bare-null case is the one that shipped green: it parses fine, is falsy,
# and every readJson caller skipped its checks without a word.
d=$(build_fixture null-manifest)
echo "null" > "$d/plugin.json"
expect_fail "root manifest is a bare null" "$d" "contains a bare null"

d=$(build_fixture array-manifest)
echo "[]" > "$d/plugin.json"
expect_fail "root manifest is an array" "$d" "top level must be a JSON object"

d=$(build_fixture missing-manifest)
rm "$d/plugin.json"
expect_fail "root manifest missing" "$d" "plugin.json: cannot read"

d=$(build_fixture unparseable-manifest)
echo "{ nope" > "$d/plugin.json"
expect_fail "root manifest is not JSON" "$d" "invalid JSON"

# --- 5. no stray tracked skills ---
d=$(build_fixture stray-skill)
mkdir -p "$d/.agents/skills/leftover"
cp "$d/skills/example-skill/SKILL.md" "$d/.agents/skills/leftover/SKILL.md"
git -C "$d" add -A
expect_fail "tracked SKILL.md outside skills/" "$d" "stray tracked skill outside skills/"

# --- 6. spec §4.1 path containment ---
d=$(build_fixture escaping-symlink)
ln -s /etc/hosts "$d/escape.json"
git -C "$d" add -A
expect_fail "symlink escaping the plugin root" "$d" "resolves outside the plugin root"

# A basename of `..` is the case that string-prefix matching accepted: the link
# lands on the repo's parent while every component looks inside it.
d=$(build_fixture dotdot-symlink)
ln -s "skills/../.." "$d/escape"
git -C "$d" add -A
expect_fail "symlink to the root's parent via .." "$d" "resolves outside the plugin root"

d=$(build_fixture dangling-symlink)
ln -s ./nowhere.json "$d/dangling.json"
git -C "$d" add -A
expect_fail "dangling symlink" "$d" "does not resolve"

# Non-ASCII path: without `git ls-files -z` git C-quotes it, lstat throws on the
# quoted literal, and the escape is skipped in silence.
d=$(build_fixture non-ascii-symlink)
mkdir -p "$d/café"
ln -s /etc/hosts "$d/café/escape.json"
git -C "$d" add -A
expect_fail "escaping symlink under a non-ASCII path" "$d" "resolves outside the plugin root"

# A symlink that stays inside the root is legal under §4.1 and must not trip it.
d=$(build_fixture contained-symlink)
ln -s ./skills "$d/skills-alias"
git -C "$d" add -A
expect_pass "symlink contained in the plugin root is allowed" "$d"

# --- 7. one Agent Plugins root ---
d=$(build_fixture nested-spec-manifest)
mkdir -p "$d/nested"
cp "$d/plugin.json" "$d/nested/plugin.json"
git -C "$d" add -A
expect_fail "nested Agent Plugins manifest" "$d" "nested Agent Plugins manifest"

# A client-specific manifest nested anywhere is legal — .claude-plugin,
# .codex-plugin, .cursor-plugin and plugins/trace-hooks all ship one.
d=$(build_fixture nested-harness-manifest)
mkdir -p "$d/plugins/other-plugin/.claude-plugin"
printf '{\n  "name": "other",\n  "version": "1.0.0"\n}\n' > "$d/plugins/other-plugin/.claude-plugin/plugin.json"
git -C "$d" add -A
expect_pass "nested client-specific manifest is allowed" "$d"

# --- the git-failure path: skipping sections 5-7 must not report success ---
# A non-git tree legitimately skips them; a checkout whose git is broken must
# not, or a run that never walked the tree still prints "validation passed".
d=$(build_fixture no-git)
rm -rf "$d/.git"
expect_pass "non-git tree skips the tracked-file checks" "$d"

d=$(build_fixture broken-git)
printf 'garbage' > "$d/.git/index"
expect_fail "broken git checkout" "$d" "sections 5-7 did not run"

if [ "$failures" -gt 0 ]; then
  echo
  echo "$failures test(s) failed"
  exit 1
fi
echo
echo "all validate-skills.mjs negative tests passed"
