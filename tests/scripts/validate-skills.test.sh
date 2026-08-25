#!/usr/bin/env bash
# Negative tests for validate-skills.mjs: every enforced invariant must fail
# the run when violated. Run: bash tests/scripts/validate-skills.test.sh
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
validator="$repo_root/tests/scripts/validate-skills.mjs"
tmp_root="$(mktemp -d)"
failures=0
skipped=0
run_count=0
# Derived from the script itself so adding a case can't silently desync.
expected_count=$(grep -cE '^\s*expect_(fail|pass) ' "${BASH_SOURCE[0]}")

cleanup_and_report() {
  rm -rf "$tmp_root"
  if [ "$run_count" -lt "$((expected_count - skipped))" ]; then
    echo
    echo "ABORT: only $run_count of $((expected_count - skipped)) expected cases ran (harness died early)"
    exit 1
  fi
}
trap cleanup_and_report EXIT

# A fixture that passes clean: one skill, a lock, four manifests, a git checkout.
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

  cat > "$d/README.md" <<'README'
# Test

<!-- BEGIN_SKILLS_TABLE -->
| Skill | What It Does |
|-------|-------------|
| **example-skill** | Test skill |
<!-- END_SKILLS_TABLE -->
README

  # Pin core.autocrlf off so fixtures hash consistently across platforms.
  git -C "$d" init -q
  git -C "$d" -c core.autocrlf=false add -A

  # --fix regenerates the lock from staged files.
  node "$validator" "$d" --fix >/dev/null 2>&1

  echo "$d"
}

# Re-stage and re-lock after editing a skill.
relock() {
  git -C "$1" -c core.autocrlf=false add -A
  node "$validator" "$1" --fix >/dev/null 2>&1 || true
}

# expect_fail <label> <dir> <pattern> — non-zero exit AND matching message.
expect_fail() {
  local label="$1" dir="$2" pattern="$3" rc=0 out
  run_count=$((run_count + 1))
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
  run_count=$((run_count + 1))
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
git -C "$d" -c core.autocrlf=false add -A
expect_fail "skill missing from the lock" "$d" "skills/second-skill has no entry"

# Untracked skill: add it to the lock with a dummy hash so folderHash runs on it.
d=$(build_fixture untracked-skill)
mkdir -p "$d/skills/second-skill"
cp "$d/skills/example-skill/SKILL.md" "$d/skills/second-skill/SKILL.md"
sed -i.bak 's/^name: example-skill/name: second-skill/' "$d/skills/second-skill/SKILL.md"
rm -f "$d/skills/second-skill/SKILL.md.bak"
node -e 'const f=process.argv[1],j=JSON.parse(require("fs").readFileSync(f));
  j.skills["second-skill"]={source:"orq-ai/assistant-plugins",sourceType:"github",computedHash:"0".repeat(64)};
  require("fs").writeFileSync(f,JSON.stringify(j,null,2))' "$d/skills-lock.json"
expect_fail "untracked skill folder" "$d" "has no tracked files"

# LF blobs with a CRLF checkout is what core.autocrlf=true produces.
d=$(build_fixture crlf-worktree)
node -e 'const fs=require("fs"),f=process.argv[1];
  fs.writeFileSync(f, fs.readFileSync(f,"utf8").replace(/\r?\n/g,"\r\n"))' "$d/skills/example-skill/SKILL.md"
git -C "$d" -c core.autocrlf=true add -A
expect_pass "CRLF working tree does not make hashes stale" "$d"

# --- 2. manifest version sync ---
d=$(build_fixture version-drift)
printf '{\n  "name": "orq",\n  "version": "9.9.9"\n}\n' > "$d/.codex-plugin/plugin.json"
expect_fail "manifest version drift" "$d" "manifest version drift"

# --- 2b. root plugin.json is a usable manifest ---
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
# `disallowed-tools` sat in 14 of 15 skills, making them skippable under §7.1.
d=$(build_fixture unknown-frontmatter-field)
sed -i.bak 's/^description: /disallowed-tools: Bash\ndescription: /' "$d/skills/example-skill/SKILL.md"
rm -f "$d/skills/example-skill/SKILL.md.bak"
relock "$d"
expect_fail "unknown frontmatter field" "$d" "unknown frontmatter field 'disallowed-tools'"

# `allowed_tools:` (underscore) was invisible to the old key regex.
d=$(build_fixture underscore-frontmatter-field)
sed -i.bak 's/^description: /allowed_tools: Bash\ndescription: /' "$d/skills/example-skill/SKILL.md"
rm -f "$d/skills/example-skill/SKILL.md.bak"
relock "$d"
expect_fail "frontmatter field with an underscore" "$d" "unknown frontmatter field 'allowed_tools'"

# Unindented non-key/comment/continuation lines must be reported.
d=$(build_fixture unreadable-frontmatter-line)
sed -i.bak 's/^description: /"quoted key": x\ndescription: /' "$d/skills/example-skill/SKILL.md"
rm -f "$d/skills/example-skill/SKILL.md.bak"
relock "$d"
expect_fail "unreadable frontmatter line" "$d" "unreadable frontmatter line"

# node, not python3: the Windows python3 shim rejects heredoc stdin under set -e.
d=$(build_fixture long-description)
node -e 'const fs=require("fs"),f=process.argv[1];
  fs.writeFileSync(f, fs.readFileSync(f,"utf8")
    .replace("description: Fixture skill", "description: " + "x".repeat(1100) + " "))' \
  "$d/skills/example-skill/SKILL.md"
relock "$d"
expect_fail "description over 1024 chars" "$d" "description 1[0-9][0-9][0-9] chars"

# >- used to be treated as the value, adding 3 chars of syntax to the measurement.
d=$(build_fixture folded-description-at-limit)
node -e 'const fs=require("fs"),f=process.argv[1];
  // 10x100 + 14 chars + 10 joining spaces = exactly 1024.
  const lines = Array(10).fill("x".repeat(100)).concat("x".repeat(14));
  fs.writeFileSync(f, fs.readFileSync(f,"utf8")
    .replace(/^description: .*$/m, "description: >-\n" + lines.map(l => "  " + l).join("\n")))' \
  "$d/skills/example-skill/SKILL.md"
relock "$d"
expect_pass "folded description of exactly 1024 chars" "$d"

# Quoted name used to keep its quotes and fail the directory-match check.
d=$(build_fixture quoted-name)
sed -i.bak 's/^name: example-skill/name: "example-skill"/' "$d/skills/example-skill/SKILL.md"
rm -f "$d/skills/example-skill/SKILL.md.bak"
relock "$d"
expect_pass "quoted name matches the directory" "$d"

# Anthropic's naming rules reject reserved words on publish.
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
# Git Bash without developer mode copies instead of linking; skip on those platforms.
symlink_cases() {
  local d
  d=$(build_fixture escaping-symlink)
  ln -s /etc/hosts "$d/escape.json"
  git -C "$d" add -A
  expect_fail "symlink escaping the plugin root" "$d" "resolves outside the plugin root"

  # `..` parent traversal that string-prefix matching would accept.
  d=$(build_fixture dotdot-symlink)
  ln -s "skills/../.." "$d/escape"
  git -C "$d" add -A
  expect_fail "symlink to the root's parent via .." "$d" "resolves outside the plugin root"

  d=$(build_fixture dangling-symlink)
  ln -s ./nowhere.json "$d/dangling.json"
  git -C "$d" add -A
  expect_fail "dangling symlink" "$d" "does not resolve"

  # Without -z, git C-quotes non-ASCII paths and lstat silently misses them.
  d=$(build_fixture non-ascii-symlink)
  mkdir -p "$d/café"
  ln -s /etc/hosts "$d/café/escape.json"
  git -C "$d" add -A
  expect_fail "escaping symlink under a non-ASCII path" "$d" "resolves outside the plugin root"

  # A symlink inside the root is legal under §4.1.
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
  # Derive the skip count from symlink_cases itself.
  skipped=$(sed -n '/^symlink_cases/,/^}/p' "${BASH_SOURCE[0]}" | grep -cE '^\s*expect_(fail|pass) ')
  echo "SKIP [$skipped symlink containment cases]: this platform does not create real symlinks — run them on Linux/CI"
fi

# --- 7. one Agent Plugins root ---
d=$(build_fixture nested-spec-manifest)
mkdir -p "$d/nested"
cp "$d/plugin.json" "$d/nested/plugin.json"
git -C "$d" add -A
expect_fail "nested Agent Plugins manifest" "$d" "nested Agent Plugins manifest"

# Client-specific manifests nested anywhere are legal.
d=$(build_fixture nested-harness-manifest)
mkdir -p "$d/plugins/other-plugin/.claude-plugin"
printf '{\n  "name": "other",\n  "version": "1.0.0"\n}\n' > "$d/plugins/other-plugin/.claude-plugin/plugin.json"
git -C "$d" add -A
expect_pass "nested client-specific manifest is allowed" "$d"

# --- 8. AGENTS.md <-> skills/ ---
# Skill exists but isn't in AGENTS.md.
d=$(build_fixture skill-missing-from-agents)
mkdir -p "$d/skills/second-skill"
cp "$d/skills/example-skill/SKILL.md" "$d/skills/second-skill/SKILL.md"
node -e 'const fs=require("fs"),f=process.argv[1];
  fs.writeFileSync(f,fs.readFileSync(f,"utf8").replace(/^name: example-skill$/m,"name: second-skill"))' \
  "$d/skills/second-skill/SKILL.md"
relock "$d"
expect_fail "skill missing from AGENTS.md path list" "$d" "path list is missing 'second-skill'"

# Phantom entry in the path list.
d=$(build_fixture phantom-agents-pathlist)
node -e 'const fs=require("fs"),f=process.argv[1];
  fs.writeFileSync(f,fs.readFileSync(f,"utf8")
    .replace(/(example-skill.*SKILL\.md")/,"$1\n - phantom-skill -> \"skills/phantom-skill/SKILL.md\""))' \
  "$d/agents/AGENTS.md"
expect_fail "phantom in AGENTS.md path list" "$d" "path list references 'phantom-skill' but skills/phantom-skill does not exist"

# Phantom entry in <available_skills>.
d=$(build_fixture phantom-available-skills)
node -e 'const fs=require("fs"),f=process.argv[1];
  fs.writeFileSync(f,fs.readFileSync(f,"utf8")
    .replace("</available_skills>","phantom-skill: \x60Does not exist.\x60\n\n</available_skills>"))' \
  "$d/agents/AGENTS.md"
expect_fail "phantom in AGENTS.md available_skills" "$d" "<available_skills> references 'phantom-skill' but skills/phantom-skill does not exist"

# Path list label doesn't match the target directory.
d=$(build_fixture agents-label-target-mismatch)
node -e 'const fs=require("fs"),f=process.argv[1];
  fs.writeFileSync(f,fs.readFileSync(f,"utf8")
    .replace("example-skill -> \"skills/example-skill/SKILL.md\"",
             "example-skill -> \"skills/wrong-dir/SKILL.md\""))' \
  "$d/agents/AGENTS.md"
expect_fail "AGENTS.md label/target mismatch" "$d" "label 'example-skill' points to skills/wrong-dir/"

# Missing AGENTS.md entirely.
d=$(build_fixture missing-agents-md)
rm -rf "$d/agents"
expect_fail "missing AGENTS.md" "$d" "AGENTS.md is missing or unreadable"

# Missing <available_skills> block.
d=$(build_fixture missing-available-skills-block)
node -e 'const fs=require("fs"),f=process.argv[1];
  fs.writeFileSync(f,fs.readFileSync(f,"utf8")
    .replace(/<available_skills>[\s\S]*<\/available_skills>/,""))' \
  "$d/agents/AGENTS.md"
expect_fail "missing available_skills block" "$d" "has no <available_skills> block"

# --- 9. README.md skills table <-> skills/ ---
d=$(build_fixture skill-missing-from-readme)
mkdir -p "$d/skills/second-skill"
cp "$d/skills/example-skill/SKILL.md" "$d/skills/second-skill/SKILL.md"
node -e 'const fs=require("fs"),f=process.argv[1];
  fs.writeFileSync(f,fs.readFileSync(f,"utf8").replace(/^name: example-skill$/m,"name: second-skill"))' \
  "$d/skills/second-skill/SKILL.md"
# Add to AGENTS.md so only the README check fires.
node -e 'const fs=require("fs"),f=process.argv[1];
  fs.writeFileSync(f,fs.readFileSync(f,"utf8")
    .replace(/(example-skill.*SKILL\.md")/,"$1\n - second-skill -> \"skills/second-skill/SKILL.md\"")
    .replace("</available_skills>","second-skill: \x60Second test skill.\x60\n\n</available_skills>"))' \
  "$d/agents/AGENTS.md"
relock "$d"
expect_fail "skill missing from README table" "$d" "README.md skills table is missing 'second-skill'"

d=$(build_fixture phantom-readme-table)
node -e 'const fs=require("fs"),f=process.argv[1];
  fs.writeFileSync(f,fs.readFileSync(f,"utf8")
    .replace("<!-- END_SKILLS_TABLE -->","| **phantom-skill** | Does not exist |\n<!-- END_SKILLS_TABLE -->"))' \
  "$d/README.md"
expect_fail "phantom in README skills table" "$d" "README.md skills table references 'phantom-skill' but skills/phantom-skill does not exist"

# Missing README.md entirely.
d=$(build_fixture missing-readme)
rm "$d/README.md"
git -C "$d" add -A
expect_fail "missing README.md" "$d" "README.md is missing or unreadable"

# Missing table markers.
d=$(build_fixture missing-table-markers)
node -e 'const fs=require("fs"),f=process.argv[1];
  fs.writeFileSync(f,fs.readFileSync(f,"utf8")
    .replace(/<!-- BEGIN_SKILLS_TABLE -->[\s\S]*<!-- END_SKILLS_TABLE -->/,""))' \
  "$d/README.md"
expect_fail "missing table markers" "$d" "has no <!-- BEGIN_SKILLS_TABLE -->"

# --- git-failure paths ---
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
