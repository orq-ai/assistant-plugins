#!/usr/bin/env bash
# Negative tests for validate-plugin-manifests.sh and for the vendored Agent Plugins
# 1.0.0 schema that CI runs against the root plugin.json: every check must fail the
# run when its invariant is broken. Run: bash tests/scripts/validate-plugin-manifests.test.sh
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
schema="$repo_root/tests/schemas/agent-plugins-1.0.0.plugin.schema.json"
tmp_root="$(mktemp -d)"
# Keep every fixture's git to its own folder: no parent repository above TMPDIR,
# and no GIT_DIR or index inherited from the caller.
export GIT_CEILING_DIRECTORIES="$tmp_root"
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE
failures=0
run_count=0
# Derived from the script itself so adding a case can't silently desync. A case run
# in a loop counts once here and runs several times, so this is a floor.
expected_count=$(grep -cE '^\s*expect_(fail|pass|schema_fail|schema_pass) ' "${BASH_SOURCE[0]}")

cleanup_and_report() {
  rm -rf "$tmp_root"
  if [ "$run_count" -lt "$expected_count" ]; then
    echo
    echo "ABORT: only $run_count of $expected_count expected cases ran (harness died early)"
    exit 1
  fi
}
trap cleanup_and_report EXIT

for tool in jq npx git; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "$tool is required but was not found on PATH."
    exit 1
  fi
done

# The validator checks its own checkout (it cds to ../.. from where it lives), so a
# fixture is a copy of the files it reads plus the script, in a git checkout. Copying
# the real manifests keeps the clean case honest: it passes for the same reason the
# repo does.
build_fixture() {
  local d="$tmp_root/$1" f
  mkdir -p "$d/tests/scripts" "$d/skills/orq-build-agent"
  for f in plugin.json .codex-plugin/plugin.json .cursor-plugin/plugin.json \
           .claude-plugin/plugin.json .mcp.json .agents/plugins/marketplace.json; do
    mkdir -p "$d/$(dirname "$f")"
    cp "$repo_root/$f" "$d/$f"
  done
  ln -s .mcp.json "$d/mcp.json"
  cp "$repo_root/skills/orq-build-agent/SKILL.md" "$d/skills/orq-build-agent/SKILL.md"
  cp "$repo_root/tests/scripts/validate-plugin-manifests.sh" "$d/tests/scripts/"
  git -C "$d" init -q
  git -C "$d" add -A
  echo "$d"
}

# Rewrite one JSON file in a fixture with a jq filter, then re-stage it.
edit_json() {
  local d="$1" file="$2" filter="$3" tmp
  tmp="$(mktemp)"
  jq "$filter" "$d/$file" > "$tmp"
  mv "$tmp" "$d/$file"
  git -C "$d" add -A
}

run_validator() { bash "$1/tests/scripts/validate-plugin-manifests.sh" 2>&1; }

# expect_fail <label> <dir> <pattern> — non-zero exit AND matching message.
expect_fail() {
  local label="$1" dir="$2" pattern="$3" rc=0 out
  run_count=$((run_count + 1))
  out=$(run_validator "$dir") || rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "FAIL [$label]: expected a non-zero exit, got 0"
    echo "$out" | sed 's/^/    /'
    failures=$((failures + 1))
    return
  fi
  if ! grep -qF -- "$pattern" <<<"$out"; then
    echo "FAIL [$label]: expected a message containing '$pattern', got:"
    echo "$out" | sed 's/^/    /'
    failures=$((failures + 1))
    return
  fi
  echo "PASS [$label]"
}

expect_pass() {
  local label="$1" dir="$2" rc=0 out
  run_count=$((run_count + 1))
  out=$(run_validator "$dir") || rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "FAIL [$label]: expected exit 0, got $rc"
    echo "$out" | sed 's/^/    /'
    failures=$((failures + 1))
    return
  fi
  echo "PASS [$label]"
}

# The root plugin.json rules are not in the shell script: CI checks them with ajv
# against the vendored schema. These run the same command on a mutated manifest.
ajv_validate="npx -y ajv-cli@5 validate --spec=draft2020"
schema_check() {
  local json="$1" file="$tmp_root/schema-case.json"
  printf '%s\n' "$json" > "$file"
  $ajv_validate -s "$schema" -d "$file" 2>&1
}

# The cases below prove a rule only while CI runs this same command, so check it.
run_count=$((run_count + 1))
ci_command="$ajv_validate -s tests/schemas/agent-plugins-1.0.0.plugin.schema.json -d plugin.json"
if ! grep -qF -- "run: $ci_command" "$repo_root/.github/workflows/skills-ci.yml"; then
  echo "FAIL [CI command]: skills-ci.yml no longer runs '$ci_command'; update schema_check to match"
  failures=$((failures + 1))
fi

# expect_schema_fail <label> <json> <pattern> — ajv rejects it with a matching error.
expect_schema_fail() {
  local label="$1" json="$2" pattern="$3" rc=0 out
  run_count=$((run_count + 1))
  out=$(schema_check "$json") || rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "FAIL [$label]: expected the schema to reject it, ajv accepted it"
    echo "$out" | sed 's/^/    /'
    failures=$((failures + 1))
    return
  fi
  if ! grep -qF -- "$pattern" <<<"$out"; then
    echo "FAIL [$label]: expected an ajv error containing '$pattern', got:"
    echo "$out" | sed 's/^/    /'
    failures=$((failures + 1))
    return
  fi
  echo "PASS [$label]"
}

expect_schema_pass() {
  local label="$1" json="$2" rc=0 out
  run_count=$((run_count + 1))
  out=$(schema_check "$json") || rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "FAIL [$label]: expected the schema to accept it, got:"
    echo "$out" | sed 's/^/    /'
    failures=$((failures + 1))
    return
  fi
  echo "PASS [$label]"
}

# --- clean fixture ---
d=$(build_fixture clean)
expect_pass "clean fixture passes" "$d"

# --- every required file exists and is JSON ---
for f in plugin.json .codex-plugin/plugin.json .cursor-plugin/plugin.json .claude-plugin/plugin.json \
         .mcp.json mcp.json .agents/plugins/marketplace.json; do
  d=$(build_fixture "missing-${f//\//-}")
  rm "$d/$f"
  git -C "$d" add -A
  expect_fail "missing $f" "$d" "Missing required file: $f"
done

d=$(build_fixture invalid-json)
printf '{ "name": "orq",\n' > "$d/.codex-plugin/plugin.json"
git -C "$d" add -A
expect_fail "invalid JSON" "$d" "Invalid JSON in .codex-plugin/plugin.json"

# --- client manifests: name, skills path, MCP ---
d=$(build_fixture codex-name); edit_json "$d" .codex-plugin/plugin.json '.name = "other"'
expect_fail "Codex name" "$d" "Codex plugin name must be 'orq'"
d=$(build_fixture codex-skills); edit_json "$d" .codex-plugin/plugin.json '.skills = "./other/"'
expect_fail "Codex skills path" "$d" "Codex plugin must declare skills path './skills/'"
d=$(build_fixture codex-mcp); edit_json "$d" .codex-plugin/plugin.json '.mcpServers = "./mcp.json"'
expect_fail "Codex MCP path" "$d" "Codex plugin must declare mcpServers path './.mcp.json'"

d=$(build_fixture cursor-name); edit_json "$d" .cursor-plugin/plugin.json '.name = "other"'
expect_fail "Cursor name" "$d" "Cursor plugin name must be 'orq'"
d=$(build_fixture cursor-skills); edit_json "$d" .cursor-plugin/plugin.json 'del(.skills)'
expect_fail "Cursor skills path" "$d" "Cursor plugin must declare skills path './skills/'"
d=$(build_fixture cursor-mcp); edit_json "$d" .cursor-plugin/plugin.json 'del(.mcpServers)'
expect_fail "Cursor MCP path" "$d" "Cursor plugin must declare mcpServers path './.mcp.json'"

d=$(build_fixture claude-name); edit_json "$d" .claude-plugin/plugin.json '.name = "other"'
expect_fail "Claude name" "$d" "Claude plugin name must be 'orq'"
d=$(build_fixture claude-skills); edit_json "$d" .claude-plugin/plugin.json '.skills = "./other/"'
expect_fail "Claude skills path" "$d" "Claude plugin must declare skills path './skills/'"
d=$(build_fixture claude-key-not-sensitive); edit_json "$d" .claude-plugin/plugin.json '.userConfig.orq_api_key.sensitive = false'
expect_fail "Claude API key option not sensitive" "$d" "must declare a sensitive string userConfig option 'orq_api_key'"
d=$(build_fixture claude-mcp-url); edit_json "$d" .claude-plugin/plugin.json '.mcpServers["orq-workspace"].url = "https://example.com/mcp"'
expect_fail "Claude inline MCP server URL" "$d" "must declare inline orq-workspace MCP server"
d=$(build_fixture claude-mcp-extra); edit_json "$d" .claude-plugin/plugin.json '.mcpServers.other = {"type": "http", "url": "https://example.com"}'
expect_fail "Claude extra MCP server" "$d" "Claude plugin mcpServers must be an inline map with only orq-workspace"

# --- root MCP config ---
d=$(build_fixture mcp-type); edit_json "$d" .mcp.json '.mcpServers["orq-workspace"].type = "sse"'
expect_fail "MCP server type" "$d" "MCP server type must be 'http'"
d=$(build_fixture mcp-url); edit_json "$d" .mcp.json '.mcpServers["orq-workspace"].url = "https://example.com/mcp"'
expect_fail "MCP server URL" "$d" "MCP server URL must be 'https://my.orq.ai/v2/mcp'"
d=$(build_fixture mcp-header); edit_json "$d" .mcp.json '.mcpServers["orq-workspace"].headers.Authorization = "Bearer sk-hardcoded"'
expect_fail "MCP header without the placeholder" "$d" "MCP authorization header must use ORQ_API_KEY placeholder"

# --- marketplace ---
d=$(build_fixture marketplace-name); edit_json "$d" .agents/plugins/marketplace.json '.name = "other"'
expect_fail "marketplace name" "$d" "Marketplace name must be 'orq-marketplace'"
d=$(build_fixture marketplace-policy); edit_json "$d" .agents/plugins/marketplace.json '.plugins |= map(.policy.installation = "AVAILABLE")'
expect_fail "marketplace plugin policy" "$d" "Marketplace must contain orq plugin with correct source, policy, and category"

# --- symlinks ---
d=$(build_fixture mcp-not-a-link)
rm "$d/mcp.json"; cp "$d/.mcp.json" "$d/mcp.json"
git -C "$d" add -A
expect_fail "mcp.json is a copy, not a symlink" "$d" "mcp.json should be a symlink but is not"
d=$(build_fixture mcp-wrong-target)
rm "$d/mcp.json"; ln -s .agents/plugins/marketplace.json "$d/mcp.json"
git -C "$d" add -A
expect_fail "mcp.json points elsewhere" "$d" "mcp.json points to '.agents/plugins/marketplace.json', expected '.mcp.json'"
d=$(build_fixture claude-plugin-symlink)
ln -s ../.mcp.json "$d/.claude-plugin/mcp.json"
git -C "$d" add -A
expect_fail "symlink under .claude-plugin/" "$d" ".claude-plugin/mcp.json is a symlink; no symlinks are allowed under .claude-plugin/"

# --- plugin root contents ---
d=$(build_fixture no-skills-dir)
rm -rf "$d/skills"; git -C "$d" add -A
expect_fail "skills/ missing" "$d" "skills/ must exist at the plugin root"
d=$(build_fixture no-build-agent)
rm "$d/skills/orq-build-agent/SKILL.md"; touch "$d/skills/.keep"; git -C "$d" add -A
expect_fail "orq-build-agent SKILL.md missing" "$d" "skills/orq-build-agent/SKILL.md must exist"

# --- git pre-flight: the symlink checks read the index ---
d=$(build_fixture no-git)
rm -rf "$d/.git"
expect_fail "not a git checkout" "$d" "is not the top of its own git checkout"
# Inside an unrelated parent repository the index reads come back empty and
# every check used to pass, so the fixture must not borrow the parent's git.
d=$(build_fixture nested-parent)
rm -rf "$d/.git"
git -C "$tmp_root" init -q
# Lift the ceiling for this one case, or git would never find the parent.
GIT_CEILING_DIRECTORIES="" expect_fail "inside a parent repository" "$d" "is not the top of its own git checkout"
rm -rf "$tmp_root/.git"
d=$(build_fixture broken-git)
printf 'garbage' > "$d/.git/index"
expect_fail "broken git index" "$d" "git ls-files failed, so .claude-plugin/ could not be checked for symlinks"

# --- root plugin.json against the vendored 1.0.0 schema (the CI ajv step) ---
root_manifest="$(cat "$repo_root/plugin.json")"
expect_schema_pass "this repo's plugin.json passes the schema" "$root_manifest"
expect_schema_fail "closed field set" "$(jq '.skills = "./skills/"' <<<"$root_manifest")" "must NOT have additional properties"
expect_schema_fail "wrong \$schema" "$(jq '."$schema" = "https://agent-plugins.org/schemas/0.9.0/plugin.schema.json"' <<<"$root_manifest")" "must be equal to constant"
expect_schema_fail "missing \$schema" "$(jq 'del(."$schema")' <<<"$root_manifest")" "must have required property '\$schema'"
for name in My-Plugin has--double -start; do
  expect_schema_fail "name '$name'" "$(jq --arg n "$name" '.name = $n' <<<"$root_manifest")" "must match pattern"
done
for value in 'null' '[]' '"str"'; do
  expect_schema_fail "non-object manifest $value" "$value" "must be object"
done

if [ "$failures" -gt 0 ]; then
  echo
  echo "$failures test(s) failed"
  exit 1
fi
echo
echo "all validate-plugin-manifests.sh and schema negative tests passed"
