import importlib.util
import os
import shutil
import subprocess
import json
import random
import re
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

SPEC = importlib.util.spec_from_file_location(
    "cost_savings_report", Path(__file__).parents[1] / "scripts" / "cost_savings_report.py"
)
c = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = c
SPEC.loader.exec_module(c)


def attrs(messages, **extra):
    return {"gen_ai": {"input": {"messages": messages}, **extra.pop("gen_ai", {})}, **extra}


# ---- routing input


@pytest.mark.parametrize(
    "part",
    [
        {"type": "text", "content": "fix the bug"},
        {"kind": "text", "text": "fix the bug"},
        {"type": "input_text", "text": "fix the bug"},
    ],
)
def test_routing_input_part_shapes(part):
    assert c.routing_input(attrs([{"role": "user", "parts": [part]}])) == ("fix the bug", "user")


def test_routing_input_plain_string_content():
    assert c.routing_input(attrs([{"role": "user", "content": "  hi  "}])) == ("hi", "user")


def test_routing_input_takes_newest_user_turn_with_text():
    msgs = [
        {"role": "user", "parts": [{"type": "text", "content": "first"}]},
        {"role": "assistant", "parts": [{"type": "text", "content": "ok"}]},
        {"role": "user", "parts": [{"type": "tool_call_response", "content": "{}"}]},
    ]
    assert c.routing_input(attrs(msgs)) == ("first", "user")


def test_routing_input_strips_complete_system_reminders_and_joins_with_space():
    parts = [
        {"type": "text", "content": "<system-reminder>ignore me</system-reminder>  do X "},
        {"type": "text", "content": "<system-reminder>only this</system-reminder>"},
        {"type": "text", "content": "and Y"},
    ]
    assert c.routing_input(attrs([{"role": "user", "parts": parts}])) == ("do X and Y", "user")


def test_routing_input_keeps_unclosed_reminder():
    text, _ = c.routing_input(attrs([{"role": "user", "content": "<system-reminder>open tag"}]))
    assert text == "<system-reminder>open tag"


def test_routing_input_falls_back_to_developer_then_instructions():
    a = attrs([{"role": "developer", "parts": [{"type": "text", "content": "be terse"}]}])
    assert c.routing_input(a) == ("be terse", "system")
    a = {"gen_ai": {"input": {"messages": []}}, "openresponses": {"instructions": "summarise"}}
    assert c.routing_input(a) == ("summarise", "instructions")
    a = {"gen_ai": {"input": {"messages": []}, "system_instructions": json.dumps([{"type": "text", "content": "sys"}])}}
    assert c.routing_input(a) == ("sys", "system")


def test_routing_input_empty_and_limit():
    assert c.routing_input(attrs([])) == (None, "empty_input")
    assert c.routing_input(attrs([{"role": "user", "content": "x" * 65_001}])) == (None, "prompt_limit")
    assert c.routing_input(attrs([{"role": "user", "content": "x" * 65_000}]))[1] == "user"


# ---- score and tier


def test_score_order_and_threshold_edges():
    assert c.score({"easy": 0.2, "medium": 0.5, "hard": 0.3}) == pytest.approx(1.1)
    assert c.tier_of(0.11, "balanced") == "easy"
    assert c.tier_of(0.11000000000000001, "balanced") == "medium"
    assert c.tier_of(0.92, "balanced") == "medium"
    assert c.tier_of(0.9200001, "balanced") == "hard"
    for profile, (t1, t2) in c.THRESHOLDS.items():
        assert c.tier_of(t1, profile) == "easy"
        assert c.tier_of(t2, profile) == "medium"


# ---- bands and pick


def pm(i, idx, price):
    return c.PoolModel(f"m{i}", idx, price)


def test_bands_three_models_one_per_band():
    b = c.bands([pm(1, 10, 1), pm(2, 30, 3), pm(3, 20, 2)])
    assert [p.id for p in b["hard"]] == ["m2"]
    assert [p.id for p in b["medium"]] == ["m3"]
    assert [p.id for p in b["easy"]] == ["m1"]


def test_bands_two_models_hard_resolves_to_medium():
    pool = [pm(1, 10, 1), pm(2, 30, 3)]
    assert c.bands(pool)["hard"] == []
    assert c.pick(pool, "hard", "balanced").id == "m2"
    assert c.pick(pool, "easy", "balanced").id == "m1"


def test_bands_five_models_and_index_ties():
    pool = [pm(i, idx, 1) for i, idx in enumerate([50, 40, 40, 20, 10])]
    b = c.bands(pool)
    assert len(b["easy"]) == 2 and len(b["hard"]) == 1 and len(b["medium"]) == 2
    assert [p.id for p in b["medium"]] == ["m1", "m2"]  # tie on index broken by id


def test_pick_in_band_tie_breaks():
    band = [pm(1, 10, 1.0), pm(2, 12, 1.0), pm(3, 30, 5)]
    pool = band + [pm(4, 40, 9), pm(5, 50, 9), pm(6, 60, 9)]
    assert c.pick(pool, "easy", "balanced").id == "m2"  # same price: higher index


def test_two_model_pool_index_order_reverses_table_order():
    # deepseek-v4-pro is table tier 1 but has the lower index; flash goes medium, v4-pro easy
    pool = [c.PoolModel("deepseek/deepseek-v4-pro", 20.8, 1.0), c.PoolModel("deepseek/deepseek-flash", 28.6, 0.3)]
    assert c.pick(pool, "easy", "balanced").id == "deepseek/deepseek-v4-pro"
    assert c.pick(pool, "medium", "balanced").id == "deepseek/deepseek-flash"
    assert c.pick(pool, "hard", "balanced").id == "deepseek/deepseek-flash"


# ---- model names


def sm(name, idx, inp, out, enabled=True, region=None):
    """A ScoredModel; prices are USD per 1M tokens, the blend is (3 * input + output) / 4."""
    return c.mf.ScoredModel(router_id=name, intelligence_index=idx, input_price=inp, output_price=out,
                            enabled=enabled, region=region)


SOL, SOL61 = sm("openai/gpt-6-sol", 40, 3, 15), sm("openai/gpt-6.1-sol", 50, 2, 10)  # blends 6 and 4
TERRA, LUNA = sm("openai/gpt-6-terra", 30, 1, 5), sm("openai/gpt-6-luna", 20, 0.1, 0.5)  # blends 2 and 0.2


def test_switch_target_is_cheapest_dominating_model():
    pricier = sm("openai/gpt-6.2-sol", 55, 2.4, 12)  # dominates sol too, blend 4.8
    assert c.switch_target(SOL, [SOL, TERRA, LUNA, pricier, SOL61]).router_id == SOL61.router_id
    assert c.switch_target(SOL, [SOL, TERRA, LUNA]) is None


def test_switch_target_needs_no_index_gain():
    near = sm("acme/acme-sol", 40.5, 0.1, 0.5)  # half a point smarter, 20x cheaper: still the switch
    rehost = sm("azure/gpt-6-sol", 40, 2, 10)  # same weights, cheaper host
    assert c.switch_target(SOL, [near, rehost]).router_id == "acme/acme-sol"
    assert c.switch_target(SOL, [sm("acme/acme-dim", 39.5, 0.1, 0.5)]) is None  # cheaper but weaker


def test_prerelease_names():
    for name in ["gemini-3.1-pro-preview", "grok-4.20-beta-latest-reasoning", "hy4-preview", "x-alpha", "m-experimental"]:
        assert c.PRERELEASE_RE.search(name), name
    for name in ["muse-spark-1.3-contributor", "gpt-6-sol", "alphabet-1", "betamax", "previewer-2"]:
        assert not c.PRERELEASE_RE.search(name), name


def test_frontier_pool_three_tiers_far_enough_apart():
    members, relaxed = c.frontier_pool(SOL, [SOL, TERRA, LUNA])
    assert [m.router_id for m in members] == [SOL.router_id, TERRA.router_id, LUNA.router_id] and not relaxed


def test_frontier_pool_drops_tier_under_ratio_and_flags_pair():
    # mid's blend (2.5) is only 1.6x under 6.1-sol's (4), under the 2x rule: the pool is a flagged pair
    mid = sm("openai/gpt-6-mid", 35, 2, 4)
    members, relaxed = c.frontier_pool(SOL61, [SOL61, SOL, mid, LUNA])
    assert [m.router_id for m in members] == [SOL61.router_id, LUNA.router_id] and relaxed


def test_frontier_pool_seats_a_tier_exactly_half_the_price():
    # terra's blend (2) is half 6.1-sol's (4): 50% cheaper is enough for a seat
    members, relaxed = c.frontier_pool(SOL61, [SOL61, SOL, TERRA, LUNA])
    assert [m.router_id for m in members] == [SOL61.router_id, TERRA.router_id, LUNA.router_id] and not relaxed


def test_frontier_pool_never_seats_dominated_model():
    old_terra = sm("openai/gpt-5.6-terra", 18, 2, 12)  # luna beats it on index and price
    members, _ = c.frontier_pool(SOL, [SOL, old_terra, LUNA])
    assert old_terra.router_id not in [m.router_id for m in members]
    assert not c.has_dominated(members)
    # a dominated incumbent at the top is not seated over the cheaper model that beats it
    members, _ = c.frontier_pool(old_terra, [old_terra, LUNA])
    assert [m.router_id for m in members] == [old_terra.router_id]


def test_frontier_pool_prefers_stronger_weakest_seat():
    glm = sm("zai/glm-flash", 28, 0.05, 0.2)  # blend 0.0875, stronger and cheaper than luna
    members, _ = c.frontier_pool(SOL, [SOL, TERRA, LUNA, glm])
    assert members[-1].router_id == "zai/glm-flash"


def test_region_of():
    assert c.region_of("aws/eu.anthropic.claude-haiku-5-5") == "eu"
    assert c.region_of("aws/us-east-1.anthropic.claude-haiku-5-5") == "us"
    assert c.region_of("google/global.claude-opus-5-5") is None
    assert c.region_of("openai/gpt-6-luna") is None


# ---- pricing

HAIKU = {
    "input": {"cost": 0.1, "per": 1_000_000},
    "output": {"cost": 0.5, "per": 1_000_000},
    "cache_read": {"cost": 0.01, "per": 1_000_000},
    "variants": [
        {"when": "input_tokens > 100000", "input": {"cost": 0.5}, "output": {"cost": 2.5}},
        {"when": 'inference_geo == "us"', "input": {"cost": 0.11}, "output": {"cost": 0.55}},
        {"when": 'inference_geo == "us" && input_tokens > 100000', "input": {"cost": 0.55}},
        {"when": 'tier == "batch"', "input": {"cost": 0.05}, "output": {"cost": 0.25}},
        {"when": '(input_tokens > 100000) && tier == "batch"', "input": {"cost": 0.25}},
        {"when": '(inference_geo == "us" && input_tokens > 100000) && tier == "batch"', "input": {"cost": 0.275}},
    ],
}


def per_m(x):
    return round(x * 1_000_000, 6)


def test_price_base_when_span_shows_nothing():
    ch = c.choose_price(HAIKU, {"input_tokens": 500})
    assert ch.variant is None and per_m(ch.prices["input"]) == 0.1


def test_price_most_specific_variant_wins():
    env = {"input_tokens": 150_000, "inference_geo": "us", "tier": "batch"}
    ch = c.choose_price(HAIKU, env)
    assert per_m(ch.prices["input"]) == 0.275


def test_price_partial_override_keeps_base_keys():
    ch = c.choose_price(HAIKU, {"input_tokens": 150_000, "inference_geo": "us"})
    assert per_m(ch.prices["input"]) == 0.55
    assert per_m(ch.prices["output"]) == 0.5  # variant lists no output: base
    assert per_m(ch.prices["cache_read"]) == 0.01


def test_price_ties_go_to_last_listed():
    p = {"input": {"cost": 0.14}, "variants": [{"when": "request_unix >= 100", "input": {"cost": 0.22}},
                                                {"when": "request_unix >= 200", "input": {"cost": 0.15}}]}
    assert per_m(c.choose_price(p, {"request_unix": 300}).prices["input"]) == 0.15
    assert per_m(c.choose_price(p, {"request_unix": 150}).prices["input"]) == 0.22


def test_price_modulo_off_peak_rule():
    when = ("request_unix >= 0 && ((request_unix / 86400 + 3) % 7 < 5) && "
            "((request_unix % 86400 >= 3600 && request_unix % 86400 < 14400) || "
            "(request_unix % 86400 >= 21600 && request_unix % 86400 < 36000))")
    node = c.parse_when(when)
    monday_2am = 4 * 86400 + 2 * 3600  # day 4 since epoch is a Monday
    assert c.eval_when(node, {"request_unix": monday_2am}) is True
    assert c.eval_when(node, {"request_unix": monday_2am + 3 * 3600}) is False
    assert c.eval_when(node, {"request_unix": 2 * 86400 + 2 * 3600}) is False  # Saturday


@pytest.mark.parametrize(
    "expr,env,want",
    [
        ('tier != "batch"', {"tier": "standard"}, True),
        ("input_tokens >= 10", {"input_tokens": 10}, True),
        ("input_tokens <= 9 || input_tokens > 100", {"input_tokens": 101}, True),
        ('speed == "fast"', {}, False),
        ('operation != "chat"', {}, False),
        ("input_tokens / 0 > 1", {"input_tokens": 5}, False),
    ],
)
def test_when_operators_and_missing_variables(expr, env, want):
    assert c.eval_when(c.parse_when(expr), env) is want


def test_unparseable_variant_is_ignored():
    p = {"input": {"cost": 1}, "variants": [{"when": "tier === 'x'", "input": {"cost": 9}}]}
    assert per_m(c.choose_price(p, {"tier": "x"}).prices["input"]) == 1


def test_missing_tier_variant_is_flagged():
    assert c.choose_price(HAIKU, {"tier": "priority"}).flag == "no priority price"
    assert c.choose_price(HAIKU, {"tier": "batch"}).flag is None


def test_span_env_service_tier_default_is_standard_and_geo_from_model():
    summ = {"started_at": "2026-11-01T00:00:00Z", "usage": {"prompt_tokens": 7}, "operation": "chat"}
    env = c.span_env(summ, {"openai": {"response": {"service_tier": "default"}}}, "aws/eu.anthropic.claude-haiku-5-5")
    assert env["tier"] == "standard" and env["inference_geo"] == "eu" and env["input_tokens"] == 7


def test_tokens_and_uncached_input():
    summ = {"usage": {"prompt_tokens": 63270, "prompt_cached_tokens": 59365, "prompt_cache_creation_tokens": 3902,
                      "completion_tokens": 10}}
    a = {"gen_ai": {"usage": {"prompt_tokens_details": {"cache_creation_30m_tokens": 3902}}}}
    t = c.span_tokens(summ, a)
    assert (t.uncached, t.cache_read, t.cache_write, t.ttl) == (3, 59365, 3902, "30m")


def test_write_price_uses_span_ttl_else_shortest():
    prices = {"cache_write_1h": 2.0, "cache_write_5m": 1.0}
    assert c.write_price(prices, "1h") == 2.0
    assert c.write_price(prices, "30m") == 1.0
    assert c.write_price({}, None) is None


def test_price_tokens_reads_at_cache_price_else_input():
    t = c.Tokens(uncached=100, cache_read=1000, cache_write=0, output=10, ttl=None, prompt=1100)
    prices = {"input": 1.0, "output": 2.0, "cache_read": 0.1, "cache_write_5m": 1.25}
    assert c.price_tokens(t, prices) == pytest.approx(100 + 100 + 20)
    assert c.price_tokens(t, {"input": 1.0, "output": 2.0}) == pytest.approx(100 + 1000 + 20)  # no read price


# ---- estimator


def test_allocate_minimum_five_and_spend_proportional():
    spend = [("a", 70.0), ("b", 20.0), ("c", 6.0), ("d", 4.0)]
    alloc = c.allocate(spend, 100)
    assert all(n >= 5 for n in alloc.values())
    assert "d" not in alloc  # 4% of 100 is under 5
    assert sum(alloc.values()) == 100


def test_allocate_long_tail_caps_strata():
    spend = [(f"m{i}", 1.0) for i in range(50)]
    alloc = c.allocate(spend, 100)
    assert len(alloc) == 20 and set(alloc.values()) == {5}


def test_weighted_ratio_by_hand():
    ds = [c.Draw("s", 2, 1.0, {"balanced": 0.5}), c.Draw("s", 1, 4.0, {"balanced": 1.0})]
    # (2*0.5 + 1*1.0) / (2*1 + 1*4) = 2/6
    assert c.stratum_rate(ds, "balanced") == pytest.approx(1 / 3)
    est = c.estimate({"s": ds}, {"s": 60.0}, "balanced", random.Random(0), boots=200)
    assert est.savings == pytest.approx(20.0)
    lo, hi = est.ci90
    assert lo <= est.savings <= hi


def test_without_top3_drops_largest_contributions():
    ds = [c.Draw("s", 1, 1.0, {"balanced": x}) for x in (10, 9, 8, 0.1, 0.1)]
    full = c.estimate({"s": ds}, {"s": 5.0}, "balanced", random.Random(0), boots=10).savings
    assert c.without_top({"s": ds}, {"s": 5.0}, "balanced") < full


def test_without_top3_ranks_by_contribution_to_the_estimate():
    a = [c.Draw("a", 1, 0.01, {"balanced": 0.005})]  # tiny saving per call, but stratum a is where the spend is
    b = [c.Draw("b", 1, 1.0, {"balanced": 0.5})]
    spend = {"a": 1000.0, "b": 10.0}
    # a's call is worth $500 of the $505 estimate, b's $5: dropping the top call leaves b's $5
    assert c.without_top({"a": a, "b": b}, spend, "balanced", k=1) == pytest.approx(5.0)


# ---- caching


def turn(t, prompt, cached=0, written=0):
    return c.Turn(t, prompt, cached, written)


PRICES = {"input": 1.0, "cache_read": 0.1, "cache_write_5m": 1.25}


def test_caching_read_and_write_per_turn():
    turns = [turn(0, 2000), turn(10, 3000), turn(20, 4000)]
    saving = c.caching_savings(turns, PRICES, 300)
    # reads: 2000 + 3000 at (1 - 0.1); writes: the first prompt 2000, then 1000 + 1000, at (1.25 - 1)
    assert saving == pytest.approx(5000 * 0.9 - 4000 * 0.25)


def test_caching_ttl_expiry_rewrites_whole_prompt():
    turns = [turn(0, 2000), turn(1000, 3000)]
    assert c.caching_savings(turns, PRICES, 300) == pytest.approx(-5000 * 0.25)


def test_caching_nets_out_existing_reads_and_writes():
    turns = [turn(0, 2000), turn(10, 3000, cached=2000, written=1000)]
    # the first turn still pays the write premium on its 2000 tokens
    assert c.caching_savings(turns, PRICES, 300) == pytest.approx(-2000 * 0.25)


def test_caching_candidate_rules():
    good = [turn(0, 2000), turn(1, 3000), turn(2, 4000)]
    assert c.caching_candidate(good, PRICES) == (True, "")
    assert not c.caching_candidate(good[:2], PRICES)[0]
    assert not c.caching_candidate(good, {"input": 1.0})[0]
    assert not c.caching_candidate([turn(0, 500), turn(1, 3000), turn(2, 4000)], PRICES)[0]
    cached = [turn(0, 2000), turn(1, 3000, cached=2000), turn(2, 4000, cached=3000)]
    assert c.caching_candidate(cached, PRICES)[0]  # already caching still counts: it can miss turns
    missed = [turn(0, 2000), turn(1, 3000, cached=2000), turn(2, 4000)]  # the last turn missed the cache
    gap = c.caching_savings(missed, PRICES, 300) - c.caching_savings(cached, PRICES, 300)
    assert gap == pytest.approx(3000 * (PRICES["input"] - PRICES["cache_read"]))  # only the missed read counts


# ---- span filter


def test_leaf_llm_filter():
    spans = [
        {"type": "trace", "has_children": True, "provider": "openai", "model": "gpt-6-sol"},
        {"type": "span.agent", "has_children": True, "provider": "openai", "model": "gpt-6-sol"},
        {"type": "span.responses", "has_children": False, "provider": "openai", "model": "gpt-6-sol"},
        {"type": "span.chat_completion", "has_children": False, "provider": "openai", "model": "gpt-6-luna"},
        {"type": "span.cache_retrieval", "has_children": False, "provider": "openai", "model": "gpt-6-sol"},
    ]
    assert len(c.Analyzer.leaf_llm(spans)) == 2
    assert len(c.Analyzer.leaf_llm(spans, "openai/gpt-6-sol")) == 1


def test_chat_model_filter():
    assert not c.is_chat_model("typesafe/jev-latest", {})
    assert not c.is_chat_model("openai/text-embedding-4", {})
    assert c.is_chat_model("openai/gpt-6-sol", {})


def test_auto_router_detection():
    assert c.has_auto_router({"orq": {"auto_router": {"profile": "balanced"}}})
    assert not c.has_auto_router({"orq": {"product": "router"}})


# ---- narrative


def test_parse_narrative_and_render_refuses_empty_slot():
    text = "## summary\n\nA.\n\n## routing\n\n<!-- todo -->\n\n## caching\n\nC.\n\n## caveats\n\nD.\n"
    n = c.parse_narrative(text)
    assert n["summary"] == "A." and n["routing"] == ""
    with pytest.raises(SystemExit, match="routing"):
        c.render({}, n, redact=False)


def test_switch_target_on_traffic_counts_cache_reads():
    flash = sm("acme/acme-flash", 41, 0.1, 0.5)  # dominates sol on the blend
    other = sm("acme/acme-other", 40, 0.5, 2)
    costs = {SOL.router_id: 1.0, "acme/acme-flash": 1.2, "acme/acme-other": 0.6}  # flash's cache reads cost more
    assert c.switch_target(SOL, [flash, other], costs.get).router_id == "acme/acme-other"
    assert c.switch_target(SOL, [flash], costs.get) is None
    assert c.switch_target(SOL, [flash, other], {SOL.router_id: 1.0}.get) is None  # unpriced: never a target


def test_frontier_pool_on_traffic_follows_cache_prices():
    # on the blend sol > terra > luna is a clean trio; on cached traffic luna's reads cost nearly what terra's do
    costs = {SOL.router_id: 6.0, TERRA.router_id: 2.0, LUNA.router_id: 1.9}
    members, relaxed = c.frontier_pool(SOL, [SOL, TERRA, LUNA], costs.get)
    assert [m.router_id for m in members] == [SOL.router_id, TERRA.router_id] and relaxed  # luna under 2x
    assert not c.has_dominated(members, costs.get)
    cheap = sm("acme/acme-flash", 25, 0.1, 0.5)  # weaker than terra, far cheaper on traffic: replaces luna
    costs[cheap.router_id] = 0.5
    members, relaxed = c.frontier_pool(SOL, [SOL, TERRA, LUNA, cheap], costs.get)
    assert [m.router_id for m in members] == [SOL.router_id, TERRA.router_id, "acme/acme-flash"] and not relaxed
    unpriced = {SOL.router_id: 6.0, TERRA.router_id: 2.0}  # luna unpriced on these spans: never seated
    members, _ = c.frontier_pool(SOL, [SOL, TERRA, LUNA], unpriced.get)
    assert LUNA.router_id not in [m.router_id for m in members]


def test_report_number_formats():
    assert c._usd(0.004) == "<$0.01" and c._usd(0) == "$0.00" and c._usd(1234.5) == "$1,234"
    assert c._change(-0.46) == "-46%" and c._change(45.4) == "46x the cost"
    assert c._day("2026-10-22T10:29:00Z") == "22 Oct 2026"
    assert c._usd0(-0.2) == "$0" and c._usd0(-3.4) == "-$3" and c._usd0(0.2) == "$0"
    assert c._scrub("see /Users/someone/repo/a.py and /home/x/y") == "see <path> and <path>"


def _bm(pm: float) -> dict:
    return {"balanced": {"per_month": pm}}


def _plan_report() -> dict:
    """Headline needs y and z enabled; the enabled-only scope differs from the headline on model b only."""
    return {"routing": {"headline": {"per_month": 100.0},
                        "strata": [{"model": "openai/a", "savings": 10.0}, {"model": "openai/b", "savings": 50.0},
                                   {"model": "openai/c", "savings": 40.0}, {"model": "openai/d", "savings": 0.0}]},
            "scenarios": [
                {"scope": "same_provider:all", "provider": "same_provider", "availability": "all", "headline": True,
                 "router": _bm(100.0), "models": [
                     {"model": "openai/a", "pool": None, "switch": "openai/x", "enable": []},
                     {"model": "openai/b", "pool": ["openai/y", "openai/x"], "switch": "openai/y",
                      "enable": ["openai/y"]},
                     {"model": "openai/c", "pool": None, "switch": "openai/z", "enable": ["openai/z"]},
                     {"model": "openai/d", "pool": None, "switch": None, "enable": []}]},
                {"scope": "same_provider:enabled", "provider": "same_provider", "availability": "enabled",
                 "headline": False, "router": _bm(30.0), "models": [
                     {"model": "openai/a", "pool": None, "switch": "openai/x", "enable": []},
                     {"model": "openai/b", "pool": None, "switch": "openai/x", "enable": []},
                     {"model": "openai/c", "pool": None, "switch": None, "enable": []},
                     {"model": "openai/d", "pool": None, "switch": None, "enable": []}]}]}


def _rows(report: dict, lever: str = "router") -> dict:
    """Rows as the page draws them: analysed and stored by analyze, then viewed by render."""
    return {x.model: {"saved": x.saved_per_month, "today": x.saved_today_per_month,
                      "after": x.saved_after_enabling_per_month, "hard": x.share_hard, "enable": x.enable,
                      "lines": c._rec_lines(x), "note": c._rec_note(x)}
            for x in c._rec_rows(report, 1.0, lever, {})}


def _chart(report: dict, lever: str = "router") -> str:
    return c._actions_chart(c._rec_rows(report, 1.0, lever, {}), c._not_analysed(report, 1.0))


def test_plan_rows_split_sums_to_enabled_and_headline_figures():
    rows = _rows(_plan_report())
    assert sum(r["today"] for r in rows.values()) == pytest.approx(30)  # enabled-scope router figure
    assert sum(r["today"] + r["after"] for r in rows.values()) == pytest.approx(100)  # the headline
    assert rows["openai/a"]["today"] == 10 and rows["openai/a"]["after"] == 0  # same move in both scopes
    assert rows["openai/b"]["today"] == pytest.approx(20) and rows["openai/b"]["after"] == pytest.approx(30)  # residual
    assert rows["openai/c"]["today"] == 0 and rows["openai/c"]["after"] == 40  # nothing enabled to act on yet
    assert rows["openai/d"]["saved"] == 0


def test_plan_rows_text_and_keep_rows():
    rows = _rows(_plan_report())
    assert rows["openai/a"]["lines"] == ["Change to x"]
    assert rows["openai/b"]["lines"] == ["Now: change to x.", "After enabling y: route over y and x."]
    assert rows["openai/c"]["lines"] == ["After enabling z: change to z."]
    assert rows["openai/d"]["lines"] == ["Keep it: no cheaper OpenAI model is as strong"]


def test_plan_rows_split_residual_by_headline_saving_when_several_moves_differ():
    report = _plan_report()
    report["scenarios"][1]["models"][0] = {"model": "openai/a", "pool": None, "switch": "openai/w", "enable": []}
    rows = _rows(report)
    assert sum(r["today"] for r in rows.values()) == pytest.approx(30)
    assert rows["openai/a"]["today"] == pytest.approx(5) and rows["openai/b"]["today"] == pytest.approx(25)


def test_hard_models_flags_half_or_more_hard_asks():
    ro = {"underpowered": [{"model": "m1", "share_hard": 0.5}, {"model": "m2", "share_hard": 0.49},
                           {"model": "m3", "share_hard": None}]}
    assert c._hard_models(ro) == {"m1": 0.5}
    assert c._hard_models({}) == {}


def test_hard_flag_shows_on_the_flagged_row_with_the_largest_saving_only():
    report = _plan_report()
    report["routing"]["underpowered"] = [{"model": "openai/a", "share_hard": 0.9}, {"model": "openai/b", "share_hard": 0.8},
                                         {"model": "openai/d", "share_hard": 1.0}]
    rows = _rows(report)
    assert rows["openai/b"]["hard"] == 0.8  # biggest saving among flagged
    assert rows["openai/a"]["hard"] is None and rows["openai/d"]["hard"] is None  # keep row has no move to warn about
    page = _chart(report)
    assert page.count("Mostly hard work") == 1 and "Mostly hard work: spot-check answers before moving it." in page


def test_row_percent_leads_the_right_column():
    report = _plan_report()
    report["routing"]["strata"][1].update(spend=100.0)
    chart = _chart(report)
    assert "<b>50% saved</b><span>$50 of $100/mo</span>" in chart  # model b: 50 saved of 100 spend


def test_uncertainty_rows_carry_percent_and_ci():
    report = _plan_report()
    for sc in report["scenarios"]:
        sc["switch"] = {"per_month": 20.0, "ci90_per_month": [15.0, 25.0]}
        sc["router"]["balanced"]["ci90_per_month"] = [90.0, 110.0]
    rows = c._uncertainty_rows(c._scopes(report, 400.0))
    assert rows[0]["lever"] == "Smart Router" and rows[0]["pct"] == 0.25 and rows[0]["ci"] == "$90 to $110"
    assert rows[1]["pct"] == 0.05


def test_keep_rows_and_not_sampled_spend_are_lines_not_bars():
    report = _plan_report()
    report["routing"]["strata"][3]["model"] = "openai/d"
    report["routing"]["not_covered"] = [{"model": "x/1", "spend": 60.0}, {"model": "x/2", "spend": 24.0}]
    chart = _chart(report)
    assert chart.count("class=row>") == 3  # a, b, c; the keep row d has no bar
    assert "d: keep, already the cheapest model that is as strong." in chart
    assert "Not analysed: $84/mo of spend on 2 models." in chart
    assert "class=ns" not in chart and "Not sampled</li>" not in chart


def test_scope_grid_marks_headline_and_enable_first():
    report = _plan_report()
    for sc in report["scenarios"]:
        sc["switch"] = {"per_month": 80.0, "ci90_per_month": [70.0, 90.0]}
        sc["router"]["balanced"]["ci90_per_month"] = [90.0, 110.0]
    grid = c._scope_grid(c._scopes(report, None))
    assert grid.count("class='cell hl'") == 1 and "<p class=ch>Any provider</p>" in grid
    assert "enable 2 models" in grid and "title='openai/y, openai/z'" in grid
    assert "90% CI" not in grid and "n/a" in grid  # the two any-provider scopes are absent
    pct = c._scope_grid(c._scopes(report, 1000.0))
    assert "<p class=f1>10%<span>Smart Router</span></p>" in pct and "<p class=f2>8%<span>Change model</span></p>" in pct
    assert "$100/mo" in pct and "$80/mo" in pct


def test_flow_router_fork_shares_tiers_and_enable():
    head = {"model": "openai/s", "pool": ["openai/s", "openai/h"], "switch": None, "enable": ["openai/h"]}
    today = {"model": "openai/s", "pool": None, "switch": "openai/m", "enable": []}
    moves = [{"used": "openai/s", "pick": "openai/s", "tier": "hard, medium", "n": 3},
             {"used": "openai/s", "pick": "openai/h", "tier": "easy", "n": 1},
             {"used": "openai/x", "pick": "openai/h", "tier": "easy", "n": 9}]
    f = c._flow("openai/s", head, moves)
    assert f.lever == "router"
    assert [(b.model, b.share_of_calls, b.tiers, b.enable, b.stays) for b in f.targets] == [
        ("openai/s", 0.75, ["medium", "hard"], False, True), ("openai/h", 0.25, ["easy"], True, False)]
    assert c._before_enabling_note(f.enable, today, head, 12.0, 48.0) == \
        "Before enabling: change to m now, 25% saved ($12/mo)."
    out = c._flow_html("openai/s", f.lever, f.targets, ["x"])
    assert "class='to fork'" in out and "75% \u00b7 Medium, Hard" in out and "<em>enable</em>" in out
    assert "<em>stays</em>" in out and "class=share" not in out  # one line per target, no mini bar
    assert "asks" not in out and out.count("<em>enable</em>") == 1


def test_flow_note_only_when_today_saves_money():
    head = {"model": "openai/s", "pool": ["openai/s", "openai/h"], "switch": None, "enable": ["openai/h"]}
    today = {"model": "openai/s", "pool": None, "switch": "openai/m", "enable": []}
    moves = [{"used": "openai/s", "pick": "openai/h", "n": 1}]
    enable = c._flow("openai/s", head, moves).enable
    assert enable and c._before_enabling_note(enable, today, head, 0.0, 48.0) == ""
    assert c._before_enabling_note(enable, today, head, None, 48.0) == ""


def test_flow_shares_weight_drawn_targets_only_and_fallbacks_get_no_tier():
    head = {"model": "openai/s", "pool": ["openai/s", "openai/h", "openai/z"], "switch": None,
            "enable": ["openai/h", "openai/z", "openai/s"]}
    moves = [{"used": "openai/s", "pick": "openai/s", "tier": "easy", "n": 5, "weight": 10.0, "fallback": 0},
             {"used": "openai/s", "pick": "openai/h", "tier": "fallback: error, hard", "n": 5, "weight": 30.0,
              "fallback": 2},
             {"used": "openai/s", "pick": "openai/q", "tier": "hard", "n": 9, "weight": 99.0, "fallback": 0}]
    f = c._flow("openai/s", head, moves)
    got = {b.model: b for b in f.targets}
    assert set(got) == {"openai/s", "openai/h"}  # z was never drawn; q is not a target
    assert got["openai/h"].share_of_calls == 0.75 and got["openai/s"].share_of_calls == 0.25  # weight, not n
    assert got["openai/h"].tiers == ["hard"] and got["openai/h"].unscored_calls == 2
    assert f.enable == ["openai/h"] and not got["openai/s"].enable  # enable only on drawn targets, never itself
    out = c._flow_html("openai/s", f.lever, f.targets, ["x"])
    assert "2 ungraded calls'" in out and "ungraded</small>" not in out  # in the chip's tooltip
    moves[1].pop("weight"), moves[0].pop("weight")
    assert {b.model: b.share_of_calls for b in c._flow("openai/s", head, moves).targets} == {
        "openai/h": 0.5, "openai/s": 0.5}  # falls back to n


def test_row_enable_set_drives_lines_and_note():
    report = _plan_report()
    report["routing"]["moves"] = [{"used": "openai/b", "pick": "openai/x", "n": 3, "fallback": 0}]
    row = _rows(report)["openai/b"]
    assert row["enable"] == [] and row["note"] == ""  # y is not drawn: nothing to enable
    assert row["lines"] == ["Route over y and x"]


def test_today_uses_enabled_scope_per_stratum_when_present():
    report = _plan_report()
    report["scenarios"][1]["router"]["balanced"]["per_stratum"] = {"openai/a": 9.0, "openai/b": 21.0}
    rows = _rows(report)
    assert rows["openai/a"]["today"] == 9 and rows["openai/b"]["today"] == 21 and rows["openai/c"]["today"] == 0


def test_legend_chart_caption_and_kept_sliver():
    report = _plan_report()
    report["routing"]["strata"][1].update(spend=100.0)
    report["routing"]["moves"] = [{"used": "openai/b", "pick": "openai/y", "n": 3, "fallback": 0},
                                  {"used": "openai/b", "pick": "openai/x", "n": 3, "fallback": 0}]
    chart = _chart(report)
    assert "Arrow: move all calls" in chart and "Fork: Smart Router picks a model per call" in chart
    assert "Dashed: enable this model in your orq.ai workspace first" in chart
    shown = re.sub(r"data-tip='[^']*'", "", chart)  # bar tooltips name the lever on purpose
    assert "Smart Router splits asks" not in shown and "Change model" not in shown
    assert "Recommended plan (Smart Router): one move per model. Together: 25% saved ($100/mo). Each bar is that model's monthly spend." \
        in c._plan_caption("router", 0.25, 100.0)
    assert chart.count("<i class=kept") == 1  # b only: a and c keep nothing worth a segment
    report["routing"]["strata"][2].update(spend=40.5)  # c saves 40 of 40.5: kept sliver under 2% of its spend
    assert _chart(report).count("<i class=kept") == 1


def test_flow_change_model_is_one_arrow_without_shares():
    head = {"model": "openai/a", "pool": None, "switch": "openai/b", "enable": []}
    f = c._flow("openai/a", head, [{"used": "openai/a", "pick": "openai/b", "tier": "easy, hard, medium", "n": 4}])
    out = c._flow_html("openai/a", f.lever, f.targets, ["Change to b"])
    assert f.lever == "switch" and f.targets[0].tiers == []
    assert "class='flow switch'" in out and "class='to one'" in out and "<small>" not in out


def test_small_sample_note_under_min_sample():
    report = _plan_report()
    report["routing"]["moves"] = [{"used": "openai/a", "pick": "openai/x", "n": c.MIN_SAMPLE - 1},
                                  {"used": "openai/b", "pick": "openai/y", "n": c.MIN_SAMPLE},
                                  {"used": "openai/b", "pick": "openai/x", "n": 3}]
    chart = _chart(report)
    assert chart.count("Small sample:") == 2  # a (14) and c (no moves: 0 calls); b has 18
    assert f"Small sample: {c.MIN_SAMPLE - 1} calls." in chart and "Small sample: 0 calls." in chart


def test_pick_ignores_profiles_other_than_cost_and_balanced_thresholds():
    assert set(c.PROFILES) == {"balanced", "cost"} and set(c.THRESHOLDS) == set(c.PROFILES)


def test_flow_tiny_share_reads_below_one_percent():
    head = {"model": "openai/s", "pool": ["openai/a", "openai/b"], "switch": None, "enable": []}
    moves = [{"used": "openai/s", "pick": "openai/a", "tier": "hard", "n": 999},
             {"used": "openai/s", "pick": "openai/b", "tier": "easy", "n": 1}]
    f = c._flow("openai/s", head, moves)
    out = c._flow_html("openai/s", f.lever, f.targets, ["x"])
    assert "&lt;1% \u00b7" in out and not re.search(r"[^0-9]0% \u00b7", out)


def test_plan_rows_follow_change_model_when_it_is_the_recommended_lever():
    report = _plan_report()
    head, enabled = report["scenarios"]
    head["switch"] = {"per_month": 90.0, "per_stratum": {"openai/a": 10.0, "openai/b": 45.0, "openai/c": 35.0}}
    enabled["switch"] = {"per_month": 25.0, "per_stratum": {"openai/a": 10.0, "openai/b": 15.0}}
    rows = {r.model: r for r in c._rec_rows(report, 1.0, "switch", {})}
    assert sum(r.saved_per_month for r in rows.values()) == pytest.approx(90)  # the change-model figure, not the router's
    assert sum(r.saved_today_per_month for r in rows.values()) == pytest.approx(25)
    b = rows["openai/b"]
    assert b.action == "switch" and [x.model for x in b.targets] == ["openai/y"]  # no fork
    assert b.enable == ["openai/y"] and b.saved_today_per_month == pytest.approx(15)
    assert "Recommended plan (change model)" in c._plan_caption("switch", 0.9, 90.0)
    assert "Recommended plan (Smart Router)" in c._plan_caption("router", 1.0, 100.0)


def test_levers_within_margin_of_error_when_ranges_overlap():
    head = {"router": {"balanced": {"ci90_per_month": [153.0, 165.0]}}, "switch": {"ci90_per_month": [151.0, 156.0]}}
    assert c._ranges_overlap(head)
    head["switch"]["ci90_per_month"] = [120.0, 140.0]
    assert not c._ranges_overlap(head)
    assert not c._ranges_overlap({"router": {"balanced": {}}, "switch": {}})  # no ranges: no claim


def test_uncertainty_range_plot_shares_one_scale():
    sc = lambda lo, est, hi: {"per_month": est, "ci90_per_month": [lo, hi]}
    report = {"scenarios": [{"scope": "same_provider:all", "headline": True,
                             "router": {"balanced": sc(150, 158, 165)}, "switch": sc(151, 153, 156)},
                            {"scope": "any_provider:all", "headline": False,
                             "router": {"balanced": sc(190, 200, 220)}, "switch": {"per_month": 10.0}}]}
    rows = c._uncertainty_rows(c._scopes(report, 400.0))
    assert len({r["range"][4:] for r in rows if r["range"]}) == 1  # every bar on one shared axis
    assert rows[0]["range"][4] < 150 and rows[0]["range"][5] > 220
    assert rows[-1]["range"] is None and c._range_cell(None) == ""  # no CI: no bar, no claim
    svg = c._range_cell(rows[0]["range"])
    assert "class='rg lv-r'" in svg and "estimate $158" in svg and "<script" not in svg
    assert "class='rg lv-s'" in c._range_cell(rows[1]["range"])
    html_table = c._table(rows, [("Range", "range", c._range_cell), ("Scope", "scope", str)])
    assert "<svg class='rg lv-r'" in html_table  # markup kept, not escaped


SETUP_REPORT = {"workspace": "acme", "scenarios": [{"headline": True, "models": [
    {"model": "openai/gpt-6-sol", "pool": ["openai/gpt-6.1-terra", "openai/gpt-6-luna"],
     "switch": "openai/gpt-6.1-terra", "enable": ["openai/gpt-6.1-terra"]},
    {"model": "anthropic/claude-sonnet-5", "pool": None, "switch": "anthropic/claude-haiku-5-5",
     "enable": ["anthropic/claude-haiku-5-5"]},
    {"model": "openai/gpt-6-luna", "pool": None, "switch": None, "enable": []}]}]}


def test_setup_router_commands_enable_then_create_one_router_per_model():
    plan = c._setup_plan(SETUP_REPORT, "router")
    # sonnet has no pool: the router figure serves it on its switch target, so the router plan changes it too
    assert plan["enable"] == ["openai/gpt-6.1-terra", "anthropic/claude-haiku-5-5"]
    assert [m["key"] for m in plan["moves"]] == ["savings-openai-gpt-6-sol"]
    assert plan["replacements"] == [{"model": "anthropic/claude-sonnet-5", "to": "anthropic/claude-haiku-5-5"}]
    cmds = c._setup_commands(plan).splitlines()
    assert "enable openai/gpt-6.1-terra" in cmds and "enable anthropic/claude-haiku-5-5" in cmds
    assert cmds[-2] == "router savings-openai-gpt-6-sol openai/gpt-6.1-terra openai/gpt-6-luna"
    assert cmds[-1] == ("echo 'then change anthropic/claude-sonnet-5 to anthropic/claude-haiku-5-5 in your code "
                        "and deployments'")
    prompt = c._setup_prompt(plan)
    assert "metadata.autorouter.v2" in prompt and "anthropic/claude-sonnet-5 -> anthropic/claude-haiku-5-5" in prompt
    assert prompt.index("smart-routers create") < prompt.index("Then change") < prompt.index("compare the answers")
    assert "its router, or its replacement" in prompt


def test_router_plan_without_pools_is_only_model_changes():
    rep = {"scenarios": [{"headline": True, "models": [SETUP_REPORT["scenarios"][0]["models"][1]]}]}
    plan = c._setup_plan(rep, "router")
    assert plan["moves"] == [] and len(plan["replacements"]) == 1
    prompt = c._setup_prompt(plan)
    assert "smart-routers create" not in prompt and "2. Change these models" in prompt
    card = c._setup_card({"router": plan, "switch": c._setup_plan(rep, "switch")}, "r", "acme")
    router = card.split("<h3>Smart Router")[1].split("<h3>")[0]
    assert "Enable 1 model, then change claude-sonnet-5." in router and "claude-sonnet-5 → claude-haiku-5-5" in router


def test_router_setup_card_says_create_routers_then_change_models():
    plans = {k: c._setup_plan(SETUP_REPORT, k) for k in ("router", "switch")}
    card = c._setup_card(plans, "r", "acme")
    router = card.split("<h3>Smart Router")[1].split("<h3>")[0]
    assert "Enable 2 models, then create 1 router and point gpt-6-sol at them, then change claude-sonnet-5." in router
    assert "<li>claude-sonnet-5 → claude-haiku-5-5</li>" in router


FAKE_ORQ = r"""#!/bin/sh
echo "$*" >> "$ORQ_LOG"
case "$*" in
  "models list"*autorouter*) case "$*" in *unscored*) echo null;; *) echo id;; esac;;
  "models list"*"&& enabled"*) case "$*" in *disabled*) echo null;; *) echo id;; esac;;
  "models list"*) case "$*" in *missing*) echo null;; *) echo id;; esac;;
  "models enable"*) echo null;;
  "smart-routers list"*) case "$*" in *k-exists*) echo ws@orq/k-exists;; *) echo null;; esac;;
  "smart-routers create"*) echo ws@orq/k-new;;
esac
"""


# -f / --norc: a user's zsh or bash startup file can put the real orq back first on PATH
@pytest.mark.parametrize("shell", [s for s in (["bash", "--norc"], ["zsh", "-f"]) if shutil.which(s[0])],
                         ids=lambda s: s[0])
def test_setup_commands_rerun_safely(tmp_path, shell):
    # the user pastes these; a rerun must not hit "key already exists", and enable must not print "null"
    (tmp_path / "orq").write_text(FAKE_ORQ)
    (tmp_path / "orq").chmod(0o755)
    plan = {"lever": "router", "enable": ["p/a", "p/missing"], "moves": [
        {"key": "k-exists", "pool": ["p/a", "p/b"], "model": "p/x"},
        {"key": "k-unscored", "pool": ["p/a", "p/unscored"], "model": "p/y"},
        {"key": "k-disabled", "pool": ["p/a", "p/disabled"], "model": "p/w"},
        {"key": "k-new", "pool": ["p/a", "p/b"], "model": "p/z"}]}
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "ORQ_LOG": str(tmp_path / "log")}
    r = subprocess.run([*shell, "-c", c._setup_commands(plan)], env=env, capture_output=True, text=True)
    assert r.stdout.splitlines() == ["enabled p/a", "k-exists exists: use ws@orq/k-exists",
                                     "k-new created: use ws@orq/k-new"]
    assert r.stderr.splitlines() == ["skip p/missing: not in this workspace's model list",
                                     "skip k-unscored: p/unscored has no Smart Router score yet (or the lookup failed)",
                                     "skip k-disabled: p/disabled is not enabled (or the lookup failed)"]
    creates = [x for x in (tmp_path / "log").read_text().splitlines() if x.startswith("smart-routers create")]
    assert creates == ["smart-routers create --key k-new --models p/a --models p/b "
                       "--body-profile SMART_ROUTER_PROFILE_BALANCED -o json -j model_ref --raw"]


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
def test_setup_commands_stop_on_failed_orq_and_carry_the_workspace(tmp_path):
    (tmp_path / "orq").write_text('#!/bin/sh\necho "$*" >> "$ORQ_LOG"\necho "403" >&2\nexit 1\n')
    (tmp_path / "orq").chmod(0o755)
    plan = {"lever": "router", "enable": ["p/a"], "workspace": "acme", "moves": [
        {"key": "k-new", "pool": ["p/a", "p/b"], "model": "p/z"}]}
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "ORQ_LOG": str(tmp_path / "log")}
    r = subprocess.run(["bash", "--norc", "-c", c._setup_commands(plan)], env=env, capture_output=True, text=True)
    assert r.stdout == ""  # a failed lookup is never "exists" nor "created"
    assert "skip p/a: could not list models" in r.stderr and "skip k-new: could not list smart routers" in r.stderr
    log = (tmp_path / "log").read_text().splitlines()
    assert log and all(x.endswith("--workspace acme") for x in log)
    assert not [x for x in log if "create" in x or "enable" in x]
    assert "workspace acme" in c._setup_prompt(plan)


def test_setup_prompt_tests_quality_before_changing_deployments():
    out = c._setup_prompt(c._setup_plan(SETUP_REPORT, "switch"))
    assert out.index("compare the answers") < out.index("Show me the diff") < out.index("deployments and agents")
    assert "only after I confirm" in out


def test_setup_change_model_lists_replacements_and_only_their_enables():
    plan = c._setup_plan(SETUP_REPORT, "switch")
    assert plan["enable"] == ["openai/gpt-6.1-terra", "anthropic/claude-haiku-5-5"]
    assert {m["model"]: m["to"] for m in plan["moves"]} == {
        "openai/gpt-6-sol": "openai/gpt-6.1-terra", "anthropic/claude-sonnet-5": "anthropic/claude-haiku-5-5"}
    assert "smart-routers" not in c._setup_commands(plan) and "->" not in c._setup_commands(plan)
    assert "scored" not in c._setup_commands(plan)  # a model switch needs no Smart Router score
    prompt = c._setup_prompt(plan)
    assert "anthropic/claude-sonnet-5 -> anthropic/claude-haiku-5-5" in prompt and "gpt-6-luna ->" not in prompt


def test_setup_card_puts_recommended_lever_first_with_collapsed_copyable_blocks():
    card = c._setup_card({k: c._setup_plan(SETUP_REPORT, k) for k in ("router", "switch")}, "s", "acme")
    assert card.index("<h3>Change model") < card.index("<h3>Smart Router")
    assert card.count("<details class=cp>") == 4 and card.count("class=copy") == 4
    assert "Copy commands" in card and "<summary>Show prompt</summary>" in card and "gpt-6-sol \u2192 gpt-6.1-terra</li>" in card
    assert "<button" not in card.split("<summary>")[1].split("</summary>")[0]  # no button inside a summary
    assert "https://my.orq.ai/acme/router/smart-router?id=new" in card
    assert "https://my.orq.ai/acme/router/models?q=gpt-6.1-terra" in card
    assert "&lt;" not in c._setup_prompt(c._setup_plan(SETUP_REPORT, "router")).replace("<id>", "")
    assert c._setup_card({k: c._setup_plan({"scenarios": []}, k) for k in ("router", "switch")}, "r", "x") == ""


def test_plan_bars_explain_each_segment_on_hover():
    tip = c._tip("Possible now with Smart Router", 51.0, 95.0)
    assert tip == "Possible now with Smart Router: $51/mo \u00b7 54% of this model's spend"
    assert c._tip("Still spent", 20.0, 400.0, "your total spend").endswith("5% of your total spend")


def test_range_plot_draws_gridlines_at_round_values():
    svg = c._range_cell((150, 158, 165, "Smart Router", 80.0, 222.0))
    assert svg.count("class=gl") == 3  # $80..$222 in steps of $50: lines at $100, $150, $200


def test_waterfall_steps_add_up_and_drop_empty_enable_step():
    import re
    w = c._waterfall(400.0, 91.0, 67.0, 242.0, "Smart Router")
    assert w.count("class=wc") == 4 and w.count("class=cn") == 3  # a connector between each pair
    tops = [float(x) for x in re.findall(r"top:([\d.]+)%;height", w)]
    hs = [float(x) for x in re.findall(r"height:([\d.]+)%", w)]
    assert tops == [0.0, 0.0, 22.75, 39.5] and abs(hs[1] + hs[2] + hs[3] - 100) < 0.01  # steps sum to the bill
    assert "−$91" in w and "<span>New bill</span><span>60%</span>" in w
    assert "<span>Possible now</span>" in w and "<span>After enabling</span>" in w and "Saved today" not in w
    w2 = c._waterfall(400.0, 158.0, 0.0, 242.0, "Change model")
    assert w2.count("class=wc") == 3 and "After enabling" not in w2


def test_router_key_keeps_the_provider_so_two_providers_do_not_collide():
    assert c._router_key("openai/gpt-4o") != c._router_key("azure/gpt-4o")


def test_range_axis_is_not_floored_at_zero_and_negatives_print_with_a_sign():
    assert c._usd0(-40) == "-$40" and c._usd0(65) == "$65"
    lever = {"coverage": 1.0, "enable": []}
    scopes = [c.SummaryScope.model_validate({
        "scope": "same_all", "headline": True, "models": [],
        "router": {"per_month": 5, "pct_of_spend": 0.1, "ci90_per_month": [-40, 60], **lever},
        "switch": {"per_month": 3, "pct_of_spend": 0.1, "ci90_per_month": [1, 10], **lever}})]
    rows = c._uncertainty_rows(scopes)
    assert rows[0]["range"][4] < -40


def test_switch_plan_skips_a_model_that_was_not_analysed():
    rep = {"workspace": "w", "routing": {"strata": [{"model": "a", "savings": None}, {"model": "b", "savings": 5}]},
           "scenarios": [{"headline": True, "models": [
               {"model": "a", "pool": None, "switch": "x", "enable": ["openai/x"]},
               {"model": "b", "pool": None, "switch": "y", "enable": ["openai/y"]}],
               "switch": {"per_stratum": {"b": 3}}}]}
    plan = c._setup_plan(rep, "switch")
    assert [m["model"] for m in plan["moves"]] == ["b"]


def test_render_needs_caching_slot_only_when_the_card_is_shown():
    rep = {"summary": {"caching": {"savings_found": 0.2, "error": None}}}
    n = {"summary": "S.", "routing": "R.", "caching": "", "caveats": "C."}
    try:  # past the slot check: the caching slot was not demanded (the stub report fails later, elsewhere)
        c.render(rep, n, redact=False)
    except ValidationError:
        pass
    rep["summary"]["caching"]["savings_found"] = 50
    with pytest.raises(SystemExit, match="caching"):
        c.render(rep, n, redact=False)


def test_summary_today_split_equals_row_sums():
    rep = _plan_report()
    rep["window"] = {"days": 30}
    rep["samples"] = []
    for sc in rep["scenarios"]:
        sc["switch"] = {"per_month": 50.0}
    S = c.summary(rep)
    rows = S["recommendations"]["router"]
    assert S["router"]["saved_today_per_month"] == pytest.approx(
        min(S["router"]["per_month"], sum(r["saved_today_per_month"] for r in rows)))


def test_now_and_after_enabling_labels_replace_saved_today():
    report = _plan_report()
    report["routing"]["strata"][1].update(spend=100.0)
    chart = _chart(report)
    assert "<span class='sw sr'></span>Possible now</li>" in chart and "<span class='sw sr2'></span>After enabling</li>" in chart
    assert "Saved today" not in chart and "saved today" not in chart and "Today:" not in chart
    assert "Possible now with Smart Router: $20/mo" in chart and "20% now \u00b7 $20" in chart


def test_a_model_the_lever_does_not_cover_is_not_analysed_not_kept():
    report = _plan_report()
    report["routing"]["strata"][3]["savings"] = None  # d: pricing check failed, no figure
    recs = {x.model: x for x in c._rec_rows(report, 1.0, "router", {})}
    assert recs["openai/d"].action == "not_analysed" and recs["openai/a"].action == "switch"
    chart = _chart(report)
    assert "d: not analysed, its sampled calls could not be priced reliably." in chart and "d: keep" not in chart
    report["scenarios"][0]["switch"] = {"per_month": 50.0, "per_stratum": {"openai/a": 10.0, "openai/b": 0.0}}
    sw = {x.model: x.action for x in c._rec_rows(report, 1.0, "switch", {})}
    assert sw == {"openai/a": "switch", "openai/b": "keep", "openai/c": "not_analysed", "openai/d": "not_analysed"}


def test_scopes_keep_enable_and_coverage_per_lever():
    report = _plan_report()
    report["scenarios"][0]["models"][1] = {"model": "openai/b", "pool": ["openai/y", "openai/w"], "switch": "openai/y",
                                           "enable": ["openai/y", "openai/w"]}
    for sc in report["scenarios"]:
        sc["router"]["balanced"]["coverage"], sc["switch"] = 0.9, {"per_month": 20.0, "coverage": 0.9}
    head = c._scopes(report, 100.0)[0]
    assert "enable" not in head.model_dump() and "coverage" not in head.model_dump()
    assert head.router.enable == ["openai/y", "openai/w", "openai/z"]  # pool members and switch targets
    assert head.switch.enable == ["openai/y", "openai/z"]  # switch targets only
    assert head.router.coverage == head.switch.coverage == 0.9
    grid = c._scope_grid(c._scopes(report, 100.0))
    assert "title='openai/y, openai/w, openai/z'>enable 3 models" in grid and "title='openai/y, openai/z'>enable 2 models" in grid
    assert "of spend covered" not in grid  # one coverage everywhere: no note
    report["scenarios"][0]["switch"]["coverage"] = 0.6
    cell = c._scope_grid(c._scopes(report, 100.0)).split("class='cell hl'")[1].split("<p class=rh>")[0]
    router, switch = cell.split("<p class=f2>")
    assert "90% of spend covered" in router and "60% of spend covered" in switch
