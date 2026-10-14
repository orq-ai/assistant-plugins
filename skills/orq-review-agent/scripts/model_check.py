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
from typing import NamedTuple

PRICE_GAP = 0.20  # candidate must be at least this much cheaper
MAX_CANDIDATES = 5
CONTEXT_FLOOR = 300_000  # 1M -> 300k is fine for most non-coding agents
# Version suffixes seen on catalogue refIds that agent slugs omit: -20251001, -0731.
DATE_SUFFIX = re.compile(r"-(20\d{6}|(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01]))$")
RULE = (
    "RES-1338 A1, guardrails agreed on RES-887 (2026-10-12): the model is dominated when "
    "another enabled chat model has an intelligence index at least as high at a blended "
    "price at least 20% lower, and: handles text in and out, plus image input where the "
    "current model has it or does not report it; has tool calling where the current model "
    "has it or does not report it, or the agent has tools attached; has structured outputs "
    "when response_format is json_schema; has a context window of at least min(current, "
    "300k), or the full current window for a coding agent, and at least 300k when the "
    "current window is not reported. A candidate must report each capability it needs. "
    "Image output is not checked: the catalogue does not report it. Each pair is "
    "scored at the agent's reasoning_effort when both models have an index for it, "
    "otherwise both at their default effort. The same model on another provider never "
    "dominates; one 20% cheaper is listed separately. Provider aliases of one model are "
    "listed once, cheapest first. For agents with tools the comparison is repeated on tau2."
)


class Needs(NamedTuple):
    """What the agent's config requires of any replacement model."""

    tools: bool
    schema: bool
    coding: bool


def benchmark(model: dict) -> dict:
    """The catalogue's benchmark block: index, price, per-effort indexes, tau2, aa_slug."""
    return ((model.get("metadata") or {}).get("autorouter") or {}).get("v2") or {}


def resolve(models: list[dict], ref: str) -> tuple[dict | None, str]:
    """Match an agent model id to one catalogue row.

    `id` and `refId` are unique keys, so they win. `model_id` is a display alias
    several provider rows can share, so it is only tried after them. Agent slugs
    can omit a date suffix (`anthropic/claude-haiku-4-5` vs
    `anthropic/claude-haiku-4-5-20251001`), so the last resort is a refId equal
    to the slug plus a date-shaped suffix, and only when exactly one row matches.
    """
    if not models:
        return None, "catalogue empty"
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
    """One key per benchmarked model, so provider aliases (azure/gpt-5.4, openai/gpt-5.4) group.

    `aa_slug`, not `metadata.autorouter.id`: that one names a family, and grouped
    gpt-5 with gpt-5.4 and gemini-3-flash-preview with gemini-3.6-flash on 2026-10-12.
    """
    return benchmark(model).get("aa_slug") or model.get("refId") or ""


def closest(models: list[dict], ref: str) -> list[dict]:
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
    return [{"refId": ref_id, "similarity": round(ratio, 2)} for *_, ref_id, ratio in scored[:MAX_CANDIDATES]]


def effort_index(model: dict, effort: str | None) -> float | None:
    """Intelligence index at this reasoning effort, or None when the catalogue has none for it."""
    if not effort:
        return None
    return ((benchmark(model).get("reasoning_efforts") or {}).get(effort) or {}).get("intelligence_index")


def default_index(model: dict) -> tuple[float | None, str]:
    data = benchmark(model)
    return data.get("intelligence_index"), data.get("default_effort") or "default"


# Capability flags the guards read. Absent is kept as None, not False: on 2026-10-13,
# supports_vision was absent on 43 of 160 chat models and supports_image_output on all 160.
FLAGS = {"tool_calling": "supports_tool_calling", "vision": "supports_vision",
         "structured_outputs": "supports_structured_outputs"}


def flag(meta: dict, key: str) -> bool | None:
    value = meta.get(key)
    return None if value is None else bool(value)


def facts(model: dict) -> dict:
    data = benchmark(model)
    meta = model.get("metadata") or {}
    return {
        "refId": model.get("refId"),
        "intelligence_index": data.get("intelligence_index"),
        # Unpriced models carry price 0 (seen on nvidia/moonshotai/kimi-k3); treat as unknown.
        "price": data.get("price") or None,
        "tau2": (data.get("aa_evaluations") or {}).get("tau2"),
        "context_window": meta.get("context_window"),
        "text": bool(meta.get("supports_text_input")) and bool(meta.get("supports_text_output")),
        **{name: flag(meta, key) for name, key in FLAGS.items()},
    }


def eligible(cand: dict, cand_facts: dict, cur_facts: dict, needs: Needs) -> bool:
    """A current flag that is True or absent is a requirement; a candidate meets it only by reporting True."""
    if cand.get("model_type") != "chat" or not cand.get("enabled") or not cand_facts["text"]:
        return False
    if (needs.tools or cur_facts["tool_calling"] is not False) and cand_facts["tool_calling"] is not True:
        return False
    if needs.schema and cand_facts["structured_outputs"] is not True:
        return False
    if cur_facts["vision"] is not False and cand_facts["vision"] is not True:
        return False
    window = cur_facts["context_window"]
    if window:
        floor = window if needs.coding else min(window, CONTEXT_FLOOR)
    else:
        floor = CONTEXT_FLOOR
    return (cand_facts["context_window"] or 0) >= floor


def current_score(cur: dict, metric: str, effort: str | None) -> tuple[float | None, str | None]:
    """The current model's own number, for the report: at the agent's effort when it has one."""
    if metric != "intelligence_index":
        return facts(cur)[metric], None
    at_effort = effort_index(cur, effort)
    return (at_effort, effort) if at_effort is not None else default_index(cur)


def paired_scores(cur: dict, cand: dict, metric: str, effort: str | None) -> tuple[float | None, float | None, str | None]:
    """Current and candidate on one footing: both at the agent's effort when both have it, else both at default."""
    if metric != "intelligence_index":
        return facts(cur)[metric], facts(cand)[metric], None
    cur_at, cand_at = effort_index(cur, effort), effort_index(cand, effort)
    if cur_at is not None and cand_at is not None:
        return cur_at, cand_at, effort
    return default_index(cur)[0], default_index(cand)[0], "default"


def keep_cheapest(rows: dict[str, dict], key: str, row: dict) -> None:
    """One row per model: the cheapest, with the other providers' refIds as aliases."""
    best = rows.get(key)
    if best is None or row["price"] < best["price"]:
        row["aliases"] = (best["aliases"] + [best["refId"]]) if best else []
        rows[key] = row
    else:
        best["aliases"].append(row["refId"])


def cheaper_provider(models: list[dict], cur: dict, needs: Needs) -> dict | None:
    """The same model on another provider at least PRICE_GAP cheaper, cheapest first; it never dominates.

    None means none was found; an unpriced current model returns `status: unknown`, never None.
    """
    cur_facts = facts(cur)
    price, cur_key = cur_facts["price"], model_key(cur)
    if price is None:
        return {"status": "unknown", "reason": f"no price for {cur.get('refId')}"}
    rows: dict[str, dict] = {}
    for cand in models:
        cand_facts = facts(cand)
        if cand is cur or model_key(cand) != cur_key or cand_facts["price"] is None:
            continue
        if cand_facts["price"] > price * (1 - PRICE_GAP) or not eligible(cand, cand_facts, cur_facts, needs):
            continue
        keep_cheapest(rows, cur_key, {**cand_facts, "price_gap": round(1 - cand_facts["price"] / price, 3), "aliases": []})
    return rows.get(cur_key)


def compare(models: list[dict], cur: dict, metric: str, effort: str | None, needs: Needs) -> dict:
    cur_facts = facts(cur)
    price = cur_facts["price"]
    cur_value, cur_effort = current_score(cur, metric, effort)
    if cur_value is None or price is None:
        return {"status": "unknown", "reason": f"no {metric} or price for {cur.get('refId')}"}
    cur_key = model_key(cur)
    hits: dict[str, dict] = {}
    compared = skipped = 0  # eligible catalogue rows with, and without, a price and score
    for cand in models:
        cand_facts = facts(cand)
        if cand is cur or model_key(cand) == cur_key or not eligible(cand, cand_facts, cur_facts, needs):
            continue
        base, value, scored_at = paired_scores(cur, cand, metric, effort)
        if cand_facts["price"] is None or value is None or base is None:
            skipped += 1
            continue
        compared += 1
        if cand_facts["price"] > price * (1 - PRICE_GAP) or value < base:
            continue
        row = {**cand_facts, "price_gap": round(1 - cand_facts["price"] / price, 3), "aliases": []}
        row.update({metric: value, "current_at_same_effort": base})
        if scored_at:
            row["effort"] = scored_at  # the agent's effort, or "default": each model at its own default
        keep_cheapest(hits, model_key(cand), row)
    if not compared:
        return {"status": "unknown",
                "reason": f"no comparable model: {skipped} eligible models lack a price or {metric}"}
    ranked = sorted(hits.values(), key=lambda h: (-h[metric], h["price"]))
    out = {
        "status": "dominated" if hits else "not dominated",
        "current": cur_value,
        "current_price": price,
        "candidates": ranked[:MAX_CANDIDATES],
        "candidate_count": len(ranked),
        "compared": compared,
        "skipped_no_data": skipped,
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
    needs = Needs(bool(tools), isinstance(fmt, dict) and fmt.get("type") == "json_schema", coding)
    cur, how = resolve(models, ref)
    out = {"role": role, "declared": ref, "resolved": how}
    if cur is None:
        out["intelligence"] = {"status": "unknown", "reason": how}
        out["closest"] = closest(models, ref)
        return out
    out["facts"] = facts(cur)
    meta = cur.get("metadata") or {}
    # Unreported on the current model, so treated as required of every candidate (context: at least 300k).
    out["unreported_flags"] = [
        key for key in ("supports_tool_calling", "supports_vision", "context_window") if meta.get(key) is None
    ]
    out["cheaper_provider"] = cheaper_provider(models, cur, needs)
    effort = params.get("reasoning_effort")
    out["intelligence"] = compare(models, cur, "intelligence_index", effort, needs)
    if tools:
        out["tau2"] = compare(models, cur, "tau2", None, needs)
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
