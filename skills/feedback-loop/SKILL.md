---
name: feedback-loop
description: Set up and analyze user feedback collection to drive data-informed improvement cycles
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, WebFetch, Task, AskUserQuestion, mcp__linear-server__*, orq*
---

# Feedback Loop

Set up user feedback collection and analyze feedback patterns to drive data-informed improvement cycles — connecting production signals back into the evaluation lifecycle.

**Companion skills:**
- `trace-analysis` — deep-dive into traces flagged by negative feedback
- `action-plan` — prioritize improvements based on feedback patterns
- `prompt-learning` — automatically turn feedback patterns into prompt rules
- `scaffold-integration` — generate SDK code for feedback collection

## When to use

- User wants to collect end-user feedback (thumbs up/down, corrections)
- User wants to analyze existing feedback data for patterns
- User needs to connect feedback signals to the eval pipeline
- User asks about feedback integration or user satisfaction tracking
- User wants to validate evaluators against real user sentiment

## orq.ai Documentation

Consult these docs when working with the orq.ai platform:
- **Feedback overview:** https://docs.orq.ai/docs/feedback/overview
- **Feedback API:** https://docs.orq.ai/docs/feedback/api
- **Traces:** https://docs.orq.ai/docs/observability/traces
- **Trace automations:** https://docs.orq.ai/docs/observability/trace-automation
- **Python SDK:** https://docs.orq.ai/docs/sdks/python
- **Node SDK:** https://docs.orq.ai/docs/sdks/node

### orq.ai Feedback Capabilities
- Feedback is attached to traces via trace ID or span ID
- Supported feedback types: thumbs up/down (binary), correction text, free-text comments
- Feedback data is accessible via list_traces with feedback filters
- Trace automations can trigger on feedback events
- SDK provides direct feedback submission methods

### orq MCP Tools

Use the orq MCP server (`https://my.orq.ai/v2/mcp`) as the primary interface.

**Available MCP tools for this skill:**

| Tool | Purpose |
|------|---------|
| `list_traces` | Pull traces filtered by feedback status |
| `list_spans` | List spans within feedback-flagged traces |
| `get_span` | Get detailed span information |
| `search_entities` | Find deployments and agents with feedback data |

**Note on feedback data access:**
There is no separate HTTP API endpoint for listing feedback. Feedback data is attached to traces and accessed through the `list_traces` MCP tool with feedback filters. For feedback submission, use the orq.ai SDK methods (Python or Node) which handle attaching feedback to the correct trace or span.

## Core Principles

### 1. Feedback Is Signal, Not Ground Truth
Users can be wrong, biased, or inconsistent. Feedback indicates potential issues — always verify with trace analysis before acting.

### 2. Correlate Feedback with Evaluator Scores
The most valuable insight is the relationship between feedback and evaluators:
- Negative feedback + high evaluator score = **evaluator gap** (evaluator misses something users care about)
- Negative feedback + low evaluator score = **confirmed issue** (evaluator correctly detects the problem)
- Positive feedback + low evaluator score = **evaluator false alarm** (evaluator may be too strict)

### 3. Design for Sparse Signals
Most users don't leave feedback. 1-5% feedback rate is normal. Design analysis for low volume — don't wait for statistical significance on every question.

### 4. Close the Loop
Feedback is only valuable if it drives action. Connect feedback analysis to the eval lifecycle: update datasets, refine evaluators, fix prompts.

## Steps

Follow these steps **in order**. Do NOT skip steps.

### Phase 1: Set Up Feedback Collection

1. **Assess current feedback infrastructure:**
   - Is feedback already being collected?
   - What feedback types are available? (thumbs up/down, corrections, free-text)
   - How is feedback submitted? (SDK, UI, API)

2. **If feedback collection is not set up:**
   - Generate SDK integration code using `scaffold-integration`:
     - Thumbs up/down buttons linked to trace IDs
     - Optional correction field for user-provided fixes
     - Optional free-text comment field
   - Ensure trace IDs are propagated to the frontend for feedback linking

3. **Configure trace automations** for feedback events (optional):
   - Auto-flag traces with negative feedback for review
   - Auto-add negative-feedback traces to an annotation queue

### Phase 2: Pull Feedback Data

4. **Fetch traces with feedback:**
   - Use `list_traces` with feedback filters
   - Pull traces from a meaningful time period (7-30 days)
   - Categorize by feedback type: positive, negative, correction, comment

5. **Build the feedback dataset:**
   ```
   | Metric | Value |
   |--------|-------|
   | Total traces (period) | [N] |
   | Traces with feedback | [N] ([X%] feedback rate) |
   | Positive feedback | [N] ([X%]) |
   | Negative feedback | [N] ([X%]) |
   | With corrections | [N] |
   | With comments | [N] |
   ```

### Phase 3: Analyze Feedback Patterns

6. **Segment negative feedback:**
   - By deployment/agent: which resource gets the most negative feedback?
   - By time: are there temporal patterns (spikes, trends)?
   - By input type: do certain query types get more negative feedback?
   - By model: does feedback differ across models in fallback chains?

7. **Analyze correction content:**
   - What do users correct most often? (factual errors, format issues, missing info)
   - Are there common patterns in corrections?
   - Do corrections suggest new failure modes not covered by evaluators?

8. **Correlate with evaluator scores:**

   ```
   | Evaluator | Positive FB (avg score) | Negative FB (avg score) | Gap |
   |-----------|------------------------|------------------------|-----|
   | Policy faithfulness | 0.96 | 0.91 | 0.05 |
   | Persona consistency | 0.93 | 0.92 | 0.01 |
   | Helpfulness | 0.89 | 0.52 | 0.37 |
   ```

   Large gaps indicate evaluators that correctly detect user-perceived quality issues.
   Small gaps indicate evaluators that miss what users care about.

### Phase 4: Connect to Eval Lifecycle

9. **For confirmed issues** (negative feedback + low evaluator score):
   - These are already measured — check if action plans address them
   - Prioritize fixes based on feedback volume

10. **For evaluator gaps** (negative feedback + high evaluator score):
    - These indicate missing evaluators or overly lenient evaluators
    - Flag traces for `trace-analysis` to identify the missing failure mode
    - Build new evaluators using `build-evaluator`

11. **For evaluator false alarms** (positive feedback + low evaluator score):
    - Review evaluator criteria — may be too strict
    - Add more edge-case examples to evaluator validation set

12. **Expand datasets:**
    - Add negative-feedback traces to evaluation datasets
    - Use corrections as reference outputs
    - Reference `curate-dataset` for dataset quality management

### Phase 5: Produce Feedback Report

13. **Generate the feedback analysis report:**

    ```markdown
    # Feedback Analysis Report
    **Period:** [date range]
    **Traces analyzed:** [N]
    **Feedback rate:** [X%]

    ## Summary
    - Positive feedback: [N] ([X%])
    - Negative feedback: [N] ([X%])
    - Key finding: [one-sentence summary]

    ## Top Issues from Negative Feedback
    1. [Issue]: [N] occurrences, [description]
    2. [Issue]: [N] occurrences, [description]
    3. [Issue]: [N] occurrences, [description]

    ## Evaluator-Feedback Correlation
    [Correlation table from Phase 3]

    ## Evaluator Gaps (negative feedback, high eval score)
    [List with trace examples]

    ## Recommended Actions
    1. [Highest priority action]
    2. [Second priority]
    3. [Third priority]
    ```

### Phase 6: Recommend Automation

14. **Suggest ongoing automation:**
    - Trace automations triggered by negative feedback
    - Weekly/monthly feedback reports
    - Auto-routing of negative-feedback traces to annotation queues
    - Threshold alerts: "negative feedback rate exceeded X%"

## Anti-Patterns

| Anti-Pattern | Why It's Wrong | What to Do Instead |
|---|---|---|
| Treating feedback as ground truth | Users can be wrong or inconsistent | Use feedback as signal, verify with trace analysis |
| Waiting for statistical significance | Feedback is sparse, you'll wait forever | Act on patterns from 10-20 consistent signals |
| Ignoring positive feedback | Misses opportunities to understand what works | Analyze both positive and negative patterns |
| Collecting feedback without analyzing | Data rot — feedback becomes stale | Set up regular analysis cadence (weekly/monthly) |
| No correlation with evaluators | Can't tell if evaluators are missing something | Always compare feedback against evaluator scores |
| Only using thumbs up/down | Loses rich correction and comment data | Collect corrections and free-text when possible |

## Open in orq.ai

After completing this skill, direct the user to the relevant platform page:

- **View traces with feedback:** `https://my.orq.ai/traces` — filter traces by feedback status to see user signals
- **Trace automations:** `https://my.orq.ai/trace-automations` — set up automations triggered by negative feedback
- **Annotation queues:** `https://my.orq.ai/annotation-queues` — review traces flagged for human review
