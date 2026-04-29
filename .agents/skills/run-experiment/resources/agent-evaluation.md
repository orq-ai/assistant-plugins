# Agent Evaluation Methodology

Specialized methodology for evaluating **agentic and tool-calling pipelines**. Use alongside the core 5-phase workflow in SKILL.md.

## Contents
- Agent core principles
- 4-stage tool calling evaluation framework
- Transition failure matrix
- Partial credit for multi-component tasks
- Non-determinism: pass@k and pass^k
- Agent-type-specific guidance
- Agent grader types

## Agent Core Principles

### Define Agency Level First

Before you can evaluate an agent, you must define what level of agency is expected:

| Agency Level | Expected Behavior | Failure Looks Like |
|-------------|-------------------|-------------------|
| **High agency** | Acts autonomously, makes decisions, retries on failure | Stopping to ask when it should have proceeded |
| **Low agency** | Conservative, asks for clarification when uncertain | Acting autonomously when it should have asked |
| **Mixed** | Autonomous for routine tasks, asks on novel/risky ones | Misclassifying task as routine vs novel |

Without this definition, you cannot distinguish errors from intended behavior.

### Grade Outcomes, Not Paths

Agents regularly find valid approaches that eval designers did not anticipate. Checking exact tool call sequences is too rigid and produces brittle tests. Instead, verify that the **end state** is correct.

### Evaluate Stages Independently

Failures cascade in agent pipelines. A wrong tool selection causes wrong arguments, which cause wrong execution, which causes wrong output interpretation. Evaluate each stage independently to find the root cause.

## 4-Stage Tool Calling Evaluation Framework

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

## Transition Failure Matrix

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

## Partial Credit for Multi-Component Tasks

```
Task: Process customer complaint
Components:
- Identified customer: Pass (1/1)
- Looked up order: Pass (1/1)
- Applied correct policy: Fail (0/1)
- Communicated resolution: N/A (blocked by above)
Score: 2/3 completed steps (67%)
```

## Non-Determinism: pass@k and pass^k

Agent behavior varies between runs. Account for this:

| Metric | Formula | When to Use |
|--------|---------|-------------|
| **pass@k** | P(at least 1 success in k trials) | When one success suffices (research, exploration) |
| **pass^k** | (per-trial success)^k | When consistent reliability matters (customer-facing) |

Run 3-5 trials per task to capture variability.

## Agent-Type-Specific Guidance

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

## Agent Grader Types

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
