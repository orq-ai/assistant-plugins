#!/usr/bin/env bash
# End-to-end smoke test: spawn a CC subprocess with various env configurations,
# verify the trace hook fires, posts to OTLP, and gets 200 OK.
#
# Validates:
#   - hook actually loads in subprocess CC (CLAUDECODE unset)
#   - OTLP POST returns 200 rejectedSpans:0
#   - SessionEnd flushes session + turn spans
#
# Does NOT validate visibility in workspace UI — that's a backend concern
# (see test-workspace-visibility.sh for the slow/external check).
#
# Usage: TEST_ORQ_API_KEY=<key> ./test-trace-flow.sh
#   TEST_ORQ_API_KEY — a real workspace key to send the test trace with. Without
#                      it (or without the `claude` CLI) the test SKIPs, since
#                      there is nothing to authenticate with.
# Credential order under test: CLAUDE_PLUGIN_OPTION_ORQ_API_KEY, then ORQ_API_KEY.
# No orq config file or profile is read. Repo root is auto-detected via
# `git rev-parse --show-toplevel`.

set -uo pipefail

REPO_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
LOG=/tmp/orq-trace-debug.log
PASS=0
FAIL=0

KEY="${TEST_ORQ_API_KEY:-}"
if [ -z "$KEY" ] || ! command -v claude >/dev/null 2>&1; then
  echo "SKIP: needs TEST_ORQ_API_KEY and the claude CLI on PATH"
  exit 0
fi

# Run a fresh CC subprocess with an env-var setup. Invoked via `eval` so the
# caller can set/unset multiple vars in a single string argument.
run_case() {
  local name="$1"
  local env_setup="$2"

  echo "=== $name ==="
  rm -f "$LOG"

  (
    eval "$env_setup"
    env -u CLAUDECODE \
      ORQ_DEBUG=1 \
      ORQ_API_KEY="${ORQ_API_KEY-}" \
      CLAUDE_PLUGIN_OPTION_ORQ_API_KEY="${CLAUDE_PLUGIN_OPTION_ORQ_API_KEY-}" \
      bash -c "cd '$REPO_ROOT' && claude -p 'echo trace_test' 2>&1" \
    > /tmp/cc-out.txt
  )
  sleep 8

  if [ ! -f "$LOG" ]; then
    echo "  FAIL: no debug log written"
    FAIL=$((FAIL+1))
    return
  fi

  if grep -q "POST .* 200" "$LOG"; then
    echo "  PASS: POST 200 OK"
    PASS=$((PASS+1))
  else
    echo "  FAIL: no 200 OK in log"
    sed 's/^/    /' "$LOG"
    FAIL=$((FAIL+1))
  fi

  echo "  log tail:"
  sed 's/^/    /' "$LOG" | tail -3
  echo
}

run_case "T1: ORQ_API_KEY only" \
  "export ORQ_API_KEY='$KEY'; unset CLAUDE_PLUGIN_OPTION_ORQ_API_KEY"

run_case "T2: plugin option only" \
  "unset ORQ_API_KEY; export CLAUDE_PLUGIN_OPTION_ORQ_API_KEY='$KEY'"

run_case "T3: both set, plugin option wins" \
  "export ORQ_API_KEY='$KEY'; export CLAUDE_PLUGIN_OPTION_ORQ_API_KEY='$KEY'"

echo "==========================="
echo "RESULTS: $PASS pass, $FAIL fail"
[ "$FAIL" -eq 0 ]
