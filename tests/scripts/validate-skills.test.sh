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
skipped=0

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

  # agents/AGENTS.md — path list + available_skills for the fixture skill.
  mkdir -p "$d/agents"
  cat > "$d/agents/AGENTS.md" <<'AGENTS'
---
name: test-agent
description: test agent
---

<skills>

These skills are:
 - example-skill -> "skills/example-skill/SKILL.md"

<available_skills>

example-skill: `Fixture skill used for validation tests.`

</available_skills>

</skills>
AGENTS

  # README.md — skills table between markers for the fixture skill.
  cat > "$d/README.md" <<'README'
# Test

<!-- BEGIN_SKILLS_TABLE -->
| Skill | What It Does |
|-------|-------------|
| **example-skill** | Test skill |
<!-- END_SKILLS_TABLE -->
README

  # The checkout has to exist before --fix runs: lock hashes are computed from
  # the git index, so regenerating them in a bare directory would fall back to
  # the working tree and produce hashes that don't match the ones validation
  # then computes from the index.
  #
  # core.autocrlf is pinned off so fixtures behave the same whatever the
  # maintainer's global git config says — and so the crlf-worktree case below is
  # the only place line endings vary.
  git -C "$d" init -q
  git -C "$d" -c core.autocrlf=false add -A

  # --fix regenerates the lock from the staged files, so the fixture carries a
  # correct hash without this test reimplementing the hash function.
  node "$validator" "$d" --fix >/dev/null 2>&1

  echo "$d"
}

# Re-stage and re-lock a fixture after editing a skill: hashes come from the
# index, so an unstaged edit isn't hashed, and a staged one makes the lock stale.
# `|| true` because --fix still exits non-zero on whatever error is under test.
relock() {
  git -C "$1" -c core.autocrlf=false add -A
  node "$validator" "$1" --fix >/dev/null 2>&1 || true
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
git -C "$d" -c core.autocrlf=false add -A   # tracked but unlocked, the real shape of this mistake
expect_fail "skill missing from the lock" "$d" "skills/second-skill has no entry"

# An untracked skill folder hashes as if it were empty, so --fix would write a
# hash for content nobody can fetch and CI would reject it.
d=$(build_fixture untracked-skill)
mkdir -p "$d/skills/second-skill"
cp "$d/skills/example-skill/SKILL.md" "$d/skills/second-skill/SKILL.md"
sed -i.bak 's/^name: example-skill/name: second-skill/' "$d/skills/second-skill/SKILL.md"
rm -f "$d/skills/second-skill/SKILL.md.bak"
node "$validator" "$d" --fix >/dev/null 2>&1 || true
expect_fail "untracked skill folder" "$d" "skills/second-skill has no tracked files"

# --- the Windows case: a CRLF working tree must not make every hash stale ---
# LF blobs with a CRLF checkout is exactly what core.autocrlf=true produces, and
# it used to report all 15 skills stale — the validator could not pass at all on
# a Windows machine, and --fix there wrote CRLF hashes for CI to reject.
d=$(build_fixture crlf-worktree)
node -e 'const fs=require("fs"),f=process.argv[1];
  fs.writeFileSync(f, fs.readFileSync(f,"utf8").replace(/\r?\n/g,"\r\n"))' "$d/skills/example-skill/SKILL.md"
git -C "$d" -c core.autocrlf=true add -A   # normalises the blob back to LF
expect_pass "CRLF working tree does not make hashes stale" "$d"

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

# --- 3. Agent Skills conformance ---
# The closed field set is the one that bit us: `disallowed-tools` sat in 14 of
# 15 skills and made every one of them skippable under Agent Plugins §7.1,
# while every check in this repo passed.
d=$(build_fixture unknown-frontmatter-field)
sed -i.bak 's/^description: /disallowed-tools: Bash\ndescription: /' "$d/skills/example-skill/SKILL.md"
rm -f "$d/skills/example-skill/SKILL.md.bak"
relock "$d"
expect_fail "unknown frontmatter field" "$d" "unknown frontmatter field 'disallowed-tools'"

# A field whose name isn't [A-Za-z-]+ was invisible to the reader, so the closed
# field set passed on it. `allowed_tools` for `allowed-tools` is the typo that
# costs the whole skill under Agent Plugins §7.1.
d=$(build_fixture underscore-frontmatter-field)
sed -i.bak 's/^description: /allowed_tools: Bash\ndescription: /' "$d/skills/example-skill/SKILL.md"
rm -f "$d/skills/example-skill/SKILL.md.bak"
relock "$d"
expect_fail "frontmatter field with an underscore" "$d" "unknown frontmatter field 'allowed_tools'"

# Anything unindented that is neither key, comment nor continuation must be
# reported rather than skipped — that is what kept the hole above open.
d=$(build_fixture unreadable-frontmatter-line)
sed -i.bak 's/^description: /"quoted key": x\ndescription: /' "$d/skills/example-skill/SKILL.md"
rm -f "$d/skills/example-skill/SKILL.md.bak"
relock "$d"
expect_fail "unreadable frontmatter line" "$d" "unreadable frontmatter line"

# node, not python3: the Windows python3 shim rejects a heredoc on stdin, and
# under `set -e` that aborted the run with eleven cases still to go.
d=$(build_fixture long-description)
node -e 'const fs=require("fs"),f=process.argv[1];
  fs.writeFileSync(f, fs.readFileSync(f,"utf8")
    .replace("description: Fixture skill", "description: " + "x".repeat(1100) + " "))' \
  "$d/skills/example-skill/SKILL.md"
relock "$d"
expect_fail "description over 1024 chars" "$d" "description 1[0-9][0-9][0-9] chars"

# A folded scalar carrying a chomping indicator, measured at exactly the limit.
# `>-` used to be treated as the value rather than as an opener, so every folded
# description was measured with a stray ">- " glued to the front — this one came
# out at 1027 and was rejected for being 3 chars of syntax over the limit.
d=$(build_fixture folded-description-at-limit)
node -e 'const fs=require("fs"),f=process.argv[1];
  // 10x100 + 14 chars of text + 10 joining spaces = exactly 1024.
  const lines = Array(10).fill("x".repeat(100)).concat("x".repeat(14));
  fs.writeFileSync(f, fs.readFileSync(f,"utf8")
    .replace(/^description: .*$/m, "description: >-\n" + lines.map(l => "  " + l).join("\n")))' \
  "$d/skills/example-skill/SKILL.md"
relock "$d"
expect_pass "folded description of exactly 1024 chars" "$d"

# A quoted scalar used to keep its quotes and fail the directory-match check
# with a name nobody wrote.
d=$(build_fixture quoted-name)
sed -i.bak 's/^name: example-skill/name: "example-skill"/' "$d/skills/example-skill/SKILL.md"
rm -f "$d/skills/example-skill/SKILL.md.bak"
relock "$d"
expect_pass "quoted name matches the directory" "$d"

# Reserved words (opper-ai/opper-skills parity): Anthropic's naming rules reject
# these on publish however well the skill conforms to the spec.
d=$(build_fixture reserved-name)
mv "$d/skills/example-skill" "$d/skills/claude-helper"
sed -i.bak 's/^name: example-skill/name: claude-helper/' "$d/skills/claude-helper/SKILL.md"
rm -f "$d/skills/claude-helper/SKILL.md.bak"
relock "$d"
expect_fail "reserved word in the skill name" "$d" "reserved word"

# --- 5. no stray tracked skills ---
d=$(build_fixture stray-skill)
mkdir -p "$d/.agents/skills/leftover"
cp "$d/skills/example-skill/SKILL.md" "$d/.agents/skills/leftover/SKILL.md"
git -C "$d" add -A
expect_fail "tracked SKILL.md outside skills/" "$d" "stray tracked skill outside skills/"

# --- 6. spec §4.1 path containment ---
# Git Bash without developer mode copies the target instead of linking, so these
# fixtures cannot be built on Windows and would report failures that say nothing
# about the validator. Probe once, skip loudly, and keep a count: CI runs on
# Linux, where the probe always succeeds and nothing is skipped.
symlink_cases() {
  local d
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
}

if ln -s target "$tmp_root/.symlink-probe" 2>/dev/null && [ -L "$tmp_root/.symlink-probe" ]; then
  rm -f "$tmp_root/.symlink-probe"
  symlink_cases
else
  rm -f "$tmp_root/.symlink-probe"
  skipped=5
  echo "SKIP [5 symlink containment cases]: this platform does not create real symlinks — run them on Linux/CI"
fi

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

# --- 8. AGENTS.md <-> skills/ ---
# A skill directory that exists but isn't in AGENTS.md: the agent runtime
# can't find or route to it.
d=$(build_fixture skill-missing-from-agents)
mkdir -p "$d/skills/second-skill"
cp "$d/skills/example-skill/SKILL.md" "$d/skills/second-skill/SKILL.md"
sed -i.bak 's/^name: example-skill/name: second-skill/' "$d/skills/second-skill/SKILL.md"
rm -f "$d/skills/second-skill/SKILL.md.bak"
relock "$d"
expect_fail "skill missing from AGENTS.md path list" "$d" "path list is missing 'second-skill'"

# A phantom entry in the path list references a skill that doesn't exist.
d=$(build_fixture phantom-agents-pathlist)
sed -i.bak '/example-skill.*SKILL.md/a\ - phantom-skill -> "skills/phantom-skill/SKILL.md"' "$d/agents/AGENTS.md"
rm -f "$d/agents/AGENTS.md.bak"
expect_fail "phantom in AGENTS.md path list" "$d" "path list references 'phantom-skill' but skills/phantom-skill does not exist"

# A phantom entry in <available_skills> references a skill that doesn't exist.
d=$(build_fixture phantom-available-skills)
sed -i.bak '/<\/available_skills>/i\phantom-skill: `Does not exist.`' "$d/agents/AGENTS.md"
rm -f "$d/agents/AGENTS.md.bak"
expect_fail "phantom in AGENTS.md available_skills" "$d" "<available_skills> references 'phantom-skill' but skills/phantom-skill does not exist"

# --- 9. README.md skills table <-> skills/ ---
d=$(build_fixture skill-missing-from-readme)
mkdir -p "$d/skills/second-skill"
cp "$d/skills/example-skill/SKILL.md" "$d/skills/second-skill/SKILL.md"
sed -i.bak 's/^name: example-skill/name: second-skill/' "$d/skills/second-skill/SKILL.md"
rm -f "$d/skills/second-skill/SKILL.md.bak"
# Add the new skill to AGENTS.md so only the README check fires.
sed -i.bak '/example-skill.*SKILL.md/a\ - second-skill -> "skills/second-skill/SKILL.md"' "$d/agents/AGENTS.md"
rm -f "$d/agents/AGENTS.md.bak"
sed -i.bak '/<\/available_skills>/i\second-skill: `Second test skill.`' "$d/agents/AGENTS.md"
rm -f "$d/agents/AGENTS.md.bak"
relock "$d"
expect_fail "skill missing from README table" "$d" "README.md skills table is missing 'second-skill'"

d=$(build_fixture phantom-readme-table)
sed -i.bak '/<!-- END_SKILLS_TABLE -->/i\| **phantom-skill** | Does not exist |' "$d/README.md"
rm -f "$d/README.md.bak"
expect_fail "phantom in README skills table" "$d" "README.md skills table references 'phantom-skill' but skills/phantom-skill does not exist"

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
if [ "$skipped" -gt 0 ]; then
  echo "all validate-skills.mjs negative tests passed ($skipped skipped on this platform)"
else
  echo "all validate-skills.mjs negative tests passed"
fi
