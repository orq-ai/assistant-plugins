"""End to end: analyze and render against a fake `orq` CLI (the one external boundary), synthetic data only."""

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("cost_savings_report_e2e", ROOT / "scripts" / "cost_savings_report.py")
c = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = c
SPEC.loader.exec_module(c)

FIX = json.loads((ROOT / "tests" / "fixtures" / "models.json").read_text())
SCHEMA = json.loads((ROOT / "resources" / "report.schema.json").read_text())
SOL, SOL61, TERRA, LUNA, ACME = ("openai/gpt-6-sol", "openai/gpt-6.1-sol", "openai/gpt-6-terra", "openai/gpt-6-luna",
                                  "acme/acme-flash")
EASY_ASK, HARD_ASK = "say hello", "design a sharded database with cross-region failover"


def usage(prompt, cached=0, completion=100):
    return {"prompt_tokens": prompt, "prompt_cached_tokens": cached, "prompt_cache_creation_tokens": 0,
            "completion_tokens": completion}


def sol_cost(u):
    return (u["prompt_tokens"] - u["prompt_cached_tokens"]) * 3e-6 + u["prompt_cached_tokens"] * 0.3e-6 + \
        u["completion_tokens"] * 15e-6


LEAVES = {
    "s1": {"at": "2026-11-10T10:00:01Z", "ask": EASY_ASK, "usage": usage(2000)},
    "s2": {"at": "2026-11-10T10:00:05Z", "ask": HARD_ASK, "usage": usage(2000, cached=1500)},
}


class FakeOrq:
    def __init__(self, retention=None):
        self.decisions, self.retention = 0, retention

    def __call__(self, *args, body=None, cache=True):
        a = list(args)
        if a[:2] == ["telemetry", "query"]:
            return self.telemetry(a)
        if a[:2] == ["workspaces", "get"]:
            if self.retention is None:
                raise c.OrqError(a, 1, "HTTP 403 not authorized")
            return {"workspace": {"key": a[2], "features": {"retention_period": self.retention}}}
        if a[:2] == ["model-catalog", "list"]:
            return {"data": FIX["catalog"]}
        if a[:2] == ["models", "list"]:
            return FIX["cards"]
        if a[:2] == ["traces", "search"]:
            flt = json.loads(a[a.index("--filters") + 1])[0]
            if flt["field"] == "thread_id":
                return {"data": [{"trace_id": "tA", "agent": {"key": "support"},
                                  "attributes": {"orq": {"product": "agents"}}}]}
            return {"data": [{"trace_id": "tA"}]}
        if a[:2] == ["traces", "list-spans"]:
            leaves = [{"type": "span.responses", "has_children": False, "provider": "openai", "model": "gpt-6-sol",
                       "span_id": k, "started_at": v["at"], "usage": v["usage"]} for k, v in LEAVES.items()]
            return {"data": [{"type": "trace", "has_children": True, "span_id": "root"}, *leaves], "has_more": False}
        if a[:2] == ["traces", "get-span"]:
            leaf = LEAVES[a[3]]
            return {"span": {
                "summary": {"provider": "openai", "model": "gpt-6-sol", "started_at": leaf["at"], "operation": "chat",
                            "usage": leaf["usage"], "cost": {"total": sol_cost(leaf["usage"])}},
                "attributes": {"gen_ai": {"input": {"messages": [{"role": "user", "content": leaf["ask"]}]}}}}}
        if a[:2] == ["decisions", "create"]:
            self.decisions += 1
            hard = body["state"] == HARD_ASK
            return {"answers": {"difficulty": {"probabilities": {"easy": 0.0 if hard else 1.0, "medium": 0.0,
                                                                  "hard": 1.0 if hard else 0.0}}}}
        raise AssertionError(f"unexpected call {a}")

    @staticmethod
    def telemetry(a):
        compute = json.loads(a[a.index("--compute") + 1])[0]
        groups = [a[i + 1] for i, x in enumerate(a) if x == "--group-by"]
        metric = compute["metric"]
        if not groups:
            return {"data": []}
        if groups == ["model"]:
            return {"data": [{"group": {"model": SOL}, "metrics": {"genai.cost:sum": 10.0}},
                             {"group": {"model": "openai/text-embedding-4"}, "metrics": {"genai.cost:sum": 1.0}}]}
        if groups == ["project"]:
            return {"data": [{"group": {"project": "p1"}, "timestamp": f"2026-11-10T{h:02d}:00:00Z",
                              "metrics": {"genai.requests:count": 4}} for h in (9, 10, 11)]}
        if groups == ["thread", "project"]:
            key = "genai.cost:sum" if metric == "genai.cost" else "genai.requests:count"
            return {"data": [{"group": {"thread": "th1", "project": "p1"}, "metrics": {key: 4 if "count" in key
                                                                                       else 2.0}}]}
        if groups == ["trace_framework"]:
            return {"data": [{"group": {"trace_framework": "openai-sdk"}, "metrics": {"orq.gen_ai.cost:sum": 9.0}}]}
        return {"data": []}


def ns(tmp_path, **kw):
    d = {"workspace": "demo", "out": str(tmp_path), "sample": 10, "counterfactual_test": False, "cache_threads": 5,
         "seed": 7, "redact": False, "yes": True, "window": 30, "json": False}
    d.update(kw)
    return argparse.Namespace(**d)


def run(tmp_path, **kw):
    an = c.Analyzer(ns(tmp_path, **kw))
    an.orq = FakeOrq()
    return an, an.run()


def test_analyze_end_to_end(tmp_path):
    an, rep = run(tmp_path)
    assert rep["status"] == "ok", rep["stop"]
    jsonschema.validate(json.loads((tmp_path / "report.json").read_text()), SCHEMA)

    assert {r["model"] for r in rep["samples"]} == {SOL}  # embedding model never sampled
    by_span = {r["span_id"]: r for r in rep["samples"]}
    easy, hard = by_span["s1"], by_span["s2"]
    s1, s2 = sol_cost(LEAVES["s1"]["usage"]), sol_cost(LEAVES["s2"]["usage"])
    # headline scope (same provider, all available): the pool tops out at disabled gpt-6.1-sol, which beats sol
    assert easy["picks"]["balanced"] == LUNA
    assert easy["savings"]["balanced"] == pytest.approx(s1 - (2000 * 0.1e-6 + 100 * 0.5e-6))
    # the hard turn moves to 6.1-sol with the same uncached, cached and output tokens: no cold-cache penalty
    warm = s2 - (500 * 2e-6 + 1500 * 0.2e-6 + 100 * 10e-6)
    assert hard["picks"]["balanced"] == SOL61
    assert hard["savings"]["balanced"] == pytest.approx(warm)
    # enabled only: hard stays on sol, nothing saved
    assert hard["scenario_savings"]["router|same_provider:enabled|balanced"] == 0.0
    assert hard["scenario_savings"]["switch|same_provider:all"] == pytest.approx(warm)
    assert hard["scenario_savings"]["switch|same_provider:enabled"] == 0.0
    assert all(r["weight"] == 2.0 for r in rep["samples"])

    head = rep["routing"]["headline"]
    assert head["coverage"] == pytest.approx(10 / 11)
    lo, hi = head["ci90"]
    assert lo <= head["savings"] <= hi
    assert {s["model"]: s["covered"] for s in rep["routing"]["strata"]} == {SOL: True}
    assert any(n["model"] == "openai/text-embedding-4" for n in rep["routing"]["not_covered"])
    assert rep["harness"]["unattributed"] == pytest.approx(2.0)
    assert rep["harness"]["rows"][0]["share"] == pytest.approx(1.0)
    assert rep["caching"]["threads_scanned"] == 1
    assert an.orq.decisions == 2  # identical asks scored once

    scen = {s["scope"]: s for s in rep["scenarios"]}
    sol = {sc: next(m for m in s["models"] if m["model"] == SOL) for sc, s in scen.items()}
    assert sol["same_provider:all"] | {"reason": ""} == {"model": SOL, "pool": [SOL61, TERRA, LUNA], "switch": SOL61,
                                                       "enable": [SOL61], "relaxed": False, "reason": ""}
    assert (sol["same_provider:enabled"]["pool"], sol["same_provider:enabled"]["switch"]) == ([SOL, TERRA, LUNA], None)
    assert sol["any_provider:all"]["pool"] == [SOL61, TERRA, ACME]  # acme-flash beats luna on index and price
    assert sol["any_provider:enabled"]["pool"] == [SOL, TERRA, ACME]
    garden = an.garden
    for s in sol.values():
        assert not c.has_dominated([garden[x] for x in s["pool"]])
    assert scen["same_provider:all"]["headline"] and rep["routing"]["headline"]["savings"] == pytest.approx(
        scen["same_provider:all"]["router"]["balanced"]["savings"])
    for sc in scen.values():  # per-model savings stay in every scenario; they add up to the scenario total
        assert sum(v or 0 for v in sc["router"]["balanced"]["per_stratum"].values()) == pytest.approx(
            sc["router"]["balanced"]["savings"])
        assert "per_stratum" in sc["switch"]
    assert sum(m["weight"] for m in rep["routing"]["moves"]) == pytest.approx(sum(x["weight"] for x in rep["samples"]))
    dom = {d["model"]: d["dominated_by"] for d in rep["routing"]["pareto"]}
    assert dom[SOL][0] == {"model": SOL61, "index": 50.0, "blend_price": 4.0, "enabled": False}
    assert (tmp_path / "narrative-slots.md").read_text().count("## ") == 4


def test_router_without_pool_serves_the_switch_target(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "frontier_pool", lambda top, cands, cost=None: ([top], True))
    _, rep = run(tmp_path)
    for r in rep["samples"]:
        assert r["pool"] is None
        for sc in c.SCOPES:
            assert r["scenario_savings"][f"router|{sc}|balanced"] == r["scenario_savings"][f"switch|{sc}"]
    assert rep["samples"][0]["scenario_savings"]["switch|same_provider:all"] > 0
    # the router figure includes that model change, so following the router plan must make it too
    s = rep["summary"]
    assert s["router"]["per_month"] > 0 and s["router"]["routers"] == []
    assert s["router"]["replacements"] == [{"model": SOL, "to": SOL61}] and SOL61 in s["router"]["enable"]
    jsonschema.validate(rep, SCHEMA)


def test_render_merges_narrative(tmp_path):
    run(tmp_path)
    narrative = tmp_path / "narrative.md"
    narrative.write_text("## summary\n\nRouting would save money.\n\n## routing\n\n- easy asks move to luna\n\n"
                         "## caching\n\nNone found.\n\n## caveats\n\nSmall sample.\n")
    assert c.main(["render", "--out", str(tmp_path), "--narrative", str(narrative)]) == 0
    page = (tmp_path / "report.html").read_text()
    assert "Routing would save money." in page and "<li>easy asks move to luna</li>" in page
    assert "class='cell hl'><p class=tag>This report's figure</p>" in page  # the grid highlights the headline cell
    assert "tokenize differently" in page and "approximate" in page
    hero = page.split("<section class=hero")[1].split("<h2 id=do>")[0]
    assert "90% CI" not in hero and "saved, $" in hero and "%</p>" in hero.replace("<small>", "</p>")  # percent first
    assert "90% CI" not in page.split("<h2 id=scopes>")[1].split("<h2 id=details>")[0]
    appendix = page.split("<summary>Estimates with uncertainty")[1]
    assert "90% CI ($/mo)" in appendix and "% saved" in appendix and "sampling noise only" in appendix
    assert ("Router saves" in page or "Router adds nothing" in page) and "Up to $" not in page
    for gone in ("Dominated", "Routing profiles", "Spend by model", "Scenario figures as a table", "class=plot"):
        assert gone not in page
    assert "<dt>Smart Router</dt>" in page and "Method and caveats" in page  # Terms live in Method
    assert "class=ex" not in page   # sampled ask text never reaches the page
    assert "or, pick one" not in page and "<h2 id=do>What to do</h2>" in page and "class=plan" not in page
    assert "Models with many hard asks" not in page and "jev-general" not in page
    # an outline linking to every section, all sections on the page in order; sun/moon theme toggle
    toc = page.split("<nav class=toc")[1].split("</nav>")[0]
    assert [k for k in ("do", "scopes", "details") if f"href='#{k}'" in toc] == ["do", "scopes", "details"]
    assert page.index("<h2 id=do>") < page.index("<h2 id=scopes>") < page.index("<h2 id=details>")
    assert "tablist" not in page and "pane-" not in page
    assert "<button type=button class=theme" in page and "class=sun" in page and "class=moon" in page
    assert "report-theme" in page


def test_counterfactual_test_stops_after_pilot(tmp_path):
    _, rep = run(tmp_path, counterfactual_test=True)
    assert rep["status"] == "ok" and "routing" not in rep
    cf = rep["counterfactual_test"]
    assert cf["per_model"][SOL]["n"] == len(rep["samples"])
    assert cf["per_model"][SOL]["cheaper"] == sum(1 for x in cf["asks"] if x["differs"])
    jsonschema.validate(rep, SCHEMA)


def test_without_yes_and_no_tty_stops_before_paid_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    an, rep = run(tmp_path, yes=False)
    assert rep["status"] == "stopped" and rep["stop"]["check"] == "confirmation"
    assert an.orq.decisions == 0
    jsonschema.validate(rep, SCHEMA)


def test_rerun_after_confirmation_reuses_window(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    _, first = run(tmp_path, yes=False)
    _, second = run(tmp_path, yes=True)
    assert second["status"] == "ok" and second["window"] == first["window"]


def test_window_flag_sets_the_period_and_a_new_period_is_not_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    _, first = run(tmp_path, yes=False)
    assert first["window"]["days"] == first["window"]["requested"] == 30
    _, second = run(tmp_path, yes=False, window=14)
    assert second["window"]["days"] == second["window"]["requested"] == 14


def test_window_is_capped_at_the_workspace_retention(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    an = c.Analyzer(ns(tmp_path, window=90))
    an.orq = FakeOrq(retention=30)
    an.set_window()
    assert an.report["window"]["days"] == 30 and an.report["window"]["retention"] == 30
    assert an.report["window"]["requested"] == 90


def test_disabled_decision_model_stops(tmp_path, monkeypatch):
    cards = [dict(x, enabled=False) if x["model_id"] == "jev-latest" else x for x in FIX["cards"]]
    monkeypatch.setitem(FIX, "cards", cards)
    _, rep = run(tmp_path)
    assert rep["stop"]["check"] == "decision model enabled"


def test_harness_above_spend_gives_shares_and_caveat(tmp_path, monkeypatch):
    real = FakeOrq.telemetry

    def high(a):
        out = real(a)
        if "trace_framework" in a:
            out["data"][0]["metrics"]["orq.gen_ai.cost:sum"] = 50.0
        return out

    monkeypatch.setattr(FakeOrq, "telemetry", staticmethod(high))
    _, rep = run(tmp_path)
    assert rep["harness"]["unattributed"] is None
    assert any(x["check"] == "harness metric exceeds spend" for x in rep["checks"]["caveats"])


def test_render_refuses_stopped_report(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    run(tmp_path, yes=False)
    (tmp_path / "n.md").write_text("## summary\n\nx\n")
    with pytest.raises(SystemExit, match="no finished analysis"):
        c.main(["render", "--out", str(tmp_path), "--narrative", str(tmp_path / "n.md")])


def test_sample_cap():
    with pytest.raises(SystemExit):
        c.main(["analyze", "--out", "/tmp/x", "--sample", "101"])


def test_unscored_turn_goes_to_the_top_seat(tmp_path, monkeypatch):
    monkeypatch.setattr(c.Analyzer, "decide", lambda self, text: {"ok": False, "error": "decision_timeout"})
    _, rep = run(tmp_path)
    for r in rep["samples"]:
        assert r["fallback"] == "decision_timeout" and r["picks"]["balanced"] == SOL61
        assert r["savings"]["balanced"] == pytest.approx(r["scenario_savings"]["switch|same_provider:all"])


def test_discounted_same_host_offering_is_skipped(tmp_path):
    an = c.Analyzer(ns(tmp_path))

    def entry(i, of, inp, host="meta"):
        return {"id": i, "offering_of": of, "provider": {"id": host},
                "pricing": {"input": {"cost": inp, "per": 1_000_000}, "output": {"cost": 1, "per": 1_000_000}}}
    an.cat = {e["id"]: e for e in [
        entry("meta/spark", "meta/spark", 1.25), entry("meta/spark-contributor", "meta/spark", 0.1),
        entry("meta/spark-2026-09-01", "meta/spark", 1.25), entry("aws/spark", "meta/spark", 0.5, "aws")]}
    assert an.discounted_offering("meta/spark-contributor")
    assert not an.discounted_offering("meta/spark-2026-09-01")  # dated snapshot, same price
    assert not an.discounted_offering("aws/spark")  # another host's offering
    assert not an.discounted_offering("meta/spark")


def _analyzer_with(tmp_path, extra):
    """Analyzer over the fixture models plus `extra`: (provider, model_id, enabled, index, price, input, output,
    effort card fields)."""
    an = c.Analyzer(ns(tmp_path))
    an.orq = FakeOrq()
    an.load_models()
    for prov, mid, idx, inp, out, meta in extra:
        key = f"{prov}/{mid}"
        v2 = {"intelligence_index": idx, "price": (3 * inp + out) / 4, **meta}
        an.cards[key] = {"provider": prov, "model_id": mid, "enabled": True, "input_cost": inp / 1000,
                         "output_cost": out / 1000, "metadata": {"autorouter": {"v2": v2}}}
        an.cat[key] = {"id": key, "offering_of": key, "provider": {"id": prov}, "modalities": {"output": ["text"]},
                       "pricing": {"input": {"cost": inp, "per": 1000000}, "output": {"cost": out, "per": 1000000}}}
    an.garden = c.mf.garden_from_cards(list(an.cards.values()))
    return an


def test_google_and_google_ai_are_one_provider_and_one_candidate(tmp_path):
    an = _analyzer_with(tmp_path, [("google", "gem-flash", 40.0, 0.1, 0.4, {}),
                                   ("google-ai", "gem-flash", 40.0, 0.2, 0.4, {}),
                                   ("google-ai", "gem-pro", 45.0, 1.0, 4.0, {})])
    assert an.host("google-ai/gem-pro") == an.host("google/gem-flash") == "google"
    assert an.in_scope("google/gem-flash", "google-ai/gem-pro", "same_provider:all")
    cands = [x.router_id for x in an.candidates("openai/gpt-6-sol", "any_provider:all")]
    assert [x for x in cands if x.endswith("/gem-flash")] == ["google/gem-flash"]  # the cheaper route, once


def test_models_without_a_default_effort_score_are_never_targets(tmp_path):
    effort = {"parameter": "reasoningEffort", "config": {"default": "high", "options": [
        {"value": "low"}, {"value": "high"}]}}
    an = _analyzer_with(tmp_path, [("zai", "glm-x", 40.0, 0.1, 0.4, {"default_effort_scored": False}),
                                   ("zai", "glm-y", 40.0, 0.1, 0.4, {})])
    an.cards["zai/glm-x"]["parameters"] = [effort]  # a reasoning-effort card with no score at its default
    an.garden = c.mf.garden_from_cards(list(an.cards.values()))
    assert an.garden["zai/glm-x"].ranked_on_headline and not an.garden["zai/glm-y"].ranked_on_headline
    ids = [x.router_id for x in an.candidates("openai/gpt-6-sol", "any_provider:all")]
    assert "zai/glm-x" not in ids and "zai/glm-y" in ids
    assert any("zai/glm-x" in x["detail"] for x in an.report["checks"]["caveats"])


def test_a_model_without_a_smart_router_score_is_never_a_target(tmp_path):
    # in the garden (e.g. scored by another source) but its card has no autorouter.v2: Smart Router would reject it
    an = _analyzer_with(tmp_path, [("openai", "gpt-x-unscored", 60.0, 0.1, 0.4, {}),
                                   ("openai", "gpt-x-scored", 60.0, 0.1, 0.4, {})])
    an.cards["openai/gpt-x-unscored"]["metadata"] = {}
    assert "openai/gpt-x-unscored" in an.garden and not an.poolable("openai/gpt-x-unscored")
    ids = [x.router_id for x in an.candidates("openai/gpt-6-sol", "any_provider:all")]
    assert "openai/gpt-x-unscored" not in ids and "openai/gpt-x-scored" in ids
    res = an.pool_for("openai/gpt-6-sol", "same_provider:all")
    assert "openai/gpt-x-unscored" not in [p.id for p in res["pool"] or []] + [res["switch"]] + res["enable"]


def test_report_has_no_quality_profile_and_a_minor_schema_version(tmp_path):
    _, rep = run(tmp_path)
    assert rep["schema_version"] == "1.2"
    assert set(rep["routing"]["profiles"]) == {"balanced", "cost"}
    assert all(set(s["router"]) == {"balanced", "cost"} for s in rep["scenarios"])


def test_underpowered_and_moves_count_fallback_samples(tmp_path):
    an, _ = run(tmp_path)
    an.samples = an.samples[:2]
    an.samples[0]["fallback"], an.samples[0]["tier"] = "prompt_limit", {}
    an.samples[1]["tier"] = {"balanced": "hard", "cost": "hard"}
    an.pools = {SOL: an.pools[SOL]}
    up = an.underpowered()[0]
    assert up["n"] == len(an.samples) == 2 and up["n_scored"] == 1  # the fallback counts in n, not in the shares
    assert sum(m["n"] for m in an.moves()) == 2 and sum(m["fallback"] for m in an.moves()) == 1
    row = c._moves_rows([{"used": "a", "pick": "b", "n": 11, "fallback": 1}, {"n": 3, "fallback": 0}])
    assert row[0]["calls"] == "11 (incl. 1 fallback)" and row[1]["calls"] == "3"


def test_hero_tags_the_bigger_lever_and_small_samples_get_a_note(tmp_path):
    run(tmp_path)
    narrative = tmp_path / "narrative.md"
    narrative.write_text("## summary\n\nx\n\n## routing\n\nx\n\n## caching\n\nx\n\n## caveats\n\nx\n")
    assert c.main(["render", "--out", str(tmp_path), "--narrative", str(narrative)]) == 0
    page = (tmp_path / "report.html").read_text()
    hero = page.split("<section class=hero")[1].split("<h2 id=do>")[0]
    assert hero.count("<span class=rec>Recommended</span>") == 1
    assert "Small sample: 10 calls." in page.split("<h2 id=do>")[1].split("<h2 id=scopes>")[0]


def test_summary_holds_the_headline_numbers_and_recommendations(tmp_path):
    _, rep = run(tmp_path)
    s = rep["summary"]
    head = next(sc for sc in rep["scenarios"] if sc["headline"])
    assert s["router"]["per_month"] == rep["routing"]["headline"]["per_month"]
    assert s["switch"]["per_month"] == head["switch"]["per_month"]
    assert s["router"]["pct_of_spend"] == pytest.approx(s["router"]["per_month"] / rep["spend"]["per_month"])
    assert s["recommended"] in ("router", "switch")
    sol = next(r for r in s["recommendations"][s["recommended"]] if r["model"] == SOL)
    assert sol["action"] == s["recommended"] and sol["saved_per_month"] > 0 and sol["targets"]
    assert sol["saved_today_per_month"] + sol["saved_after_enabling_per_month"] == pytest.approx(sol["saved_per_month"])
    assert sol["small_sample"] and sol["sampled_calls"] == 10
    if sol["action"] == "router":
        assert sol["router_key"] == s["router"]["routers"][0]["key"]


def test_render_reads_the_stored_summary(tmp_path):
    run(tmp_path)
    rep = json.loads((tmp_path / "report.json").read_text())
    rep["summary"]["router"]["pct_of_spend"] = 0.77  # a stored figure the page must show, not recompute
    rep["summary"]["recommended"] = "router"
    (tmp_path / "report.json").write_text(json.dumps(rep))
    narrative = tmp_path / "narrative.md"
    narrative.write_text("## summary\n\nx\n\n## routing\n\nx\n\n## caching\n\nx\n\n## caveats\n\nx\n")
    assert c.main(["render", "--out", str(tmp_path), "--narrative", str(narrative)]) == 0
    assert "77%" in (tmp_path / "report.html").read_text().split("<h2 id=do>")[0]
    del rep["summary"]
    (tmp_path / "report.json").write_text(json.dumps(rep))
    with pytest.raises(SystemExit, match="no summary"):
        c.main(["render", "--out", str(tmp_path), "--narrative", str(narrative)])


def test_json_flag_prints_the_summary_and_skips_the_narrative(tmp_path, capsys):
    run(tmp_path, json=True)
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ok" and out["summary"]["recommendations"]["router"]
    assert not (tmp_path / "narrative-slots.md").exists()


def test_an_orq_failure_stops_cleanly_and_replaces_the_old_report(tmp_path):
    (tmp_path / "report.json").write_text(json.dumps({"status": "ok"}))

    class Down(FakeOrq):
        def __call__(self, *a, **kw):
            raise c.OrqError(["x"], 1, "HTTP 401")

    an = c.Analyzer(ns(tmp_path))
    an.orq = Down()
    rep = an.run()
    assert rep["status"] == "stopped" and rep["stop"]["check"] == "orq call failed"
    assert json.loads((tmp_path / "report.json").read_text())["status"] == "stopped"


def test_summary_sampled_calls_counts_drawn_calls_and_carries_window_and_caching_error(tmp_path):
    _, rep = run(tmp_path)
    s = rep["summary"]
    assert s["sampled_calls"] == len(rep["samples"])
    assert s["window"]["requested"] == 30 and "retention" in s["window"] and "caveats" in s
    rep["caching"] = {"error": "caching scan failed"}
    assert c.summary(rep)["caching"]["error"] == "caching scan failed"


def test_router_setup_skips_models_the_page_says_to_keep():
    rep = {"workspace": "a", "routing": {"strata": [{"model": "openai/gpt-6-sol", "savings": -1.0}]},
           "scenarios": SETUP_REPORT_FOR_KEEP["scenarios"]}
    assert c._setup_plan(rep, "router")["moves"] == []


SETUP_REPORT_FOR_KEEP = {"scenarios": [{"headline": True, "models": [
    {"model": "openai/gpt-6-sol", "pool": ["openai/a", "openai/b"], "switch": None, "enable": []}]}]}


def test_setup_commands_have_no_comment_lines():
    plan = {"lever": "router", "enable": ["openai/a"], "moves": [{"model": "m", "key": "k", "pool": ["openai/a"]}]}
    assert not [x for x in c._setup_commands(plan).splitlines() if x.lstrip().startswith("#")]


def test_a_non_orq_failure_also_stops_cleanly_and_replaces_the_old_report(tmp_path):
    (tmp_path / "report.json").write_text(json.dumps({"status": "ok"}))

    class Missing(FakeOrq):
        def __call__(self, *a, **kw):
            raise FileNotFoundError("orq")

    an = c.Analyzer(ns(tmp_path))
    an.orq = Missing()
    rep = an.run()
    assert rep["status"] == "stopped" and rep["stop"]["check"] == "analyze failed"
    assert json.loads((tmp_path / "report.json").read_text())["status"] == "stopped"


class Budget(FakeOrq):
    """Hourly cells exceed the time-series budget; `mode` says how the coarser grains behave."""

    def __init__(self, mode):
        super().__init__()
        self.mode, self.grains = mode, []

    def __call__(self, *args, body=None, cache=True):
        a = list(args)
        if a[:2] == ["telemetry", "query"] and "project" in a and "--mode" in a:
            grain = a[a.index("--grain") + 1] if "--grain" in a else a[a.index("--interval-seconds") + 1]
            self.grains.append(grain)
            rows = [{"group": {"project": "p1"}, "timestamp": f"2026-11-{d:02d}T00:00:00Z",
                     "metrics": {"genai.requests:count": 4}} for d in (9, 10)]
            if grain == "hour" or self.mode == "truncated":
                return {"data": rows[:1], "has_more": True}
            if grain == "21600":
                raise c.OrqError(a, 1, "HTTP 400 time-series result exceeds the safety budget")
            return {"data": rows}
        if a[:2] == ["traces", "search"]:
            self.windows = getattr(self, "windows", []) + [(a[a.index("--from") + 1], a[a.index("--to") + 1])]
        return super().__call__(*args, body=body, cache=cache)


def test_hourly_cells_over_budget_fall_back_to_a_coarser_grain(tmp_path):
    an = c.Analyzer(ns(tmp_path))
    an.orq = Budget("ok")
    rep = an.run()
    assert rep["status"] == "ok", rep["stop"]
    assert an.orq.grains == ["hour", "21600", "day"]  # 6h failed the query, day fits
    assert rep["params"]["cell_grain"] == {SOL: "day"}
    assert not [x for x in rep["checks"]["caveats"] if x["check"] == "telemetry truncated"]
    assert rep["samples"] and all(c.parse_ts(f).date() in (c.parse_ts("2026-11-09T00:00:00Z").date(),
                                                           c.parse_ts("2026-11-10T00:00:00Z").date())
                                  for f, _ in an.orq.windows)  # draws span the whole day bucket
    jsonschema.validate(rep, SCHEMA)


def test_cells_caveat_only_when_even_daily_cells_are_truncated(tmp_path):
    an = c.Analyzer(ns(tmp_path))
    an.orq = Budget("truncated")
    rep = an.run()
    assert rep["params"]["cell_grain"] == {SOL: "day"}
    trunc = [x for x in rep["checks"]["caveats"] if x["check"] == "telemetry truncated"]
    assert trunc == [{"check": "telemetry truncated", "detail": f"{SOL}: request counts per project and day"}]


def test_coverage_is_stored_per_lever(tmp_path):
    _, rep = run(tmp_path)
    s, head = rep["summary"], next(sc for sc in rep["scenarios"] if sc["headline"])
    assert "coverage" not in s
    assert s["router"]["coverage"] == head["router"]["balanced"]["coverage"]
    assert s["switch"]["coverage"] == head["switch"]["coverage"]
    assert all({"coverage", "enable"} <= set(sc[k]) for sc in s["scopes"] for k in ("router", "switch"))


def test_no_saving_means_no_recommended_lever(tmp_path):
    _, rep = run(tmp_path)
    rep["routing"]["headline"]["per_month"] = 0.0
    for sc in rep["scenarios"]:
        sc["switch"]["per_month"] = -1.0
        sc["router"]["balanced"]["per_month"] = 0.0
    S = c.summary(rep)
    assert S["recommended"] is None
    rep["summary"] = S
    jsonschema.validate(rep, SCHEMA)
    n = {"summary": "No saving found.", "routing": "x", "caching": "x", "caveats": "x"}
    page = c.render(rep, n, redact=False)
    assert "<span class=rec>Recommended</span>" not in page and "class=wf" not in page
    assert "No saving found: no sampled model has a cheaper model that is as strong." in page
    assert "Neither lever saves money on the sampled calls." in page and "Recommended plan" not in page
    rep["summary"]["recommended"] = "router"  # a lever that saves: the tag and the plan caption come back
    assert "<span class=rec>Recommended</span>" in c.render(rep, n, redact=False)


def test_render_reads_only_the_summary(tmp_path):
    _, rep = run(tmp_path)
    rep["caching"] = {"threads_scanned": 2, "savings_found": 9.0, "rows": [
        {"thread": "thread-kept-1", "agent": None, "model": SOL, "turns": 4, "candidate": True, "savings": 9.0, "action": "a"},
        {"thread": "thread-dropped-2", "agent": "x", "model": SOL, "turns": 3, "candidate": False, "savings": 0.0, "action": "b"}]}
    rep["summary"] = c.summary(rep)
    assert [r["thread"] for r in rep["summary"]["caching"]["rows"]] == ["thread-kept-1"]
    jsonschema.validate(rep, SCHEMA)
    n = {"summary": "S.", "routing": "R.", "caching": "C.", "caveats": "V."}
    page = c.render(rep, n, redact=False)
    only = {k: rep[k] for k in ("summary", "workspace", "generated_at")}
    assert c.render(only, n, redact=False) == page  # nothing outside summary and the run header is read
    assert "thread-kept-1" in page and "thread-dropped-2" not in page and "(hidden)" in c.render(only, n, redact=True)
