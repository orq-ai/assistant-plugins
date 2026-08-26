#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

trap 'echo "ERROR: Validation failed at line $LINENO (exit code $?)" >&2' ERR

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required but was not found on PATH."
  exit 1
fi

assert_jq() {
  local file="$1" expr="$2" msg="$3"
  local jq_err
  if ! jq_err=$(jq -e "$expr" "$file" 2>&1 >/dev/null); then
    echo "FAIL: $msg"
    echo "  file: $file"
    echo "  expression: $expr"
    [[ -n "$jq_err" ]] && echo "  jq error: $jq_err"
    exit 1
  fi
}

assert_path() {
  local flag="$1" path="$2" msg="$3"
  # Resolved through content_path so a link this checkout could not materialise
  # is still tested against what it points at — see the note above assert_symlink.
  local resolved; resolved="$(content_path "$path")"
  if ! test "$flag" "$resolved"; then
    echo "FAIL: $msg"
    echo "  path: $path"
    [[ "$resolved" != "$path" ]] && echo "  resolved: $resolved (unmaterialised symlink)"
    exit 1
  fi
}

# The index is the authority on what ships, not the checkout. Windows git
# materialises a tracked symlink as a regular file whose content is the target
# path, so `-L` is false and `jq` is handed a path where it expected JSON. Both
# were reported as repo faults — "should be a symlink but is not" and "Invalid
# JSON in mcp.json" — when the repo is fine and the checkout simply cannot
# represent a link.
tracked_mode() { git ls-files -s -- "$1" 2>/dev/null | cut -d' ' -f1; }
is_tracked_symlink() { [[ "$(tracked_mode "$1")" == "120000" ]]; }

# The path whose *content* should be validated, following any unmaterialised
# link. Walks component by component, because a link can sit anywhere along the
# way: `.claude-plugin/skills/orq-build-agent/SKILL.md` passes through one.
# On a checkout that did materialise the links, `-L` is true and this is a no-op.
content_path() {
  local rest="$1" acc="" comp
  while [[ -n "$rest" ]]; do
    comp="${rest%%/*}"
    if [[ "$comp" == "$rest" ]]; then rest=""; else rest="${rest#*/}"; fi
    acc="${acc:+$acc/}$comp"
    if [[ ! -L "$acc" && -f "$acc" ]] && is_tracked_symlink "$acc"; then
      acc="$(dirname "$acc")/$(cat "$acc")"
    fi
  done
  echo "$acc"
}

assert_symlink() {
  local link="$1" expected_target="$2"
  local actual_target
  if [[ -L "$link" ]]; then
    actual_target="$(readlink "$link")"
  elif is_tracked_symlink "$link"; then
    actual_target="$(cat "$link")"   # unmaterialised: the blob content is the target
  else
    echo "FAIL: $link should be a symlink but is not, and is not tracked as one"
    exit 1
  fi
  if [[ "$actual_target" != "$expected_target" ]]; then
    echo "FAIL: $link points to '$actual_target', expected '$expected_target'"
    exit 1
  fi
}

# --- File existence and JSON validity ---

json_files=(
  "plugin.json"
  ".codex-plugin/plugin.json"
  ".cursor-plugin/plugin.json"
  ".claude-plugin/plugin.json"
  ".mcp.json"
  "mcp.json"
  ".agents/plugins/marketplace.json"
)

for file in "${json_files[@]}"; do
  if [[ ! -f "$file" ]]; then
    echo "FAIL: Missing required file: $file"
    exit 1
  fi
  target="$(content_path "$file")"
  if ! jq -e . "$target" >/dev/null 2>&1; then
    echo "FAIL: Invalid JSON in $file"
    [[ "$target" != "$file" ]] && echo "  (read through an unmaterialised symlink: $target)"
    jq . "$target" 2>&1 | head -5
    exit 1
  fi
done

# Root plugin.json: existence and JSON validity above; its 1.0.0 field rules
# are validated by ajv in CI against the vendored schema in tests/schemas/.

# --- Codex manifest: name, skills, and MCP ---

assert_jq .codex-plugin/plugin.json '.name == "orq"' \
  "Codex plugin name must be 'orq'"
assert_jq .codex-plugin/plugin.json '.skills == "./skills/"' \
  "Codex plugin must declare skills path './skills/'"
assert_jq .codex-plugin/plugin.json '.mcpServers == "./.mcp.json"' \
  "Codex plugin must declare mcpServers path './.mcp.json'"

# --- Cursor manifest: name, skills, and MCP ---

assert_jq .cursor-plugin/plugin.json '.name == "orq"' \
  "Cursor plugin name must be 'orq'"
assert_jq .cursor-plugin/plugin.json '.skills == "./skills/"' \
  "Cursor plugin must declare skills path './skills/'"
assert_jq .cursor-plugin/plugin.json '.mcpServers == "./.mcp.json"' \
  "Cursor plugin must declare mcpServers path './.mcp.json'"

# --- Claude manifest: name, skills, and MCP ---

assert_jq .claude-plugin/plugin.json '.name == "orq"' \
  "Claude plugin name must be 'orq'"
assert_jq .claude-plugin/plugin.json '.skills == "./skills/"' \
  "Claude plugin must declare skills path './skills/'"
assert_jq .claude-plugin/plugin.json '.mcpServers == "./.mcp.json"' \
  "Claude plugin must declare mcpServers path './.mcp.json'"

# --- MCP config: expected server ---

assert_jq .mcp.json '.mcpServers["orq-workspace"].type == "http"' \
  "MCP server type must be 'http'"
assert_jq .mcp.json '.mcpServers["orq-workspace"].url == "https://my.orq.ai/v2/mcp"' \
  "MCP server URL must be 'https://my.orq.ai/v2/mcp'"
assert_jq .mcp.json '.mcpServers["orq-workspace"].headers.Authorization == "Bearer ${ORQ_API_KEY}"' \
  "MCP authorization header must use ORQ_API_KEY placeholder"

# --- Marketplace entry ---

assert_jq .agents/plugins/marketplace.json '.name == "orq-marketplace"' \
  "Marketplace name must be 'orq-marketplace'"
assert_jq .agents/plugins/marketplace.json \
  'any(.plugins[]; .name == "orq" and .source.source == "local" and .source.path == "./" and .policy.installation == "INSTALLED_BY_DEFAULT" and .policy.authentication == "ON_INSTALL" and .category == "Productivity")' \
  "Marketplace must contain orq plugin with correct source, policy, and category"

# --- Symlink integrity ---

assert_symlink "mcp.json" ".mcp.json"
assert_symlink ".claude-plugin/.mcp.json" "../.mcp.json"
assert_symlink ".claude-plugin/skills" "../skills"

# Spec §4.1 containment and the one-plugin-root rule live in validate-skills.mjs
# (sections 6-7): resolving a symlink correctly needs realpath semantics.

# --- Claude Cowork Desktop: symlinks resolve to real targets ---

assert_path -f ".claude-plugin/.mcp.json" \
  ".claude-plugin/.mcp.json must resolve to a readable file"
assert_path -d ".claude-plugin/skills" \
  ".claude-plugin/skills must resolve to a readable directory"
assert_path -f ".claude-plugin/skills/orq-build-agent/SKILL.md" \
  ".claude-plugin/skills/orq-build-agent/SKILL.md must exist (verifies symlink resolves)"

echo "Plugin manifest validation passed."
