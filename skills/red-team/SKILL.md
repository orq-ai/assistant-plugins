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
pip install evaluatorq
# or, inside the orqkit workspace:
uv run --package evaluatorq eq redteam --help
```

## Required environment variables

Credentials are auto-detected in this order:

1. **OpenAI directly** — set `OPENAI_API_KEY` (optionally `OPENAI_BASE_URL`). The default model `gpt-5-mini` routes to OpenAI.
2. **orq gateway** — set `ORQ_API_KEY` (optionally `ORQ_BASE_URL`). All model strings route through the orq LLM gateway. `ORQ_API_KEY` is always required to invoke the target orq agent.

There is no Azure credential path — the CLI does not support Azure OpenAI directly.

Check before running:
```bash
# Verify the CLI is installed and reachable
eq --help || { echo "eq CLI not found — install evaluatorq or check PATH"; exit 1; }

# Verify required env vars
echo "ORQ_API_KEY set: $([ -n "$ORQ_API_KEY" ] && echo yes || echo NO — required for target agent)"
echo "OPENAI_API_KEY set: $([ -n "$OPENAI_API_KEY" ] && echo yes || echo not set — orq gateway will be used if ORQ_API_KEY is set)"
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
  [--max-per-category 10] \
  [--generated-strategy-count 2] \
  [--attack-model gpt-4o] \
  [--evaluator-model gpt-4o] \
  [--parallelism 10] \
  [--output-dir ./output/my-run] \
  [--save detail] \
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

| Flag | Default | Description |
|------|---------|-------------|
| `--target` / `-t` | required | `agent:<deployment-key>`. Repeatable for multi-target runs |
| `--mode` | `dynamic` | Execution mode: `dynamic`, `static`, or `hybrid` |
| `--category` / `-c` | all | OWASP category to test (e.g. `ASI01`). **Repeatable** — pass once per category |
| `--vulnerability` / `-V` | all | Vulnerability ID (e.g. `goal_hijacking`) or OWASP code. Repeatable. Takes precedence over `--category` |
| `--max-dynamic-datapoints` | none | Cap dynamic (generated) attack datapoints |
| `--max-static-datapoints` | none | Cap static (dataset) datapoints |
| `--max-per-category` | none | Cap attack strategies per category |
| `--generated-strategy-count` | 2 | LLM-generated strategies per category |
| `--attack-model` | `gpt-5-mini` | Model generating adversarial prompts |
| `--evaluator-model` | `gpt-5-mini` | Model judging whether attacks succeeded |
| `--attacker-instructions` | none | Domain context to steer attack generation (e.g. "this agent handles financial transactions") |
| `--parallelism` | 10 | Concurrent attack jobs |
| `--output-dir` | none | Directory for saved JSON stage files (required with `--save detail`) |
| `--save` | `final` | `none` (no files), `final` (summary JSON), or `detail` (all stage artifacts) |
| `--save-report` | none | Explicit path to write the report JSON |
| `--export-md` | none | Directory path to write a Markdown report (filename is auto-generated) |
| `--export-html` | none | Directory path to write an HTML report |
| `--dataset` | HuggingFace `orq/redteam-vulnerabilities` | Static/hybrid mode: local path, `hf:org/repo`, or `hf:org/repo/file.json` |
| `--name` / `-n` | `red-team` | Experiment name |
| `--max-turns` | 5 | Max conversation turns for multi-turn attacks |
| `--yes` / `-y` | false | Skip confirmation prompt |
| `--verbose` / `-v` | 0 | Increase verbosity (repeatable: `-v` info, `-vv` debug) |
| `--quiet` / `-q` | false | Suppress progress bars |

### Category examples

```bash
# Single category — prompt injection (OWASP ASI01)
eq redteam run --target agent:my-agent --category ASI01

# Multiple categories — pass --category once per value
eq redteam run --target agent:my-agent --category ASI01 --category ASI02

# By vulnerability ID
eq redteam run --target agent:my-agent --vulnerability goal_hijacking

# Static mode with local dataset
eq redteam run --target agent:my-agent --mode static --dataset ./my-dataset.json

# Hybrid mode — both dynamic and static legs
eq redteam run --target agent:my-agent --mode hybrid --category ASI01 --max-dynamic-datapoints 30 --max-static-datapoints 50
```

## Output and reports

After a run, the report is auto-saved to `.evaluatorq/runs/<name>_<ts>.json`. If `--save-report <path>` is passed, the report JSON is also written there.

With `--save detail` and `--output-dir <dir>`, staged artifacts are saved:

```
<output-dir>/
├── 01_agent_context.json       # Agent metadata
├── 02_strategy_selection.json  # Attack strategies chosen per category
├── 03_generated_prompts.json   # Adversarial prompts generated
└── 04_attack_results.json      # Raw attack results
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

The report JSON contains a `summary` block with these fields:

| Field | Meaning |
|-------|---------|
| `resistance_rate` | Fraction of attacks the agent resisted (0.0–1.0). Higher = more robust. |
| `vulnerabilities_found` | Count of attacks the agent failed (lower is better) |
| `vulnerability_rate` | Attack Success Rate (ASR). `1.0 - resistance_rate` |
| `total_attacks` | Total attack datapoints evaluated |
| `categories_tested` | List of OWASP categories covered in this run |
| `by_technique` | Per-technique breakdown with `vulnerabilities_found` and `resistance_rate` |
| `pipeline` | `dynamic`, `static`, or `hybrid` |
| `framework` | OWASP framework used (e.g. `OWASP-ASI`, `OWASP-LLM`) |

**Interpreting resistance_rate:** `1.0 - resistance_rate` = ASR. A `resistance_rate` of `0.65` means 35% of attacks succeeded.

## OWASP category reference

| Framework | Categories | Description |
|-----------|------------|-------------|
| OWASP Agentic 2026 | `ASI01`–`ASI10` | Agent-specific vulnerabilities (prompt injection, excessive agency, etc.) |
| OWASP LLM 2025 | `LLM01`–`LLM09` | LLM vulnerabilities (hallucination, insecure output, etc.) |

## Worked example

**Goal:** Red team the `customer-support-v2` deployment against prompt injection and tool misuse, using OpenAI `gpt-4o` for attacks and evaluation.

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
  --attack-model gpt-4o \
  --evaluator-model gpt-4o \
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
| `eq: command not found` | Package not installed or not on PATH | `pip install evaluatorq` |
| `ORQ_API_KEY not set` or 401 errors | Missing env var for target agent | Export `ORQ_API_KEY` in your shell or `.env` |
| `ImportError: evaluatorq` | Incomplete install | `pip install evaluatorq[redteam]` |
| Run hangs at attack generation | LLM credential missing | Set `OPENAI_API_KEY` or `ORQ_API_KEY` for the attack/evaluator model |
| ASR = 0.0 on all categories | Evaluator model not judging correctly | Try `--evaluator-model gpt-4o` with `OPENAI_API_KEY` set |
| Confirmation prompt blocks CI | Interactive terminal required | Pass `--yes` / `-y` to skip |
| No runs shown in `eq redteam runs` | `--save none` was used | Re-run with `--save final` (default) or pass `--save-report <path>` |

## Done when

- Run completes without errors
- Summary is printed to stdout (happens automatically after each run)
- Report JSON exists (in `.evaluatorq/runs/` or at `--save-report` path)
- Categories tested and coverage gaps are noted (e.g. "only ASI01–ASI02 tested; LLM01–LLM09 not covered")

## Companion skills

- `build-evaluator` — build custom LLM judges for failure modes surfaced by red teaming
- `analyze-trace-failures` — deeper failure taxonomy from production traces
- `run-experiment` — run controlled experiments using orq deployments
