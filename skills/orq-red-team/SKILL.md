---
name: orq-red-team
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
- **ALWAYS** run the preflight when the skill is invoked, before any `eq redteam run`: confirm `eq` is installed and at least one LLM credential is set, and check `ORQ_API_KEY`. `ORQ_API_KEY` is not always required, but **is** required to target an orq `agent:`/`deployment:`, to route the LLM via the gateway, or to upload results — if the target is an orq agent and it is missing, halt and ask the user to set it.
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

### Discovering `eq` — don't install until you've checked

`eq` is frequently already reachable through a project venv (e.g. `docs/demos/*/`), a uv tool install, or the orqkit workspace. **Probe in this order and use the first hit — install only as a last resort** (a global `pip install` can be blocked by PEP 668 / `--break-system-packages`):

```bash
# 1. Already on PATH?
eq --help >/dev/null 2>&1 && echo "PATH" && EQ="eq"

# 2. Installed as a uv tool? (preferred install target — see below)
[ -z "$EQ" ] && uv tool run --from 'evaluatorq[redteam]' eq --help >/dev/null 2>&1 \
  && echo "uv tool" && EQ="uv tool run --from 'evaluatorq[redteam]' eq"

# 3. Inside the orqkit workspace? (resolves from the workspace lockfile)
[ -z "$EQ" ] && uv run --package evaluatorq eq --help >/dev/null 2>&1 \
  && echo "uv workspace" && EQ="uv run --package evaluatorq eq"

# 4. A project-local .venv with it installed?
[ -z "$EQ" ] && [ -x .venv/bin/eq ] && echo ".venv" && EQ=".venv/bin/eq"

# 5. Importable in the active python? (run via module entrypoint)
[ -z "$EQ" ] && python3 -c "import evaluatorq" 2>/dev/null \
  && echo "python -m" && EQ="python3 -m evaluatorq"

echo "Resolved eq invocation: ${EQ:-NONE}"
```

If nothing resolves, install — prefer `uv tool` (isolated, puts `eq` on PATH, no venv juggling):

```bash
# Preferred: install as a uv tool (global, isolated)
uv tool install 'evaluatorq[redteam]'

# Inside a project that already has a venv: install into that venv
uv pip install 'evaluatorq[redteam]'

# Last resort, no uv available:
pip install 'evaluatorq[redteam]'
```

The `[redteam]` extra is required (pulls in `openai`, `typer`, `huggingface-hub`). The interactive dashboard (`eq redteam ui`) additionally needs the `[ui]` extra: add `evaluatorq[redteam,ui]`.

> Throughout this skill, `eq redteam …` is written assuming `eq` is on PATH. If you resolved it via `$EQ` above (e.g. `uv run --package evaluatorq eq`), substitute that prefix — **but read the uv `.env` trap below first; running `eq` via `uv run` has a credential gotcha.**

## Required environment variables

The attack and evaluator LLMs need credentials. Routing is decided purely by **which env var is set** — the model string itself is never inspected for routing:

1. **OpenAI directly** — if `OPENAI_API_KEY` is set (optionally `OPENAI_BASE_URL`), all attack/evaluator model strings go straight to OpenAI. Use **bare** model names here (e.g. `gpt-5-mini`). `OPENAI_API_KEY` **wins if both keys are set**.
2. **orq gateway** — else if `ORQ_API_KEY` is set (optionally `ORQ_BASE_URL`), model strings route through the orq LLM gateway (`{ORQ_BASE_URL}/v3/router`, default `https://my.orq.ai`). Use the **provider-prefixed** form here (e.g. `openai/gpt-5-mini`).

If neither key is set the run fails with `CredentialError`. There is no Azure credential path — the CLI does not support Azure OpenAI directly.

**`ORQ_API_KEY` is not strictly required** — you can run with `OPENAI_API_KEY` alone (e.g. red-teaming a raw model). But without `ORQ_API_KEY`, these are **not available**:

- **Testing an orq `agent:`/`deployment:` target** — invoking the target needs `ORQ_API_KEY`. Required for any orq-agent run.
- **Routing the attack/evaluator LLM through the orq gateway** — without it, the attack LLM only works via `OPENAI_API_KEY` (direct OpenAI, bare model names).
- **Uploading results to orq** — no Experiment is created and `report.experiment_url` stays empty; results are local-only.

> Model-string form follows the route: bare `gpt-5-mini` for direct OpenAI, `openai/gpt-5-mini` for the orq gateway. When targeting an orq agent (which needs `ORQ_API_KEY` anyway), the gateway form is the common case. For a fully direct-OpenAI run — an OpenAI model under test with OpenAI-hosted attacker/evaluator, `OPENAI_API_KEY` only — see the worked example in [resources/python-sdk.md](resources/python-sdk.md).

### The `uv run` + `.env` credential trap (read before running `eq` via uv)

**Symptom:** every gateway model string (`openai/gpt-5-mini`) fails with `401 Incorrect API key` even though `ORQ_API_KEY` is set, and shell-level `unset OPENAI_API_KEY` changes nothing.

**Cause:** `uv run` auto-loads `.env` from the current directory and injects those vars into the subprocess **after** any shell-level `unset`/`env -u`. If the project `.env` contains `OPENAI_API_KEY`, every `uv run eq …` re-acquires it → routing flips to direct-OpenAI (see routing rules above, `OPENAI_API_KEY` wins) → gateway-prefixed model strings like `openai/gpt-5-mini` are sent to OpenAI, which rejects the `openai/` prefix and the orq key. The `unset OPENAI_API_KEY` workaround **does nothing under `uv run`** — uv re-reads `.env` post-unset. (This trap is specific to `uv run`; a plain `env -u OPENAI_API_KEY eq …` works fine.)

**Diagnostic — detect `.env` contamination** (compare uv *with* vs *without* `.env`; prints presence only, never the key value):
```bash
WITH=$(uv run python3 -c "import os;print('set' if os.getenv('OPENAI_API_KEY') else 'unset')")
WITHOUT=$(uv run --no-env-file python3 -c "import os;print('set' if os.getenv('OPENAI_API_KEY') else 'unset')")
echo "OPENAI_API_KEY — with .env: $WITH | without .env: $WITHOUT"
# with=set, without=unset → .env is injecting it (the trap)
# both set                → comes from your shell, not .env
# both unset              → clean
```
`--no-env-file` only tells uv to skip `.env`; it is the *baseline*, not the detector. The `with` vs `without` difference is what proves `.env` is the source. **Never print the key itself** — booleans only, here and anywhere else in this skill.

**Decide — don't auto-strip `.env`.** A key in `.env` usually means the user wants it; suppressing it silently is wrong. Check the state and guide:

- `OPENAI_API_KEY` only, no `ORQ_API_KEY` → direct-OpenAI run, bare model names (`gpt-5-mini`). No conflict.
- `ORQ_API_KEY` only → gateway run, `openai/`-prefixed names. No conflict.
- **both set + gateway target (`openai/…` model)** → conflict: `OPENAI_API_KEY` wins → the gateway-prefixed string is sent to OpenAI → 401. Surface it and let the user choose, e.g.:
  > "`OPENAI_API_KEY` is set (looks like it's coming from `.env`). With a gateway model string that routes to direct OpenAI and 401s. To use the orq gateway for this run, suppress it for the subprocess [Fix A]. To use direct OpenAI instead, drop the `openai/` prefix. If neither `ORQ_API_KEY` nor the gateway is what you want, set the key you intend here."

**Fix A — feed uv only the orq creds for this run** (used when the user wants the gateway; does **not** modify `.env`):
```bash
TMP_ENV="$(mktemp)"                                  # mode 600, removed after the run
printf 'ORQ_API_KEY=%s\n' "$ORQ_API_KEY" > "$TMP_ENV"
[ -n "$ORQ_BASE_URL" ] && printf 'ORQ_BASE_URL=%s\n' "$ORQ_BASE_URL" >> "$TMP_ENV"
uv run --no-env-file --env-file "$TMP_ENV" --package evaluatorq eq redteam run --target agent:<key> ...
rm -f "$TMP_ENV"
```
`--no-env-file` stops uv reading the project `.env`; `--env-file "$TMP_ENV"` injects only the orq creds, so routing stays on the gateway. Only pass `ORQ_BASE_URL` if the user already has one set — otherwise let evaluatorq use its own gateway default; do **not** synthesize a base URL.

**Fix B — don't use `uv run`.** If `eq` is on PATH (e.g. via `uv tool install`), invoke it directly so the trap can't fire: `env -u OPENAI_API_KEY eq redteam run …`.

Check before running — **always run this preflight when the skill is invoked**, before any `eq redteam run`:
```bash
# 1. CLI installed and reachable
eq --help >/dev/null 2>&1 || { echo "eq CLI not found — install 'evaluatorq[redteam]' or check PATH"; exit 1; }

# 2. At least one LLM credential must be set (else CredentialError mid-run)
[ -n "$OPENAI_API_KEY" ] || [ -n "$ORQ_API_KEY" ] || { echo "No LLM credential — set OPENAI_API_KEY or ORQ_API_KEY for the attack/evaluator model."; exit 1; }

# 3. ORQ_API_KEY — required ONLY for orq agent/deployment targets and orq upload. Warn (don't block) if absent.
if [ -z "$ORQ_API_KEY" ]; then
  echo "WARNING: ORQ_API_KEY not set — orq agent/deployment targets, gateway routing, and result upload are unavailable. Raw-model runs with OPENAI_API_KEY still work."
fi
echo "ORQ_API_KEY set: $([ -n "$ORQ_API_KEY" ] && echo yes || echo no)"
echo "OPENAI_API_KEY set: $([ -n "$OPENAI_API_KEY" ] && echo yes || echo no)"
```

If the user's target is `agent:<key>` or `deployment:<key>` and `ORQ_API_KEY` is absent, **stop and ask them to set it** — that run cannot proceed.

### Verify the target agent exists before running

A wrong or unprovisioned `--target agent:<key>` does **not** fail fast — it fails deep in the run, after context retrieval, with a cryptic `RetrieveAgentRequestAgentsResponseBody: Agent not found`. Confirm the key resolves up front. Three ways, in order of convenience:

**1. orq MCP (no shell creds needed)** — preferred when available:
```
mcp__orq-mcp-global__agent_get  agent_id=<key>     # one agent
mcp__orq-mcp-global__agent_list                    # browse keys/status
```
A hit returns the manifest: `key`, `status` (want `live`), `project_id`, `model`, and — relevant to the memory-poisoning constraint — `memory_stores` / `knowledge_bases`. A miss raises *agent not found*.

**2. REST API (fallback when MCP isn't wired)** — `GET /v2/agents/{agent_key}`, bearer auth:
```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  "https://api.orq.ai/v2/agents/<key>" \
  -H "Authorization: Bearer $ORQ_API_KEY"
# 200 = exists & in scope · 404 = not found OR not in this key's project · 401 = bad key
```
Full body (404 returns `{"message": "..."}`):
```bash
curl -s "https://api.orq.ai/v2/agents/<key>" -H "Authorization: Bearer $ORQ_API_KEY" | jq .
```

**3. Python SDK** — same call programmatically:
```python
import os
from orq_ai_sdk import Orq
with Orq(api_key=os.getenv("ORQ_API_KEY", "")) as orq:
    print(orq.agents.retrieve(agent_key="<key>").status)  # raises if not found
```

On a **miss**: provision the agent (demo dirs usually ship a `provision.py` — look there) or fix the key, then re-check.

> **Project-scoping caveat — a miss is not always conclusive.** The MCP server uses its *own* configured `ORQ_API_KEY`, which may be scoped to a different project than the CLI `--target` key (or your shell's `ORQ_API_KEY`). An agent visible to MCP can return `404` from the REST call if your shell key belongs to another project — *verified live: `agent_get` found `clarabelle-cow`, but `curl` with a different-project shell key returned `404` for the same agent.* So: a **hit confirms existence**; a **miss is only conclusive when the checking credential shares the agent's project**. When MCP and CLI disagree, the credential the **CLI** uses (`--target` / shell `ORQ_API_KEY`) is the one that decides whether the run works — verify with that key.

## Plan the run — decide parameters with the user

Before the first `eq redteam run`, walk the user through these decisions. **Ask one step at a time** (use `AskUserQuestion` if you have that tool; otherwise ask in plain text) — each choice has a real coverage-vs-cost tradeoff. Default toward a small, scoped run first, then widen once the wiring is proven.

**Step 1 — Mode (where attacks come from).**

| Mode | Upside | Downside | Use when |
|------|--------|----------|----------|
| `dynamic` (default) | LLM generates fresh, adaptive attacks **tailored to your agent** (its system prompt, tools, and responses) → best signal, finds novel failures | Slowest + most expensive (attacker LLM + target + evaluator call per datapoint); non-deterministic | Exploring an agent's real robustness |
| `static` | Replays a fixed dataset → cheap, fast, reproducible, CI-friendly | Only as good as the dataset; finds nothing not already in it | Regression checks, baselines, gating |
| `hybrid` | Both legs in one report → widest coverage | Highest cost (pays for both) | One-shot baseline + exploration |

**Step 2 — Datapoint budget (the main cost lever).**

- Dynamic cap: `--max-dynamic-datapoints N`. Static cap: `--max-static-datapoints N`.
- **More datapoints →** higher coverage and a more *stable* ASR. One lucky resist out of 5 attempts is noise; out of 100 it's signal. But cost and wall-clock scale ~linearly, and the count is **per category** — 3 categories × 50 = 150 datapoints.
- **Fewer datapoints →** fast smoke test, but ASR is noisy and coverage is thin. Do **not** read a clean small run as "the agent is safe."
- Rough guide: **10–20** for a smoke test / wiring check, **50–100+** per scope for a real assessment.
- **Unset = the tool's built-in default**, not unlimited. Don't assume omitting the flag runs everything — but don't assume it's tiny either; check `eq redteam run --help` for the default if cost matters.
- **Static datapoints don't have to be hand-built.** The default `--dataset` is orq's large hosted set, `orq/redteam-vulnerabilities` on HuggingFace — point a `static`/`hybrid` run at it to test against a broad corpus out of the box. Only supply a local `--dataset` for custom scenarios.

**Step 3 — Vulnerability scope (what to test).**

- Two frameworks: **OWASP-ASI** (agentic failures — goal hijacking, tool misuse, memory poisoning; for orq agents with tools/memory) and **OWASP-LLM** (model-level — prompt injection, info disclosure, output handling). Full list in the [category table](#owasp-category-reference) below.
- `--category ASI01` (repeatable) tests a whole category. `--vulnerability goal_hijacking` (repeatable) targets one specific vuln and **takes precedence over `--category`**.
- **Wrong pick is low-stakes:** categories that don't apply to the agent (e.g. memory-poisoning on a memory-less agent, tool-misuse on an agent with no tools) are **automatically filtered out by the framework** — they won't waste datapoints or inflate the run. So lean toward selecting; over-picking is cheap, the framework prunes what can't apply.
- **Omit both = test everything** → broadest, but slowest and costliest. For a first run, pick **1–3 categories that match the agent's risk surface**:

| Agent has… | Test |
|------------|------|
| Tools / actions it can take | `ASI02` (tool misuse), `LLM06` (excessive agency) |
| A memory store | `ASI06` (memory & context poisoning) — note the memory-write caveat in Constraints |
| Untrusted user input | `ASI01` (goal hijacking), `LLM01` (prompt injection) |
| Secrets / a system prompt worth protecting | `LLM02` (info disclosure), `LLM07` (system prompt leakage) |

**Step 4 — One-off CLI or a reusable script?** Ask the user how they want it delivered:

- **One-off CLI** — fastest for a single exploratory run. Nothing to maintain.
- **Reusable script** (recommended for anything you'll repeat) — write the run into a small shell script (CLI) or a Python script (SDK). Upsides: easy to re-run after a fix (the fix → re-run loop in Constraints), reproducible, reviewable in version control, and the place to bake in the `.env`/`uv` credential handling so the trap can't resurface. Recommend this whenever the user is red-teaming a real agent they'll iterate on, or wiring it into CI. For the **Python SDK** route — the `red_team()` signature, target types (incl. external frameworks and `CallableTarget`), and programmatic report handling — load **[resources/python-sdk.md](resources/python-sdk.md)** rather than reconstructing the API from memory.

**Step 5 — Confirm and assemble.** Echo the chosen **mode + datapoint caps + categories + delivery (CLI vs script)** back to the user, **state the coverage gap** (which categories are *not* tested — a passing run only speaks to what was tested), then build the `eq redteam run` command (or script) from those choices.

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

## Acting on results — next steps

The summary tells you *how bad*; the `results` array tells you *what to fix*. Each item in `results[]` is one attack with everything needed to act:

| Field | Use it to |
|-------|-----------|
| `vulnerable` | `true` = the attack succeeded. **Filter to these first.** |
| `attack.category` / `attack.attack_technique` | Which OWASP category and technique broke through (e.g. `ASI01` / `tool_output_hijack`) |
| `attack.strategy_name` / `attack.objective` | The concrete adversarial goal that worked |
| `messages` | The full conversation sent to the agent — the exact prompts that landed |
| `response` | What the agent actually did/said when it failed |
| `evaluation.explanation` | The judge's reasoning for marking it vulnerable — the *why* |

**Workflow for a coding assistant after a run:**

```bash
REPORT=./output/my-run/report.json   # or the latest .evaluatorq/runs/<name>_<ts>.json

# 1. Headline: how many got through, and where it hurts most
jq '{asr: .summary.vulnerability_rate, found: .summary.vulnerabilities_found,
     by_technique: (.summary.by_technique | to_entries
       | map({(.key): .value.vulnerabilities_found}) | add)}' "$REPORT"

# 2. List every successful attack with the judge's reasoning
jq -r '.results[] | select(.vulnerable)
       | "## \(.attack.category)/\(.attack.attack_technique)  (\(.attack.strategy_name // "n/a"))\n"
       + "objective: \(.attack.objective // "n/a")\n"
       + "why: \(.evaluation.explanation)\n"' "$REPORT"

# 3. For one failure, read the exact transcript that broke the agent
jq '.results | map(select(.vulnerable)) | .[0] | {messages, response}' "$REPORT"
```

**Prioritize, then act.** Rank by `summary.by_technique` / `by_category` (highest `vulnerabilities_found` first), not by raw count of individual results. Then map each confirmed failure to a fix in the agent under test:

| Pattern in failures | Typical next step |
|---------------------|-------------------|
| Goal hijacking (`ASI01`), prompt injection (`LLM01`) | Harden the system prompt; add an input-screening guard; separate trusted instructions from untrusted input |
| Tool misuse (`ASI02`), excessive agency (`LLM06`) | Restrict tool scope/permissions; add per-tool authorization checks; gate destructive actions |
| System prompt leakage (`LLM07`), info disclosure (`LLM02`) | Add output filtering; never place secrets in the prompt; redact before returning |
| Memory/context poisoning (`ASI06`) | Validate/scope memory writes; isolate per-session context |

After applying a fix, **re-run the same `--category`/`--vulnerability` scope** and confirm `vulnerability_rate` dropped — this is the feedback loop.

> **LLM-written recommendations** (`report.focus_area_recommendations`: ranked `category`, `risk_score`, `recommendations[]`, `patterns_observed`) are **SDK-only** — pass `generate_recommendations=True` to `red_team()`. The CLI does not produce them; mine `results[]` as above instead. See [resources/python-sdk.md](resources/python-sdk.md).

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
# 1. Preflight — CLI present and ORQ_API_KEY set (hard-fail if missing)
eq --help >/dev/null 2>&1 || { echo "eq not found"; exit 1; }
[ -n "$ORQ_API_KEY" ] || { echo "ORQ_API_KEY not set — required for the agent target"; exit 1; }

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
| `eq: command not found` | Package not installed or not on PATH | Run the discovery ladder (uv tool / uv run / .venv) before installing; install with `uv tool install 'evaluatorq[redteam]'` |
| `401 Incorrect API key` on `openai/...` despite `ORQ_API_KEY` set, under `uv run` | `uv run` re-loaded `OPENAI_API_KEY` from `.env` after your `unset` → routing flipped to direct OpenAI | `uv run --no-env-file --env-file <tmp-with-only-ORQ_API_KEY> …`, or run `eq` directly off PATH. See the uv `.env` trap section |
| `RetrieveAgentRequestAgentsResponseBody: Agent not found` mid-run | Wrong / unprovisioned `--target` key | Verify up front via MCP `agent_get` / `GET /v2/agents/{key}` / SDK `agents.retrieve`; provision or fix the key. See "Verify the target agent exists" |
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
