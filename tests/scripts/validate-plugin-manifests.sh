#!/usr/bin/env bash
set -euo pipefail

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
  if ! test "$flag" "$path"; then
    echo "FAIL: $msg"
    echo "  path: $path"
    exit 1
  fi
}

assert_symlink() {
  local link="$1" expected_target="$2"
  local actual_target
  if [[ ! -L "$link" ]]; then
    echo "FAIL: $link should be a symlink but is not"
    exit 1
  fi
  actual_target="$(readlink "$link")"
  if [[ "$actual_target" != "$expected_target" ]]; then
    echo "FAIL: $link points to '$actual_target', expected '$expected_target'"
    exit 1
  fi
}

# --- File existence and JSON validity ---

json_files=(
  ".codex-plugin/plugin.json"
  ".cursor-plugin/plugin.json"
  ".claude-plugin/plugin.json"
  ".mcp.json"
  "mcp.json"
  ".agents/plugins/marketplace.json"
  "plugins/orq/.codex-plugin/plugin.json"
  "plugins/orq/.mcp.json"
  "plugins/orq/mcp.json"
)

for file in "${json_files[@]}"; do
  if [[ ! -f "$file" ]]; then
    echo "FAIL: Missing required file: $file"
    exit 1
  fi
  if ! jq -e . "$file" >/dev/null 2>&1; then
    echo "FAIL: Invalid JSON in $file"
    jq . "$file" 2>&1 | head -5
    exit 1
  fi
done

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
  'any(.plugins[]; .name == "orq" and .source.source == "local" and .source.path == "./plugins/orq" and .policy.installation == "INSTALLED_BY_DEFAULT" and .policy.authentication == "ON_INSTALL" and .category == "Productivity")' \
  "Marketplace must contain orq plugin with correct source, policy, and category"

# --- Codex marketplace plugin folder (wired via symlinks) ---

assert_jq plugins/orq/.codex-plugin/plugin.json '.name == "orq"' \
  "Nested Codex plugin name must be 'orq'"
assert_jq plugins/orq/.codex-plugin/plugin.json '.skills == "./skills/"' \
  "Nested Codex plugin must declare skills path './skills/'"
assert_jq plugins/orq/.codex-plugin/plugin.json '.mcpServers == "./.mcp.json"' \
  "Nested Codex plugin must declare mcpServers path './.mcp.json'"
assert_jq plugins/orq/.mcp.json '.mcpServers["orq-workspace"].url == "https://my.orq.ai/v2/mcp"' \
  "Nested MCP server URL must be 'https://my.orq.ai/v2/mcp'"

assert_path -d "plugins/orq/skills" \
  "plugins/orq/skills directory must exist (symlink to ../../skills)"
assert_path -f "plugins/orq/skills/build-agent/SKILL.md" \
  "plugins/orq/skills/build-agent/SKILL.md must exist (verifies symlink resolves)"
assert_path -f "plugins/orq/.mcp.json" \
  "plugins/orq/.mcp.json must resolve to a readable file"
assert_path -f "plugins/orq/mcp.json" \
  "plugins/orq/mcp.json must resolve to a readable file"

# --- Symlink integrity ---

assert_symlink "mcp.json" ".mcp.json"
assert_symlink "plugins/orq/.mcp.json" "../../.mcp.json"
assert_symlink "plugins/orq/mcp.json" "../../.mcp.json"
assert_symlink "plugins/orq/skills" "../../skills"

echo "Plugin manifest validation passed."
