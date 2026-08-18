# Run-Key Preflight Check

When a skill invokes an orq.ai agent or deployment (via evaluatorq, the SDK, or curl), the invocation uses the **`ORQ_API_KEY`** from the environment (exported or from the project `.env`). Keys are **project-scoped** (since release 4.10, one key → one project, no cross-project reads), so the *only* credential whose verdict predicts the run is that one.

**Always verify existence with the run key, not the MCP.** The MCP authenticates with a separately-configured key, often in a different project.

## Resolve the run key

The run key is whichever `ORQ_API_KEY` the invocation will see. Resolve it in **one shell block** (each Bash call is a fresh shell — splitting `$KEY` resolution from the `curl` loses it):

```bash
KEY="${ORQ_API_KEY:-$(set -a; . ./.env 2>/dev/null; printf %s "$ORQ_API_KEY")}"
if [ -z "$KEY" ]; then
  echo "No ORQ_API_KEY in env or ./.env — STOP and ask the user for the key or its path"
else
  export ORQ_API_KEY="$KEY"
fi
```

**If no key resolves** (not exported, no `.env`, or `.env` lacks it): **stop and ask the user** — "Which `.env`/path holds `ORQ_API_KEY`, or paste the key to use?" Don't guess a path, fabricate a key, or fall back to the MCP's key.

## Check agent existence

```bash
curl -s -w '\nHTTP %{http_code}\n' "https://api.orq.ai/v2/agents/<key>" \
  -H "Authorization: Bearer $KEY"
# 200 → exists (confirm "status":"live" in body)
# 404 → not found or not in this key's project
# 401 → bad key
```

Python SDK equivalent:

```python
import os
from orq_ai_sdk import Orq

with Orq(api_key=os.environ["ORQ_API_KEY"]) as orq:
    agent = orq.agents.retrieve(agent_key="<key>")
    print(agent.status)  # raises if not found; want "live"
```

## Check deployment existence

Deployments have no single-retrieve endpoint; use `get_config`:

```bash
curl -s -w '\nHTTP %{http_code}\n' -X POST "https://api.orq.ai/v2/deployments/get_config" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"key":"<key>"}'
# 200 → exists · 404 → deployment_not_found or not in this key's project · 401 → bad key
```

Python SDK equivalent:

```python
orq.deployments.get_config(key="<key>")  # raises if not found
```

## On a miss (404)

The key is wrong or scoped to another project. If the MCP shows the target but REST 404s, the run key is in the wrong project — **ask the user for the right key** (or its `.env`/path). Then re-check.

## MCP caveat — a miss is not proof of nonexistence

The orq MCP (`get_agent`, `search_entities`) is convenient for *browsing* and **can be correct**, but it uses its own key, often in a **different project** than the run. Neither verdict is authoritative:

- **MCP miss ≠ agent absent** — its key may be in the wrong project; the agent may exist for the run key.
- **MCP hit ≠ run will work** — it may see the agent in a project the run key can't reach; the run still dies with *Agent not found*.

Use the MCP as a **browse aid** to help the user find entity names and keys. Then **verify with the run key** before proceeding to the run.

## Skills referencing this doc

Fixed (verify with run key, MCP as browse aid):

| Skill | What changed |
|-------|-------------|
| `orq-red-team` | Original fix (PR #45) — full pattern inline |
| `orq-compare-agents` | Phase 1 agent discovery + constraint |
| `orq-simulate-agent` | Phase 1 agent target resolution |
| `orq-invoke-deployment` | Phase 1 key confirmation + constraint |
| `evaluatorq` | Phase 1 agent key discovery |

Audited, no change needed:

| Skill | Reason |
|-------|--------|
| `orq-build-agent` | MCP used for KB browsing (`search_entities type: "knowledge"`) and post-creation verification (`get_agent`) — same project, no cross-project run |
| `orq-optimize-prompt` | MCP used for prompt discovery (`search_entities type: "prompt"`) — read-only, no run that could fail with a different key |
| `orq-generate-synthetic-dataset` | MCP used for dataset browsing (`search_entities type: "dataset"`) — no agent/deployment invocation |
