# Data Split Guide for LLM-as-a-Judge

## Why Split Data?

Designing an LLM-as-Judge is like training a classifier — except "training" happens through prompt engineering, not parameter tuning. You need separate data for:

1. **Teaching** the judge (few-shot examples in the prompt)
2. **Iterating** on the prompt (development feedback)
3. **Reporting** unbiased accuracy (final evaluation)

## Recommended Split

| Split | % of Data | Purpose | Usage Rules |
|-------|-----------|---------|-------------|
| **Training** | 10-20% | Source of few-shot examples | Goes INTO the prompt. Clear-cut Pass/Fail cases. |
| **Dev** | 40-45% | Prompt refinement feedback | Run judge on this during iteration. NEVER put these in the prompt. |
| **Test** | 40-45% | Final unbiased accuracy | Hold out until prompt is FINALIZED. Run ONCE. |

## Minimum Sizes

- **Dev set**: 30-50 Pass + 30-50 Fail = 60-100 examples
- **Test set**: 30-50 Pass + 30-50 Fail = 60-100 examples
- **Training set**: 5-10 Pass + 5-10 Fail = 10-20 examples
- **Total minimum**: ~130-220 labeled examples

In-context learning saturates after 1-8 examples, so the training set does not need to be large.

## Concrete Example

With 200 labeled traces (100 Pass, 100 Fail):

| Split | Pass | Fail | Total |
|-------|------|------|-------|
| Training | 10 | 10 | 20 |
| Dev | 45 | 45 | 90 |
| Test | 45 | 45 | 90 |

## Critical Rules

1. **No overlap between splits.** A trace in the training set MUST NOT appear in dev or test.
2. **Balance Pass and Fail** in each split. Imbalanced splits give misleading TPR/TNR.
3. **Training set examples = clear-cut cases.** Save the ambiguous/borderline cases for dev and test.
4. **Dev set is for iteration.** You will look at these many times. Expect your accuracy numbers on dev to be slightly optimistic.
5. **Test set is sacred.** Look at it ONCE, after the prompt is finalized. The accuracy numbers from test are the ones you report.

## What If I Don't Have Enough Labeled Data?

If you have fewer than 100 labeled traces:

1. **Start with error analysis first** — read 100+ traces, annotate what's wrong
2. **Generate synthetic data** using the structured dimension/tuple approach
3. **Use collaborative annotation** — 2+ annotators, measure Cohen's Kappa (target >= 0.6)
4. **Only build the judge** once you have sufficient labeled data

Building a judge without enough labeled data means you cannot validate it. An unvalidated judge may not reflect your actual quality criteria.

## Updating Splits Over Time

As you label new production traces:
- Add examples to ALL three splits proportionally
- Re-run alignment on the new dev set
- Re-run final validation on the new test set
- This is not a one-off task — it's part of the ongoing lifecycle
