#!/usr/bin/env bash
# Quick trace-hooks test runner. Runs a CC session with --plugin-dir,
# waits for the trace to land, then prints span list + validates.
#
# Usage:
#   ./test-trace.sh                          # default: tool call test
#   ./test-trace.sh "say hello"              # custom prompt (no tools)
#   ./test-trace.sh "use Agent to find py"   # subagent test

set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "$0")" && pwd)"
PROMPT="${1:-list files with ls then count them in one sentence}"
TEST_DIR="${TEST_CWD:-$HOME/Developer/orq/orq-python}"

echo "==> Running CC with plugin-dir: $PLUGIN_DIR"
echo "==> Prompt: $PROMPT"
echo "==> CWD: $TEST_DIR"
echo

cd "$TEST_DIR"
env -u CLAUDECODE -u ORQ_API_KEY -u ORQ_OTEL_API_KEY \
  bash -c "claude --plugin-dir '$PLUGIN_DIR' -p '$PROMPT' 2>&1"

echo
echo "==> Waiting for trace to land..."
sleep 4

TRACE_ID=$(orq trace list --limit 1 --json 2>/dev/null \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['data'][0]['trace_id'])")

echo "==> Trace: $TRACE_ID"
echo
orq trace span list "$TRACE_ID"

echo
echo "==> Validating spans..."
SPAN_LIST=$(orq trace span list "$TRACE_ID" --json 2>/dev/null)

HAS_SESSION=$(echo "$SPAN_LIST" | python3 -c "import sys,json; d=json.load(sys.stdin); print(any(s['name']=='orq.claude_code.session' for s in d['data']))")
HAS_TURN=$(echo "$SPAN_LIST" | python3 -c "import sys,json; d=json.load(sys.stdin); print(any('turn' in s['name'] for s in d['data']))")
HAS_LLM=$(echo "$SPAN_LIST" | python3 -c "import sys,json; d=json.load(sys.stdin); print(any(s['type']=='span.chat_completion' for s in d['data']))")

echo "  Session span: $HAS_SESSION"
echo "  Turn span:    $HAS_TURN"
echo "  LLM span:     $HAS_LLM"

FAILURES=0
[[ "$HAS_SESSION" == "True" ]] || { echo "  FAIL: missing session span"; FAILURES=$((FAILURES+1)); }
[[ "$HAS_TURN" == "True" ]]    || { echo "  FAIL: missing turn span"; FAILURES=$((FAILURES+1)); }
[[ "$HAS_LLM" == "True" ]]     || { echo "  FAIL: missing LLM span"; FAILURES=$((FAILURES+1)); }

if [[ $FAILURES -eq 0 ]]; then
  echo
  echo "==> All checks passed"
else
  echo
  echo "==> $FAILURES check(s) failed"
  exit 1
fi
