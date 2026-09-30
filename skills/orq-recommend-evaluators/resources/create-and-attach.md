# Create, smoke-test, attach (steps 18–21)

Every create, repair and attach in this file happens only after the user approves that one action (SKILL.md, Constraints).

## Step 18: the create body

**LLM judge.** Write the prompt with `orq-build-evaluator`'s 4-part structure ([`judge-prompt-template.md`](../../orq-build-evaluator/resources/judge-prompt-template.md)): criterion, Pass/Fail definitions, reasoning before the verdict, and the current variables (`{{input.user_query}}`, `{{output.response}}`, `{{input.retrievals}}`, `{{output.tools_called}}`, `{{input.system_instructions}}`).

```json
{"key": "declines-refund-requests", "type": "llm_eval", "mode": "single",
 "model": "openai/gpt-4.1", "output_type": "boolean",
 "project_id": "<agent project_id>",
 "description": "Unvalidated. Recommended by orq-recommend-evaluators from instructions line 12.",
 "prompt": "<judge prompt>"}
```

- `key` must match `^[a-zA-Z0-9]([a-zA-Z0-9_-]*[a-zA-Z0-9])?$`.
- Put it in the **agent's** project with `project_id`. `project_id` and `path` are mutually exclusive.
- **Judge model:** use `openai/gpt-4.1` once this check prints it: `orq models list -o json -j "[?refId=='openai/gpt-4.1'].refId" --raw`. The command takes no `--limit`, and unprojected it returns hundreds of entries. If the check prints nothing, pick another from `orq models list -o json -j "[?has_functions].refId"`. The response is a bare list, so a `data[...]` projection returns `null`. Use the routable `refId`, not `model_id`: several providers share a `model_id` such as `gpt-4.1`.

**`python_eval`.** The body carries `code` instead of `prompt`/`model`/`mode`. The `evaluate(log)` contract and the `log` keys are in [`orq-build-evaluator`](../../orq-build-evaluator/SKILL.md) ("Python evaluators"). Two traps fail silently:

- Each `log["tool_calls"]` entry names its tool under **`tool_name`**. `name` and `function.name` both read as `None`, so a check built on them passes every case. The list keeps call order.
- A count of calls is not a count of sources. An agent that scrapes one URL three times satisfies `len(tool_calls) >= 3` while breaking the rule. A coverage criterion counts distinct values of the identifying argument (`url`, `query`, `document_id`), read from each entry's `tool_arguments`. A 2026-09-29 `python_eval` invoke returned this as a parsed object; handle a string by parsing JSON if an older runtime supplies one.

A tool-order check, tested against all four orderings:

```python
def evaluate(log):
    names = [c.get("tool_name") for c in (log.get("tool_calls") or [])]
    if "issue_refund" not in names:
        return True
    return "lookup_order" in names[: names.index("issue_refund")]
```

## Step 19: create

```bash
orq evals create --from-file eval.json -o json -j '_id' --raw
```

The create response reports `output_type: null` even for a boolean evaluator. `orq evals get <id>` shows the stored value. Other `evals` quirks are in [`orq-cli`'s command map](../../orq-cli/resources/command-map.md).

## Step 20: smoke-invoke

Invoke the recommendation's `test_cases` with the invoke shapes in [`matching.md`](matching.md) section 3. Swap in a real trace when there is one.

1. **Read `value`.** On an evaluator with no guardrail, `passed` and `status` both report `passed` even when `value` is `false`.
2. **If `value` does not flip** between the Pass and Fail case, read the `explanation` first:
   - The judge names a field it could not find: it is not seeing that field.
   - The judge quotes a real problem in your Pass case: the judge is right, and the test case is what needs fixing (an unsourced claim, a missing section, a forbidden word). Rewrite the case and invoke again.
3. **The invoke fails on the model** (HTTP 500 from an end-of-life or unrouted model, or 403): ask, then repair with `orq evals update <id> --from-file eval.json` and invoke again. A judge needs a new `model`. A `python_eval` has no model, so a failure there means the code reads a field that is not in `log`; fix the `code`. If the user declines the repair, say plainly that `<key>` exists but does not run, and do not offer to attach it.

Two cases are a smoke test, not validation, and say nothing about accuracy. A real test set comes from `orq-generate-synthetic-dataset` and `orq-evaluator-alignment`. A step 15 flip on an *existing* evaluator makes it worth reusing, not proven right for this agent.

## Step 21: attach to an agent only

For a deployment, stop after smoke-invoke and give the evaluator key and id for attachment in the deployment's settings in orq.ai. The steps below apply only to an orq agent.

1. Read-modify-write `settings` whole, appending `{"id": "<id>", "execute_on": "output", "sample_rate": 100}` to `settings.evaluators`, or to `settings.guardrails` for an input guardrail.
2. Follow [`trace-queries.md` §7](../../orq-shared/resources/trace-queries.md#7-write-path--orq-agents-update) exactly. `settings.tools[]` does not round-trip and must be translated, never dropped.
3. Pass `--version-increment patch` and `--version-description "attach <key>: <one-line reason>"`.
4. Re-read the agent. The evaluator is listed, and `settings.tools[]` is unchanged.

An evaluator from another project attaches and runs by id, with no copy needed.

**Tell the user to open the agent from inside its project, not from an "All projects" view.** In that view, attached evaluators render as "Deleted Evaluator", and saving the agent from there can write back `settings.evaluators: []`, undoing the attach.

**To confirm an attached evaluator fires:** list the next trace's spans with `orq traces list-spans <trace_id> -o json -j 'data[].{id:span_id,type:type,name:name}'` and look for a `span.evaluator` named after it. Then `orq traces get-span <trace_id> <span_id> -o json -j 'span.attributes.orq.evaluator'` shows `passed` and `score.value`.

**To detach,** send `settings` whole with `"evaluators": []`.
