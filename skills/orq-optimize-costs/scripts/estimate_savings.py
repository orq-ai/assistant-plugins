# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Savings estimate for orq-optimize-costs: what would this traffic cost on other models?

Recomputes each trace's token cost at catalogue list prices, for the model it ran on
and for the model a scenario moves it to, and anchors the difference on what the trace
was actually billed. Smart Router scenarios split the pool into bands and pick a model
per trace exactly as the Smart Router does; the rules are in resources/complexity-tiers.md.

Usage:
    uv run --no-project scripts/estimate_savings.py models.json traces.json tiers.json plan.json > savings.json

models.json is `orq models list -o json`; traces.json is the projected `orq traces search` rows
(one array, or a list of pages that each hold `data`), each resolved to a catalogue row by
`base_model[0]` and then `providers[0]/models[0]`; tiers.json and plan.json are written
by the agent (SKILL.md, Phases 4 and 5). Stdlib only. Prints one JSON object; never calls
the network.
"""

import codecs
import json
import math
import re
import sys

MARGIN = 1.25  # candidate token cost is inflated by this before any saving is counted
DOMINANCE_GAP = 0.20  # a whole-workload swap needs a blended price at least this much lower
TIERS = ("easy", "medium", "hard")
DATE_SUFFIX = re.compile(r"-(20\d{6}|(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01]))$")
RULE = (
    "Per trace: list cost at a model = (uncached prompt tokens x input price + cached tokens x "
    "cache-read price, or the input price when the model has none + completion tokens x output "
    "price) / 1e6, from metadata.million_tokens_*_cost. The candidate's list cost is multiplied by "
    f"{MARGIN} (tokenizer and verbosity margin). saving = (current list cost - candidate list cost) "
    "x min(1, billed / current list cost), so a bill below list price shrinks the saving and a bill "
    "above it never grows it; a trace that would cost more counts negative. savings_point is the "
    "same without the margin. Traces with no tier, no usage, several models, or a model without a "
    "price stay on their model and save nothing. monthly = window savings x 30 / window_days."
)


def load(path: str):
    with open(path, "rb") as handle:
        raw = handle.read()
    for bom, enc in ((codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16"), (codecs.BOM_UTF8, "utf-8-sig")):
        if raw.startswith(bom):
            return json.loads(raw.decode(enc))
    return json.loads(raw.decode("utf-8"))


def resolve(models: list[dict], ref: str) -> dict | None:
    """orq-shared/resources/model-slugs.md steps 1-4: id/refId, unique model_id, unique date suffix."""
    for keys in (("id", "refId"), ("model_id",)):
        hits = [m for m in models if ref in (m.get(k) for k in keys)]
        if len(hits) == 1:
            return hits[0]
        if hits:
            return None
    dated = [m for m in models if (m.get("refId") or "").startswith(ref) and DATE_SUFFIX.fullmatch(m["refId"][len(ref):])]
    return dated[0] if len(dated) == 1 else None


def meta(model: dict) -> dict:
    return model.get("metadata") or {}


def bench(model: dict) -> dict:
    return (meta(model).get("autorouter") or {}).get("v2") or {}


def prices(model: dict) -> tuple[float, float, float] | None:
    m = meta(model)
    inp, out = m.get("million_tokens_input_cost"), m.get("million_tokens_output_cost")
    if not inp or out is None:
        return None
    return inp, out, m.get("million_tokens_cache_read_cost") or inp


def list_cost(row: dict, rates: tuple[float, float, float]) -> float:
    inp, out, cache = rates
    cached = min(row["cached"], row["prompt"])
    return ((row["prompt"] - cached) * inp + cached * cache + row["completion"] * out) / 1e6


def rows_of(data) -> list[dict]:
    if isinstance(data, dict):
        data = data.get("data") or []
    out = []
    for item in data:
        out.extend(rows_of(item) if isinstance(item, (list, dict)) and "trace_id" not in item else [item])
    return out


def slugs(row: dict) -> list[str] | None:
    """Catalogue keys to try, best first; None when the trace ran on more than one model."""
    models, providers = row.get("models") or [], row.get("providers") or []
    base = [b for b in (row.get("base_model") or []) if b]
    if len(models) != 1 or len(providers) != 1 or len(set(base)) > 1:
        return None
    return [*base[:1], f"{providers[0]}/{models[0]}"]


def num(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None


def normalise(raw_rows: list[dict], models: list[dict]) -> tuple[list[dict], dict]:
    seen, rows = set(), []
    stats = {"rows": len(raw_rows), "duplicates": 0, "multi_model": 0, "no_usage": 0, "unresolved": {}}
    for raw in raw_rows:
        tid = raw.get("trace_id")
        if not tid or tid in seen:
            stats["duplicates"] += 1
            continue
        seen.add(tid)
        refs = slugs(raw)
        if refs is None:
            stats["multi_model"] += 1
            continue
        prompt, completion = num(raw.get("prompt")), num(raw.get("completion"))
        if prompt is None or completion is None or prompt + completion == 0:
            stats["no_usage"] += 1
            continue
        model = next((m for m in (resolve(models, r) for r in refs) if m is not None), None)
        if model is None:
            stats["unresolved"][refs[0]] = stats["unresolved"].get(refs[0], 0) + 1
            continue
        rows.append({"trace_id": tid, "model": model["refId"], "billed": num(raw.get("billed")),
                     "prompt": prompt, "completion": completion, "cached": num(raw.get("cached")) or 0.0})
    stats["usable"] = len(rows)
    return rows, stats


def saving(row: dict, cur: dict, cand: dict) -> tuple[float, float] | None:
    cur_rates, cand_rates = prices(cur), prices(cand)
    if cur_rates is None or cand_rates is None:
        return None
    cur_list, cand_list = list_cost(row, cur_rates), list_cost(row, cand_rates)
    if cur_list <= 0:
        return None
    anchor = min(1.0, row["billed"] / cur_list) if row["billed"] is not None else 1.0
    return (cur_list - cand_list) * anchor, (cur_list - cand_list * MARGIN) * anchor


def bands(pool: list[dict]) -> dict[str, list[dict]]:
    """The Smart Router split: strongest first; the weaker half is easy, the rest halves into medium and hard."""
    ranked = sorted(pool, key=lambda m: (-bench(m)["intelligence_index"], m["refId"]))
    easy = len(ranked) // 2
    stronger = len(ranked) - easy
    medium = (stronger + 1) // 2
    return {"hard": ranked[:stronger - medium], "medium": ranked[stronger - medium:stronger], "easy": ranked[stronger:]}


def select_band(split: dict[str, list[dict]], tier: str) -> list[dict]:
    present = [t for t in TIERS if split[t]]
    if tier in present:
        return split[tier]
    stronger = [t for t in present if TIERS.index(t) > TIERS.index(tier)]
    return split[stronger[0] if stronger else present[-1]]


def pick_in_band(band: list[dict], profile: str) -> dict:
    def ii(m):
        return bench(m)["intelligence_index"]

    def price(m):
        return bench(m).get("price") or 0.0

    if profile == "quality":
        return min(band, key=lambda m: (-ii(m), price(m), m["refId"]))
    return min(band, key=lambda m: (price(m), -ii(m), m["refId"]))


def capability_gaps(cur: dict, cand: dict, max_prompt: float) -> list[str]:
    gaps = []
    cm, dm = meta(cur), meta(cand)
    for key in ("supports_tool_calling", "supports_structured_outputs", "supports_vision"):
        if cm.get(key) is not False and dm.get(key) is not True:
            gaps.append(f"{cand['refId']}: {key} is not reported true while {cur['refId']} has it or does not report it")
    window = dm.get("context_window")
    if not window:
        gaps.append(f"{cand['refId']}: no context_window in the catalogue")
    elif window < max_prompt:
        gaps.append(f"{cand['refId']}: context_window {window} is below the largest prompt moved ({int(max_prompt)} tokens)")
    if cand.get("model_type") != "chat" or not cand.get("enabled"):
        gaps.append(f"{cand['refId']}: not an enabled chat model in this workspace")
    return gaps


def run_scenario(sc: dict, rows: list[dict], models: list[dict], tiers: dict, window_days: float) -> dict:
    name, kind = sc.get("name", "unnamed"), sc.get("kind")
    out = {"name": name, "kind": kind, "warnings": []}
    sources = sc["from"] if isinstance(sc.get("from"), list) else [sc.get("from")]
    current = {}
    for ref in sources:
        model = resolve(models, ref or "")
        if model is None:
            out["warnings"].append(f"from model {ref!r} does not resolve to one catalogue row")
        else:
            current[model["refId"]] = model
    scoped = [r for r in rows if r["model"] in current]
    tier_map = tiers.get("tiers") or {}
    measured_at = tiers.get("profile")

    if kind == "replace":
        cand = resolve(models, sc.get("to") or "")
        if cand is None:
            return {**out, "error": f"to model {sc.get('to')!r} does not resolve to one catalogue row"}
        moved_tiers = set(sc.get("tiers") or TIERS)
        out["needs_routing"] = moved_tiers != set(TIERS)
        target = lambda row: cand if tier_map.get(row["trace_id"]) in moved_tiers else None
        for ref, cur in current.items():
            cur_ii, cand_ii = bench(cur).get("intelligence_index"), bench(cand).get("intelligence_index")
            cur_p, cand_p = bench(cur).get("price") or None, bench(cand).get("price") or None
            out.setdefault("intelligence", {})[ref] = {"current": cur_ii, "candidate": cand_ii}
            if not out["needs_routing"] and (cur_ii is None or cand_ii is None or cand_ii < cur_ii):
                out["warnings"].append(f"whole-workload swap to a model without an intelligence index at least {ref}'s: validate every tier, hard first")
            if not out["needs_routing"] and cur_p and cand_p and cand_p > cur_p * (1 - DOMINANCE_GAP):
                out["warnings"].append(f"blended price gap to {ref} is under {int(DOMINANCE_GAP * 100)}%: the saving is likely inside the margin of error")
    elif kind == "smart_router":
        profile = sc.get("profile", "cost")
        pool = []
        for ref in sc.get("pool") or []:
            model = resolve(models, ref)
            if model is None or not bench(model).get("intelligence_index"):
                out["warnings"].append(f"pool model {ref!r} is not eligible: it does not resolve or has no intelligence index")
            else:
                pool.append(model)
                if not bench(model).get("price"):
                    out["warnings"].append(f"pool model {model['refId']} has price 0 (unpriced): the in-band pick will always choose it")
        if len(pool) < 2:
            return {**out, "error": "a Smart Router pool needs at least 2 eligible models"}
        split = bands(pool)
        out["profile"] = profile
        out["bands"] = {t: [m["refId"] for m in split[t]] for t in TIERS}
        for ref in current:
            if ref not in {m["refId"] for m in pool}:
                out["warnings"].append(f"{ref} is not in the pool: its hard-tier traffic moves to another model")
        if tiers.get("source") == "approximate" and profile == "quality":
            out["warnings"].append("approximate tiers carry no mode shift; quality mode escalates more often than modelled, so this saving is overstated")
        elif tiers.get("source") == "approximate" and profile == "cost":
            out["warnings"].append("approximate tiers carry no mode shift; cost mode routes more traffic to easier bands than modelled (understated)")
        elif measured_at and measured_at != profile:
            out["warnings"].append(f"tiers were measured in {measured_at} mode; {profile} mode shifts the tier boundaries")
        target = lambda row: pick_in_band(select_band(split, tier_map[row["trace_id"]]), profile) if tier_map.get(row["trace_id"]) in TIERS else None
    else:
        return {**out, "error": f"unknown kind {kind!r}"}

    by_tier = {t: {"traces": 0, "billed": 0.0, "savings_point": 0.0, "savings_conservative": 0.0} for t in (*TIERS, "unclassified")}
    routed, skipped, max_prompt = {}, {"unpriced": 0}, {}
    for row in scoped:
        tier = tier_map.get(row["trace_id"], "unclassified")
        tier = tier if tier in TIERS else "unclassified"
        bucket = by_tier[tier]
        bucket["traces"] += 1
        bucket["billed"] += row["billed"] or 0.0
        cand = target(row)
        if cand is None or cand["refId"] == row["model"]:
            routed[row["model"]] = routed.get(row["model"], 0) + 1
            continue
        result = saving(row, current[row["model"]], cand)
        if result is None:
            skipped["unpriced"] += 1
            routed[row["model"]] = routed.get(row["model"], 0) + 1
            continue
        routed[cand["refId"]] = routed.get(cand["refId"], 0) + 1
        key = (row["model"], cand["refId"])
        max_prompt[key] = max(max_prompt.get(key, 0), row["prompt"])
        bucket["savings_point"] += result[0]
        bucket["savings_conservative"] += result[1]

    for (cur_ref, cand_ref), largest in max_prompt.items():
        out["warnings"].extend(capability_gaps(current[cur_ref], resolve(models, cand_ref), largest))
    billed = sum(b["billed"] for b in by_tier.values())
    point = sum(b["savings_point"] for b in by_tier.values())
    conservative = sum(b["savings_conservative"] for b in by_tier.values())
    out.update({
        "traces": len(scoped),
        "billed": round(billed, 6),
        "savings_point": round(point, 6),
        "savings_conservative": round(conservative, 6),
        "savings_conservative_pct": round(conservative / billed, 4) if billed else None,
        "monthly_conservative": round(conservative * 30 / window_days, 2) if window_days else None,
        "by_tier": {t: {k: round(v, 6) if isinstance(v, float) else v for k, v in b.items()} for t, b in by_tier.items()},
        "routed_to": dict(sorted(routed.items(), key=lambda kv: -kv[1])),
        "skipped": skipped,
    })
    window_billed = (sc.get("window_billed") or {})
    if window_billed and out["savings_conservative_pct"] is not None:
        total = sum(v for k, v in window_billed.items() if resolve(models, k) and resolve(models, k)["refId"] in current)
        out["window_extrapolation"] = {
            "window_billed": round(total, 6),
            "savings_conservative": round(total * out["savings_conservative_pct"], 6),
            "basis": "conservative saving share of the classified traces x the window's billed cost for the same models",
        }
    out["warnings"] = list(dict.fromkeys(out["warnings"]))
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        print(__doc__, file=sys.stderr)
        return 2
    models, raw, tiers, plan = (load(p) for p in argv[1:])
    if not isinstance(models, list):
        print("models.json must be the bare array orq models list -o json returns", file=sys.stderr)
        return 2
    rows, stats = normalise(rows_of(raw), models)
    window_days = plan.get("window_days")
    tier_counts = {}
    for tier in (tiers.get("tiers") or {}).values():
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
    result = {
        "rule": RULE,
        "window_days": window_days,
        "tiers": {"source": tiers.get("source"), "profile": tiers.get("profile"), "counts": tier_counts},
        "traces": stats,
        "scenarios": [run_scenario(sc, rows, models, tiers, window_days) for sc in plan.get("scenarios") or []],
    }
    json.dump(result, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
