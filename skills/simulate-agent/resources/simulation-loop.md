# Simulation Loop

The framework provides three entry points. Pick by what you have on hand.

| Entry point | Input you have | What it does |
|---|---|---|
| `wrap_simulation_agent()` | Persona + scenario per DataPoint | Returns a job for `evaluatorq()`. When passed into `evaluatorq(...)`, that call auto-uploads to orq.ai with OTel and the results table |
| `simulate()` | Lists of personas and scenarios (or pre-built datapoints) | Runs the simulation directly, returns `list[SimulationResult]` |
| `generate_and_simulate()` | Only an `agent_description` | Synthesizes personas and scenarios first, then simulates |

All three accept the same four target shapes for the agent under test:
`agent_key`, `target_callback`, `OrqResponsesTarget`, or a custom
`AgentTarget`.

## Target shapes

```python
from evaluatorq.simulation import (
    from_orq_deployment,
    from_chat_completions,
    OrqResponsesTarget,
)
from openai import AsyncOpenAI

# (1) Orq deployment, use the agent_key argument directly OR build a callback:
callback = from_orq_deployment("agent_xyz")

# (2) Raw OpenAI / Azure / any OpenAI-compatible provider:
client = AsyncOpenAI()
async def chat_fn(messages):
    resp = await client.chat.completions.create(
        model="azure/gpt-4o", messages=messages,
    )
    return resp.choices[0].message.content
callback = from_chat_completions(chat_fn)

# (3) Orq Responses API target:
target = OrqResponsesTarget(agent_key="agent_xyz")

# (4) Vercel AI SDK / LangChain / custom, write your own callback:
async def callback(messages):
    history = [{"role": m.role, "content": m.content or ""} for m in messages]
    return await my_agent.generate(history)
```

For full control over memory, clone, and agent context, implement
`AgentTarget` from `evaluatorq.contracts` and pass it as `target=` instead.

## Pattern 1: `wrap_simulation_agent()`, recommended

Returns a job for `evaluatorq()`. When passed into `evaluatorq(...)` with
`ORQ_API_KEY` set, that call auto-uploads results to orq.ai, emits OTel
spans, and lands a row in the Experiments table.

```python
import asyncio
from evaluatorq import evaluatorq, DataPoint
from evaluatorq.simulation import wrap_simulation_agent

job = wrap_simulation_agent(
    name="refund-flow-sim",
    agent_key="agent_xyz",            # or target_callback=callback
    max_turns=6,
    model="azure/gpt-4o-mini",        # simulator + judge + first-message-gen model
    evaluators=["goal_achieved", "criteria_met"],   # NB: 'evaluators', not 'evaluator_names'
)

# One DataPoint encodes exactly one (persona, scenario) pair.
# Both persona and scenario dicts must include every required field on the
# Pydantic models. The wrapper calls Persona.model_validate() and
# Scenario.model_validate() which reject missing fields.
data = [
    DataPoint(inputs={
        "persona": skeptical_founder.model_dump(),
        "scenario": refund_digital.model_dump(),
    }),
    # ...more pairs
]

async def main():
    await evaluatorq(
        "agent-simulation",
        data=data,
        jobs=[job],
        evaluators=[],   # add SimulationScorers or a conversation judge here
    )

asyncio.run(main())
```

The `DataPoint.inputs` may also be `{"datapoint": full_datapoint_dict}` when
you have a pre-built `Datapoint` (persona + scenario + first_message), or
`{"personas": [one], "scenarios": [one]}` for the array form. The wrapper
enforces 1:1. For many-to-one batches use `simulate()` directly.

## Pattern 2: `simulate()`, direct call

Use when you don't need an evaluatorq Experiment and just want the
`SimulationResult` list in memory.

```python
import asyncio
from evaluatorq.simulation import simulate

async def main():
    results = await simulate(
        evaluation_name="refund-flow-sim",
        agent_key="agent_xyz",                # or target_callback=..., or target=AgentTarget(...)
        personas=[skeptical_founder, patient_grandparent],
        scenarios=[refund_digital, lost_password],
        max_turns=6,
        model="azure/gpt-4o-mini",
        evaluator_names=["goal_achieved", "criteria_met"],   # NB: 'evaluator_names', not 'evaluators'
        parallelism=5,
    )

    for r in results:
        print(r.terminated_by, r.goal_completion_score, r.rules_broken)

asyncio.run(main())
```

The runner generates 4 datapoints (2 personas × 2 scenarios), calls
`FirstMessageGenerator` for each pair, and runs the conversations in
parallel. `evaluator_names` defaults to `["goal_achieved", "criteria_met"]`.
The default model is `azure/gpt-4o-mini` (`DEFAULT_MODEL`).

> **Auto-upload status:** `simulate()` is being refactored to route through
> `evaluatorq()` for auto-upload + OTel + Experiment landing (RES-594, in
> review). Until it merges, `wrap_simulation_agent()` is the only path with
> guaranteed auto-upload. Tracing via `init_tracing_if_needed()` already
> works on the direct path.

## Pattern 3: `generate_and_simulate()`, when you only have an agent description

```python
import asyncio
from evaluatorq.simulation import generate_and_simulate

async def main():
    results = await generate_and_simulate(
        evaluation_name="refund-flow-sim",
        agent_description=(
            "Customer support agent for a digital downloads marketplace. "
            "Handles refunds, license keys, download link regeneration."
        ),
        agent_key="agent_xyz",
        num_personas=5,
        num_scenarios=5,
        max_turns=6,
        model="azure/gpt-4o-mini",
    )

asyncio.run(main())
```

Runs `PersonaGenerator` + `ScenarioGenerator` in parallel from the agent
description, then proceeds as `simulate()`. Produces `num_personas ×
num_scenarios` simulations.

## Reading `SimulationResult`

```python
from evaluatorq.simulation import SimulationResult, TerminatedBy

r: SimulationResult = results[0]

r.messages                  # list[Message], full transcript
r.terminated_by             # TerminatedBy.judge | max_turns | error | timeout
r.reason                    # judge's reason string
r.goal_achieved             # bool
r.goal_completion_score     # float
r.rules_broken              # list[str]
r.turn_count                # int
r.token_usage               # TokenUsage
r.turn_metrics              # list[TurnMetrics] with per-turn judge verdicts
r.criteria_results          # dict[str, bool] | None, per-criterion satisfaction
r.metadata                  # dict, evaluator scores land here under "evaluator_scores"
                            # ("evaluation_name" key set only when evaluation_name was non-empty)
```

The built-in `JudgeAgent` produces `Judgment` per turn (see
`evaluatorq.simulation.types.Judgment`). You don't write stop logic, you
configure `criteria` on the scenario and let the judge decide.

## Custom scoring

For analysis beyond the built-in `goal_achieved` / `criteria_met` evaluators,
write a `SimulationScorer` and register it in the
`SIMULATION_EVALUATORS` dict, then reference by name:

```python
from evaluatorq.simulation import SimulationResult
from evaluatorq.simulation.evaluators import SIMULATION_EVALUATORS

def avg_turn_length(result: SimulationResult) -> float:
    if not result.messages:
        return 0.0
    return sum(len(m.content or "") for m in result.messages) / len(result.messages)

SIMULATION_EVALUATORS["avg_turn_length"] = avg_turn_length

# Then:
await simulate(..., evaluator_names=["goal_achieved", "criteria_met", "avg_turn_length"])
```

For evaluators that need `SimulationResult` fields directly (turn metrics,
judge verdicts), keep them as `SimulationScorer`. For evaluators that read
the `OpenResponses` output from `to_open_responses()`, write a normal
evaluatorq `Scorer`. `wrap_simulation_agent()` already converts the result.

## Quality and perturbation

For input-variation stress tests short of full red-teaming, see
`evaluatorq.simulation.quality.message_perturbation`. It exposes
`apply_perturbation`, `apply_random_perturbation`,
`apply_perturbations_batch`, and a `PerturbationType` enum for transforming
messages before they reach the agent under test.

## Exporting and reloading transcripts

```python
from evaluatorq.simulation import (
    export_results_to_jsonl,
    export_datapoints_to_jsonl,
    load_datapoints_from_jsonl,
    results_to_jsonl,
    parse_jsonl,
)

export_results_to_jsonl(results, "out/simulations.jsonl")
export_datapoints_to_jsonl(datapoints, "out/datapoints.jsonl")
reloaded = load_datapoints_from_jsonl("out/datapoints.jsonl")
```

Round-tripping datapoints makes simulation runs reproducible. Use these
when seeding a dataset for `run-experiment` or
`generate-synthetic-dataset`.

## TypeScript

The TypeScript port (`@orq-ai/evaluatorq`'s `./simulation` export) ships
the original RES-544 / RES-553 surface: `simulate()`, `wrapSimulationAgent()`,
`toOpenResponses()`, plus Vercel AI SDK and LangChain adapters. Python is
the more complete surface today. Mirroring of Python improvements
(persona scalars, criteria-driven judge, edge-case flag, OTel) to TS is
tracked under RES-599 (Todo). Verify TS parity per feature before relying
on it for anything beyond a basic loop.

## Where outputs land

- **OTel spans** appear automatically in orq.ai under the `orq.simulation.pipeline` span (per-turn LLM calls, judge verdicts, token usage)
- **`SimulationResult` objects** are returned in memory. Diff them between runs or export to JSONL
- **orq.ai Experiment** when the job is passed into `evaluatorq()` via `wrap_simulation_agent()`: URL printed to stdout on completion
- **JSONL export** via `export_results_to_jsonl()`, what engineers diff between runs
