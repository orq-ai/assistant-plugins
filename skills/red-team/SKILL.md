---
name: red-team
description: >
  Invoke the orq red teaming library to run adversarial attacks against deployed
  agents or static datasets. Use when asked to "run a red team", "red team this
  deployment", "test my agent for vulnerabilities", "OWASP red team", or "check
  how my agent handles adversarial inputs". Do NOT use when you only need to build
  evaluators (use build-evaluator) or analyze existing trace failures (use
  analyze-trace-failures).
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, Task, AskUserQuestion, orq*
---

# Red Team

You are an **orq.ai red team operator**. Your job is to invoke the existing red teaming library to run adversarial attacks against deployed agents or pre-built datasets, then read and interpret the resulting report.

This skill is a **reference guide and invocation helper — not a wrapper**. You call the `redteam` CLI directly; you do not reimplement it.

## Constraints

- **NEVER** reimplement red teaming logic — use the `redteam` CLI.
- **NEVER** run without confirming the target key with the user first.
- **NEVER** skip the env var check — missing `ORQ_API_KEY` will silently fail.
- **ALWAYS** use `uv run` to invoke the CLI (manages the Python environment).
- **ALWAYS** show the ASR summary after a run completes.
- **NEVER** interpret a passing run (low ASR) as "the agent is safe" — coverage depends on categories tested.

## Library location

```
research/projects/red-teaming/
```

The project is a `uv` workspace. The CLI entry point is `redteam` (defined in `pyproject.toml` as `red_teaming.cli.main:app`).

**Always invoke via:**
```bash
cd /path/to/research/projects/red-teaming
uv run redteam <command> [options]
```

## Required environment variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `ORQ_API_KEY` | Yes | Authenticates against the orq.ai API to invoke the target agent |
| `OPENAI_API_KEY` | Conditional | Required when `--attack-model` or `--evaluator-model` uses an OpenAI model (default: `azure/gpt-5-mini`) |
| `ANTHROPIC_API_KEY` | Conditional | Required when using a Claude model for attacks or evaluation |

Check before running:
```bash
echo "ORQ_API_KEY set: $([ -n "$ORQ_API_KEY" ] && echo yes || echo NO)"
```

## Core commands

### `redteam run adaptive` — live attack against a deployed orq agent

Generates adversarial prompts, sends them to the target agent, and evaluates responses.

```bash
uv run redteam run adaptive \
  --target agent:<deployment-key> \
  [--categories ASI01,ASI02] \
  [--max-attacks 50] \
  [--max-per-category 10] \
  [--generated-count 2] \
  [--attack-model azure/gpt-5-mini] \
  [--evaluator-model azure/gpt-5-mini] \
  [--parallelism 5] \
  [--out ./output/my-run] \
  [--yes]
```

**Key flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--target` | required | `agent:<deployment-key>` — the orq deployment to attack |
| `--categories` | all | Comma-separated OWASP categories (e.g. `ASI01,ASI03,LLM01`) |
| `--max-attacks` | none | Hard cap on total attack datapoints |
| `--max-per-category` | none | Cap per OWASP category |
| `--generated-count` | 2 | LLM-generated strategies per category |
| `--attack-model` | `azure/gpt-5-mini` | Model generating adversarial prompts |
| `--evaluator-model` | `azure/gpt-5-mini` | Model judging whether attacks succeeded |
| `--parallelism` | 5 | Concurrent attack jobs |
| `--out` | auto | Output directory for report + staged artifacts |
| `--yes` / `-y` | false | Skip confirmation prompt |

### `redteam run dataset` — evaluate a static dataset

Runs evaluators over a pre-built dataset (local file or the published HuggingFace dataset).

```bash
uv run redteam run dataset \
  --target agent:<deployment-key> \
  [--dataset ./path/to/dataset.json] \
  [--categories ASI01,LLM02] \
  [--num-samples 100] \
  [--evaluator-model azure/gpt-5-mini] \
  [--max-concurrent 10] \
  [--out ./output/my-run]
```

If `--dataset` is omitted, the remote `orq/redteam-vulnerabilities` HuggingFace dataset is used.

### `redteam run hybrid` — adaptive + dataset in one run

Combines a live adaptive leg and a static dataset leg against the same target.

```bash
uv run redteam run hybrid \
  --target agent:<deployment-key> \
  [--categories ASI01,ASI02] \
  [--out ./output/my-run]
```

### `redteam summarize` — print a concise report summary

```bash
uv run redteam summarize ./output/my-run/unified_report.json
```

## Output structure

After a run, the output directory contains:

```
output/my-run/
├── unified_report.json        # Full report — use this for summarize and further analysis
├── staged/                    # Intermediate per-category artifacts (if --save-intermediates)
└── evaluated_*.jsonl          # Per-target evaluated rows
```

### Reading the report

Key fields in `unified_report.json`:

| Field | Meaning |
|-------|---------|
| `asr` | Attack Success Rate (0.0–1.0) — fraction of attacks that succeeded |
| `total_attacks` | Total attack datapoints sent |
| `successful_attacks` | How many were flagged as successful by the evaluator |
| `results_by_category` | ASR broken down by OWASP category |

A result is flagged **successful** (attack succeeded) when the evaluator judges that the agent produced a vulnerable response. Higher ASR = more vulnerable.

## OWASP category reference

| Framework | Categories | Description |
|-----------|------------|-------------|
| OWASP Agentic 2026 | `ASI01`–`ASI10` | Agent-specific vulnerabilities (prompt injection, excessive agency, etc.) |
| OWASP LLM 2025 | `LLM01`–`LLM10` | LLM vulnerabilities (hallucination, insecure output, etc.) |
| Fairness | `fairness` | Bias and discrimination |
| Liability | `liability` | Legal/medical/financial risk |
| Content Policy | `content_policy` | Harmful or toxic content |

## Worked example

**Goal:** Red team the `customer-support-v2` deployment against prompt injection and excessive agency.

```bash
# 1. Navigate to the library
cd ~/Documents/orq/research/projects/red-teaming

# 2. Verify env
echo "ORQ_API_KEY set: $([ -n "$ORQ_API_KEY" ] && echo yes || echo NO)"

# 3. Run adaptive red team (scoped to 2 categories, 20 attacks max)
uv run redteam run adaptive \
  --target agent:customer-support-v2 \
  --categories ASI01,ASI02 \
  --max-attacks 20 \
  --out ./output/customer-support-$(date +%Y%m%d) \
  --yes

# 4. Read the summary
uv run redteam summarize ./output/customer-support-$(date +%Y%m%d)/unified_report.json
```

Expected summary output (JSON):
```json
{
  "asr": 0.35,
  "total_attacks": 20,
  "successful_attacks": 7,
  "results_by_category": {
    "ASI01": {"asr": 0.4, "total": 10, "successful": 4},
    "ASI02": {"asr": 0.3, "total": 10, "successful": 3}
  }
}
```

## Troubleshooting common failures

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `ORQ_API_KEY not set` or 401 errors | Missing env var | Export `ORQ_API_KEY` in your shell or `.env` |
| `ModuleNotFoundError: red_teaming` | Wrong working directory or venv | `cd research/projects/red-teaming && uv run redteam ...` |
| `Python 3.12+ required` | System Python too old | `uv` handles this — ensure `uv` is installed (`brew install uv`) |
| `ImportError: evaluatorq` | Dependency not installed | `uv sync` in the project directory |
| Run hangs at attack generation | Attack model API key missing | Set `OPENAI_API_KEY` or switch `--attack-model` to a configured provider |
| ASR = 0.0 on all categories | Evaluator model not judging correctly | Try `--evaluator-model gpt-4o` for higher-quality evaluation |
| Confirmation prompt blocks CI | Interactive terminal required | Pass `--yes` / `-y` to skip |

## Done when

- Run completes without errors
- `unified_report.json` exists in the output directory
- ASR summary has been printed and shared with the user
- Categories tested and coverage gaps are noted (e.g. "only ASI01–ASI02 tested; fairness not covered")

## Companion skills

- `build-evaluator` — build custom LLM judges for failure modes surfaced by red teaming
- `analyze-trace-failures` — deeper failure taxonomy from production traces
- `run-experiment` — run controlled experiments using orq deployments
