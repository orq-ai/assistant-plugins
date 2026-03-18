---
name: run-experiment
description: Create and run orq.ai experiments — compare configurations against datasets using evaluators, analyze results, with specialized methodology for agent, conversation, and RAG evaluation
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, Task, AskUserQuestion, mcp__linear-server__*, orq*
---

# Run Experiment

End-to-end workflow for evaluating LLM pipelines using the orq.ai platform. Covers general experiment design and execution, plus specialized methodology for agent evaluation, conversation evaluation, and RAG evaluation. Includes action planning for turning results into prioritized improvements.

**Companion skills:**
- `build-agent` — create and configure orq.ai agents
- `build-evaluator` — design judge prompts for subjective criteria
- `analyze-trace-failures` — build failure taxonomies from production traces
- `generate-synthetic-dataset` — generate diverse test scenarios
- `optimize-prompt` — analyze and rewrite prompts using a structured guidelines framework

## When to use

### General experiment triggers
- User wants to evaluate an LLM agent, deployment, or pipeline end-to-end
- User asks to create an evaluation dataset and run experiments
- User wants to analyze experiment results and create improvement tickets
- User asks to set up an eval loop (Analyze -> Measure -> Improve)
- User wants to run a new experiment after making changes
- User asks "how do I evaluate my LLM?"

### Agent evaluation triggers
- User wants to evaluate an AI agent (tool-calling, multi-step, autonomous)
- User needs to test tool selection, argument generation, or output interpretation
- User wants to debug a multi-step pipeline with cascading failures
- User asks about evaluating agentic workflows or agent reliability
- User has an orq.ai Agent and wants to assess its performance
- User mentions "tool calling evaluation" or "agent evaluation"

### Conversation evaluation triggers
- User wants to evaluate a chatbot or conversational agent
- User needs to test conversation memory, coherence, or context retention
- User asks about multi-turn evaluation or conversation quality
- User has a support/sales/coaching agent that runs multi-turn interactions
- User notices quality degradation over long conversations
- User mentions "multi-turn", "conversation quality", or "memory testing"

### RAG evaluation triggers
- User wants to evaluate a RAG system or knowledge-base-powered pipeline
- User asks "is my RAG retrieval working?" or "are my answers grounded?"
- User wants to optimize chunking strategy
- User needs to measure retrieval recall, precision, or faithfulness
- User has a pipeline using orq.ai Knowledge Bases
- User mentions "retrieval evaluation", "faithfulness", or "chunking"

### Action planning triggers
- User has experiment results and wants to know what to do next
- User asks "what should I fix first?" after running evals
- User wants a prioritized improvement plan based on scores
- User asks to create tickets/issues from experiment results
- User wants to close the loop: results -> action -> re-run

## When NOT to use

| If the user wants to... | Redirect to |
|-------------------------|-------------|
| Build or configure an agent (not evaluate one) | `build-agent` |
| Design a judge prompt in detail | `build-evaluator` |
| Analyze production traces without running an experiment | `analyze-trace-failures` |
| Generate synthetic data without running an experiment | `generate-synthetic-dataset` |
| Iterate on a prompt outside of an experiment loop | `optimize-prompt` |

## orq.ai Documentation

When working with the orq.ai platform, consult these docs pages as needed:

**Core experiment docs:**
- **Datasets overview:** https://docs.orq.ai/docs/datasets/overview
- **Creating datasets:** https://docs.orq.ai/docs/datasets/creating
- **Datasets API:** https://docs.orq.ai/docs/datasets/api-usage
- **Experiments overview:** https://docs.orq.ai/docs/experiments/overview
- **Creating experiments:** https://docs.orq.ai/docs/experiments/creating
- **Experiments API:** https://docs.orq.ai/docs/experiments/api
- **Evaluators overview:** https://docs.orq.ai/docs/evaluators/overview
- **Creating evaluators:** https://docs.orq.ai/docs/evaluators/creating
- **Evaluator library:** https://docs.orq.ai/docs/evaluators/library
- **Human review:** https://docs.orq.ai/docs/evaluators/human-review
- **Traces (for error analysis):** https://docs.orq.ai/docs/observability/traces
- **Trace automations:** https://docs.orq.ai/docs/observability/trace-automation
- **Deployments:** https://docs.orq.ai/docs/deployments/overview
- **Feedback:** https://docs.orq.ai/docs/feedback/overview
- **Analytics dashboard:** https://docs.orq.ai/docs/analytics/dashboards
- **Prompts:** https://docs.orq.ai/docs/prompts/overview
- **Prompt engineering guide:** https://docs.orq.ai/docs/prompts/engineering-guide
- **Annotation queues:** https://docs.orq.ai/docs/administer/annotation-queue

**Agent-specific docs:**
- **Agents overview:** https://docs.orq.ai/docs/agents/overview
- **Agent Studio:** https://docs.orq.ai/docs/agents/agent-studio
- **Agents API:** https://docs.orq.ai/docs/agents/agent-api
- **Tools overview:** https://docs.orq.ai/docs/tools/overview
- **Creating tools:** https://docs.orq.ai/docs/tools/creating
- **Tool calling:** https://docs.orq.ai/docs/proxy/tool-calling

**Conversation-specific docs:**
- **Conversations:** https://docs.orq.ai/docs/conversations/overview
- **Thread management:** https://docs.orq.ai/docs/proxy/thread-management
- **Conversation threads (observability):** https://docs.orq.ai/docs/observability/threads
- **Memory Stores overview:** https://docs.orq.ai/docs/memory-stores/overview
- **Memory Stores API:** https://docs.orq.ai/docs/memory-stores/using-memory-stores
- **Memory Stores in AI Studio:** https://docs.orq.ai/docs/memory-stores/using-memory-stores-in-the-ai-studio
- **Chat History tutorial:** https://docs.orq.ai/docs/tutorials/maintaining-history-with-a-model

**RAG-specific docs:**
- **Knowledge Base overview:** https://docs.orq.ai/docs/knowledge/overview
- **Creating Knowledge Bases:** https://docs.orq.ai/docs/knowledge/creating
- **Knowledge Base in Prompts:** https://docs.orq.ai/docs/knowledge/using-in-prompt
- **Knowledge Base API:** https://docs.orq.ai/docs/knowledge/api

### orq.ai Key Concepts
- **Datasets** contain Inputs, Messages, and Expected Outputs (all optional -- use what you need)
- **Experiments** run model generations against datasets, measuring Latency, Cost, and Time to First Token
- **Evaluators** types: LLM (AI-as-judge), Python (custom code), JSON (schema validation), HTTP (external API), Function (pre-built), RAGAS (RAG-specific)
- **Experiment Runs tab** lets you compare evaluator results across iterations
- **Review tab** shows individual responses with full context and evaluator results
- **Compare tab** shows multiple models side-by-side
- **Agents** can have **executable tools** in experiments -- fetching data, HTTP requests, Python code
- **Traces** show step-by-step tool interactions: tool names, args, payloads, and responses
- **Thread management** maintains conversation history across turns
- **Memory Stores** provide cross-session memory (persistent knowledge across conversations)
- **RAGAS evaluators** available in the Evaluator Library: Coherence, Conciseness, Correctness, Faithfulness, Context Precision, Context Recall, Context Entities Recall, Response Relevancy, Harmfulness, Maliciousness, Noise Sensitivity, Summarization
- LLM evaluators have access to `{{log.messages}}` (full conversation history) and `{{log.retrievals}}` (knowledge base results)
- Use **Prompts** to iterate on system prompts -- orq.ai supports versioned prompts
- Use **Deployments** to expose updated prompts/models to the API
- Use **Annotation queues** to validate evaluator accuracy with human judgment
- Use **Feedback** to collect user signals from production that inform next improvements

### orq MCP Tools

Use the orq MCP server (`https://my.orq.ai/v2/mcp`) as the primary interface. For operations not yet available via MCP, use the HTTP API as fallback.

**Available MCP tools for this skill:**

| Tool | Purpose |
|------|---------|
| `create_llm_eval` | Create an LLM evaluator |
| `list_traces` | List and filter traces for error analysis |
| `list_spans` | List spans within a trace |
| `get_span` | Get detailed span information |
| `get_agent` | Get agent configuration and details |
| `create_agent` | Create a new agent |
| `create_experiment` | Create and run an experiment |
| `list_experiment_runs` | List experiment runs |
| `get_experiment_run` | Get experiment run details |
| `list_models` | List available models (including embedding models) |

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

# Multi-turn agent testing (reuse task_id)
curl -s https://api.orq.ai/v2/agents/responses \
  -X POST \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"agent_key": "<KEY>", "message": {"role": "user", "parts": [{"kind": "text", "text": "Step 2"}]}, "task_id": "<task_id>"}' | jq

# Tool management
curl -s https://my.orq.ai/v2/tools \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Get tool details
curl -s https://my.orq.ai/v2/tools/<ID> \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Create a tool
curl -s https://my.orq.ai/v2/tools \
  -X POST \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"key": "get-weather", "description": "Get weather", "type": "function", "function": {"name": "get_weather", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}' | jq

# Memory store management (cross-session memory testing)
curl -s https://my.orq.ai/v2/memory-stores \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Create a memory store
curl -s https://my.orq.ai/v2/memory-stores \
  -X POST \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"key": "conversation-memory", "embedding_model": "text-embedding-3-small", "embedding_provider": "openai", "top_k": 5}' | jq

# Memory document management (test cross-session recall)
curl -s https://my.orq.ai/v2/memory-stores/<STORE_ID>/documents \
  -X POST \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text": "User prefers email communication", "metadata": {"source": "session_1"}}' | jq

# List memory documents
curl -s https://my.orq.ai/v2/memory-stores/<STORE_ID>/documents \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Knowledge base management
curl -s https://my.orq.ai/v2/knowledge \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Get knowledge base details
curl -s https://my.orq.ai/v2/knowledge/<ID> \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" | jq

# Create a knowledge base
curl -s https://my.orq.ai/v2/knowledge \
  -X POST \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"key": "product-docs", "embedding_model": "openai/text-embedding-3-small", "top_k": 5, "threshold": 0.7}' | jq

# Search a knowledge base (retrieval-only evaluation)
curl -s https://my.orq.ai/v2/knowledge/<ID>/search \
  -X POST \
  -H "Authorization: Bearer $ORQ_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "How do I reset my password?", "limit": 10, "threshold": 0.8}' | jq
```

## Prerequisites

- **orq.ai MCP server** connected (provides dataset, experiment, evaluator, and trace tools)
- **Linear MCP server** connected (for filing improvement tickets)
- A target LLM deployment/agent on orq.ai to evaluate

## Workflow Overview

```
Phase 1: Analyze     ->  Error analysis, identify failure modes
Phase 2: Design      ->  Create dataset + evaluator(s)
Phase 3: Measure     ->  Run experiment, collect scores
Phase 4: Act         ->  Analyze results, classify failures, plan improvements, file tickets
Phase 5: Re-measure  ->  Re-run after improvements (loop back to Phase 3)
```

---

## Steps

### Phase 1: Analyze -- Understand What to Evaluate

1. **Clarify the target system.** Ask the user:
   - What LLM deployment/agent are we evaluating?
   - What is the system prompt / persona / task?
   - What does "good" output look like? What does "bad" look like?
   - Are there known failure modes already?
   - **What type of system is it?** (general LLM, agent/tool-calling, multi-turn conversation, RAG pipeline, or a combination)

2. **Collect or generate evaluation traces.** Two paths:

   **Path A -- Real data exists:** Sample diverse traces from production using orq.ai trace tools. Target ~100 traces covering different features, edge cases, and difficulty levels.

   **Path B -- No real data yet:** Generate synthetic test cases using the structured approach:
   - Define **3+ dimensions** of variation (e.g., topic, difficulty, adversarial type, persona challenge)
   - Generate **tuples** of dimension combinations (aim for 20+ combinations)
   - Convert tuples to **natural language** test inputs in a separate step
   - Human review at each stage

3. **Error analysis.** For each trace:
   - Read the full trace (input + output, intermediate steps if agentic)
   - Note what went wrong (open coding -- freeform observations)
   - Identify the **first upstream failure** in each trace
   - Group failures into structured, non-overlapping failure modes (axial coding)

4. **Prioritize failure modes.** For each failure mode, decide:
   - **Fix the prompt first?** (specification failure -- ambiguous instructions)
   - **Code-based check?** (regex, assertions, schema validation)
   - **LLM-as-Judge?** (subjective/nuanced criteria that code can't capture)

### Phase 2: Design -- Create Dataset and Evaluator(s)

5. **Create the evaluation dataset** on orq.ai:
   - Use the orq MCP tools to create a dataset
   - Each datapoint should have: `input` (user message), `reference` (expected behavior/ideal output), and any relevant context
   - Include diverse scenarios: happy path, edge cases, adversarial inputs
   - **Minimum 8 datapoints** for a first pass; target 50-100 for production use
   - Tag datapoints by category/dimension for slice analysis
   - For multi-turn conversations, use the **Messages** column for conversation history
   - For RAG, include the correct source chunk IDs in the reference

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

### Phase 3: Measure -- Run the Experiment

8. **Create and run the experiment** on orq.ai:
   - Link the dataset and evaluator(s) to the experiment
   - Select the target model/deployment to evaluate
   - Configure system prompt (if testing prompt variations)
   - Run the experiment and wait for completion

9. **Collect results:**
   - Retrieve the experiment run results via orq MCP tools
   - Extract per-datapoint scores and overall metrics
   - Note the run cost for budget tracking

### Phase 4: Act -- Analyze Results, Plan Improvements, File Tickets

10. **Analyze results systematically:**
    - Sort scores to identify weakest areas
    - Look for patterns: which categories/dimensions score lowest?
    - Compare against threshold (if defined)
    - Identify the top 3-5 actionable improvements

11. **Present results** to the user in a clear format:

    ```
    | # | Scenario | Score | Category | Flag |
    |---|----------|-------|----------|------|
    | 1 | [worst]  | X     | ...      | ...  |
    | 2 | ...      | X     | ...      | ...  |
    ...
    | N | [best]   | X     | ...      | ...  |

    Average: X | Cost: $Y | Run: Z
    ```

12. **If previous runs exist**, show a comparison:

    ```
    | Scenario | Run 1 | Run 2 | Delta |
    |----------|-------|-------|-------|
    | ...      | 6     | 8     | +2    |
    | ...      | 9     | 7     | -2    |
    ```

    Flag any **regressions** (score decreased from previous run).

13. **Error analysis on low scores.** Read the actual traces behind the lowest-scoring datapoints. For each:
    - What was the input?
    - What did the LLM output?
    - What was the expected/reference behavior?
    - What specifically went wrong? (the evaluator's reasoning helps here)

14. **Classify each failure:**

    | Category | Description | Action |
    |----------|-------------|--------|
    | **Specification failure** | LLM was never told how to handle this case | Fix the prompt |
    | **Generalization failure** | LLM had clear instructions but still failed | Needs deeper fix |
    | **Dataset issue** | The test case or reference is flawed/ambiguous | Fix the dataset |
    | **Evaluator issue** | The judge scored incorrectly (false fail) | Fix the evaluator |

15. **Group failures by pattern.** Look for systematic issues:
    - Do all low scores share a category/dimension?
    - Is there a common failure mode?
    - Are certain input types consistently problematic?

16. **Apply the improvement hierarchy** (cheapest effective fix first):

    **Quick Wins (do these first, takes minutes to hours):**

    | Fix | When to Use | Example |
    |-----|-------------|---------|
    | Clarify prompt wording | Ambiguous or missing instructions | Add "Always respond in character, even for simple factual questions" |
    | Add few-shot examples | LLM needs demonstration of desired behavior | Add 2-3 examples of short factual answers in-character |
    | Add explicit constraints | Missing edge case handling | Add "Never break character, even if asked to" |
    | Role-based guidance | Persona/tone issues | Strengthen persona description in system prompt |
    | Request step-by-step reasoning | Complex reasoning failures | Add "Think through this step by step" |

    **Structural Changes (medium effort, hours to days):**

    | Fix | When to Use | Example |
    |-----|-------------|---------|
    | Task decomposition | Pipeline does too much in one step | Split "understand query + generate response" into two LLM calls |
    | Tool description improvements | Agent uses tools incorrectly | Clarify tool parameters and usage constraints |
    | Validation checks | Structured output issues | Add regex/schema validation before returning response |
    | RAG tuning | Retrieval-related failures | Adjust chunking, add re-ranking, rework retriever queries |

    **Heavier Fixes (high effort, days to weeks):**

    | Fix | When to Use | Example |
    |-----|-------------|---------|
    | Model upgrade | Prompt optimization exhausted, model lacks capability | Switch from gpt-4.1-mini to gpt-4.1 |
    | Expand eval dataset | Coverage gaps revealed | Add more adversarial cases, edge cases, dimension combinations |
    | Improve evaluator | Judge scores unreliable | Refine judge prompt, add few-shot examples, re-validate TPR/TNR |
    | Fine-tuning | Hundreds of labeled examples, prompting doesn't work | Fine-tune on high-quality examples (LAST RESORT) |

17. **Generate the action plan document:**

    ```markdown
    # Action Plan: [Experiment Name]
    **Run:** [run ID] | **Date:** [date] | **Average Score:** [X] | **Cost:** $[Y]

    ## Summary
    - [1-2 sentence overview of results]
    - [Key finding: what's working well]
    - [Key finding: what needs improvement]

    ## Priority Improvements

    ### P0 -- Fix Now (Quick Wins)
    1. **[Title]** -- [1-line description]
       - Affected: [which datapoints/scenarios]
       - Evidence: [scores and failure description]
       - Fix: [specific change to make]
       - Expected impact: [what should improve]

    ### P1 -- Fix This Sprint (Structural)
    2. **[Title]** -- [1-line description]
       ...

    ### P2 -- Plan for Next Sprint (Heavy)
    3. **[Title]** -- [1-line description]
       ...

    ## Dataset & Evaluator Improvements
    - [Any test cases to add/fix]
    - [Any evaluator calibration needed]

    ## Re-run Criteria
    - [ ] All P0 items completed
    - [ ] All P1 items completed (or deprioritized with justification)
    - [ ] Dataset updated (if applicable)
    - [ ] Evaluator updated (if applicable)
    - Ready to re-run experiment
    ```

18. **File tickets.** Ask the user where they want tickets created. Detect available project management tools by checking for connected MCP servers (Linear, Jira, GitHub, etc.) or ask the user directly. Options:
    - **Connected PM tool** (Linear, Jira, GitHub Issues, etc.) -- create tickets via MCP
    - **Markdown file** -- write an `action-plan-tickets.md` file in the project
    - **Skip** -- the action plan document is sufficient

    **Ticket structure:**
    ```
    Title: [P0/P1/P2] [Action verb] [specific thing]
    Priority: Urgent (P0) / High (P1) / Medium (P2)
    Description:
      ## Problem
      [What's failing and evidence from experiment]

      ## Root Cause
      [Specification failure / generalization failure / coverage gap]

      ## Proposed Fix
      [Specific, testable change]

      ## Success Criteria
      [What the re-run score should look like]

      ## Evidence
      - Datapoints affected: [list]
      - Current scores: [list]
      - Run ID: [id]
    ```

    Create a **"Re-run experiment"** ticket blocked by all improvement tickets. Create **"Expand eval coverage"** tickets for identified gaps.

### Phase 5: Re-measure -- Close the Loop

19. **After improvements are made**, re-run the experiment:
    - Verify improvement tickets are resolved
    - Update the dataset if new test cases were added
    - Update the evaluator if criteria changed
    - Run a new experiment with the same setup
    - Compare results to previous run(s)
    - File new tickets if regressions or new issues appear

20. **Track progress over time:**

    ```
    | Run | Date | Model | Avg Score | Cost | Key Changes |
    |-----|------|-------|-----------|------|-------------|
    | 1   | ...  | ...   | 7.75      | $0.005 | Baseline |
    | 2   | ...  | ...   | 8.50      | $0.005 | Improved system prompt |
    | 3   | ...  | ...   | 9.00      | $0.008 | Added adversarial cases, upgraded model |
    ```

    - Look for trends: improving? regressing? plateauing?
    - When scores stabilize above threshold, consider expanding test coverage

---

## Specialized Methodology: Agent Evaluation

Use this section when the system under test is an **agentic or tool-calling pipeline**. These methods supplement the core 5-phase workflow above.

### Agent Core Principles

#### Define Agency Level First

Before you can evaluate an agent, you must define what level of agency is expected:

| Agency Level | Expected Behavior | Failure Looks Like |
|-------------|-------------------|-------------------|
| **High agency** | Acts autonomously, makes decisions, retries on failure | Stopping to ask when it should have proceeded |
| **Low agency** | Conservative, asks for clarification when uncertain | Acting autonomously when it should have asked |
| **Mixed** | Autonomous for routine tasks, asks on novel/risky ones | Misclassifying task as routine vs novel |

Without this definition, you cannot distinguish errors from intended behavior.

#### Grade Outcomes, Not Paths

Agents regularly find valid approaches that eval designers did not anticipate. Checking exact tool call sequences is too rigid and produces brittle tests. Instead, verify that the **end state** is correct.

#### Evaluate Stages Independently

Failures cascade in agent pipelines. A wrong tool selection causes wrong arguments, which cause wrong execution, which causes wrong output interpretation. Evaluate each stage independently to find the root cause.

### 4-Stage Tool Calling Evaluation Framework

Evaluate each stage of tool calling independently:

**Stage 1 -- Tool Selection:**
- Did the agent pick the RIGHT tool?
- Common failures: wrong tool, hallucinated nonexistent tool, failed to call any tool when needed, tool confusion from ambiguous descriptions
- Grader: compare selected tool(s) against expected tool(s) for the task

**Stage 2 -- Argument Generation:**
- Are the arguments structurally valid AND semantically correct?
- Common failures: schema/type violations, missing required args, wrong formats, injection vulnerabilities
- Grader: schema validation (Pydantic/JSON schema) + semantic checks

**Stage 3 -- Execution Success:**
- Did the tool call complete and return expected results?
- Common failures: valid args but nonexistent entity (empty result), runtime errors, timeouts
- Grader: check for errors, empty results, timeouts in the trace

**Stage 4 -- Output Interpretation:**
- Did the agent correctly interpret and use the tool's response?
- Common failures: misinterpreted data points, ignored important details, failed to integrate output logically
- Grader: LLM-as-Judge comparing agent's interpretation against tool response

**Per-task stage tracking table:**
```
| Task | Tool Selection | Arg Generation | Execution | Interpretation | End-to-End |
|------|---------------|----------------|-----------|----------------|------------|
| T1   | Pass          | Pass           | Pass      | Fail           | Fail       |
| T2   | Fail          | N/A            | N/A       | N/A            | Fail       |
| T3   | Pass          | Pass           | Pass      | Pass           | Pass       |
```

### Transition Failure Matrix

Map the agent's workflow to discrete states:
```
ParseRequest -> DecideTool -> GenerateArgs -> ExecuteTool -> InterpretResult -> FormulateResponse
```
Include internal reasoning steps as states if they are visible in traces (e.g., `UpdatePlan`, `ReflectOnResult`).

For each failed trace, identify the FIRST state where something went wrong. Build the matrix:

```
First Failure In ->  Parse  Decide  GenArgs  Execute  Interpret  Respond
Last Success v
(start)               2      0        0        0         0         0
Parse                 -      5        0        0         0         0
Decide                0      -        3        0         0         0
GenArgs               0      0        -        8         0         0
Execute               0      0        0        -         4         1
Interpret             0      0        0        0         -         2
```

**Analyze the matrix:**
- Sum columns to find the most error-prone stages overall
- Inspect specific hot cells for targeted debugging
- Track the matrix over time -- fixes should make it sparser

### Partial Credit for Multi-Component Tasks

```
Task: Process customer complaint
Components:
- Identified customer: Pass (1/1)
- Looked up order: Pass (1/1)
- Applied correct policy: Fail (0/1)
- Communicated resolution: N/A (blocked by above)
Score: 2/3 completed steps (67%)
```

### Non-Determinism: pass@k and pass^k

Agent behavior varies between runs. Account for this:

| Metric | Formula | When to Use |
|--------|---------|-------------|
| **pass@k** | P(at least 1 success in k trials) | When one success suffices (research, exploration) |
| **pass^k** | (per-trial success)^k | When consistent reliability matters (customer-facing) |

Run 3-5 trials per task to capture variability.

### Agent-Type-Specific Guidance

**Coding Agents:**
- Use **deterministic test execution** as primary grader (tests pass/fail)
- Add code quality checks: linting, type checking, static analysis
- Verify tool usage patterns (read file before edit, run tests after change)

**Conversational Agents:**
- Evaluate **task completion AND interaction quality** separately
- Use a second LLM to simulate the user persona for multi-turn testing
- Measure turn efficiency (did the agent resolve in reasonable turns?)
- Test with user simulation tools or N-1 trace methodology

**Research Agents:**
- Check **groundedness** (claims supported by sources)
- Check **coverage** (key facts included)
- Check **source quality** (authoritative sources, not just first-retrieved)
- Calibrate LLM rubrics frequently against expert human judgment

### Agent Grader Types

Prefer deterministic graders when possible:

| Grader Type | Use For | Example |
|-------------|---------|---------|
| **State check** | End-state verification | `order.status == "refunded"` |
| **Schema validation** | Argument correctness | Pydantic model validation |
| **Tool call verification** | Required tools were used | Check trace for `verify_identity` call |
| **Output parsing** | Structured output correctness | JSON schema validation |

Use LLM-as-Judge graders for:
- Interaction quality / tone
- Whether the agent's reasoning was sound
- Whether the agent appropriately escalated vs handled
- Open-ended task completion assessment

---

## Specialized Methodology: Conversation Evaluation

Use this section when the system under test is a **multi-turn conversational system** (chatbot, support agent, coaching assistant). These methods supplement the core 5-phase workflow above.

### Conversation Core Principles

#### Start at Session Level

Always evaluate session-level success first: "Did the conversation achieve the user's goal?" Only drill into turn-level analysis when session-level failures need debugging.

#### Isolate Multi-Turn vs Single-Turn Failures

Before building multi-turn test infrastructure, try reproducing failures as single-turn tests. If the single-turn version still fails, the root cause is NOT multi-turn (it is a retrieval, knowledge, or reasoning issue).

#### Position-Dependent Degradation Is Common

Systems often degrade after 2-3 turns -- forgetting context, contradicting earlier responses, or drifting from instructions. Explicitly test for this.

### Session-Level vs Turn-Level Evaluation Hierarchy

| Level | What It Measures | When to Use | Cost |
|-------|-----------------|-------------|------|
| **Session level** | Did the full conversation achieve the goal? | Always -- start here | Low |
| **Turn level** | Was each individual response good? | Only when debugging specific failures | High |
| **Coherence** | Does the system maintain consistency across turns? | When contradiction/drift is suspected | Medium |
| **Memory** | Does the system remember earlier context? | When forgetting is suspected | Medium |
| **Efficiency** | How many turns to resolution? | When optimizing conversation flow | Low |

### Multi-Turn Test Data Collection

Three approaches for generating test conversations:

**Approach A -- Team simulation (recommended for starting):**
- Have 3-5 team members each simulate 10-15 realistic conversations
- Provide scenario cards describing the user's goal and persona
- Quickly generates 50-100+ diverse multi-turn traces
- Use a lightweight chat interface or orq.ai directly

**Approach B -- User simulation with LLM:**
- Use a second LLM to play the user role
- Define the user persona, goal, and behavior constraints
- The simulator sends messages; the agent under test responds
- Good for scaling but can produce unrealistic interactions
- Always have a human review a sample of simulated conversations

**Approach C -- N-1 testing (for targeted evaluation):**
- Take real conversations from production (or simulated ones)
- Provide the first N-1 turns as context
- Let the agent generate turn N
- Compare against the actual turn N (or expected behavior)
- More grounded but requires existing conversation data

### Perturbation Scenarios

| Perturbation | What It Tests | Example |
|-------------|--------------|---------|
| Goal change mid-conversation | Adaptability | "Actually, I changed my mind -- I want X instead" |
| Correction | Error handling | "No, I said the blue one, not the red one" |
| Ambiguity injection | Clarification behavior | "Can you help with the thing we discussed?" |
| Distraction | Focus maintenance | Unrelated question in the middle of a task |
| Escalation request | Handoff behavior | "I want to speak to a manager" |
| Contradiction | Consistency | Provide conflicting information across turns |
| Long gap | Memory retention | Resume conversation after many turns of other topics |

### Turn-Level Diagnostics

For sessions that failed, find the first failing turn:
- Read the conversation turn by turn
- Identify: at which turn did the conversation go off track?
- Was it a specific response that derailed things?
- Apply the "first upstream failure" principle

**Try to reproduce as a single-turn test:**
- Take the failing turn's context + input
- Run it as a standalone prompt
- If it still fails -> NOT a multi-turn issue (fix the prompt/model)
- If it passes as single-turn but fails in multi-turn -> genuine multi-turn issue (context management, memory, or position-dependent degradation)

**For genuine multi-turn failures, evaluate:**

| Check | Method | Evaluator Type |
|-------|--------|---------------|
| **Context retention** | Does the response reference earlier information correctly? | LLM-as-Judge with `{{log.messages}}` |
| **Instruction compliance** | Does the response follow rules stated earlier? | LLM-as-Judge checking specific rules |
| **Consistency** | Does the response contradict earlier statements? | LLM-as-Judge comparing turns |
| **Persona maintenance** | Does the character hold across turns? | LLM-as-Judge per-turn |
| **Position degradation** | Does quality drop after N turns? | Score by turn position, look for trends |

### Memory and Coherence Evaluation

**Within-session memory tests:**
- State a preference early in the conversation (e.g., "I prefer email communication")
- Check if the system honors it in later turns
- Vary the distance between stating and checking (1 turn, 5 turns, 10 turns)

**Cross-session memory tests (if using orq.ai Memory Stores):**
- Establish facts in Session 1
- Start a new Session 2
- Check if the system remembers facts from Session 1
- Test with: user preferences, conversation history, agreed-upon actions

**Coherence across long conversations:**
- Test with conversations of increasing length: 5, 10, 20, 50 turns
- Look for: contradictions, forgotten context, repeated questions, persona drift
- Plot quality scores against turn position to detect degradation curves

### Conversation Metrics Summary

| Metric | Level | What It Tells You |
|--------|-------|-------------------|
| **Session success rate** | Session | % of conversations achieving the goal |
| **Turns to resolution** | Session | Efficiency of the conversation |
| **First-failure turn** | Turn | Where conversations go wrong |
| **Context retention score** | Turn | Memory quality across turns |
| **Consistency score** | Coherence | Contradiction rate across turns |
| **Position degradation curve** | Coherence | Quality trend as conversations lengthen |
| **Cross-session recall** | Memory | Retention of facts across sessions |

---

## Specialized Methodology: RAG Evaluation

Use this section when the system under test is a **Retrieval-Augmented Generation pipeline**. These methods supplement the core 5-phase workflow above.

### RAG Core Principle: Evaluate Retrieval and Generation Separately

**NEVER** rely solely on end-to-end correctness scores. A wrong answer could mean:
- The retriever did not find the right documents (retrieval failure)
- The retriever found the right documents but the LLM ignored/misinterpreted them (generation failure)
- The query was ambiguous and the retriever parsed it incorrectly (query failure)

You **must** separate these to know what to fix.

### Retrieval Metrics

| Metric | Formula | When to Use |
|--------|---------|-------------|
| **Recall@k** | (relevant docs in top k) / (total relevant docs) | **Primary metric.** Missing information is worse than noise -- the LLM can ignore irrelevant chunks but cannot compensate for missing ones. |
| **Precision@k** | (relevant docs in top k) / k | When context window cost or noise matters. |
| **MRR** | 1 / (rank of first relevant doc) | When only one key fact is needed. Less useful for multi-hop questions. |
| **NDCG@k** | Normalized discounted cumulative gain | When chunks have graded relevance (not just binary). Rewards placing better items higher. Critical when context is truncated. |

**Prioritize Recall@k.** For RAG, high recall matters more than high precision because the LLM can ignore irrelevant context but cannot invent missing information.

### Recall Interpretation Table

| Recall@k | Interpretation | Action |
|----------|---------------|--------|
| > 90% | Strong retrieval | Focus on generation quality |
| 70-90% | Adequate but leaky | Improve embeddings, reranking, or query construction |
| < 70% | Retrieval is the bottleneck | Fix chunking, embedding model, or search strategy before evaluating generation |

### Chunking Optimization

Grid search over chunking parameters:
- **Chunk size:** 128, 256, 512, 1024 tokens
- **Overlap:** 0%, 10%, 25%, 50%
- Re-index the corpus for each configuration
- Measure Recall@k and NDCG@k for each

Consider content-aware chunking:
- Use document structure (headings, sections, paragraphs) instead of fixed token counts
- Augment chunks with contextual metadata (document title, section heading)
- For tables: test row-by-row vs column-by-column representations

### Generation Evaluators

Two key metrics for generation quality:

**Answer Faithfulness (grounding):**
- Does the output accurately reflect the retrieved context?
- Check for: hallucinations, omissions, misinterpretations
- Binary check per response: is everything in the answer supported by the retrieved docs?

**Answer Relevance:**
- Does the answer actually address the user's question?
- An answer can be faithful to context but miss the actual question
- Binary check: does the response answer what was asked?

### RAGAS Evaluators from orq.ai Library

Enable these from the Evaluator Library for RAG evaluation:
- **Faithfulness** -- checks if the answer is grounded in retrieved context
- **Context Recall** -- measures how much of the reference answer is supported by retrieved context
- **Context Precision** -- measures signal-to-noise ratio in retrieved context
- **Context Entities Recall** -- checks if key entities from the reference appear in retrieved context
- **Response Relevancy** -- checks if the answer addresses the question
- **Coherence** -- checks if the answer is logically coherent
- **Noise Sensitivity** -- measures robustness to irrelevant retrieved context

These evaluators use `{{log.retrievals}}` automatically.

For custom evaluators, use the `build-evaluator` skill with template variables: `{{log.input}}` (question), `{{log.output}}` (answer), `{{log.retrievals}}` (retrieved chunks), `{{log.reference}}` (ideal answer). One evaluator per criterion (faithfulness, relevance, completeness).

### RAG Failure Diagnosis Decision Tree

```
Question failed. Why?
+-- Retrieved chunks contain the answer?
|   +-- NO -> Retrieval failure. Fix search/chunking/embeddings.
|   +-- YES -> Generation failure. Fix prompt/model.
|              +-- Answer contradicts retrieved context? -> Hallucination
|              +-- Answer ignores retrieved context? -> Context utilization failure
|              +-- Answer is correct but misses the question? -> Relevance failure
```

### End-to-End RAG Evaluation

Run a full experiment combining retrieval + generation:
- Use `create_experiment` with the test dataset
- Attach both retrieval metrics and generation evaluators
- Compare against baseline

Track metrics across runs:

```
| Run | Recall@5 | Faithfulness | Relevance | End-to-End Accuracy | Changes |
|-----|----------|-------------|-----------|---------------------|---------|
| 1   | 72%      | 85%         | 90%       | 65%                 | Baseline |
| 2   | 88%      | 85%         | 90%       | 78%                 | New embedding model |
| 3   | 88%      | 92%         | 93%       | 85%                 | Added grounding instruction |
```

### Common RAG Improvement Actions

| Problem | Metric Signal | Fix |
|---------|--------------|-----|
| Missing relevant docs | Low Recall@k | Better embeddings, query expansion, reranking |
| Too much noise in context | Low Precision@k | Smaller k, reranking, chunk size reduction |
| Answer contradicts sources | Low Faithfulness | Add "only use provided context" instruction |
| Answer ignores the question | Low Relevance | Improve prompt to focus on the question |
| Inconsistent with doc structure | Low on tables/lists | Improve chunking strategy, test representations |

---

## Decision Trees

### "Should I fix the prompt or build an evaluator?"

```
Is the LLM explicitly told how to handle this case?
+-- NO -> Fix the prompt. This is a specification failure.
|         Re-run. If it still fails -> it's now a generalization failure.
+-- YES -> Is this failure catchable with code (regex, assertions)?
           +-- YES -> Build a code-based check.
           +-- NO -> Is this failure persistent across multiple traces?
                     +-- YES -> Build an LLM-as-Judge evaluator.
                     +-- NO -> It might be noise. Add more test cases first.
```

### "Should I upgrade the model?"

```
Have you tried:
+-- Clarifying the prompt? -> NO -> Do that first.
+-- Adding few-shot examples? -> NO -> Do that first.
+-- Task decomposition? -> NO -> Do that first.
+-- All of the above? -> YES -> Is the failure mode consistent?
|   +-- YES -> Model upgrade may help. Test with 2-3 models on a small subset.
|   +-- NO -> Add more test cases. Inconsistency suggests noise, not capability.
+-- Is cost a constraint?
    +-- YES -> Consider model cascades (cheap model first, escalate if unsure).
    +-- NO -> Upgrade to most capable model and re-evaluate.
```

### "When is the eval good enough to stop iterating?"

```
Is the average score above your threshold?
+-- NO -> Keep improving (follow the action plan).
+-- YES -> Check:
           +-- Are ANY individual scores below threshold? -> Fix those.
           +-- Is the dataset diverse enough (100+ traces, 3+ dimensions)? -> If not, expand.
           +-- Are adversarial cases covered (3+ per attack vector)? -> If not, add them.
           +-- Is the evaluator validated (TPR/TNR > 85%)? -> If not, validate.
           +-- All checks pass? -> Ship it. Set up production monitoring.
```

---

## Best Practice Reminders

### Dataset Design
- Use **structured synthetic data generation** (dimensions -> tuples -> natural language) for diversity
- Include **adversarial test cases** (jailbreaks, persona-breaking attempts, off-topic, language challenges)
- Test **both complex and simple** inputs -- simple factual questions often reveal persona drift
- Aim for at least **3 adversarial test cases** per anticipated attack vector
- For multi-turn: use the **Messages** column and include perturbation scenarios
- For RAG: map questions to exact source chunks and include adversarial synthetic questions

### Evaluator Design
- **Binary Pass/Fail > numeric scales** -- always challenge numeric scores
- **One evaluator per failure mode** -- resist the urge to bundle
- **Validate the judge** -- measure TPR/TNR on held-out labeled data before trusting it
- **Fix the prompt before building evals** -- many "failures" are just underspecified instructions
- For RAG: use RAGAS evaluators from the library as a starting point, then build custom judges for domain-specific criteria

### Experiment Execution
- **Start with the most capable judge model** (optimize cost later)
- **Record everything**: run ID, model, cost, date, dataset version
- **Compare apples to apples**: same dataset + evaluator across runs
- For agents: run **3-5 trials per task** to capture non-determinism
- For conversations: test with **increasing conversation lengths** (5, 10, 20+ turns)

### Result Analysis
- **A 100% pass rate means your eval is too easy**, not that your system is perfect -- target 70-85%
- **Look at the lowest scores first** -- they reveal the most about where to improve
- **Slice by category/dimension** to find systematic weaknesses
- **Cost matters** -- track it per run to inform model selection decisions
- For agents: analyze the **transition failure matrix** to find the most error-prone pipeline stage
- For conversations: check for **position-dependent degradation** by plotting scores against turn number
- For RAG: always check **retrieval metrics first** before investigating generation quality

### Ticket Filing
- **One ticket per improvement** -- not one mega-ticket
- **Block the re-run ticket** on all improvements -- prevents premature re-evaluation
- **Include evidence** -- specific datapoints, scores, and failure descriptions
- **Set clear success criteria** -- what score/behavior change indicates the fix worked
- **Score each improvement on impact vs effort** -- high impact + low effort first

---

## Anti-Patterns to Prevent

### General Anti-Patterns

| Anti-Pattern | What to Do Instead |
|---|---|
| Running experiments without a dataset | Always create a structured dataset first |
| Using generic "helpfulness" or "quality" evaluators | Build application-specific criteria from error analysis |
| One giant evaluator with 5+ criteria bundled | Separate evaluators per failure mode |
| Re-running experiments without fixing anything | File tickets, make changes, THEN re-run |
| Ignoring low scores on "easy" questions | Simple questions often reveal the most important failures |
| Skipping adversarial test cases | Always include jailbreak/persona-breaking/off-topic scenarios |
| Treating eval as a one-time setup | It is an ongoing loop -- Analyze -> Measure -> Improve -> repeat |
| Jumping to model upgrade after low scores | Fix prompt first, upgrade model last |
| Creating one mega-ticket for all improvements | One ticket per fix, each with clear success criteria |
| Ignoring false fails from the evaluator | Inspect judge reasoning; fix the evaluator if it is wrong |
| Re-running experiment without changing anything | Always make a specific, documented change before re-running |
| Optimizing for 100% pass rate | Target 70-85%; expand coverage if passing too easily |
| Building evaluators for specification failures | Fix the prompt first; only automate for generalization failures |
| Treating eval as a one-time activity | Production data drifts, new failure modes emerge |
| Fixing failures without updating the dataset | Add test cases for every failure you fix |

### Agent-Specific Anti-Patterns

| Anti-Pattern | What to Do Instead |
|---|---|
| Checking exact tool call sequences | Grade outcomes, not paths -- agents find valid alternative approaches |
| One grader for everything | Use isolated grader per dimension |
| No agency level definition | Define expected agency level (high/low/mixed) first |
| Only evaluating end-to-end | Evaluate 4 tool-calling stages independently |
| Single trial per task | Run 3-5 trials, use pass@k / pass^k |
| Testing only happy paths | Include adversarial, edge case, and escalation tasks |
| Over-relying on final outcome | Inspect the transition failure matrix for intermediate failures |
| Shared state between trials | Isolate each trial in a clean environment |

### Conversation-Specific Anti-Patterns

| Anti-Pattern | What to Do Instead |
|---|---|
| Evaluating every turn in every conversation | Start at session level, drill into turns only for failures |
| Building multi-turn tests for single-turn failures | Try reproducing as single-turn first |
| Only testing short conversations (2-3 turns) | Test with 5, 10, 20+ turn conversations |
| Using one LLM to both simulate and evaluate | Separate simulation model from judge model |
| No perturbation testing | Include goal changes, corrections, contradictions |
| Treating all turns equally | Focus analysis on the first failing turn |
| Ignoring memory store behavior | Explicitly test cross-session recall if using memory |

### RAG-Specific Anti-Patterns

| Anti-Pattern | What to Do Instead |
|---|---|
| Only measuring end-to-end accuracy | Evaluate retrieval and generation separately |
| Skipping retrieval evaluation | Always measure Recall@k first |
| Using only BLEU/ROUGE for generation | Use faithfulness + relevance evaluators |
| Fixed chunk size for all content | Use content-aware chunking where possible |
| Overfitting to synthetic eval dataset | Mix synthetic with manually curated questions |
| Choosing MRR when you need Recall | Use Recall@k for multi-hop, MRR for single-fact |
| Ignoring chunking as a tunable parameter | Grid search over chunk size and overlap |
| Feeding full documents to the LLM judge | Give the judge only the relevant chunk + question + answer |
