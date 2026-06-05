# Red-team Mode

When the user wants adversarial probing rather than persona-driven
simulation, call `evaluatorq.red_team()` directly. It already covers
attack categories, dynamic dataset generation, and multi-turn jailbreak
flows, so there is no need to hand-roll a simulator.

## When to use red_team() instead of this skill

- Goal is to find policy violations, jailbreaks, or unsafe outputs
- User mentions OWASP LLM categories (LLM01–LLM10)
- User wants automated probing rather than realistic conversations

## Minimal example

```python
import asyncio
from evaluatorq import red_team, TargetConfig

async def main():
    report = await red_team(
        "llm:gpt-4o-mini",
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

- Local: `~/.evaluatorq/runs/<name>_<timestamp>.json`
- orq.ai: auto-uploaded as an Experiment run when `ORQ_API_KEY` is set
- Local UI: `eq redteam ui` opens five dashboard tabs (Summary, Breakdown, Explorer, Usage, Methodology)

## When red_team() is NOT enough

Switch back to the persona-driven loop in this skill when:

- The goal is realism, not adversarial coverage
- Personas need to span non-attack axes (politeness, urgency, expertise)
- The user wants the simulated conversations to seed an experiment dataset
