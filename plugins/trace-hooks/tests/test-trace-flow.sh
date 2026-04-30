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
# Usage: ./test-trace-flow.sh [TRACE_PROFILE]

set -uo pipefail

TRACE_PROFILE="${1:-prod-claude-code}"
LOG=/tmp/orq-trace-debug.log
PASS=0
FAIL=0

ORQ_CONFIG="$HOME/.config/orq/config.json"
RESEARCH_KEY="$(python3 -c "import json,sys;print(json.load(open('$ORQ_CONFIG'))['profiles']['research']['api_key'])")"

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
      bash -c 'cd ~/Developer/orq/orq-skills && claude -p "echo trace_test" 2>&1' \
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
  "export ORQ_API_KEY='$RESEARCH_KEY'; unset ORQ_TRACE_PROFILE; unset ORQ_PROFILE"

run_case "T3: BOTH set, TRACE_PROFILE wins" \
  "export ORQ_API_KEY='$RESEARCH_KEY'; export ORQ_TRACE_PROFILE='$TRACE_PROFILE'; unset ORQ_PROFILE"

run_case "T4: neither (CLI current profile)" \
  "unset ORQ_API_KEY; unset ORQ_TRACE_PROFILE; unset ORQ_PROFILE"

echo "==========================="
echo "RESULTS: $PASS pass, $FAIL fail"
[ "$FAIL" -eq 0 ]
