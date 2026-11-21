# Routing levers — what each recommendation turns into

Three levers move traffic to cheaper models. This file states what each one can and cannot do, and the exact write that applies it. **Every write here prompts, and none runs before the Phase 7 validation passed and the user approved that specific change.**

## Which lever fits which finding

| Finding | Lever | Why |
|---|---|---|
| A cheaper model has an intelligence index at least as high and the same capabilities | **Replace the model** on the agent, deployment or calling code | No routing needed; every tier moves |
| The traffic of one model mixes tiers, with a large easy or medium share | **Smart Router** | Only the Smart Router sees each request's complexity |
| One slice (an agent, deployment, project, identity or metadata value) is almost all one tier | **Routing rule** on that slice | A rule matches request attributes, never complexity, so it fits only a homogeneous slice |
| Any switch, before full rollout | **Weighted routing rule** as a canary | Sends a fixed share to the candidate while the rest stays put |

A routing rule cannot split by complexity. Recommend a rule for a slice only when its classified traffic is homogeneous: the lower bound of the 95% interval of its dominant tier is at least 90%.

## Replace the model

The owner of the call changes its model:

- **orq agent** → `orq-improve-agent` owns `model.id` and its versioned write.
- **orq deployment** → the deployment's prompt version, in the orq UI or over HTTP.
- **Router traffic from code** → the `model` string in the caller's code; hand it back as a diff.

A Smart Router's `model_ref` (`<workspace-key>@orq/<key>`) is accepted anywhere a model is: `/v3/router/responses`, `/v3/router/chat/completions`, `/v3/anthropic/v1/messages`, `/v2/deployments/invoke` and Agents. Adopting a router is the same model swap, to the router's reference.

## Smart Router

Workspace-wide, 2 to 50 distinct models, each with an intelligence index; a pool under 10 is recommended. New routers default to **cost** mode in the UI; the API requires `profile`.

```bash
orq smart-routers list -o json -j "data[].{id:smart_router_id,key:key,ref:model_ref,profile:profile,models:models,enabled:enabled}"
orq smart-routers create --key <key> --body-profile SMART_ROUTER_PROFILE_COST \
  --models <strongest> --models <mid> --models <cheapest> -o json
orq smart-routers update --help      # change models or profile; the key and model_ref are immutable
```

- `--profile` is the CLI's credentials profile. The router's mode is `--body-profile`, one of `SMART_ROUTER_PROFILE_QUALITY`, `SMART_ROUTER_PROFILE_BALANCED`, `SMART_ROUTER_PROFILE_COST`.
- Pool design that keeps hard traffic safe: put the model the traffic uses today in the pool. It is usually the strongest, so it forms the hard band and hard requests stay on it.
- **Session affinity** (`session_affinity: {enabled, ttl_seconds}`, off by default) pins a thread to its first turn's model. Recommend it for multi-turn traffic that relies on provider prompt caching: without it, consecutive turns can land on different models and lose the cache, which the estimate does not model.
- A router referenced by an experiment cannot be deleted.

## Routing rules

CEL rules on **AI Gateway** requests, managed by workspace administrators. The winner among matching enabled rules: workspace-global rules beat project-scoped ones whatever their priority; inside a scope the lowest `priority` wins, ties to the oldest. The winner's `models_config` **replaces** the request's `model`, `load_balancer`, `fallbacks` and `fallback_on_codes`; nothing is merged.

| Field | Values |
|---|---|
| `display_name` | required |
| `enabled` | defaults to `false`: a rule created without it matches nothing |
| `priority` | integer ≥ 0, default `0` |
| `project_id` | omit for a workspace-wide rule |
| `expression.cel` | up to 2000 characters; empty matches every request. Variables: `model`, `model_id`, `provider`, `headers`, `metadata`, `identity`, `project`, `api_key` |
| `models_config.mode` | `fallback`, `latency_based`, `weighted`, `round_robin` |
| `models_config.models[]` | `{model, weight}`; `weight` is a fraction of matched traffic, used in `weighted` mode |

A canary that sends 10% of one slice to the candidate:

```bash
curl -X POST https://my.orq.ai/v2/routing-rules \
  -H "Authorization: Bearer $ORQ_API_KEY" -H "Content-Type: application/json" \
  -d '{
    "display_name": "canary-<slice>-<candidate>",
    "enabled": true,
    "priority": 0,
    "expression": {"cel": "model == \"<current-model>\" && metadata.<key> == \"<value>\""},
    "models_config": {"mode": "weighted", "models": [
      {"model": "<current-model>", "weight": 0.9},
      {"model": "<candidate>", "weight": 0.1}
    ]}
  }'
```

`GET /v2/routing-rules` lists existing rules; read it before proposing one, because a matching global rule already decides what that traffic runs on. The `orq routing-rules` group listed no subcommands on CLI 11.4.1; use it where `orq routing-rules --help` shows `list` and `create`, otherwise the HTTP calls above. Both need a key with routing-rules access.

[unverified: whether routing rules apply to Agent and Deployment invocations as well as direct AI Gateway calls. Confirm on one trace before relying on a rule for that traffic.]
