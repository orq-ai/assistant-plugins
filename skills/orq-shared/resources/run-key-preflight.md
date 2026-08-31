# Run-Key Preflight Check

When a skill invokes an orq.ai agent or deployment (via evaluatorq, the SDK, or curl), the invocation uses the **`ORQ_API_KEY`** from the environment (exported or from the project `.env`). Keys are **project-scoped** (since release 4.10, one key → one project, no cross-project reads), so the *only* credential whose verdict predicts the run is that one.

**Always verify existence with the run key, not the MCP.** The MCP authenticates with a separately-configured key, often in a different project.

## Resolve the run key

The run key is whichever `ORQ_API_KEY` the invocation will see. Resolve it and run the check in **one shell block** (each Bash call is a fresh shell — splitting `$KEY` resolution from the `curl` loses it). The blocks below each include resolution so they can be run as-is.

**If no key resolves** (not exported, no `.env`, or `.env` lacks it): **stop and ask the user** — "Which `.env`/path holds `ORQ_API_KEY`, or paste the key to use?" Don't guess a path, fabricate a key, or fall back to the MCP's key.

## Check agent existence

```bash
KEY="${ORQ_API_KEY:-$(set -a; . ./.env 2>/dev/null; printf %s "$ORQ_API_KEY")}"
if [ -z "$KEY" ]; then
  echo "No ORQ_API_KEY in env or ./.env — STOP and ask the user for the key or its path"
  exit 1
fi
export ORQ_API_KEY="$KEY"

curl -s -w '\nHTTP %{http_code}\n' "https://api.orq.ai/v2/agents/<key>" \
  -H "Authorization: Bearer $KEY"
# 200 → exists (confirm "status":"live" in body)
# 404 → not found or not in this key's project
# 401 → bad key
# 000 → TLS failure, NOT a verdict — see "HTTP 000 is not a verdict" below
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

Deployments have no single-retrieve endpoint; use [`get_config`](https://docs.orq.ai/reference/deployments/get-config). No additional state check is needed beyond the HTTP status (unlike agents, which require `"status":"live"`) — a 200 means the deployment has a published config and is invokable.

```bash
KEY="${ORQ_API_KEY:-$(set -a; . ./.env 2>/dev/null; printf %s "$ORQ_API_KEY")}"
if [ -z "$KEY" ]; then
  echo "No ORQ_API_KEY in env or ./.env — STOP and ask the user for the key or its path"
  exit 1
fi
export ORQ_API_KEY="$KEY"

curl -s -w '\nHTTP %{http_code}\n' -X POST "https://api.orq.ai/v2/deployments/get_config" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"key":"<key>"}'
# 200 → exists and invokable
# 204 → deployment exists but has no published version — treat as a miss (stop and ask the user to publish)
# 404 → deployment_not_found or not in this key's project
# 401 → bad key
# 000 → TLS failure, NOT a verdict — see "HTTP 000 is not a verdict" below
```

Python SDK equivalent:

```python
import os
from orq_ai_sdk import Orq

with Orq(api_key=os.environ["ORQ_API_KEY"]) as orq:
    orq.deployments.get_config(key="<key>")  # raises if not found
```

## On a miss (404 or 204)

- **404** — the key is wrong or scoped to another project. If the MCP shows the target but REST 404s, the run key is in the wrong project — **ask the user for the right key** (or its `.env`/path). Then re-check.
- **204** (deployments only) — the deployment exists but has no published version (only drafts). The agent cannot proceed without a config. **Stop and ask the user** to publish a version of the deployment, then re-check.

## HTTP 000 is not a verdict

`curl` reports `000` with *"unable to get local issuer certificate"* or *"failed to verify the legitimacy of the server"* when TLS is being intercepted locally — a corporate proxy, or antivirus with HTTPS scanning. **The request never reached orq.ai.** It is not a missing agent, not a wrong project, and not a bad key. Never record it as a miss, and never let it trigger the "ask the user for a different key" path.

Retry with verification off, the same posture this repo already takes on Windows (`httpx(verify=False)`):

```bash
curl -s -k -w '\nHTTP %{http_code}\n' "https://api.orq.ai/v2/agents/<key>" \
  -H "Authorization: Bearer $KEY"
```

If `-k` turns `000` into `200`, the key and the agent were fine all along; carry on. If it still fails, the host is genuinely unreachable, and *that* is worth reporting to the user.

**Then read the response body, not just the status code.** A 200 here returns the agent's entire config — model, `instructions`, and `settings`. `orq-improve-agent` opens by checking that config against those instructions, so parsing it now saves re-fetching it later.

## MCP caveat — a miss is not proof of nonexistence

The orq MCP (`get_agent`, `search_entities`) is convenient for *browsing* and **can be correct**, but it uses its own key, often in a **different project** than the run. Neither verdict is authoritative:

- **MCP miss ≠ agent absent** — its key may be in the wrong project; the agent may exist for the run key.
- **MCP hit ≠ run will work** — it may see the agent in a project the run key can't reach; the run still dies with *Agent not found*.

Use the MCP as a **browse aid** to help the user find entity names and keys. Then **verify with the run key** before proceeding to the run.
