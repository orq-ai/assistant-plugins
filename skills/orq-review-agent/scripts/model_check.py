# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Model check for orq-review-agent: is the agent's model dominated on the catalogue?

The rule is the RULE constant below, and it is printed in every result so the
report quotes it rather than restating it. Resolving the agent's model id to a
catalogue row follows orq-shared/resources/model-slugs.md.

Usage:
    uv run --no-project scripts/model_check.py models.json agent.json [--coding] > model-check.json

models.json is `orq models list -o json`; agent.json is `orq agents retrieve <key> -o json`.
Stdlib only. Prints one JSON object; never calls the network.
"""

import codecs
import difflib
import json
import re
import sys

PRICE_GAP = 0.20  # candidate must be at least this much cheaper
MAX_CANDIDATES = 5
CONTEXT_FLOOR = 300_000  # 1M -> 300k is fine for most non-coding agents
# Version suffixes seen on catalogue refIds that agent slugs omit: -20251001, -0731.
DATE_SUFFIX = re.compile(r"-(20\d{6}|(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01]))$")
RULE = (
    "RES-1338 A1, guardrails agreed on RES-887 (2026-10-12): the model is dominated when "
    "another enabled chat model has an intelligence index at least as high at a blended "
    "price at least 20% lower, and: handles text in and out, plus image input or output "
    "where the current model has it; has tool calling where the current model has it; has "
    "structured outputs when response_format is json_schema; has a context window of at "
    "least min(current, 300k), or the full current window for a coding agent. Both models "
    "are scored at the agent's reasoning_effort when the current model has an index for "
    "it, otherwise both at their default effort. Provider aliases of one model are listed "
    "once, cheapest first. For agents with tools the comparison is repeated on tau2."
)


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


def model_key(model: dict) -> str:
    """One key per underlying model, so provider aliases (azure/eu.gpt-5.6-luna, openai/...) group."""
    return (meta(model).get("autorouter") or {}).get("id") or v2(model).get("aa_slug") or model.get("refId") or ""


def closest(models: list[dict], ref: str, n: int = 5) -> list[dict]:
    """Likely catalogue matches for an unresolved id, for the report only; never used for the check.

    Ranks by similarity of the model name (the part after the last `/`, date
    suffix stripped) to each row's refId and model_id names; the same provider
    only breaks ties.
    """
    provider, _, name = ref.rpartition("/")
    name = name.lower()
    scored = []
    for m in models:
        ref_id = m.get("refId") or ""
        names = {
            DATE_SUFFIX.sub("", raw.rpartition("/")[2].lower())
            for raw in (ref_id, m.get("model_id") or "")
        }
        ratio = max(difflib.SequenceMatcher(None, name, c).ratio() for c in names if c) if any(names) else 0
        if ratio >= 0.6:
            scored.append((-ratio, ref_id.split("/")[0] != provider.split("/")[0], ref_id, ratio))
    scored.sort()
    return [{"refId": ref_id, "similarity": round(ratio, 2)} for *_, ref_id, ratio in scored[:n]]


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
        "text": bool(meta(model).get("supports_text_input")) and bool(meta(model).get("supports_text_output")),
        "tool_calling": bool(meta(model).get("supports_tool_calling")),
        "vision": bool(meta(model).get("supports_vision")),
        "image_output": bool(meta(model).get("supports_image_output")),
        "structured_outputs": bool(meta(model).get("supports_structured_outputs")),
    }


def eligible(cand: dict, cur: dict, needs_tools: bool, needs_schema: bool, coding: bool) -> bool:
    c, k = facts(cand), facts(cur)
    if cand.get("model_type") != "chat" or not cand.get("enabled") or not c["text"]:
        return False
    if (needs_tools or k["tool_calling"]) and not c["tool_calling"]:
        return False
    if needs_schema and not c["structured_outputs"]:
        return False
    if (k["vision"] and not c["vision"]) or (k["image_output"] and not c["image_output"]):
        return False
    if k["context_window"]:
        floor = k["context_window"] if coding else min(k["context_window"], CONTEXT_FLOOR)
        if (c["context_window"] or 0) < floor:
            return False
    return True


def score(model: dict, metric: str, effort: str | None) -> tuple[float | None, str | None]:
    if metric == "intelligence_index":
        return index_for(model, effort)
    return facts(model)[metric], None


def compare(models, cur, metric, effort, needs_tools, needs_schema, coding):
    """Score both sides at the same effort: the agent's, if the current model has an index for it."""
    efforts = v2(cur).get("reasoning_efforts") or {}
    if effort and (efforts.get(effort) or {}).get("intelligence_index") is None:
        effort = None
    price = facts(cur)["price"]
    cur_value, cur_effort = score(cur, metric, effort)
    if cur_value is None or price is None:
        return {"status": "unknown", "reason": f"no {metric} or price for {cur.get('refId')}"}
    hits: dict[str, dict] = {}
    for cand in models:
        if cand is cur or not eligible(cand, cur, needs_tools, needs_schema, coding):
            continue
        f = facts(cand)
        value, cand_effort = score(cand, metric, effort)
        if value is None or f["price"] is None:
            continue
        if value >= cur_value and f["price"] <= price * (1 - PRICE_GAP):
            hit = {**f, metric: value, "price_gap": round(1 - f["price"] / price, 3), "aliases": []}
            if cand_effort:
                hit["effort"] = cand_effort
            key = model_key(cand)
            best = hits.get(key)
            if best is None or hit["price"] < best["price"]:
                hit["aliases"] = (best["aliases"] + [best["refId"]]) if best else []
                hits[key] = hit
            else:
                best["aliases"].append(hit["refId"])
    ranked = sorted(hits.values(), key=lambda h: (-h[metric], h["price"]))
    out = {
        "status": "dominated" if hits else "not dominated",
        "current": cur_value,
        "current_price": price,
        "candidates": ranked[:MAX_CANDIDATES],
        "candidate_count": len(ranked),
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


def check(models, agent, ref, params, role, coding):
    if not ref:
        return {"role": role, "declared": None, "resolved": "no model id",
                "intelligence": {"status": "unknown", "reason": f"{role} model has no id"}}
    tools = (agent.get("settings") or {}).get("tools") or []
    fmt = params.get("response_format") or {}
    needs_schema = isinstance(fmt, dict) and fmt.get("type") == "json_schema"
    cur, how = resolve(models, ref)
    out = {"role": role, "declared": ref, "resolved": how}
    if cur is None:
        out["intelligence"] = {"status": "unknown", "reason": how}
        out["closest"] = closest(models, ref)
        return out
    out["facts"] = facts(cur)
    effort = params.get("reasoning_effort")
    out["intelligence"] = compare(models, cur, "intelligence_index", effort, bool(tools), needs_schema, coding)
    if tools:
        out["tau2"] = compare(models, cur, "tau2", None, True, needs_schema, coding)
    return out


def load_json(path: str):
    """Read UTF-8 with or without a BOM, or the UTF-16 that PowerShell 5.1 `>` writes."""
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
        return json.loads(raw.decode("utf-16"))
    return json.loads(raw.decode("utf-8-sig"))


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--coding"]
    coding = "--coding" in sys.argv[1:]
    if len(args) != 2:
        sys.exit("usage: model_check.py models.json agent.json [--coding]")
    models = load_json(args[0])
    agent = load_json(args[1])
    ref, params = model_ref(agent.get("model"))
    entries = [("primary", ref, params)]
    for fb in agent.get("fallback_models") or []:
        fb_ref, fb_params = model_ref(fb)
        entries.append(("fallback", fb_ref, fb_params))
    result = {
        "rule": RULE,
        "context_floor": "full current window (--coding)" if coding else f"min(current, {CONTEXT_FLOOR})",
        "catalogue_size": len(models),
        "models": [check(models, agent, r, p, role, coding) for role, r, p in entries],
    }
    json.dump(result, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
