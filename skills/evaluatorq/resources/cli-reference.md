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
  --artifacts-dir ./redteam-results

eq redteam ui ./redteam-results/03_summary_report.json
```

### Generate + simulate + export

```bash
export ORQ_API_KEY="..."

eq sim generate \
  --agent-description "Travel booking assistant" \
  --num-personas 5 \
  --num-scenarios 5 \
  --datapoints datapoints.jsonl

eq sim simulate \
  --input datapoints.jsonl \
  --target agent:my-travel-agent \
  --results sim-results.jsonl

eq sim export \
  --input sim-results.jsonl \
  --output openresponses-payload.json
```

(`export --input` takes a **results** JSONL — the simulate step in between is required; generated datapoints alone won't export.)

---

## eq redteam

Adversarial red teaming against OWASP vulnerability categories.

> For the full walkthrough — modes, categories, output format, dashboard — use the **`orq-red-team` skill**.

Quick reference:

```bash
eq redteam run --target agent:<AGENT_KEY> --mode dynamic
eq redteam ui report.json   # open Streamlit dashboard
```

---

## eq sim

Multi-turn agent simulation with a user-simulator and LLM judge.

> For the full walkthrough — persona generation, scenario setup, goal-achievement scoring — use the **`orq-simulate-agent` skill**.

Quick reference:

```bash
eq sim generate --agent-description "..." --datapoints dp.jsonl   # --datapoints is required
eq sim simulate --input dp.jsonl --target agent:<AGENT_KEY>
```
