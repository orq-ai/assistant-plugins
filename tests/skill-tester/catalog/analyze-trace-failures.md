# Catalog: analyze-trace-failures

Tests for [`skills/analyze-trace-failures/SKILL.md`](../../../skills/analyze-trace-failures/SKILL.md).
Source scenarios: [`../../skills.md`](../../skills.md) (analyze-trace-failures) and
[`../../mcp-tools.md`](../../mcp-tools.md) (trace tools).

## Functional cases

### F1. Trace listing works
- **Operation:** `list_traces`.
- **Verify:** returns an array (possibly empty). Confirms the primary data-collection tool the skill
  depends on is reachable.
- **Cleanup:** none (read-only).

### F2. Spans + span detail (if traces exist)
- **Operation:** `list_spans` for a returned trace; `get_span` for a returned span.
- **Verify:** span list and span detail are returned. **SKIP** with a note if the workspace has no
  traces.
- **Cleanup:** none (read-only).

## Behavioural scenarios

### B1. Analyze recent failures
- **Type:** explicit
- **Trigger:** "Analyze recent trace failures"
- **Expected routing:** analyze-trace-failures
- **PASS:** calls `list_traces` and attempts to read spans (`list_spans` / `get_span`); describes a
  **mixed sampling strategy** (random / failure-driven / outlier); uses open coding → axial coding to
  build a taxonomy; targets 4-8 non-overlapping failure modes; finds the FIRST upstream failure.
- **Anti-patterns (FAIL):** jumps to building evaluators or changing prompts before reading traces;
  starts from a predetermined taxonomy; uses Likert (1-5) annotation instead of binary; labels
  downstream cascading failures.

### B2. Implicit — a need, not the artifact
- **Type:** implicit
- **Trigger:** "I don't understand why my agent keeps giving bad answers in production — where do I even start?"
- **Expected routing:** analyze-trace-failures
- **PASS:** routes here on the description; proposes reading traces first (open→axial coding) before
  any fix; describes sampling.
- **Anti-patterns (FAIL):** misroutes to optimize-prompt or build-evaluator before any trace reading.

## Negative controls (must NOT fire analyze-trace-failures)

### N1. No traces yet → set up tracing first
- **Type:** negative
- **Trigger:** "I'm building a brand-new app and want to start capturing traces in orq.ai."
- **Expected routing:** setup-observability — analyze-trace-failures must not fire (nothing to analyze
  yet; instrumentation comes first).
- **PASS:** routes to setup-observability.
- **Fired = FAIL:** trying to analyze failures when no traces exist yet.
