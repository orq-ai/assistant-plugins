#!/usr/bin/env bash
# Verify a freshly emitted trace appears in the orq workspace via orqi CLI.
#
# KNOWN-FAILING as of 2026-04-30 in prod-claude-code workspace: OTLP endpoint
# returns 200 with rejectedSpans:0, but `orqi trace list` / `orqi trace get`
# do not surface the trace. Reproduced with both the trace hook and a
# minimal synthetic OTLP POST. Backend / ingestion-pipeline issue, not a
# hook bug. Excluded from run-all.sh until backend is fixed.
#
# Depends on:
#   - orqi CLI installed
#   - orqi profile <TRACE_PROFILE> configured with valid api_key
#   - backend ingestion lag tolerable within INGEST_WAIT_SEC
#
# Usage: ./test-workspace-visibility.sh [TRACE_PROFILE]

set -uo pipefail

TRACE_PROFILE="${1:-prod-claude-code}"
INGEST_WAIT_SEC="${INGEST_WAIT_SEC:-30}"
LOG=/tmp/orq-trace-debug.log

if ! command -v orqi >/dev/null 2>&1; then
  echo "FAIL: orqi CLI not found"
  exit 1
fi

echo "Switching orqi profile to $TRACE_PROFILE..."
orqi profile set "$TRACE_PROFILE" >/dev/null

echo "Capturing baseline trace_id list..."
BEFORE=$(orqi trace list --v3 --limit 20 --fields trace_id 2>/dev/null \
  | awk 'NR>2 {print $NF}' | grep -E '^[a-f0-9]{16,}')

echo "Emitting fresh trace via subprocess CC..."
rm -f "$LOG"
( unset ORQ_API_KEY; env -u CLAUDECODE ORQ_DEBUG=1 ORQ_TRACE_PROFILE="$TRACE_PROFILE" \
  bash -c 'cd ~/Developer/orq/orq-skills && claude -p "echo workspace_visibility_test" 2>&1' ) >/dev/null
sleep 5

if ! grep -q "POST .* 200" "$LOG"; then
  echo "FAIL: hook did not post 200"
  tail -20 "$LOG" 2>/dev/null
  exit 1
fi
echo "Hook posted OK. Waiting ${INGEST_WAIT_SEC}s for ingestion..."
sleep "$INGEST_WAIT_SEC"

AFTER=$(orqi trace list --v3 --limit 20 --fields trace_id 2>/dev/null \
  | awk 'NR>2 {print $NF}' | grep -E '^[a-f0-9]{16,}')

NEW=$(comm -13 <(echo "$BEFORE" | sort) <(echo "$AFTER" | sort))

if [ -z "$NEW" ]; then
  echo "FAIL: no new trace_ids visible after ${INGEST_WAIT_SEC}s"
  echo "  Possible causes: ingestion lag > ${INGEST_WAIT_SEC}s, key/workspace mismatch, list pagination"
  exit 1
fi

echo "PASS: new trace(s) visible:"
echo "$NEW" | sed 's/^/  /'
