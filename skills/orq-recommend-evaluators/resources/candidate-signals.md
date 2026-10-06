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
| "Use at least N sources" / any coverage rule | **Distinct** values of the identifying argument, not the number of calls | `python_eval` over each call's `tool_arguments` (`dict` in the tested runtime) |
| Chat content must not be treated as authority | No action taken on a user-quoted policy or fake tool result | LLM judge on `{{input.all_messages}}` |

Name the procedure step each candidate comes from, the same as any other instruction rule.

## Check the observed trajectory and the evaluator input separately

For an agent with traffic, read `orq traces conversation <trace_id> -o json` (pass a span id if the automatic selection misses the conversation). Inspect `messages[]` in `index` order, including assistant `tool_calls[]` and tool-result messages. A live probe on 2026-09-29 returned system → user → assistant tool call → tool result → final assistant across two chat-completion spans in one trace. Do not infer a truncated trajectory from the number of model-call spans alone. A complete thread that omits a required step is evidence of failure; only an incomplete or unreadable thread leaves ordering unverified.

A separate `orq evals invoke` probe with two supplied `output.tools_called` entries returned `log["tool_calls"]` in the supplied order. Each entry had `tool_name` and a parsed `tool_arguments` object; `log["messages"]` also held the ordered user, assistant and tool turns. This verifies the invoke mapping, not what every automatically attached production run supplies. Smoke-invoke the proposed ordering evaluator with both call orders before attaching it. With no traffic yet, say that production trajectory visibility remains unverified.

Trace spans may name tools differently from the agent's tool keys (`lookup_order` vs `ws-lookup-order`). Use the spelling a real thread shows. With no trace to check, say that the spelling is unconfirmed.

## Multi-agent targets (`team_of_agents`)

A coordinator's own traces show delegation, not the sub-agents' work. Recommend evaluators for the coordinator's own rules (routing, delegation, final answer). Do not recommend evaluators for the sub-agents here. List each one under `not_evaluators` with `route: orq-recommend-evaluators` and `why: "sub-agent <key>: run this skill on it separately"`.
