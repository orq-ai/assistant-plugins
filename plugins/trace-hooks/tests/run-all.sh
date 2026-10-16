#!/usr/bin/env bash
# Run all trace-hooks tests in order. Stops on first failure.
#
# Usage: ./run-all.sh   (TEST_ORQ_API_KEY=<key> enables the subprocess trace-flow test)

set -uo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_ROOT="$(cd "$DIR/.." && pwd)"

EXIT=0

echo "########## 1/9 manifest schema ##########"
# The manifest is inline in the orq-trace entry of the marketplace (strict: false); a nested
# plugin.json would make Claude Desktop skip the ./-sourced plugins at the repo root.
node -e "
const m = JSON.parse(require('fs').readFileSync('$PLUGIN_ROOT/../../.claude-plugin/marketplace.json','utf8'));
const e = m.plugins.find(p => p.name === 'orq-trace');
if (!e || e.strict !== false || !e.hooks || !e.version) process.exit(1);
if (require('fs').existsSync('$PLUGIN_ROOT/.claude-plugin/plugin.json')) process.exit(1);
" && echo "PASS: orq-trace marketplace entry is a complete inline manifest" || EXIT=1

echo
echo "########## 2/9 runtime files ##########"
node "$PLUGIN_ROOT/src/validate-runtime.js" || EXIT=1

echo
echo "########## 3/9 value redaction ##########"
node "$DIR/test-redact.mjs" || EXIT=1

echo
echo "########## 4/9 queue destination ##########"
node "$DIR/test-queue-destination.mjs" || EXIT=1

echo
echo "########## 5/9 trace switch ##########"
node "$DIR/test-trace-switch.mjs" || EXIT=1

echo
echo "########## 6/9 credential resolution ##########"
node "$DIR/test-resolution.mjs" || EXIT=1

echo
echo "########## 7/9 post-tool-use hook ##########"
node "$DIR/test-post-tool-use.mjs" || EXIT=1

echo
echo "########## 8/9 transcript spans ##########"
node "$DIR/test-transcript-spans.mjs" || EXIT=1

echo
echo "########## 9/9 trace flow (subprocess CC) ##########"
bash "$DIR/test-trace-flow.sh" || EXIT=1

# test-workspace-visibility.sh deliberately not in run-all: known-failing
# because of a backend ingestion bug (OTLP returns 200 but list/get cannot
# find the trace). Run manually after the backend fix.

echo
echo "########## DONE ##########"
[ "$EXIT" -eq 0 ] && echo "ALL TESTS PASS" || echo "SOME TESTS FAILED"
exit "$EXIT"
