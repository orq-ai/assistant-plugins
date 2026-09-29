# Filter and rank (step 14)

An evaluator only measures: it scores responses after the fact and changes nothing unless someone acts on the score. Recommend one only where that score is worth watching. Order the list so that the cap never cuts a check more important than one it keeps.

## 1. Fix at the source first

If a strict `json_schema`, a tool setting (`requires_approval`), or removing a tool enforces the rule, put that fix under `not_evaluators` with `route: config`.

For a rule that guards an **irreversible action** (a refund, a sent message, a deleted record), also name the prompt or tool fix there. If you keep the evaluator, it only confirms that the fix holds, and it keeps the rank its consequence earns.

## 2. The bar

Keep a candidate only if at least one of these holds:

- Breaking the rule has a real consequence: security, legal or compliance, money, wrong facts given to a user, or a broken workflow.
- A failure was seen in traces or in the error-analysis artifact.
- The user acts on the output and is harmed when the rule breaks (booked on a stale price, sent somewhere unsafe, asked for a PIN).

Style and tone rules fail the bar, even when phrased as "always" and even when a `python_eval` could check them. Judge a format, language or "include X when relevant" rule by what happens when it breaks, not by how it is phrased:

- It passes when a program or person downstream depends on it (the agent's defined output structure, a routing label a parser reads), or when the user cannot act without it (a missing visa requirement gets a traveller refused at boarding).
- It fails when the reply is only less tidy or less complete.

Guardrail-shaped risks (PII, injection) with no instruction behind them fail the bar.

Write a one-line `consequence` for each candidate that passes. If you cannot name one, the candidate fails.

## 3. Sort by importance

Put each candidate in a severity tier:

- **Tier 1:** physical safety or health (unsafe areas, allergens, medical), security, legal or compliance, money.
- **Tier 2:** harm to a user who acts on the output, wrong facts given to a user.
- **Tier 3:** a broken workflow.

The agent's **core rule** moves up one tier. That is the rule the instructions mark as MUST, as critical, or as the agent's purpose (a coordinator that must always delegate), or the output structure its job is defined by. Mark at most two candidates as core. A core rule already in tier 1 stays in tier 1 and gains only on the `core_rule` key.

Then compare candidates on these keys, in this order, and stop at the first key that separates them:

1. `tier`
2. `seen_failing`: a failure in traces or the artifact beats none, and a higher observed rate beats a lower one.
3. `core_rule`: a core rule beats a non-core one.
4. `exposure`: a rule that applies to most conversations, or that the traffic is actively targeting (attack traces), beats a rare edge case.
5. `nothing_else`: no attached evaluator and no partial enforcement in the tool.
6. `instruction_order`: the earlier rule wins, so ties are never broken at random.

Number the result as `rank` (1 = most important). Record on each item, as `ranked_by`, the key that placed it above the next item. For the last recommendation, compare it with the first `optional` item that still cleared the bar. If that item was dropped by step 6 below rather than outranked, record the key that would have separated them and say so in `caveats`.

**Priority** follows the tier after the core-rule move, not the wording of the rule. "Always" or "never" alone never raises a candidate.

| Tier | Priority |
|---|---|
| 1, or tier 2 seen failing | `high` |
| 2, or tier 3 seen failing | `medium` |
| 3 | `low` |

## 4. Cap after sorting

The top 5 by rank are the recommendations. Put everything else under `optional`, in rank order, with one line each saying why: first what cleared the bar, then what failed it.

- Never swap a higher-ranked candidate out for a lower one because the lower one is easier to build or reuse.
- Never promote a candidate that failed the bar to fill a slot.

## 5. Prefer code

Before settling on an LLM judge, check whether the rule can be decided from message order, tool-call order or arguments, a regex, or a count. If it can, make it a `python_eval`.

## 6. Last cut: is it necessary?

For each survivor, say in one line who would read the score and what they would change when it drops. Move it to `optional` in any of these cases:

- The honest answer is "nobody would act on it".
- The score would be the same on almost every response, because nothing in this agent's traffic stresses the rule.
- A higher-ranked recommendation already covers the same failure from a different angle.

A list of 5 needs 5 distinct consequences to justify it. Two to four necessary checks is the normal result.
