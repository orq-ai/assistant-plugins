---
name: red-team
description: >
  Invoke the evaluatorq red teaming CLI to run adversarial attacks against deployed
  agents or static datasets. Use when asked to "run a red team", "red team this
  deployment", "test my agent for vulnerabilities", "OWASP red team", or "check
  how my agent handles adversarial inputs". Do NOT use when you only need to build
  evaluators (use build-evaluator) or analyze existing trace failures (use
  analyze-trace-failures).
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, orq*
---

# Red Team

You are an **orq.ai red team operator**. Your job is to invoke the `eq redteam` CLI to run adversarial attacks against deployed agents or pre-built datasets, then read and interpret the resulting report.

This skill is a **reference guide and invocation helper — not a wrapper**. You call the CLI directly; you do not reimplement it.

## Constraints

- **NEVER** reimplement red teaming logic — use the `eq redteam` CLI.
- **NEVER** run against a deployment the user does not own or is not explicitly authorized to test. Confirm authorization before the first run.
- **NEVER** run without confirming the target key with the user first.
- **NEVER** skip the env var check — a missing or misconfigured LLM credential will fail mid-run.
- **ALWAYS** check that `eq` is installed before running (run `eq --help`).
- **NEVER** interpret a passing run (low ASR) as "the agent is safe" — coverage depends on categories tested.
- **BE AWARE** dynamic runs against an agent **that has a memory store** write entities into it (e.g. ASI06 memory-poisoning). These are cleaned up after the run unless `--no-cleanup-memory` is passed. No-op for memory-less agents, raw models, and static mode — but on a memory-backed production agent this mutates state, so confirm before running.

## Library location

The red teaming CLI is part of the `evaluatorq` package in `orqkit`:

```
orqkit/packages/evaluatorq-py
```

The package is installed as `evaluatorq` and exposes two equivalent entry points: `eq` and `evaluatorq`. The redteam subcommand is `eq redteam`.

**Always invoke via:**
```bash
eq redteam <command> [options]
```

If `eq` is not on PATH, install it with:
```bash
pip install 'evaluatorq[redteam]'
# or, inside the orqkit workspace:
uv run --package evaluatorq eq redteam --help
```

The `[redteam]` extra is required (pulls in `openai`, `typer`, `huggingface-hub`). The interactive dashboard (`eq redteam ui`) additionally needs the `[ui]` extra: `pip install 'evaluatorq[ui]'`.

## Required environment variables

The attack and evaluator LLMs need credentials. Routing is decided purely by **which env var is set** — the model string itself is never inspected for routing:

1. **OpenAI directly** — if `OPENAI_API_KEY` is set (optionally `OPENAI_BASE_URL`), all attack/evaluator model strings go straight to OpenAI. Use **bare** model names here (e.g. `gpt-5-mini`). `OPENAI_API_KEY` **wins if both keys are set**.
2. **orq gateway** — else if `ORQ_API_KEY` is set (optionally `ORQ_BASE_URL`), model strings route through the orq LLM gateway (`{ORQ_BASE_URL}/v3/router`, default `https://my.orq.ai`). Use the **provider-prefixed** form here (e.g. `openai/gpt-5-mini`).

If neither key is set the run fails with `CredentialError`. Separately, **`ORQ_API_KEY` is always required to invoke an `agent:`/`deployment:` target**, regardless of which key routes the attack LLM. There is no Azure credential path — the CLI does not support Azure OpenAI directly.

> Model-string form follows the route: bare `gpt-5-mini` for direct OpenAI, `openai/gpt-5-mini` for the orq gateway. Since hitting an orq agent already requires `ORQ_API_KEY`, the gateway form (`openai/gpt-5-mini`) is the common case.

Check before running:
```bash
# Verify the CLI is installed and reachable
eq --help || { echo "eq CLI not found — install 'evaluatorq[redteam]' or check PATH"; exit 1; }

# Verify required env vars
echo "ORQ_API_KEY set: $([ -n "$ORQ_API_KEY" ] && echo yes || echo NO — required for target agent)"
echo "OPENAI_API_KEY set: $([ -n "$OPENAI_API_KEY" ] && echo yes || echo not set — orq gateway will route attack LLM if ORQ_API_KEY is set)"
```

## Core command: `eq redteam run`

All three modes use the same `run` command with a `--mode` flag.

```bash
eq redteam run \
  --target agent:<deployment-key> \
  --mode dynamic \
  [--category ASI01] \
  [--category ASI02] \
  [--max-dynamic-datapoints 50] \
  [--attack-model openai/gpt-5-mini] \
  [--evaluator-model openai/gpt-5-mini] \
  [--save-report ./output/my-run/report.json] \
  [--yes]
```

### Mode flag

| Mode | Description |
|------|-------------|
| `dynamic` (default) | Live adaptive attack — generates adversarial prompts and sends them to the target agent |
| `static` | Evaluate a pre-built dataset (local file or HuggingFace) |
| `hybrid` | Both dynamic and static legs against the same target(s) |

### Key flags

These are the flags you need for a first run. For the complete set (`--max-static-datapoints`, `--max-per-category`, `--generated-strategy-count`, `--no-generate-strategies`, `--parallelism`, `--export-md`/`--export-html`, `--attacker-instructions`, `--name`, `--max-turns`, `--verbose`/`--quiet`, …) run `eq redteam run --help`.

| Flag | Default | Description |
|------|---------|-------------|
| `--target` / `-t` | required | `agent:<key>` or `deployment:<key>`. Repeatable for multi-target runs. (Raw models are SDK-only — see [Python SDK](#python-sdk-when-the-cli-cant)) |
| `--mode` | `dynamic` | Execution mode: `dynamic`, `static`, or `hybrid` |
| `--category` / `-c` | all | OWASP category to test (e.g. `ASI01`). **Repeatable** — pass once per category |
| `--vulnerability` / `-V` | all | Vulnerability ID (e.g. `goal_hijacking`) or OWASP code. Repeatable. Takes precedence over `--category` |
| `--max-dynamic-datapoints` | none | Cap dynamic (generated) attack datapoints — the main cost lever for a `dynamic` run |
| `--attack-model` | `gpt-5-mini` | Model generating adversarial prompts (prefix per route: `openai/gpt-5-mini` via gateway) |
| `--evaluator-model` | `gpt-5-mini` | Model judging whether attacks succeeded |
| `--system-prompt` | none | System prompt for the target model/agent |
| `--save` | `final` | `none` (no files, run not listed by `eq redteam runs`), `final` (summary JSON), or `detail` (all stage artifacts) |
| `--save-report` | none | Explicit path to write the report JSON |
| `--output-dir` | none | Directory for saved JSON stage files (**required** with `--save detail`) |
| `--dataset` | HuggingFace `orq/redteam-vulnerabilities` | Static/hybrid mode: local path, `hf:org/repo`, or `hf:org/repo/file.json` |
| `--no-cleanup-memory` | false | Keep memory entities written during a dynamic run instead of cleaning them up (debugging). Only relevant for memory-backed agents — see Constraints |
| `--yes` / `-y` | false | Skip confirmation prompt |

### Category examples

```bash
# Single category — Agent Goal Hijacking (OWASP ASI01)
eq redteam run --target agent:my-agent --category ASI01

# Prompt injection is LLM01 (not ASI01)
eq redteam run --target agent:my-agent --category LLM01

# Multiple categories — pass --category once per value
eq redteam run --target agent:my-agent --category ASI01 --category ASI02

# By vulnerability ID
eq redteam run --target agent:my-agent --vulnerability goal_hijacking

# Validate a custom dataset BEFORE running static mode against it
eq redteam validate-dataset ./my-dataset.json

# Static mode with local dataset
eq redteam run --target agent:my-agent --mode static --dataset ./my-dataset.json

# Hybrid mode — both dynamic and static legs
eq redteam run --target agent:my-agent --mode hybrid --category ASI01 --max-dynamic-datapoints 30 --max-static-datapoints 50

# Target a deployment instead of an agent
eq redteam run --target deployment:my-deployment --category ASI01
```

## Output and reports

After a run, the report is auto-saved to `.evaluatorq/runs/<name>_<ts>.json`. If `--save-report <path>` is passed, the report JSON is also written there.

With `--save detail` and `--output-dir <dir>`, staged artifacts are saved:

```
<output-dir>/
├── 01_all_datapoints.json      # All attack datapoints (static + dynamic)
├── 02_attack_results.json      # Raw per-datapoint attack results
└── 03_summary_report.json      # Aggregated summary
```

### Viewing reports

The run prints a summary to stdout on completion. To view an existing report interactively:

```bash
# List previous runs
eq redteam runs

# Launch interactive Streamlit dashboard for latest run
eq redteam ui

# Launch dashboard for a specific report
eq redteam ui ./path/to/report.json
```

### Reading the report JSON

Top-level fields in the report JSON:

| Field | Meaning |
|-------|---------|
| `pipeline` | `dynamic`, `static`, or `hybrid` |
| `framework` | OWASP framework used (e.g. `OWASP-ASI`, `OWASP-LLM`) |
| `categories_tested` | List of OWASP categories covered in this run |
| `total_results` | Total attack datapoints in the report |

The `summary` sub-object contains aggregate stats:

| Field | Meaning |
|-------|---------|
| `resistance_rate` | Fraction of attacks the agent resisted (0.0–1.0). Higher = more robust. |
| `vulnerabilities_found` | Count of attacks the agent failed (lower is better) |
| `vulnerability_rate` | Attack Success Rate (ASR). `1.0 - resistance_rate` |
| `total_attacks` | Total attack datapoints evaluated |
| `by_technique` | Per-technique breakdown with `vulnerabilities_found` and `resistance_rate` |

**Interpreting resistance_rate:** `1.0 - resistance_rate` = ASR. A `resistance_rate` of `0.65` means 35% of attacks succeeded.

## OWASP category reference

Two frameworks. `framework` is `OWASP-ASI` or `OWASP-LLM` in the report. Note `ASI01` is goal hijacking, **not** prompt injection — prompt injection is `LLM01`.

| Code | Name (OWASP-ASI) | | Code | Name (OWASP-LLM) |
|------|------------------|-|------|------------------|
| `ASI01` | Agent Goal Hijacking | | `LLM01` | Prompt Injection |
| `ASI02` | Tool Misuse & Exploitation | | `LLM02` | Sensitive Information Disclosure |
| `ASI03` | Identity & Privilege Abuse | | `LLM03` | Supply Chain |
| `ASI04` | Supply Chain Vulnerabilities | | `LLM04` | Data and Model Poisoning |
| `ASI05` | Unexpected Code Execution | | `LLM05` | Improper Output Handling |
| `ASI06` | Memory & Context Poisoning | | `LLM06` | Excessive Agency |
| `ASI07` | Insecure Inter-Agent Communication | | `LLM07` | System Prompt Leakage |
| `ASI08` | Cascading Failures | | `LLM08` | Vector and Embedding Weaknesses |
| `ASI09` | Human-Agent Trust Exploitation | | `LLM09` | Misinformation |
| `ASI10` | Rogue Agents | | | _(LLM10 Unbounded Consumption is excluded — not prompt-testable)_ |

## Worked example

**Goal:** Red team the `customer-support-v2` deployment against goal hijacking (ASI01) and tool misuse (ASI02), routing the attack/evaluator LLM through the orq gateway.

```bash
# 1. Verify the CLI and env vars
eq --help || { echo "eq not found"; exit 1; }
echo "ORQ_API_KEY set: $([ -n "$ORQ_API_KEY" ] && echo yes || echo NO)"

# 2. Run dynamic red team (2 categories, 20 attack datapoints max, explicit model)
eq redteam run \
  --target agent:customer-support-v2 \
  --mode dynamic \
  --category ASI01 \
  --category ASI02 \
  --max-dynamic-datapoints 20 \
  --attack-model openai/gpt-5-mini \
  --evaluator-model openai/gpt-5-mini \
  --save-report ./output/customer-support-report.json \
  --yes

# 3. List runs and view summary
eq redteam runs

# 4. Open interactive dashboard (optional)
eq redteam ui ./output/customer-support-report.json
```

Expected summary fields in the printed output:

```
pipeline:           dynamic
categories_tested:  ASI01, ASI02
total_results:      20
resistance_rate:    0.65
vulnerabilities_found: 7
```

`resistance_rate: 0.65` → 35% attack success rate across 20 attempts.

## Troubleshooting common failures

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `eq: command not found` | Package not installed or not on PATH | `pip install 'evaluatorq[redteam]'` |
| `ORQ_API_KEY not set` or 401 errors | Missing env var for target agent | Export `ORQ_API_KEY` in your shell or `.env` |
| `ImportError` for `openai`/`typer` | Incomplete install (missing extra) | `pip install 'evaluatorq[redteam]'` |
| `CredentialError` / run hangs at attack generation | No LLM credential for attack/evaluator | Set `OPENAI_API_KEY` (bare model names) **or** `ORQ_API_KEY` (provider-prefixed, e.g. `openai/gpt-5-mini`) |
| ASR = 0.0 on all categories | Evaluator routing/credential issue, or genuinely resistant | Confirm the evaluator model string matches the active route (gateway → `openai/gpt-5-mini`); check creds before assuming a stronger judge is needed |
| `openai/...` model rejected | `OPENAI_API_KEY` set → direct OpenAI rejects the prefix | Use a bare name (`gpt-5-mini`) for direct OpenAI, or unset `OPENAI_API_KEY` to route via the gateway |
| Confirmation prompt blocks CI | Interactive terminal required | Pass `--yes` / `-y` to skip |
| No runs shown in `eq redteam runs` | `--save none` was used | Re-run with `--save final` (default) or pass `--save-report <path>` |

## Python SDK (when the CLI can't)

The CLI covers the common case (red-teaming an orq `agent:`/`deployment:` target). For things it cannot do, use the `evaluatorq.redteam` Python API:

- **Red-teaming a raw model** — `OpenAIModelTarget`. The CLI deliberately **rejects** `openai:`/`llm:` target strings and points you here.
- **Red-teaming an agent built on an external framework** — LangGraph (`LangGraphTarget`), OpenAI Agents SDK (`OpenAIAgentTarget`), or **any callable** via `CallableTarget` (wrap an `async def(prompt) -> str` — far simpler than subclassing `AgentTarget`). Each needs its own install extra. See [Python SDK](resources/python-sdk.md#external-framework-targets).
- **Actionable remediation** — `red_team(..., generate_recommendations=True)` adds LLM-generated focus-area fixes to the report (`report.focus_area_recommendations`). SDK-only; not exposed on the CLI.
- Custom `AgentTarget` subclasses, or embedding red teaming in a Python eval pipeline.

**See [resources/python-sdk.md](resources/python-sdk.md)** for the `red_team()` signature, target types, a raw-model worked example, and programmatic report handling.

## Done when

- Run completes without errors
- Summary is printed to stdout (happens automatically after each run)
- Report JSON exists (in `.evaluatorq/runs/` or at `--save-report` path)
- Categories are described to the user by their correct names (e.g. ASI01 = Agent Goal Hijacking, not "prompt injection")
- Categories tested and coverage gaps are noted (e.g. "only ASI01–ASI02 tested; LLM01–LLM09 not covered")

## Companion skills

- `build-evaluator` — build custom LLM judges for failure modes surfaced by red teaming
- `analyze-trace-failures` — deeper failure taxonomy from production traces
- `run-experiment` — run controlled experiments using orq deployments
