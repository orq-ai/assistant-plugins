# LLM-as-a-Judge Prompt Template

Use this template when creating a new judge evaluator. Fill in all `[PLACEHOLDERS]`.

---

```
You are an expert evaluator assessing outputs from [SYSTEM DESCRIPTION — e.g., "a customer support chatbot for an e-commerce platform"].

## Your Task
Determine if the assistant's response [SPECIFIC BINARY QUESTION — e.g., "correctly addresses the customer's refund request without hallucinating store policies"].

## Evaluation Criterion: [CRITERION NAME — e.g., "Policy Faithfulness"]

### Definition of Pass/Fail
- **Fail**: [PRECISE DESCRIPTION — e.g., "The response references a store policy that does not exist, misquotes an existing policy, or makes promises that contradict the actual refund policy."]
- **Pass**: [PRECISE DESCRIPTION — e.g., "The response accurately reflects the store's refund policy as documented, or appropriately declines to answer if the policy is unclear."]

[FOR CATEGORICAL EVALUATORS, replace the two definitions above with one per label:]
### Definition of Each Label
- **[LABEL_1]**: [PRECISE DESCRIPTION]
- **[LABEL_2]**: [PRECISE DESCRIPTION]
- **[LABEL_3]**: [PRECISE DESCRIPTION]
- **other**: [Catch-all. Always include one — without it the judge invents a label or forces a bad fit.]

Labels must be mutually exclusive and jointly exhaustive: every output falls in exactly one.

[OPTIONAL CONTEXT SECTION — domain knowledge, persona descriptions, policy documents, etc.]

## Output Format
Return your evaluation as a JSON object with exactly two keys:
1. "reasoning": A brief explanation (1-2 sentences) for your decision.
2. "answer": Either "Pass" or "Fail".

[FOR CATEGORICAL EVALUATORS, replace the answer instruction above with:]
2. "answer": One of: "[LABEL_1]", "[LABEL_2]", "[LABEL_3]"

Do NOT include any text outside the JSON object.

## Examples

### Example 1:
**Input**: [User query from training set — a clear Fail case]
**Output**: [LLM response that exhibits the failure mode]
**Evaluation**: {"reasoning": "[Brief explanation of why this fails the criterion]", "answer": "Fail"}

### Example 2:
**Input**: [User query from training set — a clear Pass case]
**Output**: [LLM response that does NOT exhibit the failure mode]
**Evaluation**: {"reasoning": "[Brief explanation of why this passes]", "answer": "Pass"}

### Example 3:
**Input**: [Borderline case from training set]
**Output**: [LLM response]
**Evaluation**: {"reasoning": "[Explanation of the deciding factor]", "answer": "[Pass or Fail]"}

[FOR CATEGORICAL EVALUATORS, use the category labels instead of Pass/Fail:]
### Example 4 (categorical):
**Input**: [User query]
**Output**: [LLM response with a clearly professional tone]
**Evaluation**: {"reasoning": "The response uses formal language and avoids slang.", "answer": "professional"}

[Add 1-5 more examples. Total: 2-8 examples. In-context learning saturates after ~8.]

## Now evaluate the following:
**Input**: {{input.user_query}}
**Output**: {{output.response}}
[OPTIONAL: **Reference**: {{input.expected_output}}]
[OPTIONAL: **System Instructions**: {{input.system_instructions}}]
[OPTIONAL: **Tools Called**: {{output.tools_called}}]

Your JSON Evaluation:
```

---

## Template Usage Notes

1. **[SYSTEM DESCRIPTION]**: Be specific. "A real estate CRM assistant" is better than "an AI chatbot".

2. **[SPECIFIC BINARY QUESTION]**: Must be answerable with Pass or Fail (or a categorical label if using categorical output type). Good: "maintains the Flemish cowboy persona throughout". Bad: "how good is the response".

3. **Pass/Fail definitions**: Define Fail FIRST (it's the failure mode you're detecting). Pass is the absence of that failure. Be concrete — include what specific behaviors constitute pass vs fail.

4. **Examples**: Draw from your TRAINING split only. Never from dev or test. Include:
   - At least 1 clear Pass
   - At least 1 clear Fail
   - Ideally 1 borderline case with explanation of the deciding factor

5. **Reasoning before answer**: The template puts "reasoning" before "answer" in the JSON to encourage chain-of-thought before the final judgment.

6. **Variables** (v4.14+) — this list is canonical for the skill; add new variables here, not in `SKILL.md`.
   These are the seven the prompt editor offers (type `{{` in the editor to pick from the list). All render as strings.

   | Variable | Content |
   |----------|---------|
   | `{{input.user_query}}` | The last message sent to the model |
   | `{{input.all_messages}}` | The full conversation, **including** the graded turn |
   | `{{input.system_instructions}}` | The system prompt the run used |
   | `{{input.retrievals}}` | Knowledge Base retrievals |
   | `{{input.expected_output}}` | The reference to compare the output against |
   | `{{output.response}}` | The response the evaluated model generated |
   | `{{output.tools_called}}` | The tool calls made during the run, with their results |

   Custom values passed at invocation are available under their own name: `{{my_custom_var}}`.

   **Indexing.** Index into a variable to reach one message or tool call:

   ```text
   {{input.all_messages[0].content}}
   {{input.all_messages[-1].role}}
   {{output.tools_called[0].name}}
   {{output.tools_called[0].arguments}}
   ```

   A tool call has `name`, `arguments`, `status` (`""`, `in_progress`, `completed`, `incomplete`, `failed`) and an optional `output`. A message is role-tagged: `system`/`developer`/`user` carry `content`; `assistant` carries an optional `content` plus optional `tool_calls[]` of `{id, type, function: {name, arguments}}`; `tool` carries `tool_call_id` and `content`.

   **Legacy `{{log.*}}` variables** still resolve, so existing evaluators keep working — prefer the above for new ones. The mapping is not quite one-to-one:

   | Legacy | Current |
   |--------|---------|
   | `{{log.input}}` | `{{input.user_query}}` |
   | `{{log.output}}` | `{{output.response}}` |
   | `{{log.retrievals}}` | `{{input.retrievals}}` |
   | `{{log.reference}}` | `{{input.expected_output}}` |
   | `{{log.tool_calls}}` | `{{output.tools_called}}` |
   | `{{log.messages}}` | **Not** `{{input.all_messages}}` — `log.messages` excludes the graded turn, `input.all_messages` includes it. Swapping them changes what the judge sees. |

   There is no `log.*` equivalent for `{{input.system_instructions}}`.
