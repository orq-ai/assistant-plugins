# evaluatorq CLI Reference

Quick reference for the `eq` / `evaluatorq` CLI. Install with:

```bash
pip install 'evaluatorq[redteam]'
```

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

---

## eq redteam

Adversarial red teaming against OWASP vulnerability categories.

> For the full walkthrough — modes, categories, output format, dashboard — use the **`red-team` skill**.

Quick reference:

```bash
eq redteam run --target agent:<AGENT_KEY> --mode dynamic
eq redteam ui report.json   # open Streamlit dashboard
```

---

## eq sim

Multi-turn agent simulation with a user-simulator and LLM judge.

> For the full walkthrough — persona generation, scenario setup, goal-achievement scoring — use the **`simulate-agent` skill**.

Quick reference:

```bash
eq sim generate --agent-description "..." --agent-key <AGENT_KEY>
eq sim run --datapoints dp.jsonl --agent-key <AGENT_KEY>
```
