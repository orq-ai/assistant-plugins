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

[OPTIONAL CONTEXT SECTION — domain knowledge, persona descriptions, policy documents, etc.]

## Output Format
Return your evaluation as a JSON object with exactly two keys:
1. "reasoning": A brief explanation (1-2 sentences) for your decision.
2. "answer": Either "Pass" or "Fail".

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

[Add 1-5 more examples. Total: 2-8 examples. In-context learning saturates after ~8.]

## Now evaluate the following:
**Input**: {{input}}
**Output**: {{output}}
[OPTIONAL: **Reference**: {{reference}}]

Your JSON Evaluation:
```

---

## Template Usage Notes

1. **[SYSTEM DESCRIPTION]**: Be specific. "A real estate CRM assistant" is better than "an AI chatbot".

2. **[SPECIFIC BINARY QUESTION]**: Must be answerable with Pass or Fail. Good: "maintains the Flemish cowboy persona throughout". Bad: "how good is the response".

3. **Pass/Fail definitions**: Define Fail FIRST (it's the failure mode you're detecting). Pass is the absence of that failure. Be concrete — include what specific behaviors constitute pass vs fail.

4. **Examples**: Draw from your TRAINING split only. Never from dev or test. Include:
   - At least 1 clear Pass
   - At least 1 clear Fail
   - Ideally 1 borderline case with explanation of the deciding factor

5. **Reasoning before answer**: The template puts "reasoning" before "answer" in the JSON to encourage chain-of-thought before the final judgment.

6. **Variables**: Use `{{input}}`, `{{output}}`, and optionally `{{reference}}` as template variables. On orq.ai, these map to `{{log.input}}`, `{{log.output}}`, `{{log.reference}}`.
