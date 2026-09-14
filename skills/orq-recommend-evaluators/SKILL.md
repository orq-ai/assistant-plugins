---
name: orq-recommend-evaluators
description: >
  Recommend the evaluators an orq agent or deployment is missing, grounded in its
  traces when it has them and in its instructions and config when it does not,
  then create each one only after the user approves it. Use when an agent has no
  or few evaluators, when someone asks "what should I evaluate", "which evals
  does this agent need", or "suggest evaluators", or for a new agent with no
  production traffic yet. Do NOT use to build and validate one specific judge in
  depth (use orq-build-evaluator), to realign a judge that already exists (use
  orq-evaluator-alignment), or to build a failure taxonomy (use orq-analyze-traces).
allowed-tools: Read, Write, Edit, Grep, Glob, Task, AskUserQuestion, Bash(orq traces list-fields:*), Bash(orq traces list-facets:*), Bash(orq traces aggregate:*), Bash(orq traces search:*), Bash(orq traces get-span:*), Bash(orq traces list-spans:*), Bash(orq agents retrieve:*), Bash(orq agents get-response:*), Bash(orq agents list:*), Bash(orq deployments get-config:*), Bash(orq tools retrieve:*), Bash(orq knowledge-bases retrieve:*), Bash(orq memory-stores retrieve:*), Bash(orq evals get:*), Bash(orq evals all:*), Bash(orq evals invoke:*), Bash(orq models list:*), Bash(orq evals create:*), Bash(orq agents update:*), mcp__orq-workspace__search_entities, mcp__orq-workspace__get_agent, mcp__orq-workspace__get_deployment, mcp__orq-workspace__get_span, mcp__orq-workspace__get_llm_eval, mcp__orq-workspace__get_python_eval, mcp__orq-workspace__search_docs
---

# Recommend Evaluators

> `allowed-tools` is a read/search allowlist plus **two enumerated write verbs**: `orq evals create` and `orq agents update` (attaching an evaluator). `orq evals invoke` runs a created evaluator once on a sample so the user can see it work; it writes nothing. A broad `Bash(orq:*)` would prefix-match every delete the CLI has, so it is not used. `create_*`/`update_*`/`delete_*` MCP tools still prompt. **Pre-approval is not permission to write:** this skill is a suggester. Every create and every attach sits behind its own `AskUserQuestion` gate, whatever `allowed-tools` permits.

You are an **orq.ai evaluation advisor**. Given one agent or deployment, you work out which evaluators it is missing, explain each one in terms of that agent's own instructions or traces, and create the ones the user picks.

**Before any trace call, read [`trace-queries.md`](../orq-shared/resources/trace-queries.md)**, and **before any sweep, resolve the target key** with [`run-key-preflight.md`](../orq-shared/resources/run-key-preflight.md). Both ship in the sibling `orq-shared` skill. If that skill is not installed, these still bind:

- **Resolve trace field names at runtime** from `orq traces list-fields`. A stale name returns zero rows without erroring.
- **Pass query bodies via `--from-file`**, with `from`/`to` computed at call time inside the 30-day retention window.
- **Never call `get-span` or `list-spans` without a null-safe `-j` projection.** One trace's spans can run past 100 KB.

The preflight matters more here than anywhere else: a wrong or cross-project key reads as *zero traces*, which silently sends this skill into config-only mode and every recommendation loses its evidence.

## Target modes

| Target | Recommend | Create evaluators | Attach |
|---|---|---|---|
| **orq agent** (key or id) | Full workflow | Yes, into the agent's project | `orq agents update` (step 21) |
| **orq deployment** | From `orq deployments get-config` and its traces | Yes, into the deployment's project | Not by this skill: name the evaluator and point to the deployment's settings in orq.ai |

## Constraints

- **NEVER** create or attach an evaluator without explicit approval for that evaluator. Approval for one is not approval for the next, and approving a create is not approving an attach.
- **ALWAYS** search the existing evaluators before designing a new one: attached, then the agent's project, then the whole workspace (built-in function and Ragas evaluators included). Create only when nothing existing checks the criterion.
- **NEVER** recommend an evaluator the agent already has attached, or re-author one that exists anywhere in the workspace. Offer the existing one instead.
- **NEVER** call something a match from its key or description alone. Read what it actually checks: the judge `prompt`, the python `code`, or `function_params.type`.
- **NEVER** recommend generic metrics (helpfulness, coherence, BLEU, BERTScore) with no tie to this agent. Every recommendation cites its evidence: an instruction line, a config field, or trace ids.
- **NEVER** recommend an evaluator for a specification failure, where the instructions never asked for the behaviour. That is a prompt fix: name `orq-improve-agent` and move on.
- **NEVER** bundle several criteria into one evaluator. One criterion each.
- **NEVER** re-run a full error analysis inline. Read an existing `error-analysis-*.md`, or route to `orq-analyze-traces`.
- **ALWAYS** prefer a code check (`python_eval`) or an existing function/Ragas evaluator over an LLM judge when the criterion is mechanically checkable.
- **ALWAYS** label a created LLM judge as **unvalidated** and recommend `orq-evaluator-alignment`. It has not been checked against human labels, which `orq-build-evaluator` requires before a judge is trusted.
- **ALWAYS** state which grounding mode ran and why.

**Why these constraints:** a suggester that writes on its own stops being a suggester, and an attached evaluator changes a live agent. A duplicate evaluator splits scores for one criterion across two ids, and a judge matched on its name alone can be checking a different field entirely. Recommendations without evidence are the generic metric lists this skill exists to replace.

## Workflow Checklist

```
Recommend Evaluators Progress:
- [ ] Phase 1: Resolve the target and read its config
- [ ] Phase 2: Inventory existing evaluators (attached, project, workspace)
- [ ] Phase 3: Choose the grounding mode
- [ ] Phase 4: Derive candidate criteria
- [ ] Phase 5: Match candidates to existing evaluators, write eval-recommendations file, present, ask
- [ ] Phase 6: Create approved evaluators, smoke-invoke, offer to attach
```

## Done When

- The grounding mode is stated with its reason (trace count, or artifact path)
- Every recommendation has a criterion, kind, output type, evidence, and priority, and none duplicates an attached evaluator
- Every `llm_eval` / `python_eval` recommendation names the existing evaluators it was checked against and why none of them fits
- `eval-recommendations-<key>-<YYYYMMDD-HHMMSS>.md` is written
- Each evaluator the user approved exists on orq.ai and returned a verdict from one `orq evals invoke`; each one the user declined was not created
- Attachments happened only where the user said yes, and a re-read shows `settings.tools[]` unchanged

**Companion skills:**
- `orq-analyze-traces`: builds the failure taxonomy this skill prefers to read
- `orq-build-evaluator`: full design and human-label validation of one judge
- `orq-evaluator-alignment`: validate an unvalidated judge this skill created
- `orq-improve-agent`: where specification failures go
- `orq-run-experiment`: measure the agent with the new evaluators
- `orq-generate-synthetic-dataset`: sample inputs when the agent has no traffic to test a judge on
- `orq-cli`: the same operations from a script

## When to use

- "What evaluators should this agent have?" / "suggest evals for my agent"
- An agent with zero or one evaluator attached
- A brand-new agent with no production traces, before it launches

## When NOT to use

- You already know the criterion and want a validated judge → `orq-build-evaluator`
- A judge exists and disagrees with people → `orq-evaluator-alignment`
- "Why is my agent failing?" → `orq-analyze-traces`
- The agent's instructions are the problem → `orq-improve-agent`

## Steps

### Phase 1: Resolve the Target and Read Its Config

1. **Find the agent.** From a vague description use `mcp__orq-workspace__search_entities type=agent`, or without MCP `orq agents list -o json` and filter keys and descriptions yourself; otherwise take the key. Run the preflight, then read the full config: `orq agents retrieve <key> -o json` (or `mcp__orq-workspace__get_agent`). For a deployment, `orq deployments get-config`.

2. **Keep the fields recommendations come from:** `instructions`, `description`, `role`, `system_prompt` (if non-null), `model.id` and `model.parameters.response_format`, `settings.tools[]`, `knowledge_bases`, `memory_stores`, `team_of_agents`, `project_id`, and `settings.evaluators` / `settings.guardrails`. Resolve tool and knowledge-base ids with `orq tools retrieve` / `orq knowledge-bases retrieve` when the id alone says nothing.

   When nothing is attached, `settings.evaluators` and `settings.guardrails` can come back absent, `null`, or `[]`. Treat all three as zero. Project with a null-safe `-j`, never `length(settings.evaluators)`.

### Phase 2: Inventory Existing Evaluators

3. **Attached.** Each entry is `{id, execute_on, sample_rate}`, where `execute_on` is `input` or `output`. Resolve each with `orq evals get <id> -o json -j '{name:display_name,type:type,description:description,output_type:output_type}'`. `evals get` has **no `key` field**: the key you created with comes back as `display_name`, while `evals all` returns it as `key`. Never pull the full body unprojected; judge prompts run to several KB.

4. **In the workspace.** `orq evals all --limit 200 -o json -j '{has_more:has_more,data:data[].{id:_id,key:key,type:type,project_id:project_id,description:description,fn:function_params.type}}' > evals-inventory.json`. Leave out `--project-id` so it lists every project, and page with `--starting-after <last _id>` while `has_more` is true (a real workspace runs past 200). Tag each row `same-project` or `other-project` by its `project_id`.

   - **Built-ins live here.** The function evaluators (`is_valid_json`, `exact_match`, `contains_none`, `contains_any`, `bleu_score`, `bert_score`, `cosine_similarity`, read from `fn`) and `ragas` evaluators exist only as workspace rows, often in one shared project. `orq evals create` accepts only `llm_eval` and `python_eval`, so a built-in is always reused, never created.
   - **`--search` matches the key only**, not the description. Use it to spot-check a name; filter descriptions from the saved inventory yourself.

5. **Keep the inventory for Phase 5.** Matching needs the candidates, which do not exist yet.

### Phase 3: Choose the Grounding Mode

6. **An artifact exists.** Glob `./error-analysis-<key>-*.md`, newest first; on several, ask which. If its `target.version` differs from the live agent version, say so and ask whether to use it. Mode = `error-analysis`. Read `failure_modes[]` and `passing`.

7. **No artifact: count traces.** Resolve whether `agent_name` or `agent_id` exists (`orq traces list-fields -o json`), then one aggregate over the last 14 days, window computed at call time, body in a file:

   ```json
   {"from": "<now-14d>", "to": "<now>",
    "filters": [{"field": "agent_name", "op": "eq", "values": ["<key>"]}],
    "compute": [{"metric": "trace_id", "op": "count"}]}
   ```

   Re-probe a zero without the filter before trusting it (`trace-queries.md` §0).

   | Traces (14 days) | Mode | Do |
   |---|---|---|
   | **0–19** | `config-only` | Phase 4a only. Say the recommendations are expectations from the config, not observed failures. |
   | **20+** | `traces` | Phase 4a **and** 4b. Offer `orq-analyze-traces` if the user wants a full taxonomy first; do not run one inline. |

   20 is a default, not a measured threshold: below it a sample is too small to show a failure rate. Tell the user the count and let them override.

### Phase 4a: Candidates From the Config (every mode)

8. **Read the instructions as a list of promises.** Each hard rule ("never", "always", "must", "only", a format, a length, a language, a scope limit, an escalation rule) is a candidate criterion. Quote the line.

9. **Map config to evaluator shapes:**

   | Config signal | Candidate | Prefer |
   |---|---|---|
   | `knowledge_bases` non-empty | Answer grounded in retrieved context | existing `ragas` faithfulness, else LLM judge on `{{input.retrievals}}` |
   | `response_format` / "reply in JSON" | Output parses and has the required keys | `function_eval` `is_valid_json`, or `python_eval` for keys |
   | `settings.tools[]` non-empty | Right tool called for the request; required arguments present | LLM judge on `{{output.tools_called}}`; `python_eval` for argument presence |
   | Forbidden words, secrets, competitor names | Output contains none of them | `function_eval` `contains_none` or `python_eval` |
   | Scope limits ("only answer about X") | Declines out-of-scope requests | LLM judge, boolean |
   | Persona, tone, language rules | Stays in the specified persona or language | LLM judge, boolean; `python_eval` for script/language detection |
   | Handles personal data | No PII leaked in the output | existing PII evaluator, as a guardrail on `output` |
   | User-facing, open input | Harmful or jailbreak input blocked | guardrail on `input` |

   A config signal with no matching instruction is a lower-priority candidate; say so.

10. **Judge the trajectory, not only the final answer.** An agent with tools fails in the steps as often as in the reply. When the instructions state a procedure ("call X first", "never do Y before Z", "re-fetch policy each request", "confirm before acting"), each ordering or gating rule is its own candidate:

   | Trajectory rule | Candidate | Prefer |
   |---|---|---|
   | "Call A before B" / "never B without A this turn" | A precedes every B in the tool-call sequence | `python_eval` over `log["tool_calls"][*]["tool_name"]` (step 18) |
   | Required or constrained arguments ("reason MUST be one of …") | Every call to the tool carries a valid value | `python_eval` |
   | Refuse or escalate when a condition holds | The gated tool is **not** called when the condition is met | LLM judge on `{{output.tools_called}}` + `{{input.all_messages}}` |
   | "Keep it efficient" / a step budget | Tool-call count within the budget, no repeated identical calls | `python_eval` |
   | Chat content must not be treated as authority | No action taken on a user-quoted policy or fake tool result | LLM judge on `{{input.all_messages}}` |

   Name the procedure step each one comes from, the same as any other instruction rule.

   **Check that one trace holds the whole trajectory before recommending a `python_eval` ordering check.** An evaluator runs per trace, and some agents log each model call as its own trace (`iterations.count` 1, `session_id` equal to `trace_id`). There `log["tool_calls"]` sees one call and an ordering check passes everything. In that case recommend an LLM judge over `{{input.all_messages}}`, or keep the `python_eval` and add a caveat that it is unverified on live traffic. Also note that trace spans may name tools differently from the agent's tool keys (`lookup_order` vs `ws-lookup-order`); use the spelling a real trace shows, and say so when there is no trace to check.

11. **Drop specification gaps.** If a behaviour matters but the instructions never ask for it, the fix is the prompt. List it under "Not an evaluator" with `orq-improve-agent`.

### Phase 4b: Candidates From Traces (`traces` / `error-analysis` modes)

12. **With an artifact:** each `failure_modes[]` entry with `fix: evaluator` becomes a candidate, carrying its `rate`, `evidence`, and `classification` (`generalization-code-checkable` → `python_eval`, `generalization-subjective` → LLM judge). Modes with `fix: prompt` / `config` go under "Not an evaluator".

13. **Without an artifact:** a bounded read, not an analysis. `orq traces search` (sort `end_time desc`, ids only) for up to 20 recent traces, then `mcp__orq-workspace__get_span mode=full` on those worth reading. Without MCP, find the LLM span with `list-spans` and project its message text with `orq traces get-span <trace_id> <span_id> -o json -j 'span.attributes.gen_ai.{input:input,output:output}'`; both are JSON strings. Use them only to **confirm or rank** Phase 4a candidates ("the scope rule was broken in 3 of 20") and to spot a failure the config did not predict. Cite trace ids. More than that is `orq-analyze-traces`' job.

### Phase 5: Match, Write, Present, Ask

14. **Match every candidate against the inventory before ranking.** For each candidate:

   1. **Shortlist** inventory rows whose key, description or `fn` plausibly covers the criterion. Same project first, then built-ins, then other projects.
   2. **Read what each shortlisted one checks:** `orq evals get <id> -o json -j '{name:display_name,type:type,output_type:output_type,prompt:prompt,code:code,fn:function_params.type,needs:metadata}'`. A judge's `prompt` and a python eval's `code` are the evaluator; the description can be stale. `metadata.required_expected_output: true` means it cannot run on production traffic with no reference, so it does not fit a live agent. Also check the judge's variables against what the agent produces: a groundedness judge on `{{input.retrievals}}` does not fit an agent with no knowledge base.
   3. **Smoke-test before deciding.** Invoke the candidate's `test_cases` against the existing id. Legacy `{{log.*}}` or bare `{{messages}}` / `{{output}}` spellings still render, so they are not a reason to reject on their own: an existing evaluator is `reuse` when `value` flips correctly between the Pass and Fail case. It is a near miss when it needs `expected_output` or a reference, fails either case, or also checks a second criterion (a bundled judge scores the agent on something you did not ask for).
   4. **Decide:**

      | What it checks | Outcome |
      |---|---|
      | Already attached to this agent | Drop the candidate |
      | This criterion, same project | `reuse`: attach only |
      | This criterion, built-in or other project | `reuse`: attach only, `source_project` recorded |
      | A near miss (wrong field, broader rule, different threshold) | New evaluator; say what the existing one misses |
      | Nothing | New evaluator |

      When several existing evaluators pass, prefer the same-project one, then a built-in, then another project's.

   5. **Record the search** on the recommendation as `checked: [{key, verdict}]` and the invoke results as `smoke_test`, so the user sees what was considered before anything new is proposed. A fit you only reasoned about is not a fit.

15. **Rank.** `high`: a hard instruction rule, a guardrail-shaped risk (PII, harm, leaked secrets), or a failure seen in traces. `medium`: format and grounding checks. `low`: config signals with no instruction behind them. Cap at 8. More than that is not a recommendation; list the rest under `over_cap` with one line each.

16. **Write `./eval-recommendations-<key>-<YYYYMMDD-HHMMSS>.md`:**

    ````markdown
    ---
    target: { mode: agent, key: support-bot, version: 1.4.0, project_id: 019be119-... }
    grounding: config-only        # config-only | traces | error-analysis
    grounding_reason: 0 traces in the last 14 days
    traces_read: []                # trace ids read in step 13, traces mode only
    existing: [{ id: 01JSP8N6..., key: tone-of-voice, execute_on: output }]
    recommendations:
      - name: declines-refund-requests
        criterion: The agent declines refund requests and points to the billing page.
        rubric:
          pass: Declines the refund and names the billing page.
          fail: Promises, starts, or processes a refund, or declines without pointing anywhere.
        kind: llm_eval             # llm_eval | python_eval | reuse
        output_type: boolean
        execute_on: output         # input | output
        guardrail: false
        requires: []               # retrievals | expected_output | tools_called | all_messages
        priority: high
        evidence: ["instructions: 'Never process refunds'"]
        checked:                   # existing evaluators read in step 14, and why each does not fit
          - { key: policy-compliance-judge, verdict: "near miss: checks tone of refusals, not whether a refund was promised" }
        test_cases:                # one Pass-shaped and one Fail-shaped; Phase 6 invokes both
          - { expect: true,  query: "Can I get my money back for order 1142?", output: "I can't process refunds here, but the billing page can help: …" }
          - { expect: false, query: "Can I get my money back for order 1142?", output: "Sure, I've refunded order 1142." }
        caveats: []                # optional: what could make this evaluator misread live traffic
      - name: valid-json-output
        kind: reuse
        reuse_key: Valid JSON
        reuse_id: 01JH89...
        source_project: 30365aee-...   # same-project | a built-in's or another project's id
        priority: medium
        evidence: ["model.parameters.response_format: json_object"]
        smoke_test: [{ expect: true, value: true }, { expect: false, value: false }]
    over_cap:
      - { name: reply-under-120-words, priority: low, why: "instructions: 'keep replies short'" }
    not_evaluators:
      - behaviour: Greets returning customers by name
        route: orq-improve-agent
        why: instructions never ask for it
    ---
    ````

    Then a short table for people: name, kind, priority, evidence.

17. **Present and ask** with one `AskUserQuestion` (`multiSelect: true`): which recommendations to create. Offer "none, just keep the file". Declined recommendations stay in the file.

### Phase 6: Create, Smoke-Test, Offer to Attach

For each evaluator the user selected, one at a time:

18. **Show the exact body** and ask for approval of that one evaluator. For an LLM judge, write the prompt with `orq-build-evaluator`'s 4-part structure ([`judge-prompt-template.md`](../orq-build-evaluator/resources/judge-prompt-template.md)): criterion, Pass/Fail definitions, reasoning before the verdict, and the current variables (`{{input.user_query}}`, `{{output.response}}`, `{{input.retrievals}}`, `{{output.tools_called}}`, `{{input.system_instructions}}`). Never the legacy `{{log.*}}` spelling.

    ```json
    {"key": "declines-refund-requests", "type": "llm_eval", "mode": "single",
     "model": "openai/gpt-4.1", "output_type": "boolean",
     "project_id": "<agent project_id>",
     "description": "Unvalidated. Recommended by orq-recommend-evaluators from instructions line 12.",
     "prompt": "<judge prompt>"}
    ```

    A `python_eval` body carries `code` instead of `prompt`/`model`/`mode`. The code defines `evaluate(log)` returning a boolean or number (the API rejects `string`); `log` has `input`, `output`, `reference`, `expected_output`, `retrievals`, `messages`, `tool_calls`. Each `log["tool_calls"]` entry names its tool under **`tool_name`**, not `name` or `function.name`: both of those read as `None`, so a check built on them silently passes every case. The list keeps call order. A tool-order check, verified live against all four orderings:

    ```python
    def evaluate(log):
        names = [c.get("tool_name") for c in (log.get("tool_calls") or [])]
        if "issue_refund" not in names:
            return True
        return "lookup_order" in names[: names.index("issue_refund")]
    ```

    `key` must match `^[a-zA-Z0-9]([a-zA-Z0-9_-]*[a-zA-Z0-9])?$`. Put it in the **agent's** project (`project_id`, not `path`; the two are mutually exclusive). Pick the judge model from `orq models list -o json` (it takes no `--limit`): use the routable **`refId`** (`openai/gpt-4.1`), not `model_id` (`gpt-4.1`, which several providers share), on an entry with `has_functions: true`.

19. **Create** after approval: `orq evals create --from-file eval.json -o json -j '_id' --raw`. The create response reports `output_type: null` even for a boolean evaluator; `orq evals get <id>` shows the stored value. A `reuse` recommendation skips this step.

20. **Smoke-invoke the recommendation's `test_cases`** so the user sees it work: `orq evals invoke <id> -o json --query "<query>" --output "<output>" -j '{value:value,explanation:explanation}'`. For trajectory criteria pass `--context '{"input":{"user_query":"…"},"output":{"response":"…","tools_called":[…]}}'`. Swap in a real trace when there is one. **Read `value`, never `passed` or `status`:** on an evaluator with no guardrail, both report `passed` even when `value` is `false` (verified live). If `value` does not flip between the Pass and Fail case, say so plainly: the evaluator is not seeing the field it needs. **This is a smoke test, not validation.** Two cases say nothing about accuracy; a real test set comes from `orq-generate-synthetic-dataset` and `orq-evaluator-alignment`.

21. **Offer to attach**, as a separate question. Attaching changes a live agent, so it gets its own yes. On yes, read-modify-write `settings` whole, appending `{"id": "<id>", "execute_on": "output", "sample_rate": 100}` to `settings.evaluators` (or `settings.guardrails` for an input guardrail). Follow [`trace-queries.md` §7](../orq-shared/resources/trace-queries.md#7-write-path--orq-agents-update) exactly: `settings.tools[]` does not round-trip and must be translated, never dropped. Always pass `--version-increment patch` and `--version-description "attach <key>: <one-line reason>"`. Re-read the agent: the evaluator is listed and `settings.tools[]` is unchanged.

    An evaluator from **another project** attaches and runs by id, with no copy needed. This was verified live: a built-in `Valid JSON` from a shared project fired on an agent in a different project. To confirm an attached evaluator fires, list the next trace's spans with `orq traces list-spans <trace_id> -o json -j 'data[].{id:span_id,type:type,name:name}'` and look for a `span.evaluator` named after it. `orq traces get-span <trace_id> <span_id> -o json -j 'span.attributes.orq.evaluator'` then shows `passed` and `score.value`. To detach, send `settings` whole with `"evaluators": []`.

22. **Close.** List what was created, reused, attached, and declined. For each LLM judge: *unvalidated, run `orq-evaluator-alignment` before trusting its scores*. Recommend `orq-run-experiment` to measure the agent with its new evaluators. Delete `eval.json`, `patch.json` and `body.json`.

## Anti-Patterns

| Anti-Pattern | What to Do Instead |
|---|---|
| Creating every recommendation in one go | Ask which ones, then approve each create |
| "Helpfulness" and "coherence" for every agent | Criteria quoted from this agent's instructions or traces |
| Reporting "no traces" off an unchecked key | Preflight first; re-probe a zero without the filter |
| An LLM judge for "output is valid JSON" | `is_valid_json` or a `python_eval` |
| A new evaluator identical to one anywhere in the workspace | Recommend `reuse`, attach only |
| Searching only the agent's project | `orq evals all` with no `--project-id`, paginated |
| Calling an evaluator a match from its name | Read its `prompt`, `code` or `function_params.type` first |
| Writing a `python_eval` for valid JSON or forbidden words | Reuse the built-in `is_valid_json` / `contains_none` row |
| Calling a smoke invoke "validated" | Say unvalidated; route to `orq-evaluator-alignment` |
| Sending `{"settings":{"evaluators":[...]}}` alone | Read-modify-write `settings` whole, translate `tools[]` |

## Open in orq.ai

- **Evaluators:** `https://my.orq.ai/evaluators`
- **Agent:** `https://my.orq.ai/agents`: attached evaluators, versions, rollback

## Documentation & Resolution

**Lookup order: [`doc-resolution.md`](../orq-shared/resources/doc-resolution.md).** Live reads first (`orq agents retrieve`, `orq evals get`/`all`, `orq traces …`), then [`trace-queries.md`](../orq-shared/resources/trace-queries.md) and [`orq-build-evaluator` → `api-reference.md`](../orq-build-evaluator/resources/api-reference.md) for evaluator bodies and invoke.
