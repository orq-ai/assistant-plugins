---
name: simulate-agent
description: >
  Run multi-turn agent simulations using evaluatorq's first-class simulation
  primitives (`simulate()`, `generate_and_simulate()`, `wrap_simulation_agent()`).
  Drive an agent under test with a simulated user LLM, scored by a built-in
  JudgeAgent that decides per turn whether the goal was achieved or rules
  broken. Use when generating realistic multi-turn data for experiments,
  stress-testing conversational agents, or producing seed transcripts for
  dataset curation. Do NOT use when you have enough real production
  conversations (use `analyze-trace-failures`). Do NOT use for adversarial
  red-teaming sweeps (use evaluatorq's built-in `red_team()` directly, see
  `resources/redteam-mode.md`).
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, WebFetch, Task, AskUserQuestion, orq*
---

# Simulate Agent

You are an **orq.ai agent simulation specialist**. Your job is to set up
multi-turn simulations using `evaluatorq.simulation`, then make sure the
resulting transcripts land somewhere the user can inspect and reuse.

The simulation framework runs three agents per turn: a **UserSimulatorAgent**
plays the persona, the **agent under test** responds, and a **JudgeAgent**
decides whether to continue or terminate based on goal achievement and rule
violations. You almost never need to hand-roll the loop.

## Constraints

- **NEVER** use the same model for the user simulator and the agent under test if a downstream LLM-as-judge is reading both. Collusion inflates scores. `simulate()` accepts one `model=` kwarg that drives the simulator, judge, and first-message generator. The agent's own model lives inside `agent_key` or the `target_callback`. To run the simulator on a different model than the default, build a `UserSimulatorAgent(model=...)` and pass it as `user_simulator=`.
- **NEVER** let the simulator run unbounded. `simulate()` defaults to `max_turns=10`. Lower it for cheap exploration, raise it for memory tests.
- **NEVER** hand-roll the loop around `orq.agents.responses.create()` when `simulate()` or `wrap_simulation_agent()` covers the case. The framework already handles parallelism, judge-based termination, OTel tracing, and result conversion.
- **NEVER** invent persona scalars from a one-line brief. `patience`, `assertiveness`, `politeness`, `technical_level` are floats `[0-1]`. Pick them deliberately and write them down.
- **NEVER** discard the conversation log. `SimulationResult.messages` is the primary artifact, OTel spans land in orq.ai automatically, and `wrap_simulation_agent()` returns a job that auto-uploads to orq.ai when you pass it to `evaluatorq(...)` with `ORQ_API_KEY` set.
- **ALWAYS** review at least one full transcript with the user before scaling to N personas. Simulated users go off the rails in ways only humans notice.
- **ALWAYS** sanitize untrusted persona/scenario text with `evaluatorq.common.sanitize.delimit()` when the content comes from external input. Wrap the `background` and `context` fields, which feed system-prompt context. The `goal` is shown to the simulator as a goal statement, so prefer sanitizing the inputs that build the system prompt rather than the goal itself.

**Why these constraints:** Unbounded loops burn tokens and produce repetitive late-turn dialog. Same-model simulator and agent score themselves favorably. Hand-rolled loops miss tracing and auto-upload. Persona scalars drive simulator behavior more than freeform text does.

## Companion Skills

- `generate-synthetic-dataset`, turn reviewed simulation transcripts into a curated dataset
- `run-experiment`, once transcripts exist, evaluate them with conversation-level scorers
- `build-evaluator`, design a `SimulationScorer` that reads `SimulationResult`
- `analyze-trace-failures`, prefer this if real production conversations already exist

## When to use

- "simulate a user talking to my agent for N turns"
- "I need 50 realistic conversations to seed a dataset"
- Stress-testing memory, context retention, or persona drift across turns
- Generating trajectories with built-in goal/criteria scoring

## When NOT to use

- Real production conversations are available, use `analyze-trace-failures`
- Single-turn input/output evaluation, use `run-experiment`
- Red-teaming sweeps with attack categories, call `evaluatorq.red_team()` directly (see [resources/redteam-mode.md](resources/redteam-mode.md))

## Workflow Checklist

```
Simulation Progress:
- [ ] Phase 1: Identify the agent under test and pick a target shape
- [ ] Phase 2: Define persona(s), scalars + communication_style + background
- [ ] Phase 3: Define scenario(s), goal + criteria + starting_emotion
- [ ] Phase 4: Pick the entry point (simulate / generate_and_simulate / wrap_simulation_agent)
- [ ] Phase 5: Dry-run one persona x one scenario, review the transcript
- [ ] Phase 6: Scale to N personas x M scenarios, surface where outputs live
```

---

## Phase 1: Identify the agent under test

Pick one of the three target shapes that `simulate()` accepts:

| Shape | When | How |
|---|---|---|
| `agent_key="..."` | Agent lives in orq.ai as a deployment | Pass the deployment key directly |
| `target_callback=fn` | Agent is a local function or third-party SDK | Wrap with `from_chat_completions(...)` or write a `Callable[[list[Message]], str]` |
| `target=AgentTarget(...)` | Full control over memory, clone, agent context | Implement the `AgentTarget` protocol from `evaluatorq.contracts` |

For driving an LLM directly through the orq.ai Responses API (rather than an agent deployment), see `OrqResponsesTarget(config=LLMCallConfig(...))` from `evaluatorq.simulation`.

If the user wants to drive an existing orq agent, use `search_entities` with `type: "agent"` to resolve the key. Verify it answers one turn end-to-end before wrapping it in a loop.

The framework ships `from_orq_deployment(agent_key)` and `from_chat_completions(fn)` adapters in `evaluatorq.simulation.adapters`. For Vercel AI SDK or LangChain agents, write a small `target_callback` that calls your agent's generate/invoke method and returns the assistant text.

## Phase 2: Define the persona

A `Persona` has seven required fields and two optional ones. Fill all seven explicitly. The simulator reads the scalars to decide tone, length, escalation behavior, and willingness to cooperate.

| Field | Type | Notes |
|---|---|---|
| `name` | str | Stable handle, used in trace metadata |
| `patience` | float `[0-1]` | 0 = explodes at the first delay, 1 = endless wait |
| `assertiveness` | float `[0-1]` | 0 = defers, 1 = pushes for outcomes |
| `politeness` | float `[0-1]` | 0 = rude, 1 = formal courtesy |
| `technical_level` | float `[0-1]` | 0 = non-technical, 1 = power user |
| `communication_style` | `CommunicationStyle` | `formal` / `casual` / `terse` / `verbose` |
| `background` | str | One-paragraph who-they-are |
| `emotional_arc` | `EmotionalArc?` | optional: `stable`, `escalating`, `de_escalating`, `volatile`, `manipulative`, `hostile` |
| `cultural_context` | `CulturalContext?` | optional: `neutral`, `direct`, `indirect`, `high_context`, `low_context`, `hierarchical` |

All seven required fields must be present when the persona is serialized into a `DataPoint`. The `wrap_simulation_agent()` wrapper calls `Persona.model_validate(inputs["persona"])` and Pydantic rejects any missing field.

See [resources/persona-scenario-template.md](resources/persona-scenario-template.md) for filled examples and the rendering helpers.

For multi-persona runs, vary along the scalar axes the agent should handle well. Generate personas as a grid across two or three scalars instead of writing freeform. Or use `PersonaGenerator` to synthesize from an agent description.

## Phase 3: Define the scenario

`Scenario` decouples *who* the user is from *what they want this conversation to be about*. The runner generates one datapoint per `(persona, scenario)` pair.

| Field | Type | Notes |
|---|---|---|
| `name` | str | Stable handle |
| `goal` | str | What the simulated user is trying to achieve |
| `context` | str? | Optional background the user has in mind |
| `starting_emotion` | `StartingEmotion?` | `neutral`, `frustrated`, `confused`, `happy`, `urgent` |
| `criteria` | `list[Criterion]?` | Each is `{description, type: must_happen \| must_not_happen}` |
| `is_edge_case` | bool? | Marks edge-case scenarios for analysis |
| `conversation_strategy` | `ConversationStrategy?` | `cooperative`, `topic_switching`, `contradictory`, `multi_intent`, `evasive`, `repetitive`, `ambiguous` |
| `ground_truth` | str? | What the correct outcome looks like (read by the judge) |
| `input_format` | `InputFormat?` | `plain_text`, `with_url`, `with_attachment`, `form_data`, `code_block`, `mixed_media` |

The built-in JudgeAgent reads `criteria` and decides per turn whether each is satisfied or violated. You don't write a `should_stop()` function. `Judgment.should_terminate` plus `max_turns` handles it.

## Phase 4: Pick the entry point

Three entry points, in order of preference:

| Entry point | Use when | Auto-upload to orq.ai |
|---|---|---|
| `wrap_simulation_agent()` | You want the simulation to flow through `evaluatorq()` with evaluators, datapoints, and experiments | Yes, when you pass the returned job into `evaluatorq(...)` with `ORQ_API_KEY` set |
| `simulate()` | You have personas + scenarios already and just want results back | Auto-upload tracked in RES-594 (in review). OTel tracing via `init_tracing_if_needed()` already works. Verify auto-upload status before depending on it. |
| `generate_and_simulate()` | You only have an `agent_description` and want personas + scenarios synthesized | Same as `simulate()` |

**Default to `wrap_simulation_agent()`** if the goal is to land results in an orq.ai experiment. It returns a job for `evaluatorq()` which inherits auto-upload, OTel, and the results table.

Note the kwarg naming differs across entry points: `simulate()` takes `evaluator_names=[...]` (defaults to `["goal_achieved", "criteria_met"]`); `wrap_simulation_agent()` takes `evaluators=[...]` for the same purpose.

Full code in [resources/simulation-loop.md](resources/simulation-loop.md).

## Phase 5: Dry-run and review

Run one persona × one scenario with `max_turns=3` first. Print:

- `result.messages`, the full transcript
- `result.terminated_by`, one of `judge`, `max_turns`, `error`, `timeout`
- `result.goal_achieved`, `result.goal_completion_score`
- `result.rules_broken`

Show that to the user. Ask:

1. Does the simulated user stay in character with the persona scalars?
2. Does the agent's behavior look representative of what production users would see?
3. Did the JudgeAgent terminate at the right point, or did it stop too early / run to `max_turns` unnecessarily?

Only after the user confirms, scale to the full persona × scenario grid. This review is the single biggest quality lever.

## Phase 6: Surface where outputs live

| Location | What's there | How to access |
|---|---|---|
| **OTel spans in orq.ai** | Per-turn LLM calls, judge decisions, token usage, pipeline span | Traces tab in orq.ai, auto-emitted via `init_tracing_if_needed()` |
| **`SimulationResult` in memory** | Full transcript, judge verdicts, turn metrics, criteria results, evaluator scores under `metadata["evaluator_scores"]` | Returned by `simulate()` or accessible via the job output |
| **orq.ai Experiment** | When the job is passed into `evaluatorq()`, results auto-upload as an Experiment run | URL printed to stdout when the run finishes |
| **JSONL export** | `export_results_to_jsonl(results, path)` for offline review or dataset seeding. `export_datapoints_to_jsonl()` and `load_datapoints_from_jsonl()` round-trip datapoints for reproducibility | Local file |

Tell the user all four. The OTel span and Experiment URL are what designers and PMs will want. The JSONL export is what engineers diff between runs.

## Done When

- At least one persona × scenario simulated end-to-end with the agreed `max_turns`
- `SimulationResult.terminated_by` is `judge` (not `max_turns`) for the majority of runs, OR the user has acknowledged that hitting `max_turns` is acceptable for this experiment
- Spans visible in orq.ai under the simulation pipeline
- User has reviewed at least one transcript and signed off on quality
- If routed through `evaluatorq()`: Experiment URL printed and surfaced to the user

---

## Companion resources

- [resources/persona-scenario-template.md](resources/persona-scenario-template.md), `Persona` and `Scenario` filled examples with all enum values listed
- [resources/simulation-loop.md](resources/simulation-loop.md), `simulate()`, `generate_and_simulate()`, `wrap_simulation_agent()` patterns with all target shapes
- [resources/redteam-mode.md](resources/redteam-mode.md), when to switch to `evaluatorq.red_team()` instead
