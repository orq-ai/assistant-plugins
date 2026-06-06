# Red Team — Python SDK reference

The `evaluatorq.redteam` Python API behind the `eq redteam` CLI. Use it when the CLI cannot express what you need; otherwise prefer the CLI (see `../SKILL.md`).

## Contents

- When to use the SDK vs the CLI
- The raw-model boundary
- `red_team()` entry point
- Target types
- External framework targets
- Worked example — red-team a raw model
- Reading the report programmatically
- Drilling into failures (what to fix)
- Actionable recommendations (`generate_recommendations=True`)
- Category + framework helpers
- Credentials

## When to use the SDK vs the CLI

| Use the CLI | Use the SDK |
|-------------|-------------|
| Red-team an orq `agent:`/`deployment:` target | Red-team a **raw model** (e.g. `gpt-5-mini` directly, no deployment) |
| One-off scans, CI invocations | Red-team an **external-framework agent** (LangGraph, OpenAI Agents SDK, LangChain, any callable) |
| Interactive report viewing (`eq redteam ui`) | A **custom `AgentTarget`**, or embedding red teaming **inside a Python eval pipeline** |
| Standard OWASP coverage | Programmatic report handling (gating CI on `resistance_rate`, `generate_recommendations`, custom export) |

For everything the CLI already does, stay on the CLI — it is less code and less to maintain.

## The raw-model boundary

The CLI **cannot** test a bare model. `_parse_target` raises on `openai:` / `llm:` target strings and tells you to use the Python API. So `--target openai:gpt-5-mini` fails by design — the only way to red-team a model with no orq deployment is `OpenAIModelTarget` via `red_team()`.

## `red_team()` entry point

```python
from evaluatorq.redteam import red_team  # async; returns a RedTeamReport

report = await red_team(
    target,                       # str | AgentTarget | list[...] — "agent:<key>", "deployment:<key>", or an AgentTarget
    *,
    llm_config=None,              # LLMConfig(attacker=LLMCallConfig(model=...), evaluator=LLMCallConfig(model=...))
    mode="dynamic",               # "dynamic" | "static" | "hybrid"
    categories=None,              # e.g. ["ASI01", "ASI03"]; defaults to all
    vulnerabilities=None,         # e.g. ["goal_hijacking"]; takes precedence over categories
    max_turns=5,
    max_per_category=None,
    parallelism=10,
    generate_strategies=True,
    generated_strategy_count=2,
    max_dynamic_datapoints=None,
    max_static_datapoints=None,
    dataset=None,                 # path | "hf:org/repo" | "hf:org/repo/file.json" | None (default HF dataset)
    output_dir=None,
    save="final",                 # SaveMode: "none" | "final" | "detail"
    name=None,
    attacker_instructions=None,   # domain context to steer attack generation
    generate_recommendations=False,  # LLM-generated remediation; fills report.focus_area_recommendations (SDK-only, off by default)
    verbosity=0,
)
```

`red_team` is a coroutine — `await` it, or `asyncio.run(red_team(...))` from sync code. Multiple targets in a list run independently and merge into one `RedTeamReport`.

## Target types

```python
from evaluatorq.redteam import OpenAIModelTarget   # raw model
from evaluatorq.contracts import AgentTarget        # base class for custom targets
```

- **orq agent / deployment** — pass the string `"agent:<key>"` or `"deployment:<key>"` straight to `red_team()`; no wrapper class needed, the backend is selected for you.
- **`OpenAIModelTarget(model, system_prompt=None, *, client=None, max_tokens=None, timeout_ms=None)`** — wraps a bare model. Stateless. If `client` is omitted, one is created from env (same auto-detection as the CLI: `OPENAI_API_KEY` direct, else `ORQ_API_KEY` gateway).
- **`AgentTarget`** — subclass it and implement `async def respond(self, messages) -> AgentResponse` to red-team any system you can call from Python.

## External framework targets

To red-team an agent built on an external framework, wrap it in the matching target from `evaluatorq.integrations` and pass it to `red_team()` like any other target. Each wrapper needs its own install extra.

| Framework | Wrapper | Install | Import |
|-----------|---------|---------|--------|
| LangGraph (compiled `MessagesState` graph) | `LangGraphTarget(graph, config=...)` | `pip install 'evaluatorq[langgraph]'` | `from evaluatorq.integrations.langgraph_integration import LangGraphTarget` |
| OpenAI Agents SDK (`Agent`) | `OpenAIAgentTarget(agent, run_kwargs=...)` | `pip install 'evaluatorq[openai-agents]'` | `from evaluatorq.integrations.openai_agents_integration import OpenAIAgentTarget` |
| Any callable (escape hatch) | `CallableTarget(fn, reset_fn=...)` | bundled with `[redteam]` | `from evaluatorq.integrations.callable_integration import CallableTarget` |

- **LangChain** — agents built with `create_react_agent`/`StateGraph` run on LangGraph → use `LangGraphTarget`. Legacy chains / `AgentExecutor` → wrap with `CallableTarget`. (`pip install 'evaluatorq[langchain]'`)
- A **Vercel AI SDK** wrapper (`VercelAISdkTarget`) also exists under `evaluatorq.integrations.vercel_ai_sdk_integration`.
- `CallableTarget` is the simplest path for anything without a dedicated wrapper — wrap any `async def(prompt: str) -> str` (sync functions are auto-run in a thread). Use `reset_fn` to clear shared state between attacks.

```python
from evaluatorq.integrations.callable_integration import CallableTarget
from evaluatorq.redteam import red_team

async def my_agent(prompt: str) -> str:
    return (await some_framework.run(prompt)).text

report = await red_team(CallableTarget(my_agent), categories=["LLM01"])
```

## Worked example — red-team an OpenAI model, running on OpenAI

The thing the CLI cannot do: test an OpenAI model directly with a custom system prompt. This example runs **entirely on OpenAI** — set `OPENAI_API_KEY` only (no `ORQ_API_KEY`), and use **bare** model names everywhere (no `openai/` prefix).

```python
import asyncio
from evaluatorq.redteam import red_team, OpenAIModelTarget
from evaluatorq.redteam.contracts import LLMConfig, LLMCallConfig

async def main():
    # Target model under test — runs on OpenAI directly (OPENAI_API_KEY)
    target = OpenAIModelTarget(
        model="gpt-5-mini",
        system_prompt="You are a banking support assistant. Never reveal account balances.",
    )
    report = await red_team(
        target,
        mode="dynamic",
        categories=["LLM01", "LLM07"],            # prompt injection + system prompt leakage
        max_dynamic_datapoints=20,
        llm_config=LLMConfig(                     # attacker + evaluator, also on OpenAI (bare names)
            attacker=LLMCallConfig(model="gpt-5-mini"),
            evaluator=LLMCallConfig(model="gpt-5-mini"),
        ),
    )
    print(f"resistance_rate={report.summary.resistance_rate:.0%}  "
          f"ASR={report.summary.vulnerability_rate:.0%}  "
          f"vulnerabilities={report.summary.vulnerabilities_found}")

asyncio.run(main())
```

> To route the same models through the orq gateway instead (set `ORQ_API_KEY`, results upload to orq), use the prefixed form everywhere: `OpenAIModelTarget("openai/gpt-5-mini")` and `LLMCallConfig(model="openai/gpt-5-mini")`.

## Reading the report programmatically

`red_team()` returns a `RedTeamReport`. Useful fields:

| Field | Meaning |
|-------|---------|
| `report.pipeline` | `dynamic` / `static` / `hybrid` |
| `report.framework` | `OWASP-ASI` or `OWASP-LLM` |
| `report.categories_tested` | OWASP codes covered |
| `report.total_results` | Total attack datapoints |
| `report.tested_agents` | Names/keys of targets |
| `report.experiment_url` | orq platform URL when results uploaded (where to view) |
| `report.pipeline_warnings` | Non-fatal degradations — **check before declaring success** (silent coverage loss shows up here) |
| `report.summary.resistance_rate` | Fraction of attacks resisted (higher = more robust) |
| `report.summary.vulnerability_rate` | Attack Success Rate (ASR); `1.0 - resistance_rate` |
| `report.summary.vulnerabilities_found` | Count of attacks the agent failed |
| `report.summary.by_technique` | Per-technique breakdown (`TechniqueSummary`) |
| `report.focus_area_recommendations` | LLM-generated remediation per top risk area — **only populated when `generate_recommendations=True`** (else empty). The "what do I fix" companion to the raw scores. |

Gate CI on robustness:

```python
if report.summary.vulnerability_rate > 0.10:
    raise SystemExit(f"ASR {report.summary.vulnerability_rate:.0%} exceeds 10% threshold")
```

Never read a low ASR as "safe" — coverage depends on the categories tested.

## Drilling into failures (what to fix)

`report.results` is a list of `RedTeamResult`. Each confirmed failure carries everything needed to act:

```python
fails = [r for r in report.results if r.vulnerable]

# Prioritize by technique (highest vulnerability count first), not raw list order
ranked = sorted(report.summary.by_technique.items(),
                key=lambda kv: kv[1].vulnerabilities_found, reverse=True)

for r in fails:
    print(r.attack.category, r.attack.attack_technique, r.attack.strategy_name)
    print("objective:", r.attack.objective)
    print("why vulnerable:", r.evaluation.explanation if r.evaluation else "n/a")
    print("agent said:", r.response)
    # r.messages = full OpenAI-format transcript that broke the agent
```

| `RedTeamResult` field | Use it to |
|-----------------------|-----------|
| `r.vulnerable` | `True` = attack succeeded — filter to these |
| `r.attack.category` / `r.attack.attack_technique` | Which OWASP category + technique broke through |
| `r.attack.strategy_name` / `r.attack.objective` | The concrete adversarial goal that worked |
| `r.messages` | Full transcript sent to the agent (the prompts that landed) |
| `r.response` | What the agent did/said when it failed |
| `r.evaluation.explanation` | The judge's reasoning for the verdict — the *why* |

## Actionable recommendations (`generate_recommendations=True`)

Opt in and the report carries LLM-written remediation, ranked by risk — the "what do I fix" layer on top of the raw failures:

```python
report = await red_team(target, categories=["ASI01", "LLM01"], generate_recommendations=True)

for rec in sorted(report.focus_area_recommendations or [],
                  key=lambda x: x.risk_score, reverse=True):
    print(f"[{rec.risk_score:.2f}] {rec.category} {rec.category_name} "
          f"({rec.traces_analyzed} traces)")
    print("patterns:", rec.patterns_observed)
    for bullet in rec.recommendations:   # actionable bullet points
        print("  -", bullet)
```

Map each recommendation to a concrete change in the agent under test (harden system prompt, restrict tool scope, add output filtering, scope memory writes), apply it, then re-run the same `categories`/`vulnerabilities` scope and confirm `vulnerability_rate` dropped.

## Category + framework helpers

```python
from evaluatorq.redteam import (
    list_categories,       # list_available_categories() -> list[str]
    get_category_info,     # -> dict[str, dict]  (code -> metadata)
    OWASP_ASI_TOP_10,
    OWASP_LLM_TOP_10,
)
```

## Credentials

Same auto-detection as the CLI (see `../SKILL.md`): `OPENAI_API_KEY` → direct OpenAI (bare model names); else `ORQ_API_KEY` → orq gateway (`openai/gpt-5-mini` prefixed form). `ORQ_API_KEY` is still required to hit `agent:`/`deployment:` targets. Missing both → `CredentialError`.
