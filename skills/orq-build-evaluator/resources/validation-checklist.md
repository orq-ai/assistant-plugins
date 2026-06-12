# LLM-as-a-Judge Validation Checklist

Use this checklist to validate a judge evaluator before deploying it.

## Pre-Build Checks

- [ ] Confirmed this is a **generalization failure**, not a specification failure (prompt gap)
- [ ] Confirmed this **cannot be solved with code-based checks** (regex, assertions, execution tests)
- [ ] The criterion targets **exactly one failure mode**
- [ ] Have **100+ labeled traces** (human-annotated Pass/Fail for this specific criterion)
- [ ] Labeled data is split into **train / dev / test** (10-20% / 40-45% / 40-45%)
- [ ] Each split has a **balance of Pass and Fail** examples (target 30-50 of each in dev and test)
- [ ] No data leakage between splits

## Prompt Quality Checks

- [ ] Prompt targets **one criterion only** (not bundled)
- [ ] Output format is **binary Pass/Fail** (not a scale)
- [ ] **Fail definition** is precise and concrete
- [ ] **Pass definition** is precise and concrete
- [ ] Includes **2-8 few-shot examples** from the training split only
- [ ] Examples cover both clear Pass and clear Fail cases
- [ ] Output is **structured JSON** with "reasoning" and "answer"
- [ ] **Reasoning comes before answer** (chain-of-thought)
- [ ] No dev/test examples appear as few-shot examples in the prompt

## Alignment Checks (Dev Set)

- [ ] Ran judge on **full dev set**
- [ ] Computed **TPR** (true positive rate): _____%
- [ ] Computed **TNR** (true negative rate): _____%
- [ ] Inspected **false passes** — understood why judge missed failures
- [ ] Inspected **false fails** — understood why judge flagged good outputs
- [ ] Iterated prompt until TPR > 90% and TNR > 90% on dev set
- [ ] If alignment stalled: tried more capable model / decomposed criterion / added examples

## Final Validation (Test Set — ONE TIME ONLY)

- [ ] Ran finalized judge on **held-out test set** (never seen during development)
- [ ] **Final TPR**: _____% (target: > 85%)
- [ ] **Final TNR**: _____% (target: > 85%)
- [ ] **TPR + TNR - 1 > 0** (judge is better than random)
- [ ] Computed **corrected success rate** if applicable
- [ ] Computed **bootstrap 95% confidence interval** if applicable

## Documentation

- [ ] Criterion name and one-line description documented
- [ ] Pass/Fail definitions documented
- [ ] Judge model name and version documented
- [ ] Final TPR and TNR documented
- [ ] Number of labeled examples per split documented
- [ ] Known limitations or edge cases documented
- [ ] Maintenance cadence agreed (weekly re-alignment recommended)

## Ongoing Maintenance

- [ ] Plan to re-run alignment after significant pipeline changes
- [ ] Plan to label new production traces regularly
- [ ] Plan to recompute TPR/TNR and check CI width
- [ ] Plan to create new evaluators for new failure modes (not expand existing ones)
