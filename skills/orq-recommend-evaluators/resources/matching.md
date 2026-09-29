# Match candidates to existing evaluators (step 15)

Work top of the list first. For each candidate that made the cut in step 14, follow sections 1 to 5.

## 1. Shortlist

Shortlist inventory rows whose key, description or `fn` plausibly covers the criterion, same project first.

Then decide whether the criterion is **generic**: an evaluator for it could exist elsewhere. Generic criteria include valid JSON or a schema, forbidden or required words, PII, groundedness or faithfulness to retrieved context, toxicity, response language, and similarity to a reference.

- **Generic, and no same-project row looks plausible:** shortlist built-ins, then other projects' rows.
- **Generic, and every same-project row fails section 3** (a wrong field, a failed smoke case, an error): come back here once and shortlist built-ins and other projects before concluding "nothing fits". A generic criterion reaches a new evaluator only after the built-ins have been read.
- **Specific to this agent** (its tool order, its refund threshold, its escalation rule): shortlist within the agent's project only, and add the caveat "other projects not checked; criterion is specific to this agent".

## 2. Read what each one checks

```bash
orq evals get <id> -o json -j '{name:display_name,type:type,output_type:output_type,model:model,prompt:prompt,code:code,fn:function_params,guardrail:guardrail_config,needs:metadata}'
```

A judge's `prompt` and a python eval's `code` are the evaluator. The description can be stale.

- **A reference variable** (current or legacy spelling: `{{reference}}`, `{{log.reference}}`, `{{expected_output}}`) usually means the evaluator cannot run on production traffic, but not always: the variable may render empty while the other requirements still grade correctly. Treat it as a caveat to smoke-test, not a rejection, and record what goes vacuous without the reference.
- **Built-in `contains_any` / `contains_none`** carry a fixed keyword list in `function_params`, so they fit only those exact words.
- **Variables vs the agent.** A groundedness judge on `{{input.retrievals}}` does not fit an agent with no knowledge base.

## 3. Smoke-test before deciding

With several candidates and the Task tool available, run sections 2 and 3 for each candidate in its own subagent. Pass on the CLI rules (read-only, null-safe `-j`). Each subagent returns the candidate's drafted `test_cases`, and for every shortlisted evaluator its key, id, what it checks and the smoke results. Decide the verdicts (section 4) in the main context, after every subagent has returned.

Draft the candidate's Pass and Fail `test_cases` now (step 16 records them) and invoke both against the existing id:

```bash
orq evals invoke <id> -o json --query "<query>" --output "<output>" -j '{value:value,explanation:explanation}'
```

Invoke shapes by criterion:

- **Grounded in the agent's instructions** (no invented facts, follows the procedure): pass `--messages` with a system turn, via `--from-file` for anything long. With only `--query`/`--output`, such a judge fails correct answers.
- **Trajectory criteria, and anything beyond query and output:** take the body shape from [`api-reference.md` "Programmatic Invoke"](../../orq-build-evaluator/resources/api-reference.md), which documents `--context` against `--from-file`, the flat aliases and the variable names. Invoke-body `tools_called` entries are `{name, arguments, output}`, and `arguments` is a JSON **string**; an object returns HTTP 400. Inside a `python_eval`, the tested runtime exposed those calls in order as `log["tool_calls"]` entries with `tool_name` and parsed `tool_arguments` objects.
- **Retrievals and tool results:** pass them as a `retrievals` array in a flat `--from-file` body. A judge that checks figures against a tool's output reads the tool result from there.
- **HTTP 520:** transient. Retry once.

**An invoke that never completes** (a timeout, a dropped connection, a subagent that never returns) leaves that evaluator unchecked. The candidate gets the caveat "existing evaluator <key> could not be checked and may cover this". An invoke that completes with an error is different: it is "Errors on invoke" below.

Judge the result by `value`:

| Result | Verdict |
|---|---|
| Boolean: Pass gives `true`, Fail gives `false` | `reuse` |
| Number or inverted polarity (0/1, 1–5, `true` = attack found): the two cases land on opposite sides of a threshold you state | `reuse`, with `threshold` recorded |
| HTTP 500 (end-of-life model), 403 (scope), deleted model | Errors on invoke. No verdict came back, so it is not a near miss |
| Needs a reference, fails either case, scores empty input, or also checks a second criterion | Near miss |

A legacy variable spelling alone is no reason to reject, but legacy variables do not always render: `{{log.messages}}` (legacy) has rendered empty. Only the invoke tells you.

## 4. Decide

| What it checks | Outcome |
|---|---|
| Already attached to this agent and working | Drop the candidate |
| Already attached but broken (step 3) | Keep the candidate; recommend replacing the broken one |
| Errors on invoke | Not reusable; list under `unusable` so nobody attaches it |
| This criterion, same project | `reuse`: attach only |
| This criterion, built-in or other project | `reuse`: attach only, `source_project` recorded |
| A broader rule that still passes both smoke cases | `reuse`, with a caveat naming what else it scores |
| A near miss (wrong field, fails a smoke case, different threshold) | New evaluator; say what the existing one misses |
| Nothing | New evaluator. With an incomplete inventory (step 4), keep its caveat: "no match" is not "nothing exists" |

When several existing evaluators pass, prefer the same-project one, then a built-in, then another project's.

## 5. Record the search

Record it on the recommendation as `checked: [{key, verdict, smoke}]`, so the user sees what was considered before anything new is proposed. A fit you only reasoned about is not a fit. A rejection by reading is fine when the prompt or code plainly checks something else.

If matching drops a candidate (already attached and working), the highest-ranked `optional` candidate that cleared the bar moves up and is matched in turn. Do not smoke-test candidates that failed the bar.
