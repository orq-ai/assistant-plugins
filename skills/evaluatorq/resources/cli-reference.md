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
| `eq redteam run` | Run a red team evaluation (dynamic, static, or hybrid mode) |
| `eq redteam ui [report.json]` | Open the interactive Streamlit dashboard |
| `eq redteam validate-dataset [source]` | Validate a local or HuggingFace dataset |
| `eq redteam runs` | List previous runs saved to `.evaluatorq/runs/` |

### `eq redteam run`

```bash
eq redteam run \
  --target agent:<AGENT_KEY> \     # repeatable; also accepts deployment:<KEY>
  --mode dynamic \                 # dynamic (default) | static | hybrid
  --category ASI01 \               # repeatable; defaults to all categories
  --save final \                   # none | final (default) | detail
  --output-dir ./results \         # required when --save detail
  --parallelism 10 \
  --yes                            # skip confirmation prompt
```

**Modes:**

| Mode | Description |
|------|-------------|
| `dynamic` (default) | LLM-generated adversarial prompts — broadest coverage |
| `static` | Runs against the OWASP static dataset only |
| `hybrid` | Both dynamic and static in one run |

**`--save` values:**

| Value | What is written |
|-------|----------------|
| `none` | Nothing saved to disk |
| `final` (default) | `report.json` summary only |
| `detail` | All stage artifacts + `report.json` (requires `--output-dir`) |

When `--save detail` is used, the following files are written to `--output-dir`:

```
01_agent_context.json
02_attack_objectives.json
03_attack_strategies.json
04_datapoints.json
05_summary_report.json    ← same schema as report.json
```

**Key flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--target` | required | Target ID(s), e.g. `agent:<key>` or `deployment:<key>`. Repeatable. |
| `--mode` | `dynamic` | Execution mode: `dynamic`, `static`, or `hybrid` |
| `--category` / `-c` | all | OWASP category codes to test (e.g. `ASI01`, `LLM01`). Repeatable. |
| `--vulnerability` / `-V` | — | Specific vulnerability IDs (takes precedence over `--category`). Repeatable. |
| `--save` | `final` | `none`, `final`, or `detail` |
| `--output-dir` | — | Directory for saved files (required with `--save detail`) |
| `--parallelism` | `10` | Concurrent evaluatorq jobs |
| `--max-turns` | `5` | Max conversation turns for multi-turn attacks |
| `--attack-model` | platform default | Model for adversarial prompt generation |
| `--evaluator-model` | platform default | Model for OWASP evaluation scoring |
| `--attacker-instructions` | — | Domain context to steer attack generation |
| `--yes` / `-y` | `False` | Skip confirmation prompt |
| `--verbose` / `-v` | 0 | `-v` for progress, `-vv` for debug |
| `--quiet` / `-q` | `False` | Suppress progress bars |

**report.json key fields:**

| Field | Type | Description |
|-------|------|-------------|
| `resistance_rate` | float | Fraction of attacks resisted (higher = safer) |
| `vulnerabilities_found` | list | Vulnerability IDs where attacks succeeded |
| `total_results` | int | Total number of attacks evaluated |
| `categories_tested` | list | OWASP categories covered |
| `top_techniques` | list | Most effective attack techniques |
| `framework` | str | `"owasp-llm"` or `"owasp-asi"` |
| `pipeline` | str | `"dynamic"`, `"static"`, or `"hybrid"` |

### `eq redteam ui`

```bash
eq redteam ui report.json        # open a specific report
eq redteam ui --latest           # open the most recent run
eq redteam ui                    # same as --latest
```

Requires: `pip install 'evaluatorq[ui]'`

### Target formats

| Format | Example | Notes |
|--------|---------|-------|
| `agent:<key>` | `agent:my-support-agent` | Requires `ORQ_API_KEY` |
| `deployment:<key>` | `deployment:my-prompt` | Requires `ORQ_API_KEY` |

For OpenAI models, use the Python API (`OpenAIModelTarget`) — not supported via CLI target string.

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
  --sim-model azure/gpt-4o-mini \   # user-simulator + judge model
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

### Run red team then view report

```bash
export ORQ_API_KEY="..."

eq redteam run \
  --target agent:my-agent \
  --mode dynamic \
  --save detail \
  --output-dir ./redteam-results

eq redteam ui ./redteam-results/05_summary_report.json
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
