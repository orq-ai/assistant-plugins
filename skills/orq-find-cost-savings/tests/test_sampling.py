"""Trace sampling, pacing and the adaptive stop: plan limits, 429 handling, the PPS estimator, window halving,
routing-input fallback, the cluster bootstrap, change model from token totals, and cache deletion."""

import random
import subprocess
import sys
from datetime import timedelta

import pytest
from test_analyze import AGG, HARD_ASK, LEAVES, SOL, FakeOrq, c, ns, row, run


# ---- plan and pacing


@pytest.mark.parametrize("ws, want", [
    ({"subscription": {"metadata": {"type": "pro2025", "rateLimit": "300"}}}, ("pro2025", 300, None, "metadata.rateLimit")),
    ({"subscription": {"metadata": {"type": "team"}}}, ("team", 1000, None, "plan type")),
    ({"subscription": {"metadata": {"type": "growth"}}}, ("growth", 100, None, "plan type")),
    ({"subscription": {"type": "custom"}}, ("custom", 1500, None, "plan type")),
    ({"subscription": {"metadata": {"type": "free"}}}, ("free", 20, 50, "plan type")),
    ({"subscription": {"metadata": {"type": "mystery"}}}, ("free", 20, 50, "default")),
    ({}, ("free", 20, 50, "default")),
])
def test_plan_sets_the_rate_limit(ws, want):
    p = c.plan_of(ws)
    assert (p.type, p.rate_limit, p.daily_limit, p.source) == want
    assert p.per_minute == pytest.approx(max(1.0, c.PACE_SHARE * want[1]))


def test_default_sample_follows_the_plan():
    assert [c.plan_of({"subscription": {"type": t}}).default_sample for t in ("free", "growth", "team", "pro")] == \
        [15, 100, 500, 500]


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_pacer_spaces_calls_and_backs_off_on_429():
    clk = Clock()
    p = c.Pacer(6, clock=clk, sleep=clk.sleep)  # one call every 10 s
    stamps = []
    for _ in range(3):
        p.acquire()
        stamps.append(clk.t)
    assert stamps == [0.0, 10.0, 20.0]
    p.throttled(45)  # Retry-After 45: hold every worker, and halve the pace
    p.acquire()
    assert clk.t == pytest.approx(65.0) and p.gap == 20.0 and p.throttles == 1
    p.acquire()
    assert clk.t == pytest.approx(85.0)


def _proc(code, stderr="", stdout=""):
    return subprocess.CompletedProcess([], code, stdout, stderr)


def test_429_waits_retry_after_from_verbose_headers_without_using_retries(tmp_path, monkeypatch):
    replies = [_proc(1, "INFO cli.go using profile\n  ╎ Retry-After: 7\n  ╎ {\"state\": \"secret\"}\n"
                        "Error: HTTP 429 Too Many Requests")] * 5 + [_proc(0, stdout='{"ok": 1}')]
    argvs = []

    def fake_run(argv, **_kw):
        argvs.append(argv)
        return replies.pop(0)

    monkeypatch.setattr(c.subprocess, "run", fake_run)
    waits = []

    class P(c.Pacer):
        def throttled(self, wait_s):
            waits.append(wait_s)

    o = c.Orq("ws", None, retries=1)
    clk = Clock()
    assert o("decisions", "create", pacer=P(1000, clock=clk, sleep=clk.sleep)) == {"ok": 1}
    assert waits == [7.0] * 5 and all("--verbose" in a for a in argvs)  # 5 waits on a 1-retry budget


def test_persistent_429_stops_the_run_with_a_named_check(tmp_path, monkeypatch):
    monkeypatch.setattr(c.subprocess, "run", lambda argv, **kw: _proc(1, "  ╎ x\nError: HTTP 429"))
    o = c.Orq("ws", None)
    clk = Clock()
    p = c.Pacer(1000, clock=clk, sleep=clk.sleep)
    with pytest.raises(c.RateLimited) as e:
        o("decisions", "create", pacer=p)
    assert "╎" not in e.value.stderr  # verbose lines (headers, the request body) are never kept
    assert p.throttles == c.MAX_429 and clk.t >= c.MAX_429 * c.RATE_WINDOW_S  # no Retry-After: wait out the window

    an = c.Analyzer(ns(tmp_path))
    an.pacer = c.Pacer(1000, clock=clk, sleep=clk.sleep)
    an.orq = o
    with pytest.raises(c.Stop) as s:
        an.decide("hello")
    assert s.value.check == "decision rate limit"


def test_a_429_without_a_pacer_is_a_jittered_retry(monkeypatch):
    replies = [_proc(1, "HTTP 429"), _proc(0, stdout="{}")]
    monkeypatch.setattr(c.subprocess, "run", lambda argv, **kw: replies.pop(0))
    slept = []
    monkeypatch.setattr(c.time, "sleep", slept.append)
    assert c.Orq("ws", None)("traces", "search") == {}
    assert len(slept) == 1 and 0.5 <= slept[0] <= 1.5


# ---- estimator


def test_pps_ratio_estimator_is_unbiased_on_a_toy_population():
    rng = random.Random(3)
    cost = [rng.uniform(0.01, 5) for _ in range(40)]
    save = [x * rng.uniform(-0.2, 0.8) for x in cost]
    truth = sum(save) / sum(cost)
    p = [x / sum(cost) for x in cost]
    est = []
    for _ in range(4000):
        idx = rng.choices(range(len(cost)), weights=cost, k=8)
        ds = [c.Draw("m", 1 / p[i], cost[i], {"k": save[i]}) for i in idx]
        est.append(c.stratum_rate(ds, "k"))
    assert sum(est) / len(est) == pytest.approx(truth, abs=0.005)


def test_bootstrap_resamples_threads_not_traces():
    one = [c.Draw("m", 1.0, 1.0, {"k": v}, "thread-1") for v in (0.0, 1.0, 0.5, 0.2)]
    e = c.estimate({"m": one}, {"m": 10.0}, "k", random.Random(1), boots=200)
    assert e.ci90[0] == e.ci90[1] == pytest.approx(e.savings)  # one cluster: every resample is the same thread
    apart = [c.Draw("m", 1.0, 1.0, {"k": v}) for v in (0.0, 1.0, 0.5, 0.2)]
    e2 = c.estimate({"m": apart}, {"m": 10.0}, "k", random.Random(1), boots=200)
    assert e2.ci90[0] < e2.savings < e2.ci90[1]
    assert [len(g) for g in c.clusters(one + apart)] == [4, 1, 1, 1, 1]


def test_adaptive_stop_rule():
    assert c.stop_reason(10.0, [8.0, 12.0], 1000.0) == "relative_precision"  # half width 2 <= 25% of 10
    assert c.stop_reason(10.0, [0.0, 20.0], 1000.0) == "absolute_precision"  # 10 <= 1% of 1000
    assert c.stop_reason(10.0, [0.0, 20.0], 100.0) is None
    assert c.stop_reason(0.0, [-0.5, 0.5], 100.0) == "absolute_precision"


def test_draw_order_takes_every_minimum_first_then_interleaves():
    order = c.draw_order({"a": 9, "b": 6}, minimum=5)
    assert sorted(order[:10]) == ["a"] * 5 + ["b"] * 5 and len(order) == 15
    assert order[10:].count("a") == 4 and order[10:].count("b") == 1


def test_a_run_stops_early_once_the_headline_range_is_tight(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "ROUND", 10)
    monkeypatch.setattr(c, "STOP_MIN_DRAWS", 10)
    monkeypatch.setattr(c, "stop_reason", lambda s, ci, spend: "relative_precision")
    an, rep = run(tmp_path, sample=40)
    sa = rep["sampling"]
    assert (sa["stop_reason"], sa["drawn"], sa["rounds"], sa["target"]) == ("relative_precision", 10, 1, 40)
    assert rep["summary"]["sampling"]["stop_reason"] == "relative_precision"


# ---- drawing


def test_window_halves_until_under_the_row_cap(tmp_path):
    an = c.Analyzer(ns(tmp_path))
    widths = []

    def search(project, frm, to, filters):
        widths.append((to - frm).total_seconds())
        return [{"trace_id": f"t{i}"} for i in range(c.SEARCH_CAP if to - frm > timedelta(minutes=10) else 30)]

    an.search = search
    rows, k = an.window_rows(SOL, c.Cell("p1", "2026-11-10T10:00:00Z", 1.0, 60), random.Random(0))
    assert len(rows) == 30 and k == 3 and widths == [3600, 1800, 900, 450]


def test_traces_with_more_than_one_model_are_dropped_and_counted(tmp_path):
    class Multi(FakeOrq):
        def __call__(self, *args, **kw):
            out = super().__call__(*args, **kw)
            if list(args[:2]) == ["traces", "search"] and "model" in args[args.index("--filters") + 1]:
                out["data"] = [r | {"models": ["gpt-6-sol", "gpt-6-luna"]} if r["trace_id"] == "tB" else r
                               for r in out["data"]]
            return out

    an = c.Analyzer(ns(tmp_path))
    an.orq = Multi()
    rep = an.run()  # only the easy trace is left, so the single-tier check stops it after sampling
    assert {r["trace_id"] for r in rep["samples"]} == {"tA"}
    assert rep["sampling"]["multi_model_dropped"] > 0
    assert any(x["check"] == "multi-model traces" for x in rep["checks"]["caveats"])


def test_routing_input_falls_back_to_the_last_llm_call(tmp_path):
    class Agent(FakeOrq):
        def __call__(self, *args, **kw):
            a = list(args)
            if a[:2] == ["traces", "get-span"] and a[3] == "root":
                return {"span": {"attributes": {"orq": {"agent": "x"}}}}
            if a[:2] == ["traces", "list-spans"]:
                self.calls.append(a)
                leaves = [{"type": "span.responses", "has_children": False, "span_id": k, "started_at": v["at"]}
                          for k, v in LEAVES.items()]
                return {"data": [{"type": "agent", "has_children": True, "span_id": "root"}, *leaves]}
            return super().__call__(*args, **kw)

    an = c.Analyzer(ns(tmp_path))
    an.orq = Agent()
    span_id, span = an.routing_span(row("s1") | {"root_span_id": "root"}, "tA", "p1")
    assert span_id == "s2"  # started last
    assert c.routing_input(span["attributes"])[0] == HARD_ASK
    assert an.routing_span(row("s1"), "tA", "p1")[0] == "s1"  # a root with messages is used as is


# ---- levers, plan defaults, cache


def test_change_model_is_priced_from_token_totals(tmp_path):
    an = c.Analyzer(ns(tmp_path))
    an.orq = FakeOrq(agg=True)
    rep = an.run()
    assert rep["status"] == "ok", rep["stop"]
    head = next(sc for sc in rep["scenarios"] if sc["headline"])
    sw = head["switch"]
    assert sw["from_totals"] == [SOL] and sw["ci90"][0] == sw["ci90"][1] == pytest.approx(sw["savings"])
    agg = an.agg[SOL]
    assert (agg.uncached, agg.cache_read, agg.output) == (1_000_000, 1_000_000, AGG[
        "gen_ai.client.inference.usage.output_tokens"])
    own = c.price_tokens(agg, an.agg_price(SOL).prices)
    target = c.price_tokens(agg, an.agg_price(head["models"][0]["switch"]).prices)
    assert sw["savings"] == pytest.approx(10.0 * (own - target) / own)
    assert next(s for s in rep["routing"]["strata"] if s["model"] == SOL)["switch_source"] == "totals"
    assert rep["sampling"]["calls_per_trace"] == {SOL: 2.0}


def test_free_plan_draws_15_and_the_confirmation_names_the_daily_limit(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    an = c.Analyzer(ns(tmp_path, sample=None, yes=False))
    an.orq = FakeOrq(plan="free", agg=True)
    rep = an.run()
    assert rep["params"]["sample"] == 15 and rep["params"]["sample_source"] == "plan"
    assert rep["stop"]["check"] == "confirmation" and an.orq.decisions == 0
    detail = rep["stop"]["numbers"]["detail"]
    assert "of your 50 daily decision calls" in detail and "appear as traces in this workspace" in detail
    assert "min" in detail


def test_free_plan_still_gives_the_change_model_figure(tmp_path):
    an = c.Analyzer(ns(tmp_path, sample=None))
    an.orq = FakeOrq(plan="free", agg=True)
    rep = an.run()
    assert rep["status"] == "ok", rep["stop"]
    assert rep["sampling"]["plan"]["type"] == "free" and rep["sampling"]["drawn"] == 15
    assert rep["summary"]["switch"]["per_month"] is not None
    assert rep["summary"]["sampling"]["switch_from_totals"] == [SOL]


def test_cache_is_deleted_after_analyze_unless_kept(tmp_path):
    an = c.Analyzer(ns(tmp_path, keep_cache=False))
    an.orq = FakeOrq()
    (tmp_path / "cache" / "x.json").write_text("{}")
    assert an.run()["status"] == "ok"
    assert not (tmp_path / "cache").exists()

    kept = tmp_path / "kept"
    an = c.Analyzer(ns(kept, keep_cache=True))
    an.orq = FakeOrq()
    an.run()
    assert (kept / "cache").is_dir()


def test_a_run_stopped_at_the_confirmation_keeps_the_cache_for_the_rerun(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    an = c.Analyzer(ns(tmp_path, keep_cache=False, yes=False))
    an.orq = FakeOrq()
    assert an.run()["stop"]["check"] == "confirmation"
    assert (tmp_path / "cache").is_dir()
