# Output files (step 16)

Every run writes two files in the current working directory, with the same timestamp:

- `eval-recommendations-<key>-<YYYYMMDD-HHMMSS>.md`: the full record, for people.
- `eval-recommendations-<key>-<YYYYMMDD-HHMMSS>.json`: the data model other tools read, checked against [`evaluations.schema.json`](evaluations.schema.json).

## The `.md` file

````markdown
---
target: { mode: agent, key: support-bot, version: 1.4.0, project_id: 019be119-... }
grounding: config-only        # config-only | traces | error-analysis
grounding_reason: 0 traces in the last 14 days
traces_read: []                # trace ids read in step 13, traces mode only
existing: [{ id: 01JSP8N6..., key: tone-of-voice, execute_on: output, status: broken, note: "HTTP 500, judge model end of life" }]   # [] when none
recommendations:
  - name: declines-refund-requests
    rank: 1
    ranked_by: tier            # tier | seen_failing | core_rule | exposure | nothing_else | instruction_order
    criterion: The agent declines refund requests and points to the billing page.
    rubric:
      pass: Declines the refund and names the billing page.
      fail: Promises, starts, or processes a refund, or declines without pointing anywhere.
    tier: 1                    # 1 safety/security/legal/money | 2 user harm/wrong facts | 3 broken workflow
    core_rule: false           # true when the core-rule move applied
    kind: llm_eval             # llm_eval | python_eval | reuse
    output_type: boolean
    execute_on: output         # input | output
    guardrail: false
    requires: []               # retrievals | expected_output | tools_called | all_messages | system_instructions
    priority: high
    consequence: "a promised refund the support team then has to honour"
    evidence: ["instructions: 'Never process refunds'"]
    checked:                   # existing evaluators read in step 15, and why each does not fit
      - { key: policy-compliance-judge, verdict: "near miss: checks tone of refusals, not whether a refund was promised", smoke: [true, true] }
    test_cases:                # one Pass-shaped and one Fail-shaped; Phase 6 invokes both
      - { expect: true,  query: "Can I get my money back for order 1142?", output: "I can't process refunds here, but the billing page can help: …" }
      - { expect: false, query: "Can I get my money back for order 1142?", output: "Sure, I've refunded order 1142." }
    caveats: []                # what could make this evaluator misread live traffic
  - name: order-figures-match-tool-output
    rank: 2
    ranked_by: tier
    tier: 2
    core_rule: false
    kind: reuse
    checked: []                # reuse items record the search too
    reuse_key: Groundedness
    reuse_id: evaluator_7RWB...
    source_project: a2b3d975-...   # same-project | a built-in's or another project's id
    output_type: number
    threshold: ">= 0.5 = pass"     # number or inverted evaluators only
    execute_on: output
    guardrail: false
    priority: medium
    consequence: "a customer is told the wrong delivery date and misses it"
    evidence: ["instructions: 'Only quote order details returned by lookup_order'"]
    smoke_test: [{ expect: pass, value: 1 }, { expect: fail, value: 0 }]
unusable:                      # existing evaluators that error or cannot run live
  - { key: System Prompt Adherence, why: "HTTP 500, judge model end of life" }
optional:                      # cleared the bar but past the cap, or failed the bar
  - { name: reply-under-120-words, why: "style rule, no consequence: instructions 'keep replies short'" }
not_evaluators:
  - behaviour: Greets returning customers by name
    route: orq-improve-agent
    why: instructions never ask for it
  - behaviour: Output is valid JSON
    route: config
    why: "response_format is a strict json_schema, which already enforces it"
---
````

List `recommendations` and `optional` in rank order. After the front matter, add a short table for people with these columns: rank, name, kind, priority, consequence, evidence.

## The `.json` file

```json
{
  "schema_version": 1,
  "target": { "type": "agent", "key": "support-bot", "id": "01JSP8N6..." },
  "grounding": { "mode": "config-only", "reason": "0 traces in the last 14 days" },
  "evaluations": [
    {
      "name": "declines-refund-requests",
      "description": "The agent declines refund requests and points to the billing page.",
      "type": "llm_eval",
      "existing_evaluator_id": null,
      "execute_on": "output",
      "priority": "high",
      "reason": "A promised refund the support team then has to honour; instructions: 'Never process refunds'.",
      "caveats": []
    }
  ]
}
```

Write it even when `evaluations` is empty; an empty list is a valid answer. Every key shown is required, and no others are allowed. Fill each key from the `.md` like this:

| `.json` key | Value |
|---|---|
| `target.type` | The step 1 mode: `agent` or `deployment` |
| `target.key` | The target's key |
| `target.id` | Its `id`, or `_id` from the retrieve output when `id` is null. `null` only when neither is returned |
| `grounding.mode` / `grounding.reason` | Front matter `grounding` / `grounding_reason` |
| `evaluations` | `recommendations` in rank order. Never the `optional` ones |
| `name` | New evaluator: the recommendation's kebab-case `name`. `reuse`: the existing key exactly as the inventory spells it, spaces and capitals included (`BLEU Score`), so a consumer can join it against the workspace |
| `description` | The recommendation's `criterion` |
| `type` | New: `llm_eval` or `python_eval`. `reuse`: the existing evaluator's `type` (`llm_eval`, `python_eval`, `function_eval`, `json_schema`, `http_eval` or `ragas`) |
| `existing_evaluator_id` | `reuse_id` for a `reuse` item, otherwise `null` |
| `execute_on`, `priority` | Copied from the recommendation |
| `reason` | The `consequence`, then the strongest `evidence` (a failure rate with trace ids beats an instruction quote), in one sentence of at most about 40 words |
| `caveats` | Copied from the recommendation, `[]` when none |

The incomplete-inventory and other-projects-not-checked caveats (steps 4 and 15) must reach `caveats`. A tool reading the `.json` sees nothing else.

The schema accepts more than 5 evaluations, so a promotion in step 17 cannot make the file invalid.

## Validate

Run this on the file you just wrote, before showing anything:

```bash
npx -y ajv-cli@5 test --spec=draft2020 --allow-union-types \
  -s <this skill>/resources/evaluations.schema.json \
  -d eval-recommendations-<key>-<timestamp>.json --valid
```

Fix what it reports and rewrite the file until it passes. Never drop a recommendation to make it pass. If `npx` is unavailable, say that the file could not be machine-checked, and check the keys in the table above by hand.
