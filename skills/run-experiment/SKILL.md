---
name: run-experiment
description: End-to-end LLM evaluation workflow — error analysis, dataset creation, experiment execution, result analysis, and ticket filing
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, Task, AskUserQuestion, mcp__linear-server__*, orq*
---

# Run Experiment

End-to-end workflow for evaluating LLM pipelines using the orq.ai platform, grounded in evaluation best practices.

**Companion skills:**
- `build-evaluator` — use that skill for detailed judge prompt design. This skill orchestrates the broader workflow that wraps around it.
- `prompt-learning` — automated feedback-driven rule generation validated via experiments

## When to use

- User wants to evaluate an LLM agent, deployment, or pipeline end-to-end
- User asks to create an evaluation dataset and run experiments
- User wants to analyze experiment results and create improvement tickets
- User asks to set up an eval loop (Analyze → Measure → Improve)
- User wants to run a new experiment after making changes

## orq.ai Documentation

When working with the orq.ai platform, consult these docs pages as needed:
- **Datasets overview:** https://docs.orq.ai/docs/datasets/overview
- **Creating datasets:** https://docs.orq.ai/docs/datasets/creating
- **Datasets API:** https://docs.orq.ai/docs/datasets/api-usage
- **Experiments overview:** https://docs.orq.ai/docs/experiments/overview
- **Creating experiments:** https://docs.orq.ai/docs/experiments/creating
- **Experiments API:** https://docs.orq.ai/docs/experiments/api
- **Evaluators overview:** https://docs.orq.ai/docs/evaluators/overview
- **Creating evaluators:** https://docs.orq.ai/docs/evaluators/creating
- **Evaluator library:** https://docs.orq.ai/docs/evaluators/library
- **Traces (for error analysis):** https://docs.orq.ai/docs/observability/traces
- **Deployments:** https://docs.orq.ai/docs/deployments/overview
- **Feedback:** https://docs.orq.ai/docs/feedback/overview

### orq.ai Key Concepts
- **Datasets** contain Inputs, Messages, and Expected Outputs (all optional — use what you need)
- **Experiments** run model generations against datasets, measuring Latency, Cost, and Time to First Token
- **Evaluators** types: LLM (AI-as-judge), Python (custom code), JSON (schema validation), HTTP (external API), Function (pre-built), RAGAS (RAG-specific)
- **Experiment Runs tab** lets you compare evaluator results across iterations
- **Review tab** shows individual responses with full context and evaluator results
- **Compare tab** shows multiple models side-by-side

### orq MCP Tools

Use the orq MCP server (`https://my.orq.ai/v2/mcp`) as the primary interface. For operations not yet available via MCP, use the HTTP API as fallback.

**Available MCP tools for this skill:**

| Tool | Purpose |
|------|---------|
| `create_llm_eval` | Create an LLM evaluator |
| `list_traces` | List and filter traces for error analysis |
| `list_spans` | List spans within a trace |
| `get_span` | Get detailed span information |
| `get_agent` | Get agent configuration |
| `create_experiment` | Create and run an experiment |
| `list_experiment_runs` | List experiment runs |
| `get_experiment_run` | Get experiment run details |

**HTTP API fallback** (for operations not yet in MCP):

```bash
# List evaluators
curl -s https://my.orq.ai/v2/evaluators \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Get evaluator details
curl -s https://my.orq.ai/v2/evaluators/<ID> \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Invoke an evaluator
curl -s https://my.orq.ai/v2/evaluators/<ID>/invoke \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"output": "LLM output", "query": "Original input", "reference": "Expected answer"}' | jq

# List agents
curl -s https://my.orq.ai/v2/agents \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Invoke an agent (for generating test traces)
curl -s https://api.orq.ai/v2/agents/responses \
  -X POST \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"agent_key": "<KEY>", "message": {"role": "user", "parts": [{"kind": "text", "text": "Test input"}]}}' | jq
```

## Prerequisites

- **orq.ai MCP server** connected (provides dataset, experiment, evaluator, and trace tools)
- **Linear MCP server** connected (for filing improvement tickets)
- A target LLM deployment/agent on orq.ai to evaluate

## Workflow Overview

```
Phase 1: Analyze     →  Error analysis, identify failure modes
Phase 2: Design      →  Create dataset + evaluator(s)
Phase 3: Measure     →  Run experiment, collect scores
Phase 4: Act         →  Analyze results, file tickets, plan improvements
Phase 5: Re-measure  →  Re-run after improvements (loop back to Phase 3)
```

## Steps

### Phase 1: Analyze — Understand What to Evaluate

1. **Clarify the target system.** Ask the user:
   - What LLM deployment/agent are we evaluating?
   - What is the system prompt / persona / task?
   - What does "good" output look like? What does "bad" look like?
   - Are there known failure modes already?

2. **Collect or generate evaluation traces.** Two paths:

   **Path A — Real data exists:** Sample diverse traces from production using orq.ai trace tools. Target ~100 traces covering different features, edge cases, and difficulty levels.

   **Path B — No real data yet:** Generate synthetic test cases using the structured approach:
   - Define **3+ dimensions** of variation (e.g., topic, difficulty, adversarial type, persona challenge)
   - Generate **tuples** of dimension combinations (aim for 20+ combinations)
   - Convert tuples to **natural language** test inputs in a separate step
   - Human review at each stage

3. **Error analysis.** For each trace:
   - Read the full trace (input + output, intermediate steps if agentic)
   - Note what went wrong (open coding — freeform observations)
   - Identify the **first upstream failure** in each trace
   - Group failures into structured, non-overlapping failure modes (axial coding)

4. **Prioritize failure modes.** For each failure mode, decide:
   - **Fix the prompt first?** (specification failure — ambiguous instructions)
   - **Code-based check?** (regex, assertions, schema validation)
   - **LLM-as-Judge?** (subjective/nuanced criteria that code can't capture)

### Phase 2: Design — Create Dataset and Evaluator(s)

5. **Create the evaluation dataset** on orq.ai:
   - Use the orq MCP tools to create a dataset
   - Each datapoint should have: `input` (user message), `reference` (expected behavior/ideal output), and any relevant context
   - Include diverse scenarios: happy path, edge cases, adversarial inputs
   - **Minimum 8 datapoints** for a first pass; target 50-100 for production use
   - Tag datapoints by category/dimension for slice analysis

6. **Design evaluator(s).** For each failure mode needing LLM-as-Judge:
   - **Invoke the `build-evaluator` skill** for detailed judge prompt design
   - Key principles (non-negotiable):
     - **Binary Pass/Fail** per criterion (NOT Likert scales)
     - **One evaluator per failure mode** (do not bundle criteria)
     - **Few-shot examples** in the prompt (2-8 from training split)
     - **Structured JSON output** with "reasoning" before "answer"
   - Create the evaluator on orq.ai using the MCP tools
   - Select a capable judge model (gpt-4.1 or better to start)

7. **If using a composite score** (pragmatic shortcut for early iterations):
   - Acknowledge this deviates from best practice
   - Plan to decompose into separate binary evaluators as the eval matures
   - Document what each sub-criterion means precisely

### Phase 3: Measure — Run the Experiment

8. **Create and run the experiment** on orq.ai:
   - Link the dataset and evaluator(s) to the experiment
   - Select the target model/deployment to evaluate
   - Configure system prompt (if testing prompt variations)
   - Run the experiment and wait for completion

9. **Collect results:**
   - Retrieve the experiment run results via orq MCP tools
   - Extract per-datapoint scores and overall metrics
   - Note the run cost for budget tracking

### Phase 4: Act — Analyze Results and Create Action Items

10. **Analyze results systematically:**
    - Sort scores to identify weakest areas
    - Look for patterns: which categories/dimensions score lowest?
    - Compare against threshold (if defined)
    - Identify the top 3-5 actionable improvements

11. **Present results** to the user in a clear format:

    ```
    | Scenario | Score | Category |
    |----------|-------|----------|
    | ...      | ...   | ...      |

    **Average:** X/Y | **Cost:** $Z | **Run ID:** ABC
    ```

12. **Create improvement tickets** on Linear (if connected):
    - One ticket per actionable improvement
    - Set appropriate priority (High for score < threshold, Medium otherwise)
    - Include: what failed, why, suggested fix, relevant datapoint IDs
    - Create a "re-run experiment" ticket blocked by all improvement tickets
    - Example ticket structure:

    ```
    Title: [Improve/Fix/Expand] [specific issue]
    Priority: High/Medium
    Description:
    - What: [observed failure]
    - Evidence: [specific datapoints and scores]
    - Suggested fix: [concrete action]
    - Success criteria: [what "fixed" looks like]
    ```

### Phase 5: Re-measure — Close the Loop

13. **After improvements are made**, re-run the experiment:
    - Verify improvement tickets are resolved
    - Update the dataset if new test cases were added
    - Update the evaluator if criteria changed
    - Run a new experiment with the same setup
    - Compare results to previous run(s)
    - File new tickets if regressions or new issues appear

14. **Track progress over time:**
    - Maintain a results history (run ID, date, avg score, cost)
    - Look for trends: improving? regressing? plateauing?
    - When scores stabilize above threshold, consider expanding test coverage

## Best Practice Reminders

### Dataset Design
- Use **structured synthetic data generation** (dimensions → tuples → natural language) for diversity
- Include **adversarial test cases** (jailbreaks, persona-breaking attempts, off-topic, language challenges)
- Test **both complex and simple** inputs — simple factual questions often reveal persona drift
- Aim for at least **3 adversarial test cases** per anticipated attack vector

### Evaluator Design
- **Binary Pass/Fail > numeric scales** — always challenge numeric scores
- **One evaluator per failure mode** — resist the urge to bundle
- **Validate the judge** — measure TPR/TNR on held-out labeled data before trusting it
- **Fix the prompt before building evals** — many "failures" are just underspecified instructions

### Experiment Execution
- **Start with the most capable judge model** (optimize cost later)
- **Record everything**: run ID, model, cost, date, dataset version
- **Compare apples to apples**: same dataset + evaluator across runs

### Result Analysis
- **A 100% pass rate means your eval is too easy**, not that your system is perfect
- **Look at the lowest scores first** — they reveal the most about where to improve
- **Slice by category/dimension** to find systematic weaknesses
- **Cost matters** — track it per run to inform model selection decisions

### Ticket Filing
- **One ticket per improvement** — not one mega-ticket
- **Block the re-run ticket** on all improvements — prevents premature re-evaluation
- **Include evidence** — specific datapoints, scores, and failure descriptions
- **Set clear success criteria** — what score/behavior change indicates the fix worked

## Anti-Patterns to Prevent

| Anti-Pattern | What to Do Instead |
|---|---|
| Running experiments without a dataset | Always create a structured dataset first |
| Using generic "helpfulness" or "quality" evaluators | Build application-specific criteria from error analysis |
| One giant evaluator with 5+ criteria bundled | Separate evaluators per failure mode |
| Re-running experiments without fixing anything | File tickets, make changes, THEN re-run |
| Ignoring low scores on "easy" questions | Simple questions often reveal the most important failures |
| Skipping adversarial test cases | Always include jailbreak/persona-breaking/off-topic scenarios |
| Treating eval as a one-time setup | It's an ongoing loop — Analyze → Measure → Improve → repeat |

