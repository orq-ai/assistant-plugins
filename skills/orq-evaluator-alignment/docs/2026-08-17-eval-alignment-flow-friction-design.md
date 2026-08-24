# Evaluator-Alignment Flow Friction — Design (RES-TBD)

**Ticket:** _to be created_ — proposed title: "Smooth the evaluator-alignment run loop (dataset mapping, ground truth, row selection, provider quirks)".
**Related:** [`2026-08-10-res978-multitype-instability-design.md`](./2026-08-10-res978-multitype-instability-design.md) (Part 1 — measurement), [`2026-08-11-res980-grey-zone-assessment-design.md`](./2026-08-11-res980-grey-zone-assessment-design.md) (Part 2 — grey zone; **§11.4 of that doc is amended by §2 here**).
**Status:** design proposed (2026-08-17), not implemented.
**Evidence:** a full live run of the shipped skill against evaluator `01KWH52J97NHV67674D28SNCQ4` ("safety", boolean, `alibaba/qwen3.5-flash`) using workspace dataset `01KZNQJVSBF9ZFWSF1R59XJDSZ` ("Content Safety Judge - Harmful/Illegal (v1)", 42 adversarial rows with ground-truth labels). Every item below is a friction point that run actually hit, in the order it hit them.

---

## 0. One-paragraph summary

The skill's *logic* held up end-to-end — measurement, the cross-model probe, and the human gates all did what they claim. What cost the session time was **plumbing around the edges**: a dataset whose rows were perfectly mappable got 100% skipped because the mapper only reads `inputs` and never looks inside `messages`; the dataset's ground-truth labels — the one thing that can close this method's central blind spot — were carried into `traces.jsonl` and then ignored by every downstream step; running a *subset* of rows required hand-building sub-run directories because `--num_samples` takes the first N and overwrites `stability.json`; and a provider quirk (Alibaba models 400 unless the prompt contains the word "json") aborted the first run entirely. Nine fixes are proposed, ranked. **Two of them (§2 mapping, §3 ground truth) are worth more than the other seven combined** — the first removes a total-failure mode on the most common dataset shape, the second changes what the skill can honestly claim.

**Design decisions taken in this doc:**

| # | Decision | Choice |
|---|----------|--------|
| 1 | Deriving `query`/`output` from `messages` | **A rule, not a guess** — last assistant turn → `output`, last user turn → `query`. Applied automatically, *disclosed* in the log and in run metadata. Does not violate the "never guess" constraint because it is a deterministic, documented contract, not an inference about intent. |
| 2 | Ground-truth labels from a dataset | **Used, never laundered.** They feed a new correctness check and a new confuser class, but carry `label_source: "dataset_reference"` and never masquerade as the human's grey-zone answers. |
| 3 | Provider format quirks | **Fixed in the harness, not the rubric.** One-shot retry with a JSON nudge, recorded in metadata — not a manual edit of the run-dir prompt, which is what the live session had to do. |
| 4 | Row selection | **Explicit `--rows`, and merge-not-clobber.** The sub-run + hand-merge dance the live session performed becomes a flag. |
| 5 | "The judge is fine" | **A first-class terminal state** with its own artifact, not a dead end that loops back to step 1a. |

---

## 1. Scope

### In scope
- `lib/seed.py` mapping rules and the `dataset_inputs.py pull` diagnostics (§2).
- Ground-truth propagation through `metrics.py`, `build_queue.py`, `grey_zone.py`, `retest.py` (§3).
- Provider-compat handling in `lib/judge.py` + a preflight in `stability.py` (§4).
- Row selection / merge semantics in `stability.py`; sampling strategy (§5, §6).
- `cross_model.py` default scope, cost gate, and the Windows env crash (§7).
- Cost estimation coverage for the paid steps that currently have none (§8).
- `SKILL.md` doc drift and a new terminal state (§9, §10).

### Out of scope
- Any change to the instability metrics themselves, the grey-zone question flow, or the rewrite/approve gates. They worked.
- The multi-turn / context problem (judge sees only `log.output`, not the prompt that produced it). That is an evaluator-design question for the user, not a skill defect.
- Automating the human gates. All five stay.

### Non-negotiable constraints (inherited)
- Never confuse "stable" with "correct" — §3 *strengthens* this check rather than replacing it.
- Never fabricate a datapoint field. §2 derives from data that is present; it does not invent.
- The evaluator in orq is never mutated; rewrites create a new one after explicit approval.
- Every script stays self-contained (PEP 723, `uv run scripts/<name>.py`).

---

## 2. Dataset variable mapping — the total-failure mode (P0)

### 2.1 What happened

`dataset_inputs.py pull` reported **`✓ Added 0 dataset rows; 42 skipped (unmappable)`**, with `skipped datapoint #N: missing ['log.output']` for every row. The dataset was fine. The rows carried exactly what the judge needed.

Root cause, precisely:

- `field_for_variable('log.output')` → `'output'` (correct — `lib/content.py` suffix table).
- `seed.map_datapoint` fills `row['output']` **only** from `datapoint['inputs']` keys whose leaf maps to `output`.
- This dataset's `inputs` held metadata only: `{category, challenge_type, difficulty, ground_truth}`. The actual exchange lived in `messages: [{role: user, ...}, {role: assistant, ...}]`, which `map_datapoint` assigns *wholesale* to `row['messages']` and nothing else.
- `unresolved_variables` therefore reported `log.output` missing → every row skipped.

The information the judge needed was present and unambiguous — **`log.output` is the assistant's last message** — but no code path connects those two facts. Production traces never exposed this because orq's `gen_ai.input` supplies `output` explicitly, so `fetch_traces` never needs to derive it. The dataset path has no such field, and that is the one path that has to derive.

This is precisely the drift `lib/content.py`'s own module docstring warns about ("two copies of the suffix table means a variable can be recovered into a field the renderer never reads back") — one level up: the *derivation* is what has no shared home.

### 2.2 Fix

Extend `seed.map_datapoint` with a **messages fallback**, applied only when a field is still empty after the `inputs` pass:

| Row field | Derived from | Rule |
|---|---|---|
| `output` | `messages` | text of the **last** message with `role == "assistant"` |
| `query` | `messages` | text of the **last** message with `role == "user"` |

Rules:
- Use `content.message_text()` for extraction, so Responses-API `parts[]` and multimodal `content: [...]` shapes work identically to the scanner. Do **not** hand-roll `m["content"]`.
- `inputs` always wins. A dataset that explicitly names an `output`/`response` key keeps its value; derivation is fallback only.
- Skip `tool` / `system` roles entirely.
- If there is no message with the needed role, the field stays empty and the row is skipped **as today** — the failure mode is preserved for genuinely unmappable data.
- Roleless messages: do **not** guess positionally. Report as unmappable (§2.3 makes that legible).

`row_to_datapoint` (the save-back direction) needs no change — it writes `inputs` keyed by the evaluator's own variable names, which round-trips.

**This amends §11.4 of the RES-980 design**, whose contract table has no `messages → query/output` row. Add:

| orq datapoint field | trace row field | rule |
|---|---|---|
| `messages[-1]` where `role == "assistant"` | `output` | **derived**, only if `output` still empty after the `inputs` pass |
| `messages[-1]` where `role == "user"` | `query` | **derived**, only if `query` still empty after the `inputs` pass |

### 2.3 Diagnostics — the second half of the fix

Even with §2.2, a genuine mismatch must be *diagnosable in one read*. Today the conductor gets N identical `missing [...]` lines and has to go inspect the raw API response by hand (the live session wrote three throwaway scripts to do this). Replace with a single inventory + proposal block:

```
✗ 0/42 rows mapped. The judge needs: ['log.output'] → row field 'output'.
  Dataset fields seen:
    inputs keys : category, challenge_type, difficulty, ground_truth   (none map to 'output')
    messages    : 42/42 rows, roles [user, assistant]                  ← 'output' derivable from last assistant turn
    expected_output : 42/42 rows (true/false)                          → 'reference'
  Proposed: log.output ← last assistant message.  Confirm, or pass --map log.output=<field>.
```

Rules: report **per dataset, not per row** (42 identical warnings is noise); name the row field the variable resolves to, not just the variable; state what *is* available next to what is missing.

### 2.4 `--map` escape hatch

`dataset_inputs.py pull --map "log.output=messages.assistant.last" --map "log.input=inputs.user_question"`.

Grammar, deliberately small: `inputs.<key>` | `messages.<role>.last` | `messages.<role>.first` | `messages.all` | `expected_output`. Anything else is an error, not a silent skip. Persist the resolved mapping into `<run_dir>/input_mapping.json` so the run is reproducible and the conductor can state what it did.

### 2.5 Testing

- Dataset row with metadata-only `inputs` + user/assistant `messages` → maps (the live regression).
- Explicit `inputs.output` **plus** messages → `inputs` wins.
- Assistant turn in Responses-API `parts[]` shape → text recovered.
- Multiple assistant turns → last one wins.
- Roleless messages → still skipped, with the §2.3 inventory emitted.
- `--map` with an unknown grammar term → hard error.

---

## 3. Ground truth is collected and then thrown away (P0)

### 3.1 What happened

The dataset carried a human label on every row (`expected_output: "true"/"false"`, plus `ground_truth: safe/unsafe` in `inputs`). `map_datapoint` correctly routed it to `row['reference']` — and then **nothing downstream ever read it**, because the evaluator declares no `{{reference}}` variable. `metrics.py` reported instability only. `build_queue.py` ranked by instability only.

So the live session computed accuracy with an ad-hoc script — and that number (20/20 correct) was the single most valuable output of the whole run. It is what let the final report say *"it's not consistent-but-wrong, it's right"* instead of the usual hedge.

That is backwards. The skill's most-repeated caveat is that it "structurally cannot find cases the judge gets wrong with total confidence". **When labels are present, it can.** It just doesn't look.

### 3.2 Fix

When ≥1 row has a non-empty `reference` that parses into the evaluator's verdict space:

**a) `metrics.py`** gains a `correctness` block alongside `instability`:
```json
"correctness": {
  "n_labelled": 20, "n_correct": 20, "accuracy": 1.0,
  "confusion": {"true→true": 11, "false→false": 9, "true→false": 0, "false→true": 0},
  "label_source": "dataset_reference",
  "by_band": {"stable": {"n": 20, "accuracy": 1.0}, "noisy": {}, "unreliable": {}}
}
```
`by_band` is the point: **accuracy among the *stable* rows is the blind-spot measurement.** A judge that is 100% stable and 60% correct on stable rows is the exact failure this skill otherwise cannot see, and it becomes a headline number.

Type handling: boolean/categorical by equality; numeric by the shared `numeric_tol` (§Configuration in `SKILL.md`); string is **excluded** for the same `==` reason `recommend.py` refuses it — the conductor scores those by reading, as it already does at step 8.

**b) `build_queue.py`** gains a third confuser `reason`, ranked *after* instability and cross-model:
`reason: "wrong_vs_reference"` — rows where the judge was **stable and wrong**. These are the highest-information rows in the whole run and today they are invisible.

**c) `grey_zone.py assemble`** carries `reference` and `judge_correct` per confuser, so the conductor can group by *how* it's wrong (systematically over-flagging fiction, say) rather than only by what it's unsure about.

**d) `retest.py`** may use dataset labels for gate (b) when no policy label exists for a row, with provenance recorded separately in `metadata.label_provenance` (`human_confirmed` / `derived` / **`dataset_reference`**). Never merged into the human-answer count.

**e) `SKILL.md`** — the blind-spot paragraph gains a conditional branch: when labels exist, say what was actually verified and on how many rows, instead of the unconditional caveat. The caveat stays for unlabelled runs.

### 3.3 Guardrail

A dataset label is **not** the user's verdict. It is someone's prior judgement, possibly stale, possibly from a different rubric version. Rules: always tag `label_source: "dataset_reference"`; when a `wrong_vs_reference` row enters the grey zone, the conductor asks the user to confirm the label before it drives a rewrite; never auto-approve a rewrite on dataset labels alone.

### 3.4 Testing

- Labelled dataset, judge correct on all → `accuracy: 1.0`, no `wrong_vs_reference` confusers, clean-bill artifact (§10).
- Labelled dataset, judge stable-but-wrong on 3 rows → those 3 appear as `wrong_vs_reference`, `by_band.stable.accuracy < 1`.
- Mixed labelled/unlabelled → `n_labelled` counts only the labelled.
- Unparseable label (`"maybe"`) → excluded from `n_labelled`, warned once, never coerced.
- String evaluator → `correctness` block omitted with an explicit reason field.

---

## 4. Provider format quirks abort the run (P1)

### 4.1 What happened

First real run: **every** call failed with HTTP 400 —
`'messages' must contain the word 'json' in some form, to use 'response_format' of type 'json_object'` — and `stability.py` correctly aborted with `0/2 rows produced a usable verdict`.

`lib/judge.py` sends `response_format: json_schema`; Alibaba's endpoint downgrades that to `json_object` and enforces the constraint. The rubric never says "json" — it says *"Use the tool to give your answer"*, because orq's hosted evaluator reaches the same contract via tool-calling. So the judge is fine in production; only the re-run harness breaks.

The live workaround was to hand-edit `prompt` in the run-dir `evaluator.json`. That works but is unpleasant: it is a manual step, it silently makes the measured judge a variant of the production one, and remembering to disclose it is left to the conductor.

### 4.2 Fix

In `lib/judge.py`, catch a 400 whose body matches `/must contain the word ['"]json['"]/i` and **retry once** with a harness-appended contract line (the exact string the live session used), then record on the row and in `stability.json` `meta`:

```json
"provider_compat": {"json_nudge_applied": true, "reason": "provider requires literal 'json' in messages", "appended": "Return your answer as a JSON object with fields \"explanation\" (string) and \"value\" (boolean)."}
```

`SKILL.md` step 3 gains a disclosure duty: when `json_nudge_applied` is true, say so in the step-4 report and carry it into the final summary — it is a real (small) deviation from the production judge.

Rationale for retry-not-preflight-list: maintaining a per-provider quirk table rots. The error is unambiguous and self-describing; react to it.

### 4.3 Preflight

Independently: `stability.py` should fire **one** call before the full fan-out and abort on a non-transient error. The live run only avoided burning 336 calls because the user chose a smoke run — that shouldn't be luck. Cost: one call. Skip with `--no_preflight`.

### 4.4 Testing

- Mocked 400 with the json message → nudge applied, second call succeeds, metadata recorded.
- Mocked 400 with an unrelated message → **no** nudge, fails as today (no blind retry).
- Nudge already unnecessary (prompt contains "json") → not applied twice.
- Preflight fails → abort before any fan-out; call count is 1.

---

## 5. Row selection and merge semantics (P1)

### 5.1 What happened

The user asked to run "the last 10" examples. There is no way to express that:
- `--num_samples N` slices `indexed[:N]` — always the **first** N.
- `stability.py` writes `stability.json` wholesale, so a second partial run **destroys** the first.

The workaround was three hand-written scripts: build a sub-run dir with a 10-row `traces.jsonl` + `index_map.json`, run stability there, then merge rows back into the parent with remapped `source_index`. That is `retest.py`'s internal pattern, executed manually — a strong signal it belongs in the tool.

### 5.2 Fix

**a) `--rows` selector** on `stability.py` (and `cross_model.py`, `retest.py`): `--rows "32-41"`, `--rows "0,5,17"`, `--rows "last:10"`, `--rows "first:10"`. Composes with `--num_samples` (which stays as the cheap smoke knob).

**b) Merge, don't clobber.** When `stability.json` exists and its `traces_fingerprint` matches, a partial run **merges by `source_index`** (new rows replace same-index rows, others are preserved) and records per-row `run_id` + `n_repeats`. A fingerprint mismatch refuses, as today. `--replace` forces the old behaviour.

**c) `meta.coverage`** — `{"n_traces": 42, "n_measured": 20, "measured_indices": [...], "partial": true}`, so `metrics.py` and the conductor can state coverage without recomputing it. The live run's merged file needed a hand-written `note` field for exactly this.

### 5.3 Testing

- `--rows "32-41"` after a `--num_samples 10` run → 20 rows in `stability.json`, both slices intact, `partial: true`.
- Same index run twice → replaced, not duplicated.
- Fingerprint mismatch → refuses with the existing message.
- `--rows` out of range → hard error naming valid bounds.

---

## 6. Sampling order is a methodological trap (P2)

`--num_samples 10` takes the first 10 rows in file order. In this dataset that was a benign 5/5 safe/unsafe split — **by luck**. The file was ordered roughly by category, so the first 10 held zero self-harm rows, zero keyword-false-positives, and zero fiction cases: the entire hard tail sat in the unmeasured middle. A user who ran `--num_samples 10` and stopped would draw a confident conclusion from a systematically easy slice.

**Fix:** `--sample {first,random,stratified}`, default **`stratified`** when labels exist (balance on `reference`; secondary balance on any `inputs` field with 2–10 distinct values, e.g. `category`), else `random` with a fixed seed. `first` stays available and is what `--num_samples 2` smoke runs should use. Record `meta.sampling` (`strategy`, `seed`, `strata`) and have the conductor state it: *"10 of 42, balanced across safe/unsafe and category"* is a materially different claim from *"the first 10"*.

---

## 7. `cross_model.py` — scope and a hard crash (P1)

### 7.1 Scope surprise

Invoked after a **20-row** stability run, it announced `42 rows × 8 repeats = 336 judge calls`. It reads `traces.jsonl` directly rather than the rows `stability.json` actually measured — and rows it probes but has no model-A verdict for cannot be compared at all.

**Fix:** default to the intersection with `stability.json` rows; `--all_rows` to widen (which should then warn that unmeasured rows need a model-A run to be comparable). Print the call count and require confirmation, like `estimate_cost.py` does for step 3.

### 7.2 Windows crash

The script aborted with `OPENSSL_Uplink(...): no OPENSSL_Applink` immediately after evaluatorq logged `resolve_results_base_url`. Its PEP 723 dependency list is **identical** to `stability.py`'s; the difference is that `uv` builds a separate cached environment per script, and cross_model's freshly-resolved env trips the applink abort while stability's older cached env does not. (Same family as the known `import mlflow` abort on this machine.)

**Fix, in order of preference:**
1. **Reuse, don't re-resolve** — `cross_model.py` already delegates to `stability.main`; have it *exec* `stability.py` in stability's own environment rather than importing it into its own. Removes the second env entirely and fixes the class of bug, not the instance.
2. Failing that, pin the transitive dependency set across both scripts (lockfile or explicit pins) so the two envs cannot diverge.
3. Detect the abort and emit the §7.2 workaround as an actionable message rather than a bare OpenSSL string.

Add a note to `docs/troubleshooting.md` either way.

### 7.3 Testing

- Stability over rows 0–9 then cross_model → probes 10 rows, not all 42.
- `--all_rows` → probes all, warns about the uncomparable ones.
- Cost line printed before any call.

---

## 8. Cost estimation has holes (P2)

`estimate_cost.py` covers step 3 only. `SKILL.md` tells the conductor to hand-compute the retest (*"there's no estimator for this step"*) and says nothing for `cross_model`. Hand-arithmetic in front of a spend gate is where mistakes happen.

**Fix:** `estimate_cost.py --step {stability,cross_model,retest}`, reading the same inputs each step will use (labelled-row count for retest, `--baseline_rerun` doubling, `--with_low_flip` addition, stability∩traces for cross_model). Same output shape as today. `SKILL.md` steps 7-alt and 8 then point at it instead of describing arithmetic.

---

## 9. Documentation drift (P3)

Small, but each one cost a cycle:

| Symptom | Reality | Fix |
|---|---|---|
| `SKILL.md` documents `--no-metrics` on `stability.py` | python-fire rejects it; it is `--metrics False`. The run *completes*, then errors — so it looks like the work failed when it hadn't | Correct the doc; consider accepting `--no-metrics` as an alias since fire supports `--no<flag>` for some bool forms |
| Step 1 says `fetch_evaluator.py` "fetches the judge and **stops**" | The parameter table lists `--with_traces` defaulting to **True** | Verify actual behaviour and align the two; the prose and the table cannot both be right |
| `dataset_inputs.py pull` prints the run dir twice | `print(out_dir)` plus fire's return-value echo | Return `None` or suppress the echo (cosmetic; affects every script) |

---

## 10. "The judge is already good" is not a dead end (P2)

`SKILL.md` step 4 says: if the judge is consistent everywhere, **go back to step 1a**. The live run hit that state twice (20/20 stable, then 0/20 cross-model disagreements) and there was no defined way to *finish*. The flow implicitly assumes there is always something to fix.

But "verified clean" is a legitimate — and valuable — outcome, especially with §3's correctness check behind it. It deserves an artifact rather than a chat message that scrolls away.

**Fix:** a step 4b terminal state writing `verification_report.json`:
```json
{
  "verdict": "no_alignment_needed",
  "rows_measured": 20, "rows_available": 42,
  "instability": {"mean": 0.0, "bands": {"stable": 20, "noisy": 0, "unreliable": 0}},
  "correctness": {"n_labelled": 20, "accuracy": 1.0, "stable_row_accuracy": 1.0},
  "cross_model": {"model": "deepseek/deepseek-v4-flash", "n_disagreements": 0},
  "provider_compat": {"json_nudge_applied": true},
  "coverage_gaps": ["rows 10-31 unmeasured: self-harm cluster, fiction-safe, harm-reduction, non-english-instructions"],
  "caveats": ["examples came from a dataset, not production traces",
              "judge prompt carried a harness-appended JSON contract line"]
}
```
`coverage_gaps` is the important field: it is what stops a clean bill of health from being read as *"the judge is fine"* when it means *"the judge is fine on the half we looked at"*. Populate it from `meta.coverage` (§5.2c) plus any `inputs` strata absent from the measured slice.

`SKILL.md` gains this as an explicit exit next to the loop-back, with the rule: **never rewrite a judge that shows no instability and no disagreement** — there is nothing to steer the rewrite with, and the guards can only preserve, not improve.

---

## 11. Priority

| # | Item | Impact | Effort | Priority |
|---|---|---|---|---|
| §2 | Dataset mapping from `messages` + diagnostics | Removes a 100%-skip failure on the commonest dataset shape | S | **P0** |
| §3 | Ground-truth correctness + `wrong_vs_reference` | Directly attacks the method's core blind spot | M | **P0** |
| §4 | Provider json-nudge + preflight | Removes a hard abort and a manual prompt edit | S | P1 |
| §5 | `--rows` + merge semantics | Removes the sub-run/hand-merge dance | M | P1 |
| §7 | cross_model scope + env crash | Cost surprise; hard crash on Windows | S–M | P1 |
| §6 | Stratified sampling | Prevents confident conclusions from an easy slice | S | P2 |
| §8 | Estimator for cross_model/retest | Removes hand-arithmetic at a spend gate | S | P2 |
| §10 | Clean-bill terminal state | Makes a good outcome reportable | S | P2 |
| §9 | Doc drift | Papercuts | XS | P3 |

A P0-only pass (§2 + §3) is the highest-value increment and is independently shippable.

---

## 12. Open questions

1. **Should `wrong_vs_reference` rows outrank instability confusers in `build_queue`?** They carry more information, but the queue's ordering is currently a clean single-metric ranking and mixing criteria makes "top N" ambiguous. Leaning: separate section in the payload, not interleaved.
2. **Does the messages-derivation belong in `lib/content.py` instead of `lib/seed.py`?** `content.py` is the shared vocabulary and is deliberately stdlib-only; if the trace scanner ever meets a span with messages but no explicit `output`, it needs the same rule. Leaning: put it in `content.py` as `derive_io_from_messages()` and have `seed.py` call it — one home, per that module's own stated purpose.
3. **Stratified sampling with no labels and no low-cardinality `inputs`** — fall back to random, or refuse and ask? Leaning: random with a recorded seed.
4. **Should the retest be allowed to score gate (b) purely on dataset labels** when the user answered no grey-zone questions (because there were none)? That would let a labelled-dataset run reach step 8 without any human input at all, which cuts against the skill's gate philosophy. Leaning: allowed, but the report must state that no human confirmed anything.
5. **Ticket split** — one ticket or two (P0 data-path vs. P1/P2 ergonomics)? Two gives a shippable P0 without waiting on the rest.
