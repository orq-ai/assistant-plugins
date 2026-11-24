---
name: orq-instrumentation-health
description: >
  Audit how well the traces already in an orq.ai workspace are instrumented,
  without changing anything: model and token counts, unpriced models, flat or
  orphaned span trees, missing span types, session and user id coverage and
  generic trace names, plus an opt-in pass over inputs, outputs and sensitive
  data, scored per project and per agent with example trace links. Use when
  someone asks "are my traces instrumented properly", "why are token counts
  missing from my traces", "audit the instrumentation of my existing traces"
  or "check trace quality across my agents". Do NOT use to add tracing to an
  app (use orq-setup-observability), to find out why conversations fail (use
  orq-analyze-traces), or to health-check one agent's config (use
  orq-review-agent).
allowed-tools: Read, Write, AskUserQuestion, Skill, Bash(orq traces list-fields:*), Bash(orq traces aggregate:*), Bash(orq traces search:*), Bash(orq traces list-spans:*), Bash(orq traces list-facet-values:*), Bash(orq traces conversation:*), Bash(orq pii detect:*), Bash(uv run --no-project */scripts/health_sweep.py *)
metadata:
  verified: "2026-11-24"
  surface: cli
---

# Instrumentation Health

> **This skill never writes to the platform.** The shell grant is the read verbs of `orq traces`, `orq pii detect` (a read that may be billed, see the content pass) and the bundled script, which only writes request bodies and reads the saved JSON back.

You audit the traces a workspace already has and say, per project and per agent, which instrumentation gaps they show and how many traces each one touches. Every gap names its fix in [`baseline-checklist.md`](../orq-setup-observability/resources/baseline-checklist.md) and hands off to `orq-setup-observability`.

**Before any `orq traces` call, read [`trace-queries.md`](../orq-shared/resources/trace-queries.md) §0, §1 and §6; resolve which `ORQ_API_KEY` the CLI will use with the "Resolve the run key" section of [`run-key-preflight.md`](../orq-shared/resources/run-key-preflight.md).** Skip its agent existence check: this skill does not invoke anything. Trace links follow [`orq-trace-format`](../orq-trace-format/SKILL.md).

## Gotchas (read first)

Probed 2026-11-24 with `orq` CLI 12.13.0 on one project of the research workspace (RES-1809).

1. **`list-facet-values` needs a window.** Without `--from` and `--to` it returns HTTP 400. Pass the window from `window.json`.
2. **A filter takes `values`, a list.** `{"field":"product","op":"eq","value":"router"}` is rejected; write `"values":["router"]`.
3. **`tokens.total` is a per-trace rollup and rejects `not_exists`.** "Model but no token count" is computed as traces with a model minus traces with a model and `tokens.total exists`. The script does this.
4. **A failed call carries a model and 0 tokens.** 60 of 60 zero-token traces in the probe window had `status: error`. The token checks leave errored traces out.
5. **`search` returns one row per root span, not per trace.** One agent trace id came back on dozens of rows. The script dedupes and asks for 200 rows to find 20 traces.
6. **`aggregate` drops the `agent_name` key for traffic with no agent**, and leaves out groups with a count of 0. A missing row is 0; the "no agent" group is the row with only `project_id`.
7. **Agent wrapper spans look unpriced.** The `responses agent/<key>` span has `provider: agent`, the agent key as its model, rolled-up tokens and cost 0. The script skips those; only real model calls count as unpriced.
8. **No session id reads as `session_id == trace_id`.** The search row fills it with the trace id when the app never set one.
9. **Router traces are flat by design.** Rows with `product: router` are exempt from the flat check. No Claude Code session traces existed in the probe workspace, so their marker is unknown: report a flat trace whose name or `product` points to Claude Code as information, not as a gap.
10. **`pii detect` returns counts only**, for example `{"entities":{"EMAIL_ADDRESS":1,"PERSON":1},"has_pii":true}`; no detected values come back. It rejects empty text with HTTP 400, and its response shows nothing about billing.
11. **Ordinary text trips low-risk entity types.** `DATE_TIME`, `JOB_TITLE`, `URL`, `ORGANIZATION` and `PERSON` showed up in test chats. Lead with high-risk types (`IBAN_CODE`, `CREDIT_CARD`, `EMAIL_ADDRESS`, `PHONE_NUMBER`, national ids, keys) and give the rest as counts.
12. **`aggregate` and `search` have no `--all-accessible-projects`.** With a project key the audit covers that one project; say which project it covered. `list-fields`, `list-spans` and `list-facet-values` do take the flag.
13. **A body read with `--from-file` is sent as given**, so it must carry `from` and `to`; `init` writes them into every body.
14. **On HTTP 429**, wait a few seconds and rerun that one command. Never rerun the whole sweep.

## When to use

- "Are my traces instrumented properly?" / "why are token counts missing?" / "audit the instrumentation of my existing traces"
- After instrumenting several services or agents, to see which ones still have gaps
- Before trusting cost, session or user analytics built on the traces

## When NOT to use

- Add or fix tracing in the application code → `orq-setup-observability`
- Find out why conversations fail, or build a failure taxonomy → `orq-analyze-traces`
- Health-check one agent's instructions, tools, evaluators and model → `orq-review-agent`
- Render or link one trace → `orq-trace-format`

## Constraints

- **NEVER** call a create, update, delete or invoke verb. Fixes happen in the app's code, through `orq-setup-observability`.
- **NEVER** start the content pass without asking. It reads trace inputs and outputs, and `pii detect` may be billed per call: say how many calls it will make and ask a second time before sending them.
- **NEVER** paste trace content or detected values into the chat. Report entity counts by type and the trace link.
- **ALWAYS** label count checks "traces with at least one such span" over the full window, and sample checks "in N sampled traces".
- **ALWAYS** report per project, then per agent, with the "no agent" bucket named as such. Groups can overlap; never sum them.
- **ALWAYS** report what could not be checked in `unobservable` with its reason. A zero only means healthy when the group's total is above zero.
- **ALWAYS** show a trace as a Studio link, never a bare id ([`orq-trace-format`](../orq-trace-format/SKILL.md)).

## Workflow Checklist

```
Instrumentation Health Progress:
- [ ] Phase 1: Window, run directory, field check
- [ ] Phase 2: Count checks
- [ ] Phase 3: Sample checks
- [ ] Phase 4: Content pass (opt-in)
- [ ] Phase 5: Scorecard and hand-off
```

## Steps

All files go in one run directory in the working directory, `instrumentation-health-<YYYYMMDD-HHMMSS>/`, written `<run>` below. Run the script from the working directory as `uv run --no-project <this skill's directory>/scripts/health_sweep.py`, written `health_sweep.py` below. Every `orq` command adds `-o json` and redirects into `<run>`.

### Phase 1: Window, run directory, field check

1. Ask nothing about the window unless the user named one: the default is 7 days, `--days N` overrides. Run `health_sweep.py init <run> --days 7`. It writes `window.json` and the count bodies under `<run>/bodies/`.
2. `orq traces list-fields -o json > <run>/fields.json`. The script marks a check `unobservable` when its field or operator is missing there.

**Done when** `window.json`, `fields.json` and five `bodies/count-*.json` exist.

### Phase 2: Count checks

3. For each of `total`, `model`, `model-tokens`, `model-tokens-zero`, `user-id`: `orq traces aggregate --from-file <run>/bodies/count-<check>.json -o json > <run>/count-<check>.json`.
4. `orq traces list-facet-values name --from <from> --to <to> -o json > <run>/names.json`, with `from` and `to` from `window.json`, so the names cover the same project as the counts.
5. If `count-total.json` has no rows, rerun step 3 for `total` once to rule out a transient empty result ([`trace-queries.md`](../orq-shared/resources/trace-queries.md) §0). Still empty: tell the user there are no traces in the window and stop.

**Done when** the five count files and `names.json` are saved, or the user was told the window is empty.

### Phase 3: Sample checks

6. `health_sweep.py plan <run>` writes one search body per group and prints the groups with their trace counts.
7. For each group `n`: `orq traces search --from-file <run>/bodies/search-<n>.json -o json > <run>/search-<n>.json`.
8. `health_sweep.py sample <run>` picks up to 20 traces per group and prints their ids in `list_spans`, and the up to 5 per group for Phase 4 in `content_pass`.
9. For each id: `orq traces list-spans <trace_id> --limit 100 -o json > <run>/spans/<trace_id>.1.json`. While the saved page has `has_more: true`, fetch the next with `--page-token <next_page_token>` into `<trace_id>.2.json`, `.3.json` and so on. A trace left unpaged is reported `unobservable`, never healthy.

**Done when** every sampled trace has its pages saved up to `has_more: false`.

### Phase 4: Content pass (opt-in)

10. Ask with `AskUserQuestion`: **run the content pass** or **skip it**. Say that it reads the inputs and outputs of up to 5 sampled traces per group, sends that text to `orq pii detect`, and that those calls may be billed. Also say that the text is saved under `<run>` in the working directory, so `<run>` must not be committed.
11. On yes: for each id in `content_pass` from step 8, `orq traces conversation <trace_id> -o json --tool-max-chars 500 > <run>/conversation/<trace_id>.json` (the default `--max-chars 4000` stays).
12. `health_sweep.py pii <run>` writes one `pii/<trace_id>.body.json` per non-empty conversation and prints `pii_calls`. Ask a second time, with that number: **send N `pii detect` calls** or **skip sensitive data**.
13. On yes: for each body, `orq pii detect --from-file <run>/pii/<trace_id>.body.json -o json > <run>/pii/<trace_id>.json`.

**Done when** the user skipped the pass, or every conversation and approved `pii detect` response is saved.

### Phase 5: Scorecard and hand-off

14. `health_sweep.py score <run> > <run>/scorecard.json`.
15. Answer in chat, per project, then per agent:

    | Check | Read from | Gap means | Fix (baseline checklist) |
    |---|---|---|---|
    | Model, no token count | `counts.model_no_tokens` | the provider or framework does not report usage | #2 token usage |
    | Model, 0 tokens | `counts.model_tokens_zero` | usage recorded as zero on successful calls | #2 token usage |
    | Provider, no model | `sample.provider_no_model` | the LLM span lacks `gen_ai.request.model` | #1 model name |
    | Span type missing | `sample.type_missing` | spans were not typed by the instrumentor | #5 span types |
    | Tokens, no cost | `sample.tokens_no_cost` | the model is not priced in orq, so cost reads 0 | model pricing, AI Router |
    | Orphan parent | `sample.orphan_parent` | a span points at a parent that never arrived (lost export or context) | #4 hierarchy |
    | Flat tree | `sample.flat` | three or more spans, all children of the root | #4 hierarchy |
    | No session id | `sample.no_session_id` | suggestion only; expected for single-turn apps | Phase 5 enrichment |
    | User id set | `counts.user_id_set` of `traces` | suggestion only | Phase 5 enrichment |
    | Generic names | `generic_names` | per project, not per group; router names such as `responses.anthropic` are normal | #3 trace names |
    | Empty input or output | `content.no_input`, `content.no_output` | capture is off or the wrong span is captured | #7 input and output |
    | Sensitive data | `content.pii` | entity counts by type (gotcha 11) | #6 sensitive data |

    Give each gap its count and denominator, up to 3 example traces as links (`examples` in the scorecard; for `model_tokens_zero`, run that count body's filters through `orq traces search` with `"limit": 3`; `model_no_tokens` is a subtraction and has no example links), and the fix. Then the `unobservable` list, and `content_pass` (`skipped`, `no pii` or `done`). State the window, the project covered, and that a longer window may run past retention.
16. Offer `orq-setup-observability` for the fixes, naming the gaps it would address. Tell the user `<run>` holds trace content when the content pass ran, and that they can delete it.

**Done when** the scorecard was given in chat with counts, denominators, example links and `unobservable`, and nothing was written to the platform.

## Done When

- `<run>/scorecard.json` exists and every group in it was reported
- Every gap has a count, a denominator, a fix and example links; suggestions are marked as suggestions
- The content pass ran only after two yes answers, and no trace content or detected value appeared in the chat
- Anything unread is in `unobservable` with its reason

## Open in orq.ai

- **A trace:** the links from step 15, built with [`orq-trace-format`](../orq-trace-format/SKILL.md)
- **Traces view:** `<host>/<workspace-key>/traces`, with the project selector on **All projects** when a link opens empty

## Companion skills

- `orq-setup-observability`: fixes the gaps in the application's instrumentation
- `orq-review-agent`: the setup review of one agent, which reads its traces for run health
- `orq-trace-format`: renders and links the example traces
- `orq-analyze-traces`: failure analysis once the traces are trustworthy

## Documentation & Resolution

**Lookup order: [`doc-resolution.md`](../orq-shared/resources/doc-resolution.md).** Live reads first (`orq traces list-fields`, the saved run files), then the `orq-shared` files named above.
