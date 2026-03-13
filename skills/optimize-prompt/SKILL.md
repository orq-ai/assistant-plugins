---
name: optimize-prompt
description: Systematically iterate on a prompt deployment using trace data, A/B testing, and structured refinement techniques
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, WebFetch, Task, AskUserQuestion, mcp__linear-server__*, orq*
---

# Optimize Prompt

Systematically improve prompt deployments through trace-driven failure analysis, structured refinement techniques, and A/B experimentation on the orq.ai platform.

**Companion skills:**
- `prompt-optimizer` — quick automated prompt improvement without trace data (use when you just want to improve a prompt's structure and clarity)
- `trace-analysis` — identify failure patterns that inform prompt edits
- `run-experiment` — run A/B experiments comparing prompt versions
- `build-evaluator` — create evaluators to measure prompt improvements

## When to use

- User wants to improve a prompt that's underperforming
- Traces show specification failures (ambiguous instructions, missing constraints)
- Action plan recommends prompt fixes
- User wants to A/B test prompt variants
- User asks "how do I make my prompt better?"
- User notices persona drift, format violations, or missing constraints in outputs

## orq.ai Documentation

Consult these docs when working with the orq.ai platform:
- **Prompts overview:** https://docs.orq.ai/docs/prompts/overview
- **Prompt management:** https://docs.orq.ai/docs/prompts/management
- **Prompt versioning:** https://docs.orq.ai/docs/prompts/versioning
- **Deployments overview:** https://docs.orq.ai/docs/deployments/overview
- **Experiments:** https://docs.orq.ai/docs/experiments/creating
- **Traces:** https://docs.orq.ai/docs/observability/traces
- **Evaluators:** https://docs.orq.ai/docs/evaluators/overview

### orq.ai Prompt Capabilities
- Prompts are versioned — each edit creates a new version, previous versions are preserved
- Deployments link to specific prompt versions and model configurations
- Experiments can compare two prompt versions on the same dataset
- Template variables: `{{log.input}}`, `{{log.output}}`, `{{log.messages}}`, `{{log.retrievals}}`, `{{log.reference}}`

### orq MCP Tools

Use the orq MCP server (`https://my.orq.ai/v2/mcp`) as the primary interface. For operations not yet available via MCP, use the HTTP API as fallback.

**Available MCP tools for this skill:**

| Tool | Purpose |
|------|---------|
| `search_entities` | Find prompts (`type: "prompts"`) and deployments |
| `list_traces` | Pull recent traces to identify failures |
| `list_spans` | List spans within a trace |
| `get_span` | Get detailed span information |
| `create_experiment` | Run A/B experiment comparing prompt versions |
| `list_experiment_runs` | Check experiment progress |
| `get_experiment_run` | Get experiment results |

**HTTP API fallback** (for operations not yet in MCP):

```bash
# List prompts
curl -s https://my.orq.ai/v2/prompts \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Get prompt details with versions
curl -s https://my.orq.ai/v2/prompts/<ID> \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Create a new prompt version
curl -s -X POST https://my.orq.ai/v2/prompts/<ID>/versions \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"messages": [...], "model": "...", "parameters": {...}}' | jq
```

## Core Principles

### 1. Fix Specification Before Measuring Generalization
Most prompt failures are specification failures — the LLM had unclear instructions. Fix the prompt first, then measure whether the LLM generalizes correctly.

### 2. One Change at a Time
Isolate variables. If you change constraints AND examples AND persona simultaneously, you cannot attribute improvement to any single edit. Make one targeted change per iteration.

### 3. Always A/B Test Against Baseline
Never "vibes check" a prompt change. Run the old and new versions on the same dataset with the same evaluators. Subjective assessment is unreliable.

### 4. Prefer Constraints Over Examples
Cost-effectiveness hierarchy for prompt improvements:
1. **Add explicit constraints** — cheapest, most reliable ("Never mention competitor products")
2. **Add few-shot examples** — effective but increases token cost
3. **Strengthen persona** — helps with tone/style consistency
4. **Adjust output format** — structural changes (JSON schema, step-by-step)
5. **Tune temperature/parameters** — last resort, smallest effect

### 5. Version Everything
Never modify a live prompt in-place. Create a new version, test it, then promote. The old version remains as rollback.

## Destructive Actions

The following actions require explicit user confirmation via `AskUserQuestion` before execution:
- Promoting a new prompt version to a production deployment
- Overwriting or archiving an existing prompt version
- Modifying a live deployment's prompt configuration

## Steps

Follow these steps **in order**. Do NOT skip steps.

### Phase 1: Understand the Current Prompt

1. **Fetch the current prompt configuration:**
   - Use `search_entities` with `type: "prompts"` to find the target prompt
   - Use HTTP API to get full prompt details including all versions
   - Document: system message, user message template, model, parameters, any tools/knowledge attached

2. **Understand the deployment context:**
   - Which deployment uses this prompt?
   - What model and parameters are configured?
   - Are there guardrails or evaluators attached?

### Phase 2: Analyze Failures from Traces

3. **Pull recent traces** using `list_traces`:
   - Filter for the target deployment
   - Sample at least 30-50 traces
   - Include both successful and failed traces

4. **Categorize failures** into these types:

   | Failure Type | Description | Example |
   |-------------|-------------|---------|
   | **Missing constraint** | Prompt doesn't forbid something it should | Agent mentions competitor products |
   | **Ambiguous instruction** | Prompt is vague, LLM interprets differently than intended | "Be concise" means different things to different models |
   | **Persona drift** | LLM breaks character under certain inputs | Formal assistant becomes casual with slang input |
   | **Missing few-shot** | LLM doesn't understand expected format/style | Output format varies wildly |
   | **Output format violation** | LLM doesn't follow structural requirements | Returns prose when JSON was expected |
   | **Context overload** | Too much context, LLM ignores key instructions | Long system prompt, model skips constraints at the end |

5. **Quantify each failure type:**
   ```
   | Failure Type | Count | Rate | Severity |
   |-------------|-------|------|----------|
   | Missing constraint (competitor mentions) | 8 | 16% | High |
   | Persona drift on technical questions | 5 | 10% | Medium |
   | Output format violations | 3 | 6% | Low |
   ```

### Phase 3: Propose Targeted Edits

6. **For each failure type, propose a specific edit** using these techniques:

   **Constraint Addition** (for missing constraints):
   - Add explicit "DO NOT" rules near the beginning of the system prompt
   - Place critical constraints BEFORE lengthy context sections
   - Example: `"NEVER mention competitor products by name. If asked to compare, describe our features only."`

   **Few-Shot Injection** (for format/style issues):
   - Add 2-4 input/output examples demonstrating the desired behavior
   - Include at least one edge case example
   - Place examples AFTER instructions, BEFORE the actual input

   **Persona Strengthening** (for persona drift):
   - Add explicit persona boundaries: when to break character, how to handle off-topic
   - Add recovery instructions: "If you accidentally break character, acknowledge and return to persona"

   **Output Format Refinement** (for structural violations):
   - Add JSON schema or explicit format template
   - Add a "before you respond, verify your output matches this format" instruction

   **Context Restructuring** (for context overload):
   - Move critical instructions to the TOP of the system prompt
   - Use headers and sections for organization
   - Remove redundant or low-value context

7. **Present proposed edits to the user** before making changes. Show:
   - Current prompt section
   - Proposed change
   - Which failure type this targets
   - Expected impact

### Phase 4: Create Versioned Prompt Variant

8. **Create a new prompt version** with the proposed edits:
   - Use HTTP API to create a new version on the existing prompt
   - **Ask user confirmation** before modifying the prompt
   - Document what changed and why

9. **Keep changes minimal and targeted.** One iteration should address 1-2 failure types, not all at once.

### Phase 5: A/B Test with Experiment

10. **Set up a comparison experiment:**
    - Use `create_experiment` to create an experiment
    - Configure two runs: baseline (old version) vs variant (new version)
    - Use the same dataset for both runs
    - Attach evaluators that measure the targeted failure types

11. **Run the experiment and wait for results:**
    - Use `list_experiment_runs` to monitor progress
    - Use `get_experiment_run` to fetch results

### Phase 6: Analyze Results and Iterate

12. **Compare results:**
    ```
    | Evaluator | Baseline | Variant | Delta |
    |-----------|----------|---------|-------|
    | Competitor mentions | 84% pass | 96% pass | +12% |
    | Persona consistency | 90% pass | 91% pass | +1% |
    | Overall quality | 88% pass | 89% pass | +1% |
    ```

13. **Decision framework:**
    - **Clear win** (>5% improvement on target, no regression elsewhere) → Promote variant
    - **Mixed results** (improvement on target, regression elsewhere) → Investigate and iterate
    - **No improvement** → Re-examine failure analysis, try different technique
    - **Regression** → Revert to baseline, re-analyze

14. **If promoting:** Ask user confirmation, then update the deployment to use the new prompt version.

15. **If iterating:** Return to Phase 3 with updated failure analysis. Expect 2-4 iterations to converge.

## Anti-Patterns

| Anti-Pattern | Why It's Wrong | What to Do Instead |
|---|---|---|
| Rewriting the entire prompt at once | Cannot attribute improvement, high risk of regression | Make one targeted change per iteration |
| "Vibes checking" prompt changes | Subjective assessment is unreliable | Always A/B test with evaluators on a dataset |
| Tuning temperature first | Temperature has the smallest effect on quality | Fix constraints, examples, and persona first |
| Adding more context to fix failures | Longer prompts cause context overload | Restructure and trim instead of adding |
| Copying prompts from the internet | Generic prompts don't match your application | Build from your specific failure analysis |
| Testing on the same examples used to write the prompt | Overfitting to known cases | Use a held-out test set |
| Changing the model and prompt simultaneously | Cannot tell which change helped | Change one variable at a time |

## Open in orq.ai

After completing this skill, direct the user to the relevant platform page:

- **View/edit the prompt:** `https://my.orq.ai/prompts` — open the prompt editor to review versions and changes
- **Check traces:** `https://my.orq.ai/traces` — inspect recent traces for the deployment
- **View experiment results:** `https://my.orq.ai/experiments` — review the A/B test comparison
