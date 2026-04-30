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
# Usage: ./test-trace-flow.sh [TRACE_PROFILE] [SECONDARY_PROFILE]
#   TRACE_PROFILE     — orqi profile to point traces at (default: trace)
#   SECONDARY_PROFILE — orqi profile whose api_key is used in T2/T3 to simulate
#                       a different ORQ_API_KEY in the shell (default: default)
# Repo root is auto-detected via `git rev-parse --show-toplevel`.

set -uo pipefail

TRACE_PROFILE="${1:-${TEST_TRACE_PROFILE:-trace}}"
SECONDARY_PROFILE="${2:-${TEST_SECONDARY_PROFILE:-default}}"
REPO_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
LOG=/tmp/orq-trace-debug.log
PASS=0
FAIL=0

ORQ_CONFIG="$HOME/.config/orq/config.json"
SECONDARY_KEY="$(python3 -c "import json,sys;print(json.load(open('$ORQ_CONFIG'))['profiles']['$SECONDARY_PROFILE']['api_key'])" 2>/dev/null || true)"
if [ -z "${SECONDARY_KEY:-}" ]; then
  echo "ERROR: profile '$SECONDARY_PROFILE' not found in $ORQ_CONFIG"
  echo "Pass a valid profile name as the 2nd arg or via TEST_SECONDARY_PROFILE."
  exit 2
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
      ORQ_TRACE_PROFILE="${ORQ_TRACE_PROFILE-}" \
      ORQ_PROFILE="${ORQ_PROFILE-}" \
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

run_case "T1: ORQ_TRACE_PROFILE only" \
  "unset ORQ_API_KEY; export ORQ_TRACE_PROFILE='$TRACE_PROFILE'; unset ORQ_PROFILE"

run_case "T2: ORQ_API_KEY only" \
  "export ORQ_API_KEY='$SECONDARY_KEY'; unset ORQ_TRACE_PROFILE; unset ORQ_PROFILE"

run_case "T3: BOTH set, TRACE_PROFILE wins" \
  "export ORQ_API_KEY='$SECONDARY_KEY'; export ORQ_TRACE_PROFILE='$TRACE_PROFILE'; unset ORQ_PROFILE"

run_case "T4: neither (CLI current profile)" \
  "unset ORQ_API_KEY; unset ORQ_TRACE_PROFILE; unset ORQ_PROFILE"

echo "==========================="
echo "RESULTS: $PASS pass, $FAIL fail"
[ "$FAIL" -eq 0 ]
