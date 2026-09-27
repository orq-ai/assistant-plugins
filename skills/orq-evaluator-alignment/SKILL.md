---
name: orq-evaluator-alignment
description: >-
  Align, calibrate, or improve an existing LLM-as-a-judge (orq evaluator) so its
  verdicts match human judgment — boolean, categorical, or numeric judges. Use when
  the user wants to "align my evaluator", "improve my eval", "my judge keeps
  changing its mind", "find ambiguous cases", or "annotate an evaluator" — i.e.
  they have an LLM judge that disagrees with human labels or is inconsistent.
  Measures judge self-consistency as one 0..1 instability score via repeated runs,
  groups the least reliable examples by what makes them hard and asks a few
  questions instead of making the user label every row, rewrites the judge prompt
  from those answers, and creates the new evaluator only after the human approves.
  If the evaluator ID isn't given, ask for it after triggering. Do NOT use to
  build an evaluator from scratch (use orq-build-evaluator) or to fix failures
  with prompt tweaks (use orq-improve-agent).
allowed-tools: Read, Write, Edit, Grep, Glob, Bash(uv run:*), AskUserQuestion
---

# Evaluator Alignment

You are running a guided session that makes an LLM judge agree with the person you're
talking to. You do the mechanics; **they make every decision that costs money or
changes something in their workspace** — rewriting the prompt, creating the new judge,
re-running the test. Never skip a gate.

**How to talk to the user.** They know their domain; they may know nothing about
judges, entropy, or flip rates, and they don't need to. Describe what is happening in
terms of what the judge *did* — "it gave a different answer 6 times out of 8 on this
one" — not in terms of the metric that measured it. Keep the internal vocabulary
("confuser", "grey zone", "instability band", "open-code", "conductor") out of what
you say to them; it's fine in the artifacts. At each step, one or two sentences on
what you're about to do and why, then do it. Offer options as short lists they can
answer in a few words.

Mechanically: you run small independent scripts under `scripts/`, each writing one
file into a per-run working directory (`runs/<key>_<ts>/`, renamed to
`runs/<key>_<ts>_<model>_<N>dp/` only once a trace scan has resolved the judge model
and datapoint count).

Each script under `scripts/` is self-contained: it declares its own dependencies
via PEP 723 inline metadata, so `uv run scripts/<name>.py ...` builds an isolated,
cached environment on first run — no `uv sync`, no project venv, no repo needed.
Always invoke as `uv run scripts/<name>.py` (not `uv run python scripts/...`, which
bypasses the inline metadata).

> **TLS-intercepting antivirus / corporate proxy:** the first run of each script
> reaches PyPI to build its env. Behind SSL-inspecting AV (e.g. Norton) or a
> corporate proxy that re-signs HTTPS, `uv` fails with `invalid peer certificate:
> UnknownIssuer`. Add `--system-certs` so `uv` trusts the OS certificate store:
> `uv run --system-certs scripts/<name>.py ...`. Only the first (uncached) build
> per script needs it.

## Constraints

- **Multi-type.** The whole flow — measurement (stability → instability metrics →
  confuser ranking) and the improve half (score → recommend → rewrite → create →
  retest) — supports **boolean, categorical, and numeric** judges (RES-978 Part 1 +
  Part 2 / RES-980). Instability is one 0..1 scale (boolean flip-rate, categorical
  label entropy, numeric score spread); the rewrite preserves the evaluator's verdict
  space (label set / numeric scale). Numeric rewriting is deliberately shallow — it
  nudges the scale's anchor descriptions, not a calibration model. Step 1 accepts
  those three output types and fails fast on anything else, which includes orq's
  free-form `string` type: exact-match entropy over prose scores nearly every row as
  maximally unstable so it cannot rank confusers, and gate (b) at step 8 has no
  mechanical way to compare two correct answers that are worded differently. Refusing
  it at step 1 keeps a type that cannot be created at step 7 from being accepted at
  step 1 — the same list gates both (`orq_client.SUPPORTED_OUTPUT_TYPES`).
- **Consistency is a ceiling, not proof.** A steady judge is *reproducible*, not
  *right* — it can be wrong the same way every time. You find the examples it's
  unsure about; only the user can say what the answer should be.
- **The blind spot, which you must say out loud — unless you measured it.** This
  method finds examples the judge wavers on, so on its own it structurally cannot
  find the ones it gets wrong *with total confidence*. Two branches, and using the
  wrong one either overclaims or throws away the best result in the run:
  - **No ground-truth labels** (the usual trace-scanned run): state the limitation
    in the final summary every time, and offer the stable spot-check sample (config
    `low_flip_sample_size`) as the cheap partial check.
  - **Rows carried a `reference` label** (a dataset with `expected_output`, and the
    evaluator does not treat it as judge input): `metrics.json`'s `correctness` block
    has `n_labelled > 0` and the limitation **does not apply to the rows it covers**.
    Say what was actually verified and on how many rows — *"20 of 20 correct,
    including all 20 the judge was completely steady on"* — rather than reciting a
    caveat you have the data to retire. `by_band.stable` is the number that matters,
    but it only earns "the blind spot, measured" once it covers at least 10 rows and
    at least 90% of that band — below that floor it's a partial view, and the caveat
    still holds for the gap. Still name the labels as `dataset_reference` ([step 8](resources/retest.md))
    — they are someone's prior judgement, not the user's verdict — and keep the
    caveat for whatever rows were unlabelled.

## The flow

Ten steps, seven of them gated on the user. **Read the step's resource file before
you start that step** — the rules for each step live there, not here. Don't load them
all up front; load the next one when you get to it.

| Step | What happens | Gate | Read |
|---|---|---|---|
| 0 | Does a judge exist in orq? Route to `orq-build-evaluator` if not | ⟵ GATE | [resources/judge-and-examples.md](resources/judge-and-examples.md) |
| 1 | `fetch_evaluator.py` — confirm the judge, its variables and model | | same file |
| 1a | Where do the examples come from: traces, dataset, their own, generated | ⟵ GATE | same file |
| 2 | Agree repeats, examples, temperature, judge slug; `estimate_cost.py` | ⟵ GATE | [resources/measure.md](resources/measure.md) |
| 3 | `stability.py` — repeat the judge, write `stability.json` + `metrics.json` | | same file |
| 4 | Report consistency as behaviour; correctness if labels exist | | same file |
| 5 | How many unstable examples to review; `build_queue.py` | ⟵ GATE | same file |
| 6 | Group the hard examples, ask 1–5 rule questions, write the policy | ⟵ GATE | [resources/grey-zone.md](resources/grey-zone.md) |
| 7 | `rewrite_eval.py`, show the diff, `create_eval.py --approve` only on yes | ⟵ GATE | [resources/rewrite-and-create.md](resources/rewrite-and-create.md) |
| 8 | Optional `retest.py`: steadier **and** agreeing with the user? | ⟵ GATE | [resources/retest.md](resources/retest.md) |

Reference, load only when needed:
- [resources/configuration.md](resources/configuration.md) — `config.toml` keys, the
  rewrite-model backends, every script's flags.
- [resources/run-directory.md](resources/run-directory.md) — every artifact in the run
  directory, and why `source_index` must not move after labelling.

## Final summary
Tell them, in plain terms:
- **what changed in the judge and why** — tie it back to the answers they gave;
- **whether it actually got better**, on how many examples, and on both counts from
  step 8 (steadier, and agreeing with them);
- **how it does on the rare answer.** If one label is scarce (usually fail), say how many
  of those it catches before any overall accuracy — 90% accurate can mean it never
  says fail at all. Quote `cohen_kappa` when it tells a different story than accuracy;
- **what this did not check.** Everything here was measured on examples the judge was
  *unsure* about. A judge that is confidently wrong never wobbles, so it never showed
  up. Say this even when the numbers are good — especially then. Say what the stable
  spot-check examples showed (step 6.6, [grey-zone.md](resources/grey-zone.md)) and whether the retest re-judged them, and
  suggest re-running periodically.
- **why the improvement number is an upper bound.** The same examples produced the
  guidance for the rewrite and the labels that scored it, with no holdout; the rows
  were picked for maximum instability, so some of the drop is regression to the mean
  unless `--baseline_rerun` was used; and any label the user didn't confirm is your
  application of their rule. `retest_metrics.json` `caveats` lists whichever of these
  apply — none of them is optional to mention.
- if any examples came from step 1a options 2–4, say that too: they test what the
  rubric says, not what production actually sends.


## Companion Skills

- `orq-build-evaluator` — build the judge in the first place; come back here when it disagrees with humans.
- `evaluatorq` — run a judge from code. A code-defined `llm_jury()` panel is the other answer to an unstable judge: instead of rewriting one judge's prompt, poll several and read `raw_agreement` / Krippendorff's alpha. Alignment against human labels is still this skill's job — a panel that agrees with itself can be uniformly wrong.
- **orq-cli** — the same platform operations from a shell, for anything that must run again without an agent present (CI, cron, scripts, bulk): auth via `ORQ_API_KEY`, `-o json` output. See its "MCP tools or the CLI?" table before choosing.
