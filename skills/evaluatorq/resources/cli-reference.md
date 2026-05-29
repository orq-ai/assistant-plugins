# evaluatorq CLI Reference

Quick reference for the `eq` / `evaluatorq` CLI. Install with:

```bash
pip install 'evaluatorq[redteam]'
```

---

## eq redteam

Adversarial red teaming against OWASP vulnerability categories.

### Subcommands

| Subcommand | Description |
|-----------|-------------|
| `eq redteam run adaptive` | Dynamic attack generation (LLM-generated prompts) |
| `eq redteam run dataset` | Static OWASP dataset evaluation |
| `eq redteam run hybrid` | Both adaptive and dataset in one run |
| `eq redteam report summarize <file>` | Print resistance summary from a report.json |
| `eq redteam ui <file>` | Open Streamlit report viewer |

### `eq redteam run adaptive`

```bash
eq redteam run adaptive \
  --target agent:<AGENT_KEY>         # or model:<MODEL>, openai:<MODEL>
  --framework owasp-llm              # owasp-llm | owasp-asi
  --num-attacks 20                   # number of adversarial prompts
  --output-dir ./results             # where to write output files
  --verbose
```

**Output files** (written to `--output-dir`):
```
01_agent_context.json
02_attack_objectives.json
03_attack_strategies.json
04_datapoints.json
05_summary_report.json    ← same schema as report.json
```

### `eq redteam run dataset`

```bash
eq redteam run dataset \
  --target agent:<AGENT_KEY> \
  --framework owasp-llm \
  --output-dir ./results
```

**Output:** `report.json` only (no staged files).

### `eq redteam run hybrid`

```bash
eq redteam run hybrid \
  --dynamic-target agent:<AGENT_KEY> \
  --static-target agent:<AGENT_KEY> \
  --framework owasp-llm
```

### `eq redteam report summarize`

```bash
eq redteam report summarize ./results/report.json
```

**Key fields in report.json:**

| Field | Type | Description |
|-------|------|-------------|
| `resistance_rate` | float | Fraction of attacks resisted (higher = safer) |
| `vulnerabilities_found` | list | Vulnerability IDs where attacks succeeded |
| `total_results` | int | Total number of attacks evaluated |
| `categories_tested` | list | OWASP categories covered |
| `top_techniques` | list | Most effective attack techniques |
| `framework` | str | `"owasp-llm"` or `"owasp-asi"` |
| `pipeline` | str | `"adaptive"`, `"dataset"`, or `"hybrid"` |

### Target formats

| Format | Example | Notes |
|--------|---------|-------|
| `agent:<key>` | `agent:my-support-agent` | Requires `ORQ_API_KEY` |
| `model:<id>` | `model:gpt-4o` | Via orq.ai AI Router; requires `ORQ_API_KEY` |
| `openai:<id>` | `openai:gpt-4o-mini` | Direct OpenAI; requires `OPENAI_API_KEY` |

---

## eq sim

Multi-turn agent simulation with a user-simulator and LLM judge.

### Subcommands

| Subcommand | Description |
|-----------|-------------|
| `eq sim run` | Run simulations from a datapoints JSONL file |
| `eq sim generate` | Generate personas + scenarios, then simulate |
| `eq sim export` | Convert results JSONL → OpenResponses payload JSON |
| `eq sim validate-dataset` | Validate a datapoints JSONL (exit 1 on errors) |
| `eq sim runs` | List recent runs from `.evaluatorq/sim-runs/` |

### `eq sim run`

```bash
eq sim run \
  --datapoints dp.jsonl \           # pre-built datapoints file
  --agent-key <AGENT_KEY> \         # exactly one of these three
  # --openai-model gpt-4o-mini \
  # --vercel-url https://...
  --model azure/gpt-4o-mini \       # user-simulator + judge model
  --max-turns 10 \
  --parallelism 5 \
  --output results.jsonl \          # optional: write results JSONL
  --name my-run \                   # run-store entry name
  --no-save                         # skip writing to .evaluatorq/sim-runs/
```

### `eq sim generate`

```bash
eq sim generate \
  --agent-description "A customer support agent for a B2B SaaS product" \
  --agent-key <AGENT_KEY> \
  --num-personas 5 \
  --num-scenarios 5 \
  --max-turns 10 \
  --output results.jsonl
```

Generates persona × scenario combinations, then runs simulations for each.

### `eq sim export`

```bash
eq sim export \
  --input results.jsonl \
  --output payload.json
```

Converts simulation results to OpenResponses format for further evaluation.

### `eq sim validate-dataset`

```bash
eq sim validate-dataset dp.jsonl
# Prints: OK — 12 valid datapoint(s) in dp.jsonl
# Or:     exit code 1 with per-line error messages
```

### `eq sim runs`

```bash
eq sim runs              # list from .evaluatorq/sim-runs/
eq sim runs ./my-dir     # list from custom directory
eq sim runs --limit 10   # show at most 10 entries
```

### Target flags (sim)

| Flag | Description | Requires |
|------|-------------|---------|
| `--agent-key` | orq.ai deployment key | `ORQ_API_KEY` |
| `--openai-model` | OpenAI-compatible model name | `OPENAI_API_KEY` or `ORQ_API_KEY` |
| `--vercel-url` | Vercel AI SDK endpoint URL | none |

Exactly one of the three target flags must be supplied.

### SimulationResult fields (results JSONL)

Each line is a JSON object with:

| Field | Type | Description |
|-------|------|-------------|
| `goal_achieved` | bool | Whether the simulated user achieved their goal |
| `goal_completion_score` | float | 0–1 score from the judge |
| `turn_count` | int | Number of conversation turns |
| `rules_broken` | list[str] | Criteria that were violated |
| `terminated_by` | str | `"judge"`, `"max_turns"`, `"error"`, `"timeout"` |
| `messages` | list | Full conversation transcript |
| `metadata.evaluator_scores` | dict | Scorer name → float |

---

## Common patterns

### Run red team then summarize

```bash
export ORQ_API_KEY="..."

eq redteam run adaptive \
  --target agent:my-agent \
  --framework owasp-llm \
  --num-attacks 30 \
  --output-dir ./redteam-results

eq redteam report summarize ./redteam-results/05_summary_report.json
```

### Generate + simulate + export

```bash
export ORQ_API_KEY="..."

eq sim generate \
  --agent-description "Travel booking assistant" \
  --agent-key my-travel-agent \
  --num-personas 5 \
  --num-scenarios 5 \
  --output sim-results.jsonl

eq sim export \
  --input sim-results.jsonl \
  --output openresponses-payload.json
```
