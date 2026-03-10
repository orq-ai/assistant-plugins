# Prompt Learning Meta-Prompt (v2, batch-mode)

Use this meta-prompt template when generating rules from feedback. Pass it the variables described in INPUTS below.

---

```
You are a prompt engineer improving a prompt based on feedback from multiple examples.

══════════════════════════════════════════════════════════════════════
GOAL
══════════════════════════════════════════════════════════════════════
Analyze a batch of feedback (failures + positive anchors) and produce minimal, high-impact rules that:
1. Fix recurring failure patterns
2. Don't break existing good behavior (regression anchors)

══════════════════════════════════════════════════════════════════════
INPUTS
══════════════════════════════════════════════════════════════════════
1) PROMPT_TYPE: "agent" | "evaluator"
2) CURRENT_PROMPT: The prompt to improve
3) ITERATION: Current iteration number (1-8)
4) FEEDBACK_SOURCE: "human" | "ai_eval"

5) FAILURE_EXAMPLES (5-15 samples with negative feedback; typically 6-14):
   [
     {
       "user_input": "...",
       "model_output": "...",
       "reference": "..." (optional),
       "feedback": <see shapes below>
     },
     ...
   ]

6) POSITIVE_EXAMPLES (2-5 regression anchors; typically 3-5):
   [
     {
       "user_input": "...",
       "model_output": "...",
       "feedback": "pass" | { "value": true, ... }
     },
     ...
   ]

FEEDBACK SHAPES:
- Human categorical: "fail" | "pass" | "borderline"
- Human numerical: 3 (just the number)
- Human free text: "The response was too vague..."
- AI eval boolean: { "value": true|false, "explanation": "..." }
- AI eval categorical: { "value": "A"|"B"|"C", "explanation": "..." }
- AI eval numerical: { "value": 6, "scale": "1-10", "explanation": "..." }
- Enriched normalized (recommended if raw feedback lacks explanations):
  { "verdict": "fail", "severity": 4, "issue_tags": ["missing_requirement"], "explanation": "..." }

══════════════════════════════════════════════════════════════════════
PROCESS
══════════════════════════════════════════════════════════════════════

STEP 1: ANALYZE FAILURE PATTERNS
Group the failure examples by issue type. Identify recurring patterns (2+ occurrences).

ISSUE TAXONOMY:
- accuracy: factually wrong
- missing_requirement: didn't fulfill explicit requirement
- policy: violated policy/guideline
- safety: unsafe content
- formatting: wrong format/structure
- verbosity: too long/short
- tone: wrong tone/style
- tool_use: incorrect tool usage
- reasoning: flawed logic
- hallucination: made up information

Output pattern analysis:
{
  "patterns": [
    {
      "issue_tag": "<from taxonomy>",
      "count": <number of occurrences>,
      "severity": <1-5>,
      "examples": [<indices of matching examples>],
      "root_cause": "<why this keeps happening>"
    }
  ],
  "one_off_issues": [<issues that appeared only once - don't patch these>]
}

STEP 2: CHECK AGAINST POSITIVE ANCHORS
For each identified pattern, verify the proposed fix won't break positive examples.

{
  "anchor_conflicts": [
    {
      "pattern": "<issue_tag>",
      "conflicting_anchor": <index>,
      "conflict_reason": "<why fix might break this>"
    }
  ]
}

STEP 3: GENERATE RULES (only for recurring patterns without conflicts)
Create 1-5 rules that address the most impactful patterns.

Rule format: "If [TRIGGER], then [ACTION]."

Prioritize by: frequency × severity

Skip patterns that:
- Appeared only once (one-off)
- Would conflict with positive anchors
- Are too vague to create testable rules

STEP 4: FORMAT RULES_TO_APPEND
Text block for ### LEARNED_RULES section.

STEP 5: GENERATE REGRESSION TESTS
Create 5-10 test cases:
- 3-5 "should_now_pass" (based on failure patterns)
- 2-5 "should_still_pass" (based on positive anchors)

STEP 6: ITERATION GUIDANCE
Based on current iteration, suggest:
- Continue: if significant patterns remain unfixed
- Stop: if diminishing returns (patterns are one-offs or low severity)

══════════════════════════════════════════════════════════════════════
OUTPUT FORMAT
══════════════════════════════════════════════════════════════════════

### A) PATTERN_ANALYSIS
```json
{
  "patterns": [...],
  "one_off_issues": [...]
}
```

### B) ANCHOR_CHECK
```json
{
  "anchor_conflicts": [...],
  "safe_to_patch": [<pattern tags that are safe>]
}
```

### C) RULES
<numbered list, only for safe patterns>

### D) RULES_TO_APPEND
```
<text block>
```

### E) REGRESSION_TESTS
```json
[...]
```

### F) ITERATION_GUIDANCE
{
  "recommendation": "continue" | "stop",
  "reason": "<why>",
  "remaining_issues": <count of unaddressed patterns>,
  "expected_next_iteration_gain": "high" | "medium" | "low"
}

══════════════════════════════════════════════════════════════════════
EXAMPLE
══════════════════════════════════════════════════════════════════════

Input:
- PROMPT_TYPE: agent
- CURRENT_PROMPT: "You are a helpful customer service assistant..."
- ITERATION: 1
- FEEDBACK_SOURCE: ai_eval

- FAILURE_EXAMPLES:
  [
    {"user_input": "Return item bought 45 days ago", "model_output": "Sure, go to returns...", "feedback": {"value": false, "explanation": "Didn't check 30-day policy"}},
    {"user_input": "Return item from 2 months ago", "model_output": "Click return button...", "feedback": {"value": false, "explanation": "Ignored return window"}},
    {"user_input": "Refund for old purchase", "model_output": "Here's how to return...", "feedback": {"value": false, "explanation": "Should have mentioned policy limits"}},
    {"user_input": "Where is my order?", "model_output": "Check your email", "feedback": {"value": false, "explanation": "Too brief, should offer to look up"}},
    {"user_input": "Cancel subscription", "model_output": "Subscription cancelled.", "feedback": {"value": false, "explanation": "Didn't confirm or ask for feedback"}}
  ]

- POSITIVE_EXAMPLES:
  [
    {"user_input": "Return item bought yesterday", "model_output": "I'd be happy to help with your return...", "feedback": {"value": true, "explanation": "Complete and helpful"}},
    {"user_input": "Track my package", "model_output": "Let me look that up for you. Your order #123 is...", "feedback": {"value": true, "explanation": "Proactive and specific"}}
  ]

Output:

### A) PATTERN_ANALYSIS
```json
{
  "patterns": [
    {
      "issue_tag": "policy",
      "count": 3,
      "severity": 4,
      "examples": [0, 1, 2],
      "root_cause": "Model provides return instructions without checking/mentioning the 30-day return policy"
    },
    {
      "issue_tag": "verbosity",
      "count": 1,
      "severity": 2,
      "examples": [3],
      "root_cause": "Response too brief"
    },
    {
      "issue_tag": "missing_requirement",
      "count": 1,
      "severity": 3,
      "examples": [4],
      "root_cause": "Didn't confirm action or gather feedback"
    }
  ],
  "one_off_issues": ["verbosity", "missing_requirement (cancellation)"]
}
```

### B) ANCHOR_CHECK
```json
{
  "anchor_conflicts": [],
  "safe_to_patch": ["policy"]
}
```

### C) RULES
1. If customer asks about returning an item, then first check if the purchase date is within the 30-day return window before providing return instructions.
2. If return request is outside the 30-day policy, then inform customer of the policy limit and offer alternatives (store credit, exception request).

### D) RULES_TO_APPEND
```
- For return requests: Always verify purchase date against 30-day return policy before providing instructions.
- If item is outside return window: State the policy clearly, then offer alternatives rather than denying outright.
```

### E) REGRESSION_TESTS
```json
[
  {"input": "Return item bought 45 days ago", "expected": "Mention 30-day policy, offer alternatives", "type": "should_now_pass"},
  {"input": "Refund for purchase from last month (35 days)", "expected": "Check date, explain policy", "type": "should_now_pass"},
  {"input": "Return item bought 2 months ago", "expected": "Policy limit + alternatives", "type": "should_now_pass"},
  {"input": "Return item bought yesterday", "expected": "Proceed with return instructions normally", "type": "should_still_pass"},
  {"input": "Track my package", "expected": "Look up order, provide status", "type": "should_still_pass"}
]
```

### F) ITERATION_GUIDANCE
```json
{
  "recommendation": "continue",
  "reason": "Addressed 1 major pattern (policy/returns). Two one-off issues remain but may recur with more data.",
  "remaining_issues": 2,
  "expected_next_iteration_gain": "medium"
}
```

══════════════════════════════════════════════════════════════════════
NOW PROCESS THE ACTUAL INPUT
══════════════════════════════════════════════════════════════════════
```

---

## Template Usage Notes

1. **PROMPT_TYPE**: Set to `"agent"` for agent/deployment prompts, `"evaluator"` for evaluator prompts.

2. **FAILURE_EXAMPLES**: Sample f=10 failures (research-validated default). Include representative failures, not exhaustive lists. Ensure at least 2 examples per pattern you want to address.

3. **POSITIVE_EXAMPLES**: Always include p=3 positive traces as regression anchors. These prevent over-correction by ensuring rules don't break existing good behavior.

4. **FEEDBACK_SOURCE**: Set to `"human"` for user feedback (thumbs up/down, corrections, free-text) or `"ai_eval"` for evaluator scores. The same meta-prompt processes both — only the preprocessing (normalization) differs.

5. **Iteration count**: Default to 2 iterations for most models (GPT, Claude). Use up to 5 for Gemini models. Stop early if ITERATION_GUIDANCE recommends `"stop"`.

6. **Rule cap**: Maximum 10 total rules across all iterations. If approaching the cap, prioritize highest frequency × severity rules.

7. **Variables**: Fill in `CURRENT_PROMPT` with the full prompt text, `FAILURE_EXAMPLES` and `POSITIVE_EXAMPLES` with sampled traces from the target deployment.
