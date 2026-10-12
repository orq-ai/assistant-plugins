# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Model check for orq-review-agent: is the agent's model dominated on the catalogue?

Rule (RES-1338 A1, with its guardrails): a model is dominated when another
enabled chat model with at least the same capabilities has an intelligence
index at least as high, at a blended price at least 20% lower. For agents with
tools the same comparison is repeated on the tau2 agentic benchmark.

Usage:
    uv run --no-project scripts/model_check.py models.json agent.json > model-check.json

models.json is `orq models list -o json`; agent.json is `orq agents retrieve <key> -o json`.
Stdlib only. Prints one JSON object; never calls the network.
"""

import codecs
import json
import re
import sys

PRICE_GAP = 0.20  # candidate must be at least this much cheaper
MAX_CANDIDATES = 5
# Version suffixes seen on catalogue refIds that agent slugs omit: -20251001, -0731.
DATE_SUFFIX = re.compile(r"-(\d{8}|\d{4})$")


def v2(model: dict) -> dict:
    return ((model.get("metadata") or {}).get("autorouter") or {}).get("v2") or {}


def meta(model: dict) -> dict:
    return model.get("metadata") or {}


def resolve(models: list[dict], ref: str) -> tuple[dict | None, str]:
    """Match an agent model id to one catalogue row.

    `id` and `refId` are unique keys, so they win. `model_id` is a display alias
    several provider rows can share, so it is only tried after them. Agent slugs
    can omit a date suffix (`anthropic/claude-haiku-4-5` vs
    `anthropic/claude-haiku-4-5-20251001`), so the last resort is a refId equal
    to the slug plus a date-shaped suffix, and only when exactly one row matches.
    """
    for keys, how in ((("id", "refId"), "exact"), (("model_id",), "exact on model_id")):
        hits = [m for m in models if ref in (m.get(k) for k in keys)]
        if len(hits) == 1:
            return hits[0], how
        if hits:
            return None, "ambiguous: " + ", ".join(m.get("refId", "?") for m in hits)
    dated = [
        m for m in models
        if (m.get("refId") or "").startswith(ref) and DATE_SUFFIX.fullmatch(m["refId"][len(ref):])
    ]
    if len(dated) == 1:
        return dated[0], f"date-suffix match on {dated[0]['refId']}"
    if dated:
        return None, "ambiguous date suffix: " + ", ".join(m["refId"] for m in dated)
    return None, "not in the catalogue"


def index_for(model: dict, effort: str | None) -> tuple[float | None, str]:
    """Intelligence index at the given reasoning effort when the catalogue has it, else the default."""
    data = v2(model)
    efforts = data.get("reasoning_efforts") or {}
    if effort and (efforts.get(effort) or {}).get("intelligence_index") is not None:
        return efforts[effort]["intelligence_index"], effort
    return data.get("intelligence_index"), data.get("default_effort") or "default"


def facts(model: dict) -> dict:
    data = v2(model)
    return {
        "refId": model.get("refId"),
        "intelligence_index": data.get("intelligence_index"),
        # Unpriced models carry price 0 (seen on nvidia/moonshotai/kimi-k3); treat as unknown.
        "price": data.get("price") or None,
        "tau2": (data.get("aa_evaluations") or {}).get("tau2"),
        "context_window": meta(model).get("context_window"),
        "tool_calling": bool(meta(model).get("supports_tool_calling")),
        "vision": bool(meta(model).get("supports_vision")),
        "structured_outputs": bool(meta(model).get("supports_structured_outputs")),
    }


def eligible(cand: dict, cur: dict, needs_tools: bool, needs_schema: bool) -> bool:
    c, k = facts(cand), facts(cur)
    if cand.get("model_type") != "chat" or not cand.get("enabled"):
        return False
    if needs_tools and not c["tool_calling"]:
        return False
    if needs_schema and not c["structured_outputs"]:
        return False
    if k["vision"] and not c["vision"]:
        return False
    if k["context_window"] and (c["context_window"] or 0) < k["context_window"]:
        return False
    return True


def score(model: dict, metric: str, effort: str | None) -> tuple[float | None, str | None]:
    if metric == "intelligence_index":
        return index_for(model, effort)
    return facts(model)[metric], None


def compare(models, cur, metric, effort, needs_tools, needs_schema):
    """Candidates are scored at the agent's effort when they have it, so both sides match."""
    price = facts(cur)["price"]
    cur_value, cur_effort = score(cur, metric, effort)
    if cur_value is None or price is None:
        return {"status": "unknown", "reason": f"no {metric} or price for {cur.get('refId')}"}
    hits = []
    for cand in models:
        if cand is cur or not eligible(cand, cur, needs_tools, needs_schema):
            continue
        f = facts(cand)
        value, cand_effort = score(cand, metric, effort)
        if value is None or f["price"] is None:
            continue
        if value >= cur_value and f["price"] <= price * (1 - PRICE_GAP):
            hit = {**f, metric: value, "price_gap": round(1 - f["price"] / price, 3)}
            if cand_effort:
                hit["effort"] = cand_effort
            hits.append(hit)
    hits.sort(key=lambda h: (-h[metric], h["price"]))
    out = {
        "status": "dominated" if hits else "not dominated",
        "current": cur_value,
        "current_price": price,
        "candidates": hits[:MAX_CANDIDATES],
        "candidate_count": len(hits),
    }
    if cur_effort:
        out["effort"] = cur_effort
    return out


def model_ref(entry) -> tuple[str | None, dict]:
    """`model` and each fallback are a plain id string or `{id, parameters, ...}`."""
    if isinstance(entry, str):
        return entry, {}
    if isinstance(entry, dict):
        return entry.get("id"), entry.get("parameters") or {}
    return None, {}


def check(models, agent, ref, params, role):
    tools = (agent.get("settings") or {}).get("tools") or []
    fmt = params.get("response_format") or {}
    needs_schema = isinstance(fmt, dict) and fmt.get("type") == "json_schema"
    cur, how = resolve(models, ref)
    out = {"role": role, "declared": ref, "resolved": how}
    if cur is None:
        out["intelligence"] = {"status": "unknown", "reason": how}
        return out
    out["facts"] = facts(cur)
    effort = params.get("reasoning_effort")
    out["intelligence"] = compare(models, cur, "intelligence_index", effort, bool(tools), needs_schema)
    if tools:
        out["tau2"] = compare(models, cur, "tau2", None, True, needs_schema)
    return out


def load_json(path: str):
    """Read UTF-8 with or without a BOM, or the UTF-16 that PowerShell 5.1 `>` writes."""
    raw = open(path, "rb").read()
    if raw[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
        return json.loads(raw.decode("utf-16"))
    return json.loads(raw.decode("utf-8-sig"))


def main() -> None:
    models = load_json(sys.argv[1])
    agent = load_json(sys.argv[2])
    ref, params = model_ref(agent.get("model"))
    entries = [("primary", ref, params)]
    for fb in agent.get("fallback_models") or []:
        fb_ref, fb_params = model_ref(fb)
        entries.append(("fallback", fb_ref, fb_params))
    result = {
        "rule": "RES-1338 A1: >= index at >= 20% lower blended price, same capabilities",
        "catalogue_size": len(models),
        "models": [check(models, agent, r, p, role) for role, r, p in entries if r],
    }
    json.dump(result, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
