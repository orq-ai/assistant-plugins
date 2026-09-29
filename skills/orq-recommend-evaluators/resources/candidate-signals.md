# Candidate signals (steps 9 and 10)

## Config signal → candidate (step 9)

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
| `team_of_agents` non-empty | Coordinator delegates to the right sub-agent for the request | LLM judge on `{{output.tools_called}}` + `{{input.all_messages}}` |

A config signal with no matching instruction is a lower-priority candidate. Say so.

## Trajectory rule → candidate (step 10)

| Trajectory rule | Candidate | Prefer |
|---|---|---|
| "Call A before B" / "never B without A this turn" | A precedes every B in the tool-call sequence | `python_eval` over `log["tool_calls"][*]["tool_name"]` |
| Required or constrained arguments ("reason MUST be one of …") | Every call to the tool carries a valid value | `python_eval` |
| Refuse or escalate when a condition holds | The gated tool is **not** called when the condition is met | LLM judge on `{{output.tools_called}}` + `{{input.all_messages}}` |
| "Keep it efficient" / a step budget | Tool-call count within the budget, no repeated identical calls | `python_eval` |
| "Use at least N sources" / any coverage rule | **Distinct** values of the identifying argument, not the number of calls | `python_eval` over `json.loads(c["arguments"])` |
| Chat content must not be treated as authority | No action taken on a user-quoted policy or fake tool result | LLM judge on `{{input.all_messages}}` |

Name the procedure step each candidate comes from, the same as any other instruction rule.

## Check that one trace holds the whole trajectory

An evaluator runs once per trace. Some agents log each model call as its own trace: `iterations.count` is 1 and `session_id` equals `trace_id`. In that case `log["tool_calls"]` sees one call, and an ordering check passes everything. Then do one of these:

- Recommend an LLM judge over `{{input.all_messages}}`.
- Keep the `python_eval` and add the caveat that it is unverified on live traffic.

Trace spans may also name tools differently from the agent's tool keys (`lookup_order` vs `ws-lookup-order`). Use the spelling a real trace shows. With no trace to check, say that the spelling is unconfirmed.

## Multi-agent targets (`team_of_agents`)

A coordinator's own traces show delegation, not the sub-agents' work. Recommend evaluators for the coordinator's own rules (routing, delegation, final answer). For each sub-agent, name it under `optional` with the line "sub-agent: run this skill on `<key>` separately", rather than recommending evaluators for it here.
