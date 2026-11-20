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
    def __init__(self):
        self.decisions = 0

    def __call__(self, *args, body=None, cache=True):
        a = list(args)
        if a[:2] == ["telemetry", "query"]:
            return self.telemetry(a)
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
         "seed": 7, "redact": False, "yes": True}
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


def test_render_merges_narrative(tmp_path):
    run(tmp_path)
    narrative = tmp_path / "narrative.md"
    narrative.write_text("## summary\n\nRouting would save money.\n\n## routing\n\n- easy asks move to luna\n\n"
                         "## caching\n\nNone found.\n\n## caveats\n\nSmall sample.\n")
    assert c.main(["render", "--out", str(tmp_path), "--narrative", str(narrative)]) == 0
    page = (tmp_path / "report.html").read_text()
    assert "Routing would save money." in page and "<li>easy asks move to luna</li>" in page
    assert "90% CI" in page
    assert "Same provider, all available (headline)" in page and "tokenize differently" in page
    assert "Dominated models in use" in page
    assert "class=ex" not in page  # sampled ask text stays out unless --show-examples
    assert "or, pick one" in page and "<h2 id=do>What to do</h2>" in page and "class=plan" not in page
    assert "Models with many hard asks" not in page and "jev-general" not in page


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
