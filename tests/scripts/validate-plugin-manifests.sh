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
  if ! jq -e . "$file" >/dev/null 2>&1; then
    echo "FAIL: Invalid JSON in $file"
    jq . "$file" 2>&1 | head -5
    exit 1
  fi
done

# --- Root manifest: Agent Plugins 1.0.0 (spec §5) ---

assert_jq plugin.json '."$schema" == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"' \
  "Root plugin.json must declare the canonical 1.0.0 \$schema"
assert_jq plugin.json '.name == "orq"' \
  "Root plugin name must be 'orq'"
# The 1.0.0 manifest schema is closed (additionalProperties: false); the
# legacy 'skills'/'mcpServers' fields must not leak into the portable manifest.
assert_jq plugin.json 'has("skills") | not' \
  "Root plugin.json must not declare 'skills' (fixed location skills/ per spec §7.1)"
assert_jq plugin.json 'has("mcpServers") | not' \
  "Root plugin.json must not declare 'mcpServers' (fixed location mcp.json per spec §7.2)"

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
  'any(.plugins[]; .name == "orq" and .source.source == "local" and .source.path == "." and .policy.installation == "INSTALLED_BY_DEFAULT" and .policy.authentication == "ON_INSTALL" and .category == "Productivity")' \
  "Marketplace must contain orq plugin with correct source, policy, and category"

# --- Symlink integrity ---

assert_symlink "mcp.json" ".mcp.json"
assert_symlink ".claude-plugin/.mcp.json" "../.mcp.json"
assert_symlink ".claude-plugin/skills" "../skills"

# --- Path containment (spec §4.1): every tracked symlink resolves inside the repo root ---

while IFS= read -r link; do
  target="$(readlink "$link")"
  if ! resolved_dir="$(cd "$(dirname "$link")/$(dirname "$target")" 2>/dev/null && pwd)"; then
    echo "FAIL: symlink $link -> $target does not resolve"
    exit 1
  fi
  case "$resolved_dir/$(basename "$target")" in
    "$ROOT_DIR"/*) ;;
    *)
      echo "FAIL: symlink $link resolves outside the plugin root: $resolved_dir/$(basename "$target")"
      exit 1
      ;;
  esac
done < <(git ls-files -s | awk -F'\t' '$1 ~ /^120000 / {print $2}')

# --- Claude Cowork Desktop: symlinks resolve to real targets ---

assert_path -f ".claude-plugin/.mcp.json" \
  ".claude-plugin/.mcp.json must resolve to a readable file"
assert_path -d ".claude-plugin/skills" \
  ".claude-plugin/skills must resolve to a readable directory"
assert_path -f ".claude-plugin/skills/orq-build-agent/SKILL.md" \
  ".claude-plugin/skills/orq-build-agent/SKILL.md must exist (verifies symlink resolves)"

echo "Plugin manifest validation passed."
