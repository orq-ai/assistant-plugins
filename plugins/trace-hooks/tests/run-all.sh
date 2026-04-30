#!/usr/bin/env bash
# Run all trace-hooks tests in order. Stops on first failure.
#
# Usage: ./run-all.sh [TRACE_PROFILE]

set -uo pipefail

TRACE_PROFILE="${1:-${TEST_TRACE_PROFILE:-trace}}"
DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_ROOT="$(cd "$DIR/.." && pwd)"

EXIT=0

echo "########## 1/4 manifest schema ##########"
node -e "JSON.parse(require('fs').readFileSync('$PLUGIN_ROOT/.claude-plugin/plugin.json','utf8'))" \
  && echo "PASS: plugin.json parses" || EXIT=1

echo
echo "########## 2/4 runtime files ##########"
node "$PLUGIN_ROOT/src/validate-runtime.js" || EXIT=1

echo
echo "########## 3/4 credential resolution ##########"
node "$DIR/test-resolution.mjs" || EXIT=1

echo
echo "########## 4/4 trace flow (subprocess CC) ##########"
bash "$DIR/test-trace-flow.sh" "$TRACE_PROFILE" || EXIT=1

# test-workspace-visibility.sh deliberately not in run-all: known-failing
# because of a backend ingestion bug (OTLP returns 200 but list/get cannot
# find the trace). Run manually after the backend fix.

echo
echo "########## DONE ##########"
[ "$EXIT" -eq 0 ] && echo "ALL TESTS PASS" || echo "SOME TESTS FAILED"
exit "$EXIT"
