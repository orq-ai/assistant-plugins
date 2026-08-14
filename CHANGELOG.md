# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.5.0] - 2026-08-14

### Added
- `orq-evaluator-alignment`: **`orq_router` is the new default backend** for the recommendation and rewrite stages — any workspace model over the OpenAI-compatible `/v3/router`, using the `ORQ_API_KEY` the skill already requires, defaulting to `deepseek/deepseek-v4-pro`. The rewrite is a text transform, not an agentic task, and the previous default (`claude_subagent`) silently assumed the `claude` CLI was installed *and* logged in. `backend_model` is now optional per backend, so switching `backend` alone is a valid edit instead of quietly handing a Claude model id to the router. Calls are priced from the registry's own per-1K rates rather than reported as zero. Step 7 states the model, notes it must be enabled on the workspace, and offers the two alternatives (another model by `refId`, or the Claude subagent); both scripts preflight with one tiny call and stop with an actionable reason — 403 vs 404 vs 429 are distinguished — rather than failing partway through a paid run.
- `orq-evaluator-alignment`: a narrow escape hatch to the "never read the raw artifacts" rule. Reading a **single** datapoint by `source_index` is now sanctioned when the payload lacks a field the decision needs; the blanket prohibition was pushing the conductor toward guessing instead.
- `orq-evaluator-alignment`: a step 0 that asks whether the user actually has a judge in orq before anything else runs. Three routes: an evaluator id continues into the flow; a bare prompt is set up as an evaluator first (via `orq-build-evaluator`) and then continues; neither hands off to `orq-build-evaluator` and stops. Previously the flow opened by demanding an id, which is the one thing a user who needs this skill most may not have.
- `orq-evaluator-alignment`: `grey_zone.py assemble --include_low_flip` to opt the stable spot-check rows back into the payload, and `n_low_flip_excluded` / `n_confusers` in the `budget` block so the conductor can state what actually entered context.
- `orq-evaluator-alignment`: **the retest reports a `before` for signal (b), and the caveats that make both signals softer than they look.** Agreement previously had no baseline, so "accuracy 0.78, PASS" read as a win even when the original judge had scored 0.85 on the same labels — and that number costs nothing, because the original run already judged those very rows. `retest_metrics.json` now carries `agreement.before` alongside a `caveats` list the conductor is told to read out: the retested rows were selected for maximum observed instability (so re-measuring them regresses toward the mean on its own), the same examples produced the rewrite guidance *and* the labels scoring it (no holdout in v1), and any label the user did not confirm is the conductor's application of their rule rather than their verdict.
- `orq-evaluator-alignment`: `retest.py --baseline_rerun` re-runs the **original** judge over the same rows in the same pass. It doubles the cost and is the only version of gate (a) that isolates the rewrite from the selection effect; off by default, offered rather than assumed.
- `orq-evaluator-alignment`: `retest.py --with_low_flip` re-judges the queue's stable spot-check rows as an unlabelled regression check, reporting how many changed verdict. The step-5 promise to spot-check the rows the judge was completely sure about was never kept on the default path — they were excluded from the payload and, being unlabelled, from the retest too — so a rewrite that settled the grey zone by unsettling everything else was invisible. SKILL.md step 6 now also has the conductor read those rows with the user before the rewrite.
- `orq-evaluator-alignment`: `grey_zone_policy.json` labels carry a `label_source` (`derived` | `human_confirmed`) and SKILL.md gates on reading the derived labels back to the user. The human answers a *rule* question and the conductor derives each point's value from it, so by default the ground truth the retest scores against is model-derived; `apply` warns with the counts and `retest_metrics.json` records them, rather than presenting one model's reading of the rule as a human verdict.
- `orq-evaluator-alignment`: `validate_policy` checks every label against the evaluator's own verdict space. SKILL.md says never invent a label or move the scale and nothing enforced it, so an invented categorical label or an out-of-scale score passed validation, entered the rewrite guidance, and then scored 0 accuracy at step 8 — which reads as a judge failure rather than the policy error it is. Numeric tolerances must also be non-negative numbers, and a label may not reference a grey zone the policy never declares.
- `orq-evaluator-alignment`: `stability.json` and `queue.json` record a `traces_fingerprint` of the `traces.jsonl` their `source_index` values point into (`lib/content.traces_fingerprint`, sharing the scanner's own row-identity key). The grey-zone assemble warns on a mismatch and `retest.py` refuses outright.

### Removed
- `orq-evaluator-alignment`: `scripts/run_experiment.py`, superseded by `retest.py` since the Part 2 feedback loop and left behind rather than kept. It was not merely unused — it was **wrong** on the skill as it now stands. It selected labelled rows with `isinstance(value, bool)`, so on a categorical, numeric or string judge it silently scored **zero** rows; it hard-required `annotations.json`, which is the UI *fallback* artifact rather than the default `grey_zone_policy.json`; and it checked agreement alone, when the whole premise since Part 2 is that agreement without an instability drop is gameable. `retest.py` covers every case it did, for all types, on both signals. The one thing genuinely lost is `--recommend_only`, its variance-aware suggestion for the retest repeat count — that logic was boolean-only (it keyed off `flip_rate`) and `SKILL.md` already replaced it with an explicit instruction to use at least the stability run's N. Its two orphaned config keys (`retest_repeats`, `retest_repeats_cap`) go with it; the retest reads `n_repeats`.

### Changed
- `orq-evaluator-alignment`: the skill's `README.md` and the repo-level surfaces (root `README.md`, `agents/AGENTS.md`, `tests/skills.md`) describe what the skill now is. All three still advertised a **binary Pass/Fail** judge and a per-row annotation queue — the pre-RES-978 shape — so the skill's own docs were the last thing claiming it couldn't do categorical, numeric or string, and the smoke tests were verifying the flow it no longer runs. The skill README's pipeline table had also drifted past four new scripts and named a default backend that is no longer the default. The `SKILL.md` description now names the question-led grey-zone flow, which is the trigger a user most plausibly types ("my judge keeps changing its mind").
- `orq-evaluator-alignment`: `lib/seed.py` reads the shared `lib/content.field_for_variable` table instead of keeping a hand-mirrored copy of the suffix rules. That copy is what produced the 2.4.1 bug where a `{{log.reference}}` evaluator had every seeded row silently skipped; there is now one table and no mirrors, pinned by a test asserting every judge-fillable leaf resolves.
- `orq-evaluator-alignment`: the grey-zone payload renders a conversation through the same parts-aware helper the judge renders with, rather than dumping raw JSON. On a Responses-API row the text sits under `parts[]`, so the dump spent the character budget on structural noise and showed the conductor a different artifact than the judge was given.
- `orq-evaluator-alignment`: the recovery menu moved from step 4a to **step 1a** and is now offered the moment the trace scan comes up short, not several steps later after the user has already committed to a run. It gained a fourth option — bring your own examples from outside orq — alongside looking further back, using an existing orq dataset, and generating cases; the second-model option is now scoped to the flat-profile case it actually addresses.
- `orq-evaluator-alignment`: `config.toml`'s section comments use the same step numbers as `SKILL.md`. They still numbered the recommendation and rewrite as steps 8 and 9 from an earlier draft of the flow, which is the sort of drift that sends a reader looking for a step that no longer exists.
- `orq-evaluator-alignment`: `fetch_traces` takes its dedup key from `lib/content.judged_input_key` rather than keeping a private copy, so the definition of "the same datapoint" is shared with the fingerprint guard by construction.
- `orq-evaluator-alignment`: SKILL.md rewritten for a non-specialist reader. The conductor now describes judge behaviour ("it gave a different answer 6 times out of 8") rather than the statistics that measured it, and the internal vocabulary — confuser, grey zone, instability band, open-code, conductor — is kept out of what it says to the user. Each step opens with a sentence on what is about to happen and why.

### Fixed
- `orq-evaluator-alignment`: the grey-zone payload no longer silently overrides the count the user chose. `build_queue` appends `low_flip_sample_size` (default 5) *stable* spot-check rows to the queue, and `assemble_payload` was taking the whole queue — so "review 3 with me" put 3 + 5 = 8 examples into context, five of which had no grey zone in them by construction. Those rows are a control group for annotation, not material to open-code; they are excluded by default and reported separately.
- `orq-evaluator-alignment`: the aligned copy is named after its source instead of after an opaque id. `GET /v2/evaluators/{id}` returns an empty `key` for evaluators created through the current orq UI, so the old fallback produced keys like `01kzxt86ac82d1fvzw3s8wfv83-aligned-<ts>`. The name is now resolved `key` → `display_name` → `raw.key` → id, giving `groundedness-bool-testcase-aligned-<ts>`. The key is what matters here: orq overwrites the `display_name` sent on create with the key, verified against the live API.
- `orq-evaluator-alignment`: the aligned copy is created in the same project as its source rather than in a new folder named after the source's id. The evaluator record carries no `path` on either the detail or the list endpoint, so the source's folder cannot be read back directly; the new `resolve_project_key` maps the id it *does* carry to the project key that heads a create `path`. That id is `domain_id`, not `project_id`: `GET /v2/evaluators/{id}` runs through `normalizeEvalToInternalEvaluator`, an explicit field allowlist that never emits `project_id` — so the first cut of this fix resolved nothing and every copy still landed at the workspace root. Both spellings are read, the project record is matched on any of its id fields, and the lookup pages through `/v2/projects` instead of reading only the first page. Falls back to the workspace root when the project can't be resolved, instead of inventing a folder.
- `orq-evaluator-alignment`: **a free-text numeric judge no longer gets the wrong score, silently.** `parse_numeric` fell back to the *first* numeric token in the completion, so `'On a scale of 1 to 5, I give this a 4'` parsed as **1.0** with status `ok` — inside the declared scale, so nothing downstream re-checked it, and the wrong score entered `mean`, `stdev` and the instability band. It now reads the verdict the way `parse_categorical` does (after the last `Value:`/`Score:` label, else the last line), scrubs scale mentions (`of 1`, `to 5`, `out of 5`, `/5`) first, and surfaces a verdict line still carrying two different candidates as `wrong_output_type` rather than guessing between them.
- `orq-evaluator-alignment`: the preservation guard no longer passes a moved verdict space on a substring match. `endpoint not in proposed` is True for a rubric that only ever says `10`, so a rewrite moving the scale from `[1, 5]` to `[1, 10]` cleared the gate while both endpoints were in fact gone; the same shape let a dropped `spam` label read as preserved by a rubric mentioning `spammy`. Both are matched on their own boundaries now.
- `orq-evaluator-alignment`: real judge reasoning is no longer discarded as a jury placeholder. `_is_real_rationale` substring-matched `not available` / `no explanation`, so `'the tool was not available at inference time, so the claim is ungrounded'` was classed as no-rationale and the conductor was told there was nothing to read when there was. The generic phrases now only count as placeholders when they are the whole rationale; the distinctive jury notice still matches anywhere.
- `orq-evaluator-alignment`: `retest.py --num_samples` broke the comparison it was meant to smoke-test. The label scope stayed the full set while `num_samples` narrowed only the retest stability run, so `original_mean` covered every labelled row and `retest_mean` covered the first few — the subset-vs-full-run artifact the surrounding code exists to avoid. It now caps the selection itself, so both sides cover the same rows.
- `orq-evaluator-alignment`: the retest defaults its repeat count and temperature to whatever the original stability run actually used, read back from `metrics.json`, instead of to the config. Instability is estimated from N samples at a temperature and both bias the estimate, so retesting at different ones compared two different measurements; temperature was not forwarded to the retest run at all. A deliberate override to *fewer* repeats now marks the comparison not comparable rather than letting the sample size read as an improvement.
- `orq-evaluator-alignment`: label loading no longer discards the artifact the user just finished. `retest`/`rewrite` preferred `grey_zone_policy.json` whenever it existed, but the documented fallback runs in the other direction — try the grey-zone Q&A, find the examples don't group, open the annotation UI — so the labels collected last were silently ignored. The newer file wins, with a warning naming which.
- `orq-evaluator-alignment`: a free-text (string) judge is refused by `recommend.py` instead of two steps later. `rewrite_eval` and `create_eval` both fail fast on string, but `recommend` did not, so the user paid for one meta-prompt call per annotation to produce guidance the next step would reject.
- `orq-evaluator-alignment`: the numeric `scale` is no longer sent on create. The evaluator schema has no scale field and the API drops it, so the request only looked like it carried a guarantee it never had; a numeric judge's scale lives in its rubric text, which the preservation guard is what actually protects.
- `orq-evaluator-alignment`: the grey-zone payload honours `max_chars` for real. The elision marker and the `name: ` prefixes were added on top of the budget, so a 600-char ceiling across several variables rendered close to a thousand; both are charged against it now, and a budget too small to give every variable a character resolves to zero rather than overspending.
- `orq-evaluator-alignment`: cross-model disagreers are labelled and counted in the payload. The queue appends them after the instability ranking, so the token clamp drops them first — and on a judge that never wavers they are the entire signal. Each confuser carries its `reason`, and the budget block reports `n_dropped_cross_model`.
- `orq-evaluator-alignment`: `testcases/rag-groundedness/answer_key.json` no longer contradicts itself. `G3c` was listed both as unstable at 0.5 and as a stable-but-wrong case on the same judge; it is wrong *and* unstable, which is a different thing, and now sits in its own `unstable_and_wrong_cases`. `PC3` claimed six stable-wrong cases and named seven ids, three of which (`T1`, `T2`, `T4`) appear in neither measured baseline and are not accounted for by either run's agreement count — it now states what was measured and flags the traps as designed-but-unconfirmed.
- `orq-evaluator-alignment`: **the grey-zone payload no longer silently omits most of the judged input.** When judge-template inversion failed, `_compact_input` fell back to `output or query` — and on a row captured in the structured Responses-API shape `output` is the assistant's *answer*, not the rendered prompt. So a groundedness judge's context and question, the only things groundedness can be checked against, never reached the conductor. The character accounting was computed over that single field, so the budget block truthfully reported "0 elided" while most of the input was missing: a silent failure the disclosure protocol structurally could not see. The fallback now carries every captured field (`query`, `output`, `messages`), accounts for all of them, and flags itself as `input_source: "fallback"` with an `n_fallback_input` count, because no elision number can reveal a field that was never collected.
- `orq-evaluator-alignment`: an exact tie no longer presents the jury's tie-break notice as if it were the judge's reasoning. Ties are where seeing both sides matters most and are exactly where no rationale exists, so the payload now carries `reasoning_available` / `n_no_rationale` and the conductor states the gap. (Per-repetition rationales remain unavailable — evaluatorq's jury layer collapses them — so this flags the limitation rather than working around it.)
- `orq-evaluator-alignment`: `retest.py` re-judged **every** row in `traces.jsonl` rather than the labelled ones, despite `_build_retest_dir`'s docstring claiming otherwise — a 24-row run with 8 labels cost 24 × N calls, triple what the skill quoted, while agreement still scored only the 8. It is now scoped to the labelled rows, with `--all_rows` to opt out; because `source_index` is positional, the retest sub-run carries an `index_map.json` so labels still pair correctly, and the original mean is recomputed over the same subset so the reported drop isn't an artifact of row selection.

## [2.4.1] - 2026-08-14

### Added
- `orq-evaluator-alignment`: `testcases/rag-groundedness/` — a live end-to-end test case for the skill. One orq dataset of 24 RAG-groundedness datapoints (8 stable anchors, 12 grey-zone cases across 4 engineered ambiguities, 4 consistently-wrong traps) plus four judge evaluators covering every verdict type, a provisioning/teardown script, an answer key with pass criteria, and `score_run.py` to grade a finished run. Unlike `tests/`, it exercises the real API and answers what unit tests cannot: does the skill find the ambiguity that is there, and does it admit what it cannot see. Running it on `gemini-2.5-flash` reproduces the documented blind spot on demand — Gwet AC1 0.961 and mean instability 0.031 on a judge that is wrong on 5 of 24, including two verbatim-restatement anchors.

### Fixed
- `orq-evaluator-alignment`: `create_datapoints` sent the wrong request body. `POST /v2/datasets/{id}/datapoints` takes a top-level JSON array, but `build_create_datapoints_body` wrapped the rows as `{"datapoints": [...]}`, which the API rejects with `400 invalid_request_body` ("expected array, received object"). This broke dataset save-back (`seed_inputs.py save`) entirely. Verified against the live API.
- `orq-evaluator-alignment`: `seed.unresolved_variables` did not mirror `judge.make_replacements` despite its docstring saying so. It had no branch for the `reference | expected | expected_output` leaves, so an evaluator declaring `{{log.reference}}` rendered correctly at judge time while **every** one of its dataset rows was silently skipped at pull time.

## [2.4.0] - 2026-08-14

### Added
- `orq-evaluator-alignment`: multi-type judge alignment. The measure → annotate → rewrite → retest loop now covers all four verdict spaces — boolean (flip-rate), categorical (label entropy), numeric (score spread) and string (exact-match entropy) — on one shared 0..1 instability scale. String is detect + annotate only (no rewrite/retest). The annotation UI, agreement gate, recommend/aggregate stages and the created evaluator all carry the source's verdict space through.
- `orq-evaluator-alignment`: grey-zone assessment stage (RES-980) — cluster the most-unstable "confuser" datapoints, drive a policy-driven Q&A, and render the resolved policy as the free-text guidance the existing rewrite step already consumes.
- `orq-evaluator-alignment`: a context budget for the grey-zone payload, so evaluators with very large judged inputs (transcripts, RAG contexts, documents) cluster honestly. Each confuser's input is now windowed head+tail with a per-`{{variable}}` fair share — a long `{{query}}` can no longer starve `{{output}}` — and carries explicit elision accounting (`input_chars_shown` of `input_chars_original`) that the conductor is required to disclose before open-coding. `assemble` clamps the payload to the longest prefix fitting `grey_zone_max_tokens` (default 60k) and reports what it dropped; `build_queue` projects the same number at the point the user picks how many examples to review, so the budget rides on that existing decision instead of adding a gate. New `--max_tokens` flag and `grey_zone_top_k` / `grey_zone_max_chars` / `grey_zone_max_tokens` config keys.

### Changed
- `orq-evaluator-alignment`: run-directory artifacts (`aggregated.md`, `new_prompt.md`, and every JSON/JSONL) are written atomically (temp-then-replace) so an interrupted step can no longer truncate an existing artifact. `aggregate.py` and the grey-zone `apply` step now warn instead of silently clobbering when both write `aggregated.md`.
- `orq-evaluator-alignment`: judge model-slug resolution de-duplicates a display alias shared by two providers (first routable `refId` wins, with a warning) so alias→refId normalisation can't non-deterministically re-introduce the wrong-provider 403.

### Fixed
- `orq-evaluator-alignment`: the grey-zone payload no longer silently misrepresents a large judged input. It previously concatenated every `{{variable}}` and cut the result head-first at 600 chars, so for a long conversation plus a short answer the conductor saw only conversation preamble — the answer the judge actually scored was elided entirely, with nothing but a trailing ellipsis to show for it. The `grey_zone_top_k` / `grey_zone_max_chars` keys the skill documented and read were also absent from `config.toml`, leaving the payload effectively uncapped.
- `orq-evaluator-alignment`: categorical and numeric instability now clamp to `[0, 1]` — a judge that emits labels/scores outside the declared contract can no longer push the score past 1.0 and skew downstream banding.
- `orq-evaluator-alignment`: creating an aligned copy of a numeric evaluator preserves its scale whether the source stores `output_type` as `number` or `numeric` (the latter previously dropped the scale silently). `build_queue --count 0` now means "no flipped items" (low-flip sample only) instead of being folded into "all". `retest` fails with a clean message on a string evaluator instead of a raw `ValueError`.

## [2.3.2] - 2026-08-14

### Fixed
- `orq-evaluator-alignment`: the judge span is now located by a **normalised** parent link (`parent_span_id` → `parent_id` → `attributes.orq.bridge.parent_span_id`). On a Responses-API trace — the exact shape 2.3.1 added support for — orq leaves `parent_span_id` unset and puts the link on `parent_id`, so the previous scoping found no child and silently fell back to "any judge span in the trace". In a trace with two evaluator calls, both rows then took the *first* judge span's rendered prompt, recovered content and pinned judge model. All three readers (rendered IO, judge model, hollow-guard content sources) now share one scoper, and it returns nothing rather than guessing between several unparented candidates.
- `orq-evaluator-alignment`: `{{reference}}`/`{{expected}}`/`{{expected_output}}` survive template recovery. The judge renderer mapped them to the row's `reference`, but the trace-side mapper did not and the fallback path hard-set `reference = ''`, so a template like `Compare {{log.output}} with {{log.expected_output}}` re-judged against a blank ground truth. Both directions now share one suffix table (`lib/content.py`).
- `orq-evaluator-alignment`: a parts-shaped conversation (Responses API) renders its real text into a `{{messages}}` variable. The renderer read only each message's flat `content`, so the conversation the 2.3.1 extraction fix had just recovered was re-judged as a list of empty `user:` lines.
- `orq-evaluator-alignment`: a conversation-only evaluator (no `query`/`output`, only `{{messages}}`) is extracted instead of hollowed — the structured-root gate required query or output, so 2.3.1's "carries the conversation" fix could not fire on the one evaluator shape that needs it. A root carrying only a conversation still does not claim the output slot: the judge-span path runs whenever query and output are both empty.
- `orq-evaluator-alignment`: `--force` no longer overrides a shape-gap abort. It is the auth/rate-limit override, and the shape-gap message explicitly tells the operator not to force it (forcing writes empty rows that then read as perfectly stable datapoints) — it now discounts the span-detail half only.
- `orq-evaluator-alignment`: `source_index` again means "row N of `traces.jsonl`". 2.3.1's degraded-row filter renumbered the surviving rows, so annotations carried across a rerun with a different `--include_degraded` would land on different datapoints.
- `orq-evaluator-alignment`: when the degraded filter empties the set, the error names the filter (and `--include_degraded`) instead of telling the operator to run `fetch_traces.py` first — the file was full and fine.
- `orq-evaluator-alignment`: `fetch_evaluator.py --with_traces` exits non-zero when the chained trace fetch persists no datapoints. It caught the abort, logged remedies and returned normally, so automation read a hollow run as a successful step 1 and failed later on a missing `traces.jsonl`.
- `orq-evaluator-alignment`: `run_experiment.py` reads `new_prompt.md` as `utf-8-sig` like every other human-editable artifact; a BOM from a Windows editor reached the judge prompt on the retest path.
- `orq-evaluator-alignment`: `hollow_debug.json`'s span inventory prints the normalised parent alongside the raw fields — it previously showed the Responses-API judge span as unparented in the very dump meant to explain the shape.

### Changed
- CI runs the skills' pytest suites (`skill-tests` job). They existed and were green only on whoever last ran them locally, so every invariant they pin was unprotected. A skill opts in by shipping `tests/requirements.txt`.

## [2.3.1] - 2026-08-13

### Fixed
- `orq-evaluator-alignment`: trace extraction now reads the orq Responses API span shape (`span.responses`, with text under `messages[].parts[].content`) and no longer treats a reference-only root span as content. Previously every datapoint from a Responses-API evaluator came back hollow and the scan aborted with a misleading auth/rate-limit guess.
- `orq-evaluator-alignment`: the hollow-datapoint guard now distinguishes a span-detail fetch failure (auth/rate-limit) from an unrecognised span shape. It tells the two apart by intersecting the spans a row's content is actually read from — the root span and *this* eval span's own judge spans (scoped as `_judge_io` scopes them, so a repeated evaluator's 429'd judge span isn't borrowed) — with the spans whose detail fetch failed, so a 429 on a content-source span isn't misfiled as an extractor bug; it writes `hollow_debug.json` with the offending span shape on a shape-gap abort instead of guessing.
- `orq-evaluator-alignment`: `_structured_io` now carries the root span's conversation `messages` instead of hardcoding `None`, for any row it already returns (one carrying a query or an output). An evaluator whose judge template has a `{{messages}}`/`{{history}}`/`{{conversation}}` variable was re-judging with a blank conversation, and because the row's output was non-empty nothing flagged it — a silent quality drop behind a green pipeline. (A *conversation-only* evaluator — no query or output at all — is fixed in 2.3.2; this gate still rejected it.)
- `orq-evaluator-alignment`: a datapoint whose query/output extracted cleanly is no longer marked degraded merely because one of its spans' detail fetch was rate-limited. The stability step was dropping such usable rows with a false "empty output, cannot be meaningfully re-judged" reason; only genuinely-empty rows are degraded now.
- `orq-evaluator-alignment`: Windows robustness — the cosmetic run-dir rename is guarded against OneDrive/WinError 32 locks, and the artifacts feeding evaluator creation (`new_prompt.md`, `aggregated.md`, and every JSON/JSONL read) are read as `utf-8-sig` so an editor-written BOM no longer crashes reads or corrupts the created prompt's first line. (The retest path's own read of `new_prompt.md` was missed; fixed in 2.3.2.)
- `orq-evaluator-alignment`: the stability step now skips degraded/hollow datapoints by default (they waste judge calls and flatten the flip metrics); `--include_degraded` keeps them.

## [2.3.0] - 2026-08-11

### Added
- Root `plugin.json` conforming to [Agent Plugins 1.0.0](https://github.com/agentplugins/agent-plugins-spec) — one portable manifest (`$schema`, closed field set) any spec-conformant client can load from the repo root. Skills ship from the fixed `skills/` location; `mcp.json` stays as-is for now (a 1.0.0 client disables MCP for the plugin and still loads skills; conformant MCP config is a follow-up).
- CI validates the root manifest with `ajv` against `tests/schemas/agent-plugins-1.0.0.plugin.schema.json`, a vendored copy of the published 1.0.0 schema, and asserts every tracked symlink resolves inside the plugin root (§4.1 path containment). A nested manifest carrying the 1.0.0 `$schema` is rejected — the repo root must be the only Agent Plugins root.

- `tests/scripts/validate-skills.test.sh` covers every invariant the validator enforces, not just the stale lock hash: each case breaks one thing in a clean fixture and asserts both a non-zero exit and the message naming the check. A git-backed fixture means the tracked-file checks (§4.1 containment, stray skills, one plugin root) are exercised too — previously they were verified only by hand during review, which is how two of them shipped passing while not checking.

### Removed
- `disallowed-tools` from all 14 skills that carried it. It is not an Agent Skills frontmatter field — the field set there is closed (`name`, `description`, `license`, `compatibility`, `metadata`, `allowed-tools`) — and Agent Plugins §7.1 requires a client to skip a skill that does not conform to that spec. `skills-ref`, the reference validator the spec links to, reported all 14 as errors, so this release would otherwise have advertised a portable plugin from which a conformant client loads one skill out of fifteen. **This loosens the tool restrictions added in 2.2.3:** `delete_*` orq tools were removed from the pool while those skills were active, and are now merely un-pre-approved, so they prompt instead of being unavailable. No skill's body calls them. Restoring the guarantee needs a mechanism the Agent Skills spec actually has.

### Fixed
- CI validates every skill against the Agent Skills spec, not just against this repo's own conventions: closed frontmatter field set, `name` pattern and length, `description` and `compatibility` limits. The suite passed all 15 skills while 14 of them were non-conformant, because it only ever checked what this repo had thought to check.
- A `git ls-files` failure no longer skips the tracked-file checks in silence. The skip exists for a non-git tree (a test fixture); when `.git` is present and git fails anyway — a corrupt index, `detected dubious ownership` under a containerised runner — the run now fails and quotes git, instead of reporting success for three checks that never ran.

### Changed
- `.agents/plugins/marketplace.json` `source.path` now points at the repo root (`./`) instead of the removed `plugins/orq` copy.
- `docs/install-codex.md`: the personal install used an absolute `source.path`, which Codex silently ignores — it resolves `source.path` relative to the marketplace root and requires a `./`-relative path inside it. The clone now lives under `$HOME` and the entry is `./.codex/plugins/orq`.
- Manifest version sync now covers the root `plugin.json` alongside the three per-harness manifests.
- Install docs now name the repository `orq-ai/assistant-plugins` throughout; several still pointed at the pre-rename `orq-ai/orq-skills`, which worked only via GitHub's redirect.

### Removed
- `plugins/orq/` — a duplicate Codex surface wired to the repo root via symlinks that escaped its own plugin root (spec §4.1 violation). The repo root is now the single *orq skills* plugin root; `plugins/trace-hooks/` remains a separate, independently versioned Claude Code plugin. It was the documented target of the Codex personal install (`cp -rL plugins/orq …`), so `docs/install-codex.md` moves to the repo root in the same release; existing installs are unaffected, which is why this is MINOR rather than MAJOR.

## [2.2.3] - 2026-08-07

### Added
- Curated `allowed-tools` on five read-dominant skills (`orq-analyze-trace-failures`, `orq-optimize-prompt`, `orq-run-experiment`, `orq-build-evaluator`, `orq-build-agent`) — the `orq*` wildcard (which matched no tool and pre-approved nothing) replaced with explicit read/search orq tools for prompt-free lookups. Writes are not pre-approved: `create_*`/`update_*`/`invoke_*` and shell commands still prompt on these five skills. Each skill states this rationale under its title.
- `disallowed-tools` on the same five skills disables every `delete_*` orq tool outright while the skill is active — these skills never delete, so the tools are removed from the pool, not merely left prompting.
- `allowed-tools` on `orq-evaluator-alignment`, the last skill in the suite that declared none. It drives the orq API through its own bundled `uv run scripts/*.py` toolkit rather than orq tools, so its allowlist is narrow (`Bash(uv run:*)` plus file and question tools); the `delete_*` set is disallowed for consistency.
- Finished the `orq*` sweep across the remaining nine skills: the dead wildcard is replaced with the orq tools each skill's body actually calls, and `disallowed-tools` now covers every skill except `orq-manage-skills` (which legitimately deletes) and `orq-generate-synthetic-dataset` (which legitimately deletes datapoints, so only that one delete stays available — and only behind a prompt).
- Scoped `Bash` to the commands each skill actually runs (`Bash(orq:*)`, `Bash(eq:*)`, `Bash(curl:*)`, …), and dropped it entirely from the four skills with no shell examples. Unscoped `Bash` in `allowed-tools` pre-approved every shell command without a prompt, which was a wider grant than the `delete_*` tools this release set out to close.

### Changed
- `orq-manage-skills`: `create_skill`, `update_skill`, and `delete_skill` removed from `allowed-tools` (reads stay pre-approved) so every write and delete prompts; its delete workflow now uses the repo's `## Destructive Actions` + `AskUserQuestion` convention with per-entity confirmation and no bulk deletes without listing every item first.
## [2.2.2] - 2026-08-06

### Added

- Evaluatorq v1.10 features now referenced: LLM-jury and pairwise judging (`llm_jury`, `llm_jury_pairwise`, `PairwiseComparator`), `eq dashboard` run browser, `eq sim ui`, CrewAI and Pydantic AI target wrappers, redteam `--strategy`/`--delivery-method`/`--executive-summary` flags, and the `ORQ_WORKSPACE`/`ORQ_UI_BASE_URL` dashboard deep-link variables.

### Fixed

- Evaluatorq-family skills re-verified against the **current evaluatorq release (1.11.x)** (RES-1220):
  - CLI flags aligned to 1.11: `eq sim generate` writes via `--datapoints/-d`; `eq sim simulate` reads via `--input/-i` and writes via `--results/-r`; redteam report flags are `--report`/`--artifacts-dir`/`--report-md`/`--report-html` (the pre-1.11 `--save-report`/`--output-dir`/`--export-md`/`--export-html` no longer exist); Python `red_team(artifacts_dir=)` likewise.
  - `target_callback=` removed everywhere — it does not exist in the library (`target=` is the only name; passing it raises `TypeError`).
  - `simulate()`/`generate_and_simulate()` report kwargs documented (`report=`, `executive_summary=`, `save=`, `orq_results_path=`); `eq sim` subcommand list completed (`from-traces`, `upload-dataset`, `validate` with `validate-dataset` as deprecated alias); OWASP table carries `ASI10` Rogue Agents; async scorer example uses `await orq.evals.invoke_async(...)` with env-derived credentials; `orq-ai-sdk` added to the install table.
  - `orq-red-team`: credential-priority rule corrected — `ORQ_API_KEY` (gateway) wins when both keys are set, not `OPENAI_API_KEY`; the "uv run + .env credential trap" section, conflict guidance, and troubleshooting rows rewritten around the verified routing order. Removed the nonexistent `[ui]` extra (`eq redteam ui` ships inside `[redteam]`; `openai`/`typer` are core deps).
  - `evaluatorq`: `eq sim` quick references corrected — the target flag is `--target agent:<key>` (no `--agent-key`), datapoint-driven runs use `eq sim simulate` (not `run`), and the generate→export example now includes the required simulate step. Dashboard stage-file name fixed (`03_summary_report.json`).
  - `orq-simulate-agent`: `simulate()`/`generate_and_simulate()` examples no longer pass `agent_key=` (a TypeError — the parameter is `target=`); `red_team` import path corrected to `evaluatorq.redteam`; raw-model targets use `OpenAIModelTarget` (the `llm:` prefix is rejected); run store documented as CWD-relative `.evaluatorq/` with `$EVALUATORQ_DIR` override.
  - `orq-compare-agents`: the `my.`→`api.` staging URL rewrite no longer exists (removed in evaluatorq v1.10.0) — `ORQ_BASE_URL` is used verbatim with default `https://my.orq.ai`; the obsolete workaround was replaced with a historical note.

## [2.2.1] - 2026-08-06

### Changed

- `orq-cli`: command map re-verified live against the **4.13** CLI and rewritten to describe the 4.13 surface only — no version deltas or cross-version caveats. Covers the `annotation-queues` group (eval corrections / unified annotations), per-trace drill-down (`traces get` / `list-spans` / `get-span`), and notes there is no `experiments` group. The map is scoped to the supported surface; some CLI groups are intentionally not covered. (RES-1210)
- `orq-cli`: caveats re-tested live on 4.13 — plain strings accepted by generated flags (double-JSON-quoting workaround deleted); no client-side enum validation (documented as such: read the server error).
- `orq-cli`: the global projection flag is `-j/--jmespath` — 4.13 removed `-q/--query`, and body fields no longer shadow globals (colliding body fields get a `body-` prefix; a body `query` field is plain `--query`, full-text search). Every example updated; the shadowing section replaced with the `--query`-is-not-a-projection trap and the `-q`-muscle-memory error.
- MCP tool-name rot fixed across resources: `evaluator_get` → `get_llm_eval`/`get_python_eval` (4 files); phantom `list_registry_keys` row removed from `orq-invoke-deployment`; `## Companion Skills` heading case normalized.
- `orq-cli`: "MCP tools or the CLI?" decision table extended with the coverage split — MCP-exclusive (experiments, docs/entity search) vs CLI-only (schedules, identities, projects, API keys, webhooks, knowledge bases, memory stores, files).
- All 12 MCP-primary skills now cross-reference `orq-cli` for anything that must run again without an agent present (CI, cron, scripts, bulk), pointing at the canonical decision table. (RES-1163)

### Fixed

- `orq-red-team`: stale MCP references `mcp__orq-mcp-global__agent_get`/`agent_list` corrected to `mcp__orq-workspace__get_agent`/`search_entities` — the old server and tool names no longer exist.

## [2.2.0] - 2026-07-31

### Added
- **`orq-cli` skill** — drive the `orq` command-line interface end to end: verify the install (`npm i -g @orq-ai/cli` or `install.sh`, which lands in the often-not-on-`PATH` `~/.orq/bin`), authenticate via OAuth session or `ORQ_API_KEY`, scope to the right workspace, discover commands with `--help`, and run them with `--json` plus `-q` JMESPath projections. Covers the profile model, the `auth whoami` / `workspace list` / `doctor` JSON shapes, the four request-body input paths (typed flags, stdin, `--from-file`, `--example`) and bartolo shorthand, the `orq request` escape hatch, and a symptom→cause troubleshooting table. Companion `resources/command-map.md` carries the full v4.12.15 command tree, global flags and their env-var twins, verified response field names for traces/spans/agents, and JMESPath recipes — including resolving the active workspace key (`orq auth whoami --json -q active_workspace_key --raw`) for app deep-links, with a warning not to shell out to the CLI from library request paths. Registered in `agents/AGENTS.md`, `README.md`, `tests/skills.md`, and `skills-lock.json`. (RES-1140)

## [2.1.1] - 2026-07-22

### Fixed
- **`orq-evaluator-alignment`** — corrected the `judge_model` router slug format documented in `SKILL.md`. It previously stated the router requires `<provider>/openai/<model>` with a literal `openai/` segment "always required, whatever the provider". That form 404s on the router (`anthropic/openai/claude-haiku-4-5` → 404) and every user following it with a non-OpenAI provider hit a bare 404 mid-run. The correct form is the plain `<provider>/<model>` single-prefix slug (e.g. `anthropic/claude-haiku-4-5`, `google/gemini-2.5-flash`) — verified live against the Orq router (chat-completions and responses endpoints, openai/anthropic/google all route) and consistent with the agent config and the MCP `create_llm_eval` tool. Added a regression note to `tests/skills.md`. (RES-1145)

## [2.1.0] - 2026-07-02

### Added
- **`orq-evaluator-alignment` skill** — align, calibrate, or improve an existing binary Pass/Fail LLM-as-a-judge (orq evaluator) so its verdicts match human judgment. Measures judge self-consistency (flip-rate) via repeated runs, surfaces the most ambiguous datapoints for human annotation, rewrites the judge prompt from the labels, and creates the new evaluator only after human approval. Complements `orq-build-evaluator` (build from scratch) and `orq-optimize-prompt` (fix via prompt tweaks). Every step script is self-contained via PEP 723 inline dependencies, so `uv run scripts/<name>.py` builds its own environment with no repo checkout or `uv sync`. Registered in `agents/AGENTS.md`, `README.md`, `tests/skills.md`, and `skills-lock.json`.

## [2.0.0] - 2026-06-24

### Changed
- **BREAKING — renamed the Claude Code marketplace from `assistant-plugins` to `orq-claude-plugin`** (`.claude-plugin/marketplace.json` `name`). Install commands change from `claude plugin install <plugin>@assistant-plugins` to `@orq-claude-plugin`. The GitHub repo path (`orq-ai/assistant-plugins`) used by `claude plugin marketplace add` and `npx skills add` is **unchanged**, so Codex/Cursor/Warp/Gemini installs are unaffected — only Claude Code's `@<marketplace>` handle changed. Existing `@assistant-plugins` installs must re-add the marketplace. Updated install docs in `README.md`, `plugins/trace-hooks/README.md`, `plugins/trace-hooks/CLAUDE.md` (dev symlink path), and `plugins/trace-hooks/tests/README.md`; the orquesta-web docs (`claude-code.mdx`) were aligned to the already-published `@orq-claude-plugin` instructions in the changelog/tutorial/skills pages.

## [1.0.0] - 2026-06-11

### Changed
- **BREAKING — namespace every skill under the `orq-` prefix** to prevent collisions with similarly-named skills from other plugin marketplaces and make the suite discoverable as a set. Renamed 11 skill directories and their frontmatter `name`: `analyze-trace-failures`, `build-agent`, `build-evaluator`, `compare-agents`, `generate-synthetic-dataset`, `invoke-deployment`, `manage-skills`, `optimize-prompt`, `run-experiment`, `setup-observability`, `simulate-agent` → `orq-*`. `orq-red-team` already carried the prefix; `evaluatorq` is left as-is (its name already reads "orq"). Updated all cross-references in `agents/AGENTS.md` (path list + `<available_skills>`), `README.md`, `tests/`, `skills-lock.json`, and inter-skill companion references.
- **BREAKING** — renamed the `/manage-skills` slash command to `/orq-manage-skills` (`commands/manage-skills.md` → `commands/orq-manage-skills.md`, frontmatter `name`) to match its 1:1 skill.

## [0.5.2] - 2026-06-11

### Fixed
- `orq-red-team`: the "Verify the target exists" preflight now `export`s the resolved `ORQ_API_KEY` on success, so the verify call and the subsequent `eq redteam run` use the same key. Previously a key present only in `./.env` would pass the verify curl (which sourced it explicitly) but the run — `eq` reads `ORQ_API_KEY` from the environment and does not auto-read `./.env` when run directly — would get an empty key and fail deep with a cryptic 401/404.

## [0.5.1] - 2026-06-10

### Changed
- `orq-red-team`: the "Verify the target exists" preflight now checks via REST/SDK with the key the run actually uses (`ORQ_API_KEY` from the env, else the project `.env`) instead of the MCP, whose separately-configured key is often in a different project — an MCP miss isn't proof the target is absent. Covers both `agent:` (`GET /v2/agents/{key}`) and `deployment:` (`POST /v2/deployments/get_config`) targets, and falls back to asking the user when no key resolves.

## [0.5.0] - 2026-06-08

### Added
- `evaluatorq`: new skill for writing evaluatorq evaluation scripts (Python + TypeScript) and operating the `eq` CLI. Covers single-agent evaluation, custom scorers, built-in evaluators. Routes to `red-team` skill for `eq redteam` and `simulate-agent` skill for `eq sim`.
- `evaluatorq/resources/cli-reference.md`: full flag reference for `eq redteam` and `eq sim`, output file schemas, and common usage patterns.

## [0.4.0] - 2026-06-06

### Added
- `orq-red-team`: `eq` discovery ladder before installing — probe local/cheap options first (PATH → project `.venv/bin/eq` → `uv run --package evaluatorq` orqkit workspace → `python3 -m evaluatorq`) and use the first hit; install only as a last resort, preferring a project-local venv (`uv pip install` into `.venv`) over a global `uv tool install`, and avoiding global `pip` (PEP 668 / `--break-system-packages` breakage). Every resolved invocation is quote-clean and reusable as a `$EQ` prefix; the preflight CLI check honors it via `${EQ:-eq}`.
- `orq-red-team`: document the `uv run` + `.env` credential trap — `uv` injects an env-file only when opted in (`UV_ENV_FILE` set or `--env-file` passed); it does **not** auto-read `./.env` from a bare `uv run` (verified on uv 0.11.19). When such an env-file holds `OPENAI_API_KEY`, uv re-loads it *after* a shell `unset`, flipping routing to direct OpenAI and breaking gateway model strings (`openai/gpt-5-mini` → `401 Incorrect API key`). Includes a default-vs-`--no-env-file` detector that also surfaces `UV_ENV_FILE` (presence only, never prints the key), a decide-don't-auto-strip guide that surfaces the conflict and lets the user choose (a key in the env usually means they want it), Fix A (`env -u OPENAI_API_KEY uv run --no-env-file …` — strip the key and block uv from re-adding it, no temp file), Fix B (run `eq` off PATH), and a note that plain `env -u` without `uv run` is unaffected.
- `orq-red-team`: pre-run "Verify the target agent exists" step — check the `--target` key via orq MCP `agent_get`/`agent_list`, REST `GET /v2/agents/{agent_key}` (curl), or SDK `agents.retrieve`, so a wrong key fails fast instead of deep in the run with `Agent not found`. Documents the project-scoping caveat (MCP key may differ from the CLI key; a hit confirms existence, a miss is conclusive only when the checking credential shares the agent's project — verified live).
- `orq-red-team`: troubleshooting rows for the `uv run`/`.env` 401, the mid-run `Agent not found`, and the discovery-first `eq: command not found` fix.
- `orq-red-team` (`resources/python-sdk.md`): document `OrqResponsesTarget` — the hosted-agent target wrapping orq's Responses v3 API. Covers the import path (`evaluatorq.openresponses.target`), constructor signature, a `red_team()` example, and `require_orq=True` gateway routing. Notes it's usually built for you by the `openresponses` backend when you pass `"agent:<key>"`; hand-build only for a custom client/instructions/timeout/retry.
- `orq-red-team`: "Plan the run — decide parameters with the user" guided flow before the first invocation — step through mode (`dynamic`/`static`/`hybrid`, with an upside/downside table), datapoint budget (`--max-dynamic-datapoints` / `--max-static-datapoints` as the main cost lever, with smoke-test vs assessment guidance and the per-category multiplier), vulnerability scope (an agent-surface → OWASP-ASI/LLM category map, noting the framework auto-prunes non-applicable categories so over-picking is cheap), and delivery (one-off CLI vs a reusable script for the fix → re-run loop, CI, and baking in the `.env`/`uv` credential handling), then confirm the choices and state the coverage gap before assembling the command.

## [0.3.0] - 2026-06-06

### Changed
- Rename the `red-team` skill to `orq-red-team` for clearer invocation and to namespace it under orq. Skill directory `skills/red-team/` → `skills/orq-red-team/`, frontmatter `name`, and all references in `README.md`, `agents/AGENTS.md`, and `tests/skills.md`. (Treated as MINOR rather than MAJOR: `0.2.0` was never released/tagged, so the old name has no external consumers.)

## [0.2.0] - 2026-06-05

### Added
- `red-team`: `resources/python-sdk.md` progressive-disclosure reference for the `evaluatorq.redteam` Python API — covers `red_team()`, `OpenAIModelTarget` / the `agent:<key>` string target / custom `AgentTarget`, a raw-model worked example (the case the CLI cannot do), and programmatic `RedTeamReport` handling.
- `red-team`: document external-framework targets in `resources/python-sdk.md` — `LangGraphTarget` (`[langgraph]`), `OpenAIAgentTarget` (`[openai-agents]`), and `CallableTarget` (bundled, the escape hatch for any `async def(prompt) -> str`), plus LangChain/Vercel AI SDK pointers. Covers red-teaming a non-orq agent, which the CLI cannot do.
- `red-team`: document `generate_recommendations=True` and `report.focus_area_recommendations` (SDK-only LLM remediation) in both `SKILL.md` and `resources/python-sdk.md`.
- `red-team`: add "Acting on results — next steps" guidance for coding assistants — how to mine `report.results[]` (filter `vulnerable`, read `attack.category`/`attack_technique`, the transcript, and `evaluation.explanation`), prioritize by `summary.by_technique`/`by_category`, map failure patterns to concrete fixes, and close the re-run feedback loop. `jq` recipes in `SKILL.md`; the Python equivalent plus `focus_area_recommendations` handling in `resources/python-sdk.md`.
- `simulate-agent` skill: run multi-turn agent simulations using evaluatorq's first-class primitives (`simulate()`, `generate_and_simulate()`, `wrap_simulation_agent()`). Covers the real `Persona` schema (`patience` / `assertiveness` / `politeness` / `technical_level` scalars, `communication_style`, `background`, optional `emotional_arc` and `cultural_context`), `Scenario` schema (goal, criteria-driven judge termination, starting emotion, conversation strategy, edge-case flag), three target shapes (`agent_key`, `target_callback` via `from_orq_deployment` / `from_chat_completions`, custom `AgentTarget`), and where outputs land (OTel spans auto-emitted to orq.ai, `SimulationResult` in memory, auto-uploaded Experiments via `evaluatorq()` routing, JSONL export). Resources: `persona-scenario-template.md`, `simulation-loop.md`, `redteam-mode.md`. RES-732.

### Fixed
- `red-team`: correct `ASI01` label — it is **Agent Goal Hijacking**, not prompt injection (prompt injection is `LLM01`). Reframed the worked example and category guidance, and added the full OWASP-ASI (ASI01–10) / OWASP-LLM (LLM01–09) name mapping.
- `red-team`: correct the credential model — routing is decided by which env var is set (`OPENAI_API_KEY` → direct OpenAI with bare model names; else `ORQ_API_KEY` → orq gateway with provider-prefixed names like `openai/gpt-5-mini`), not by the model string. `OPENAI_API_KEY` wins if both set; `ORQ_API_KEY` always required for `agent:`/`deployment:` targets.
- `red-team`: use `openai/gpt-5-mini` in examples and drop the backwards "switch to `gpt-4o`" troubleshooting advice (the default `gpt-5-mini` is newer).
- `red-team`: remove invented framework labels ("OWASP Agentic 2026" / "OWASP LLM 2025"); use the real `OWASP-ASI` / `OWASP-LLM` identifiers.
- `red-team`: fix install instructions to `pip install 'evaluatorq[redteam]'` (and note the `[ui]` extra for the dashboard).

### Changed
- `red-team`: invocation preflight checks credentials before any `eq redteam run` — hard-fail if no LLM credential at all (`OPENAI_API_KEY` or `ORQ_API_KEY`), and check-and-warn for `ORQ_API_KEY`. Document that `ORQ_API_KEY` is not strictly required (raw-model runs work with `OPENAI_API_KEY` alone) but is needed for orq `agent:`/`deployment:` targets, gateway LLM routing, and uploading results to orq (`experiment_url`). The agent halts only when an orq-agent target is requested without the key.
- `red-team`: trim the flag table to first-run essentials and defer the full set to `eq redteam run --help`; document the `deployment:<key>` target form, the `eq redteam validate-dataset` pre-flight, and the `--system-prompt` flag.
- `red-team`: add a Constraints note (and `--no-cleanup-memory` flag row) that dynamic runs against a **memory-backed** agent write entities into its memory store (cleaned up unless `--no-cleanup-memory`); no-op for memory-less agents, raw models, and static mode.

## [0.1.0] - 2026-06-04

### Added
- `red-team`: new skill for invoking the orq red teaming library — adaptive attacks, dataset runs, hybrid mode, OWASP Agentic/LLM coverage, and ASR reporting.
- `manage-skills` skill — CRUD workflow for the orq.ai Skills entity (formerly Prompt Snippets), backed by `/v2/skills`. Covers list, get, create, update, soft-retire (tag as `retired`), and delete via the `*_skill` MCP tools. Includes authoring guidance (`display_name`, `description`, `tags`, `project_id`, `path`) and disambiguates the platform Skill entity from this repo's code-assistant Orq Skills and from the unrelated A2A `AgentCard.skills` array.
- `manage-skills`: documents both `{{skill.<display_name>}}` (canonical) and `{{snippet.<display_name>}}` (backward-compatible alias, falls back to the Skill whose `display_name` matches) as the template placeholders for consuming Skills inside prompts and agent instructions.
- `manage-skills`: reference-scan-before-delete workflow — paginates `search_entities`, fetches each candidate's body with `get_deployment` / `get_agent` / `get_skill`, and substring-matches both `{{skill.<display_name>}}` and `{{snippet.<display_name>}}` to surface consumers before any destructive operation. Defaults to tagging with `retired` (soft-retire) when references are found.
- `manage-skills`: rename-breaks-references warning on `display_name` updates — runs the same reference scan before any rename and offers to fan out updates in the same session.
- `manage-skills`: documents `GET /v2/skills` cursor pagination (`limit` / `starting_after` / `ending_before`) and the lack of server-side filters; pushes `project_id` / `tags` / `display_name` filtering to the client.
- `manage-skills`: anti-pattern guidance against `+NEVER+` / "you MUST refuse" prose constraints in `instructions` — recommends MCP tool gates for hard guardrails.
- `manage-skills`: error-handling guidance for `create_skill` `AlreadyExists` (offers either a renamed create or `update_skill` against the existing Skill).
- `/manage-skills` slash command — routes to list / get / create / update / retire / delete phases.

### Fixed
- `red-team`: rewrite skill to target the real `evaluatorq` package (`orqkit/packages/evaluatorq-py`) and `eq redteam` CLI instead of the legacy `research/projects/red-teaming` path.
- `red-team`: replace non-existent `redteam run adaptive/dataset/hybrid` subcommands with the actual `eq redteam run --mode dynamic|static|hybrid` interface.
- `red-team`: fix all CLI flags — `--category` repeatable (not `--categories` comma-separated), `--max-dynamic-datapoints`/`--max-static-datapoints` (not `--max-attacks`), `--generated-strategy-count` (not `--generated-count`), `--parallelism` default 10 (not 5), `--output-dir` (not `--out`).
- `red-team`: remove non-existent `redteam report summarize` command; replace with `eq redteam runs` / `eq redteam ui <path>`.
- `red-team`: fix default model to `gpt-5-mini`; add OpenAI `gpt-4o` as worked example model.
- `red-team`: fix env var section — document auto-detection order (`OPENAI_API_KEY` → direct OpenAI; `ORQ_API_KEY` → orq gateway); remove incorrect Azure credential guidance.
- `red-team`: fix output file naming — auto-named `redteam-report-<target>-<ts>.json` in `.evaluatorq/runs/`; use `--save-report <path>` for explicit path.
- `red-team`: add authorization guardrail — require explicit user confirmation before attacking any deployment.
- `red-team`: fix `tests/skills.md` scenarios to use correct `eq redteam run --mode dynamic` invocations.
- `agents/AGENTS.md`: remove trailing blank line after red-team `<available_skills>` entry.

## [0.0.2] - 2026-04-21

### Added
- `invoke-deployment`: document three deployment invocation patterns — variable substitution (`inputs`), message appending (`messages`), and mixed — with Python and curl templates for each.
- `invoke-deployment`: Phase 1 Step 3 now fetches `GET /v2/deployments/<key>/config` to discover `{{variable}}` placeholders before invoking.
- `invoke-deployment`: anti-pattern entry for passing `inputs` to a deployment with no matching `{{variable}}` placeholders (silently ignored).

### Changed
- `invoke-deployment`: Phase 1 marked as one-time setup — discovery steps do not belong in production invocation flows.
- `invoke-deployment`: clarify `inputs` only substitute when matching `{{variable}}` placeholder exists in the prompt template.

### Fixed
- `invoke-deployment`: replace insecure `curl -sk` with `curl -s` in deployment config fetch example (no TLS bypass).

## [0.0.1] - 2026-04-21

### Added
- `invoke-deployment` skill — invoke orq.ai deployments, agents, and models via Python SDK, Node.js SDK, or curl. Covers prompt variable substitution, multi-turn agent conversations via `task_id`, AI Router calls with `provider/model` format, and streaming.
- `setup-observability` skill — instrument LLM applications with orq.ai tracing. AI Router mode, OpenTelemetry/OpenInference mode, and the `@traced` decorator for custom spans.
- `compare-agents` skill — cross-framework agent comparisons using `evaluatorq` from orqkit. Compare orq.ai, LangGraph, CrewAI, OpenAI Agents SDK, and Vercel AI SDK head-to-head.
- Codex and Cursor plugin manifests (`.codex-plugin/`, `.cursor-plugin/`) plus Codex marketplace entry.
- `tests/scripts/validate-plugin-manifests.sh` — validates plugin JSON, field values, and symlink integrity.
- Smoke test scenarios in `tests/skills.md` for every skill.

### Changed
- README install instructions expanded to cover 5 tools: Claude Code, Cursor, Codex, npx skills CLI, and manual clone.
- Python code templates now use `os.environ["ORQ_API_KEY"]` instead of `os.environ.get()` / `os.getenv()` to fail fast on missing key.
- Renamed `instrument-app` skill to `setup-observability`.
- AI Router base URL standardized to `https://api.orq.ai/v2/router` across all skills.
