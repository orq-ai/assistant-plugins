import importlib.util
import json
import random
import sys
from pathlib import Path

import pytest

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
    assert c.pick(pool, "easy", "quality").id == "m3"


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
    assert est["savings"] == pytest.approx(20.0)
    lo, hi = est["ci90"]
    assert lo <= est["savings"] <= hi


def test_without_top3_drops_largest_contributions():
    ds = [c.Draw("s", 1, 1.0, {"balanced": x}) for x in (10, 9, 8, 0.1, 0.1)]
    full = c.estimate({"s": ds}, {"s": 5.0}, "balanced", random.Random(0), boots=10)["savings"]
    assert c.without_top({"s": ds}, {"s": 5.0}, "balanced") < full


# ---- caching


def turn(t, prompt, cached=0, written=0):
    return {"t": t, "prompt": prompt, "cached": cached, "written": written}


PRICES = {"input": 1.0, "cache_read": 0.1, "cache_write_5m": 1.25}


def test_caching_read_and_write_per_turn():
    turns = [turn(0, 2000), turn(10, 3000), turn(20, 4000)]
    r = c.caching_savings(turns, PRICES, 300)
    # reads: 2000 + 3000 at (1 - 0.1); writes: 1000 + 1000 at (1.25 - 1)
    assert r["savings"] == pytest.approx(5000 * 0.9 - 2000 * 0.25)


def test_caching_ttl_expiry_rewrites_whole_prompt():
    turns = [turn(0, 2000), turn(1000, 3000)]
    r = c.caching_savings(turns, PRICES, 300)
    assert r["savings"] == pytest.approx(-3000 * 0.25)


def test_caching_nets_out_existing_reads_and_writes():
    turns = [turn(0, 2000), turn(10, 3000, cached=2000, written=1000)]
    assert c.caching_savings(turns, PRICES, 300)["savings"] == pytest.approx(0.0)


def test_caching_candidate_rules():
    good = [turn(0, 2000), turn(1, 3000), turn(2, 4000)]
    assert c.caching_candidate(good, PRICES) == (True, "")
    assert not c.caching_candidate(good[:2], PRICES)[0]
    assert not c.caching_candidate(good, {"input": 1.0})[0]
    assert not c.caching_candidate([turn(0, 500), turn(1, 3000), turn(2, 4000)], PRICES)[0]
    cached = [turn(0, 2000), turn(1, 3000, cached=2000), turn(2, 4000, cached=3000)]
    assert not c.caching_candidate(cached, PRICES)[0]


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
    assert c._scrub("see /Users/someone/repo/a.py and /home/x/y") == "see <path> and <path>"
