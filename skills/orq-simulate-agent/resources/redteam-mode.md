# Red-team Mode

When the user wants adversarial probing rather than persona-driven
simulation, call `red_team()` from `evaluatorq.redteam` directly. It already covers
attack categories, dynamic dataset generation, and multi-turn jailbreak
flows, so there is no need to hand-roll a simulator.

## When to use red_team() instead of this skill

- Goal is to find policy violations, jailbreaks, or unsafe outputs
- User mentions OWASP LLM categories (LLM01–LLM10)
- User wants automated probing rather than realistic conversations

## Minimal example

```python
import asyncio
from evaluatorq.redteam import red_team, OpenAIModelTarget
from evaluatorq.redteam.contracts import TargetConfig

async def main():
    report = await red_team(
        OpenAIModelTarget("gpt-4o-mini"),   # llm:/openai: string prefixes are rejected — wrap raw models
        mode="dynamic",
        categories=["LLM01", "LLM07"],     # prompt injection, system prompt leakage
        max_dynamic_datapoints=5,
        max_turns=2,                       # multi-turn attack depth
        generate_strategies=False,
        target_config=TargetConfig(system_prompt="You are a customer support agent."),
    )
    print(report.summary)

asyncio.run(main())
```

## Outputs

- Local: `.evaluatorq/runs/<name>_<timestamp>.json` (relative to the working directory; override the store root with `$EVALUATORQ_DIR` — never `~/`)
- orq.ai: auto-uploaded as an Experiment run when `ORQ_API_KEY` is set
- Local UI: `eq redteam ui` opens a dashboard with Summary, Breakdown, Explorer, and Methodology tabs always present, plus Usage, Error Analysis, and Comparison tabs when the run has usage data, errors, or multiple agents

## When red_team() is NOT enough

Switch back to the persona-driven loop in this skill when:

- The goal is realism, not adversarial coverage
- Personas need to span non-attack axes (politeness, urgency, expertise)
- The user wants the simulated conversations to seed an experiment dataset
