"""Trace sampling, pacing and the adaptive stop: plan limits, 429 handling, the PPS estimator, window halving,
routing-input fallback, the cluster bootstrap, change model from token totals, and cache deletion."""

import json
import random
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import httpx
import jsonschema
import pytest
from conftest import (
    AGG,
    EASY_ASK,
    HARD_ASK,
    LEAVES,
    REAL_MAKE_CLIENT,
    SCHEMA,
    SOL,
    TRACES,
    FakeOrq,
    c,
    ns,
    row,
    run,
    sol_cost,
)

# ---- plan and pacing


@pytest.mark.parametrize("ws, want", [
    ({"subscription": {"metadata": {"type": "pro2025", "rateLimit": "300"}}, "features": {"rate_limit": 9}},
     ("pro2025", 300, None, "metadata.rateLimit")),
    ({"subscription": {"metadata": {"type": "team"}}, "features": {"rate_limit": 1000}},
     ("team", 1000, None, "features.rate_limit")),
    ({"subscription": {"metadata": {"type": "growth"}}}, ("growth", 20, None, "default")),
    ({"subscription": {"type": "custom"}}, ("custom", 20, None, "default")),
    ({"subscription": {"metadata": {"type": "free"}}}, ("free", 20, 50, "default")),
    ({"subscription": {"metadata": {"type": "mystery"}}}, ("mystery", 20, None, "default")),
    ({"subscription": {"metadata": {"rateLimit": 0}}, "features": {"rate_limit": "x"}}, ("free", 20, 50, "default")),
    ({}, ("free", 20, 50, "default")),
])
def test_rate_limit_comes_from_the_workspace_else_a_conservative_default(ws, want):
    p = c.plan_of(ws)
    assert (p.type, p.rate_limit, p.daily_limit, p.source) == want
    assert p.per_minute == pytest.approx(max(1.0, c.PACE_SHARE * want[1]))


def test_default_sample_follows_the_plan_type():
    kinds = ("free", "growth", "sandbox", "team", "pro", "mystery")
    sub = [{"subscription": {"type": t, "metadata": {"rateLimit": 300}}} for t in kinds]
    assert [c.plan_of(ws).default_sample for ws in sub] == [15, 100, 100, 500, 500, 500]


def test_default_sample_is_capped_when_the_workspace_states_no_rate_limit():
    kinds = ("free", "growth", "team", "pro", "mystery")
    assert [c.plan_of({"subscription": {"type": t}}).default_sample for t in kinds] == [15, 100, 100, 100, 100]


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


def test_pacer_doubles_the_gap_once_per_hold_period():
    clk = Clock()
    p = c.Pacer(6, clock=clk, sleep=clk.sleep)
    p.throttled(30)
    p.throttled(30)  # a 429 that arrives while the first hold is active is the same overload
    assert p.gap == 20.0 and p.throttles == 2
    clk.t = 31.0
    p.throttled(30)  # the hold has passed: a new overload
    assert p.gap == 40.0


def rate_limited(retry=None, status=429):
    headers = {"Retry-After": retry} if retry is not None else {}
    return c.SdkError("rate limited", httpx.Response(status, headers=headers, text="{}"))


@pytest.mark.parametrize("header, want", [
    ("7", 7.0), ("2.5", 2.5), (None, c.RATE_WINDOW_S), ("soon", c.RATE_WINDOW_S), ("-3", 0.0),
    ("99999", c.MAX_RETRY_AFTER_S), ("Sat, 28 Nov 2026 12:00:30 GMT", 30.0)],
    ids=["seconds", "fraction", "absent", "garbage", "negative", "capped", "http-date"])
def test_retry_after_is_seconds_or_an_http_date(header, want):
    now = datetime(2026, 11, 28, 12, 0, 0, tzinfo=UTC)
    headers = httpx.Headers({"Retry-After": header} if header is not None else {})
    assert c.retry_after(headers, now) == pytest.approx(want)


def paced(tmp_path, errors):
    """An analyzer whose decision call raises `errors` one by one before it answers."""
    an = c.Analyzer(ns(tmp_path))
    an.orq = FakeOrq()
    clk = Clock()
    an.pacer = c.Pacer(1000, clock=clk, sleep=clk.sleep)
    waits = []
    real = an.pacer.throttled
    an.pacer.throttled = lambda w: (waits.append(w), real(w))[1]
    pending = list(errors)

    def create(**body):
        if pending:
            raise pending.pop(0)
        return an.orq.decide(**body)
    an.orq.router.decisions.create = create
    return an, clk, waits


def test_a_429_waits_its_retry_after_header_and_is_retried(tmp_path):
    an, clk, waits = paced(tmp_path, [rate_limited("7")] * 5)
    assert an.decide("say hello").score is not None
    assert waits == [7.0] * 5 and an.pacer.throttles == 5 and an.orq.decisions == 1
    assert clk.t >= 7.0  # held, not retried at once


def test_a_429_without_retry_after_waits_the_rate_window(tmp_path):
    an, clk, waits = paced(tmp_path, [rate_limited()])
    an.decide("say hello")
    assert waits == [c.RATE_WINDOW_S] and clk.t >= c.RATE_WINDOW_S


def test_persistent_429_stops_the_run_with_a_named_check(tmp_path):
    an, _, _ = paced(tmp_path, [rate_limited("1")] * (c.MAX_429 + 1))
    with pytest.raises(c.Stop) as s:
        an.decide("hello")
    assert s.value.check == "decision rate limit" and an.pacer.throttles == c.MAX_429


@pytest.mark.parametrize("status", [401, 403])
def test_unauthorised_decision_calls_stop_the_run(tmp_path, status):
    an, _, _ = paced(tmp_path, [rate_limited(status=status)])
    with pytest.raises(c.Stop) as s:
        an.decide("hello")
    assert s.value.check == "decision calls allowed"


def test_other_decision_failures_are_scored_as_fallbacks(tmp_path):
    an, _, _ = paced(tmp_path, [rate_limited(status=422)])
    assert an.decide("a").error == "error"
    an, _, _ = paced(tmp_path, [rate_limited(status=408)])
    assert an.decide("a").error == "decision_timeout"
    an, _, _ = paced(tmp_path, [httpx.ReadTimeout("slow")])
    assert an.decide("a").error == "decision_timeout"


def test_decision_calls_never_exceed_the_approved_count_and_the_run_ends_ok_at_the_budget(tmp_path, monkeypatch):
    def approve_two(self, texts, drawn, jobs):
        self.approved = 2
        self.report["routing_decision_cost"] = {"calls": 2, "estimated_usd": 0.0, "minutes": 0.0}

    monkeypatch.setitem(LEAVES, "s3", LEAVES["s1"] | {"trace": "tC", "ask": "say hi"})  # a third ask
    monkeypatch.setitem(TRACES, "tC", "s3")
    monkeypatch.setattr(c.Analyzer, "confirm", approve_two)
    an, rep = run(tmp_path, sample=25)
    assert rep["status"] == "ok", rep["stop"]
    assert an.orq.decisions == 2 and rep["sampling"]["decision_calls"] == 2
    assert rep["sampling"]["stop_reason"] == "decision budget" and rep["sampling"]["drawn"] < 25
    assert {r["trace_id"] for r in rep["samples"]} == {"tA", "tB"} and all(r["fallback"] is None for r in rep["samples"])
    assert rep["summary"]["sampling"]["stop_reason"] == "decision budget"
    jsonschema.validate(rep, SCHEMA)
    assert "approved decision calls were used up" in c.render(rep, {k: "x" for k in c.SLOTS}, redact=False)


def test_a_decision_is_stored_without_its_prompt_and_read_back(tmp_path):
    an, _, _ = paced(tmp_path, [])
    an.decide(HARD_ASK)
    (f,) = (tmp_path / "cache" / "decisions").glob("*.json")
    assert HARD_ASK not in f.read_text() and f.stem == c.Analyzer._key(HARD_ASK)
    again = c.Analyzer(ns(tmp_path))
    again.load_decisions()
    assert again.decisions[f.stem].score == an.decisions[f.stem].score


def test_a_429_outside_the_decision_calls_is_a_jittered_retry(monkeypatch):
    replies = [subprocess.CompletedProcess([], 1, "", "HTTP 429"), subprocess.CompletedProcess([], 0, "{}", "")]
    monkeypatch.setattr(c.subprocess, "run", lambda argv, **kw: replies.pop(0))
    slept = []
    monkeypatch.setattr(c.time, "sleep", slept.append)
    assert c.Orq("ws", None)("traces", "search") == {}
    assert len(slept) == 1 and 0.5 <= slept[0] <= 1.5


def test_the_client_uses_the_cli_token_else_the_api_key(monkeypatch):
    seen = {}
    monkeypatch.setattr(c, "OrqSdk", lambda **kw: seen.update(kw) or "client")
    monkeypatch.setenv("ORQ_API_KEY", "key-1")  # exported, yet the CLI reads with its login
    monkeypatch.delenv("ORQ_API_BASE_URL", raising=False)
    out = {"token": "tok-1", "server": "https://srv", "expires_at": "2099-01-01T00:00:00Z"}
    monkeypatch.setattr(c.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(
        argv, 0, json.dumps(out), ""))
    assert REAL_MAKE_CLIENT("ws") == "client"
    assert seen["api_key"]() == "tok-1" and seen["server_url"] == "https://srv"
    seen.clear()
    monkeypatch.setattr(c.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "no login"))
    assert REAL_MAKE_CLIENT("ws") == "client" and seen == {"api_key": "key-1", "server_url": None}
    monkeypatch.delenv("ORQ_API_KEY")
    with pytest.raises(c.CredentialsError):
        REAL_MAKE_CLIENT("ws")


def test_a_missing_orq_binary_and_no_api_key_is_a_credentials_error_that_stops_before_any_read(tmp_path, monkeypatch):
    def no_binary(argv, **kw):
        raise FileNotFoundError(2, "No such file or directory: 'orq'")

    monkeypatch.setattr(c.subprocess, "run", no_binary)
    monkeypatch.delenv("ORQ_API_KEY", raising=False)
    with pytest.raises(c.CredentialsError):
        REAL_MAKE_CLIENT("ws")
    monkeypatch.setattr(c, "make_client", REAL_MAKE_CLIENT)
    an, rep = run(tmp_path)
    assert rep["status"] == "stopped" and rep["stop"]["check"] == "decision calls allowed"
    assert "no credentials" in rep["stop"]["numbers"]["detail"] and an.orq.calls == []


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
    final = an.headline_ci({SOL: 10.0}, c.BOOTSTRAP)  # the reported range is the full resample, not the stop check's
    assert sa["half_width"] == pytest.approx((final.ci90[1] - final.ci90[0]) / 2)
    assert sa["estimate"] == pytest.approx(final.savings)


def test_the_stop_rule_counts_traces_drawn_not_attempts(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "ROUND", 10)
    monkeypatch.setattr(c, "STOP_MIN_DRAWS", 10)
    monkeypatch.setattr(c, "stop_reason", lambda s, ci, spend: "relative_precision")
    an = c.Analyzer(ns(tmp_path, sample=40))
    an.orq = FakeOrq()
    real, calls = an.draw_one, []

    def flaky(*a):
        calls.append(1)
        return None if len(calls) <= 6 else real(*a)  # the first round attempts 10 and draws 4
    an.draw_one = flaky
    rep = an.run()
    assert (rep["sampling"]["rounds"], rep["sampling"]["drawn"]) == (2, 14)


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


class Multi(FakeOrq):
    """A third trace of the model, in a thread of its own, that also used another model."""

    def __call__(self, *args, **kw):
        out = super().__call__(*args, **kw)
        if list(args[:2]) == ["traces", "search"] and "thread_id" not in args[args.index("--filters") + 1]:
            out["data"] = [*out["data"], row("s2") | {"trace_id": "tM", "models": ["gpt-6-sol", "gpt-6-luna"]}]
        return out


def test_multi_model_traces_leave_the_pool_before_the_draw(tmp_path):
    an = c.Analyzer(ns(tmp_path))
    an.orq = Multi()
    rep = an.run()
    assert rep["status"] == "ok", rep["stop"]
    s1, s2 = sol_cost(LEAVES["s1"]["usage"]), sol_cost(LEAVES["s2"]["usage"])
    assert {r["trace_id"] for r in rep["samples"]} == {"tA", "tB"}
    # one of 3 equal cells, then a trace by its cost among the single-model traces only: tM's cost is not in the sum
    assert all(r["inclusion"] == pytest.approx(r["billed"] / (3 * (s1 + s2))) for r in rep["samples"])
    share = s2 / (s1 + s2 + s2)
    mm = rep["routing"]["not_analysed"]["multi_model"]
    assert mm["by_model"] == {SOL: pytest.approx(share)} and mm["spend"] == pytest.approx(10.0 * share)
    assert mm["share"] == pytest.approx(10.0 * share / 11.0)
    assert next(s for s in rep["routing"]["strata"] if s["model"] == SOL)["multi_model_share"] == pytest.approx(share)
    assert rep["routing"]["headline"]["coverage"] == pytest.approx(10.0 * (1 - share) / 11.0)
    assert any(x["check"] == "multi-model traces" for x in rep["checks"]["caveats"])
    assert "multi_model_dropped" not in rep["sampling"]
    assert rep["summary"]["not_analysed"]["multi_model_per_month"] == pytest.approx(
        10.0 * share * rep["spend"]["per_month"] / rep["spend"]["total"])
    page = c.render(rep, {k: "x" for k in c.SLOTS}, redact=False)
    assert "traces that used more than one model" in page


def test_multi_share_is_weighted_like_the_draws(tmp_path):
    an = c.Analyzer(ns(tmp_path))
    # a half-window of a 100-dollar cell holding 80 dollars, half of it multi-model (weight 2 / 100), and a whole
    # 10-dollar cell with none (weight 1 / 10): the unweighted mean of the two shares would be 0.25
    an.multi_obs[SOL] = [(40.0, 80.0, 2 / 100), (0.0, 10.0, 1 / 10)]
    assert an.multi_share(SOL) == pytest.approx(0.8 / 2.6)
    assert an.multi_share("other") == 0.0


def test_draws_stay_unbiased_when_cells_and_half_windows_come_up_empty(tmp_path):
    rng = random.Random(5)
    t0 = datetime(2026, 11, 10, 10, tzinfo=UTC)
    pop = []  # (time, cost, saving): hour 10 is ordinary, hour 11 is dense in its first 20 minutes, hour 12 has no
    for hour, n, span in ((0, 30, 3600), (1, 150, 1200)):  # eligible trace at all
        for i in range(n):
            cost = rng.uniform(0.01, 2.0) ** 2
            pop.append((t0 + timedelta(hours=hour, seconds=rng.uniform(0, span)), cost, cost * rng.uniform(0, 0.6)))
    truth = sum(x[2] for x in pop) / sum(x[1] for x in pop)
    cells = [c.Cell("p", (t0 + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ"), w, 60)
             for h, w in ((0, sum(x[1] for x in pop if x[0] < t0 + timedelta(hours=1))),
                          (1, sum(x[1] for x in pop if x[0] >= t0 + timedelta(hours=1))), (2, 25.0))]

    an = c.Analyzer(ns(tmp_path))
    an.routing_span = lambda row, tid, project: ("s", {})

    def search(project, frm, to, filters):
        return [{"trace_id": f"t{i}", "models": ["m"], "cost": {"total": x[1]}, "usage": {}, "i": i}
                for i, x in enumerate(pop) if frm <= x[0] < to]
    an.search = search
    draws = []
    for seed in range(4000):
        d = an.draw_one("m", cells, seed)
        if d is None:  # six empty attempts in a row
            continue
        _, cost, saving = pop[d.row["i"]]
        draws.append(c.Draw("m", d.weight, cost, {"k": saving}))
    assert len(draws) > 3500 and not an.capped
    assert c.stratum_rate(draws, "k") == pytest.approx(truth, abs=0.01)


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
    an.orq = FakeOrq()
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


class NoTotals(FakeOrq):
    """Terra spends too, but telemetry has no token totals for it."""

    def telemetry(self, a):
        out = super().telemetry(a)
        groups = [a[i + 1] for i, x in enumerate(a) if x == "--group-by"]
        if groups == ["model"] and "genai.cost" in a[a.index("--compute") + 1]:
            out["data"].append({"group": {"model": "openai/gpt-6-terra"}, "metrics": {"genai.cost:sum": 4.0}})
        return out


def test_a_model_without_token_totals_is_not_covered_and_never_sampled(tmp_path):
    an = c.Analyzer(ns(tmp_path))
    an.orq = NoTotals()
    rep = an.run()
    assert rep["status"] == "ok", rep["stop"]
    assert {r["model"] for r in rep["samples"]} == {SOL} and not an.pools.get("openai/gpt-6-terra")
    reason = {r["model"]: r["reason"] for r in rep["routing"]["not_covered"]}["openai/gpt-6-terra"]
    assert reason == "no token totals"
    assert {r["model"]: r["reason"] for r in rep["routing"]["switch_not_covered"]}["openai/gpt-6-terra"] == \
        "no token totals"
    assert rep["summary"]["switch"]["coverage"] == pytest.approx(10 / 15)


def test_no_token_totals_at_all_stops_instead_of_pricing_from_the_sample(tmp_path):
    an = c.Analyzer(ns(tmp_path))
    an.orq = FakeOrq(agg=False)
    rep = an.run()
    assert rep["status"] == "stopped" and rep["stop"]["check"] == "token totals"


def test_free_plan_draws_15_and_the_confirmation_names_the_daily_limit(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    an = c.Analyzer(ns(tmp_path, sample=None, yes=False))
    an.orq = FakeOrq(plan="free")
    rep = an.run()
    assert rep["params"]["sample"] == 15 and rep["params"]["sample_source"] == "plan"
    assert rep["stop"]["check"] == "confirmation" and an.orq.decisions == 0
    detail = rep["stop"]["numbers"]["detail"]
    assert "of your 50 daily decision calls" in detail and "appear as traces in this workspace" in detail
    assert "sampling usually stops much earlier once the range is tight" in detail
    assert "min" in detail


def test_free_plan_still_gives_the_change_model_figure(tmp_path):
    an = c.Analyzer(ns(tmp_path, sample=None))
    an.orq = FakeOrq(plan="free")
    rep = an.run()
    assert rep["status"] == "ok", rep["stop"]
    assert rep["sampling"]["plan"]["type"] == "free" and rep["sampling"]["drawn"] == 15
    assert rep["summary"]["switch"]["per_month"] is not None
    assert rep["summary"]["sampling"]["switch_from_totals"] == [SOL]


# ---- cache


def test_cache_is_deleted_after_an_ok_run_unless_kept(tmp_path):
    an = c.Analyzer(ns(tmp_path, keep_cache=False))
    an.orq = FakeOrq()
    (tmp_path / "cache" / "reads" / "x.json").write_text("{}")
    assert an.run()["status"] == "ok"
    assert not (tmp_path / "cache").exists()

    kept = tmp_path / "kept"
    an = c.Analyzer(ns(kept, keep_cache=True))
    an.orq = FakeOrq()
    an.run()
    assert (kept / "cache" / "reads").is_dir() and any((kept / "cache" / "decisions").glob("*.json"))


def test_a_run_stopped_at_the_confirmation_keeps_the_reads_for_the_rerun(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    an = c.Analyzer(ns(tmp_path, keep_cache=False, yes=False))
    an.orq = FakeOrq()
    (tmp_path / "cache" / "reads" / "x.json").write_text("{}")
    assert an.run()["stop"]["check"] == "confirmation"
    assert (tmp_path / "cache" / "reads" / "x.json").exists()


class Tty:
    def __init__(self, answer):
        self.answer = answer

    def isatty(self):
        return True

    def readline(self):
        return self.answer


def test_a_declined_confirmation_deletes_the_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdin", Tty("n\n"))
    an = c.Analyzer(ns(tmp_path, keep_cache=False, yes=False))
    an.orq = FakeOrq()
    rep = an.run()
    assert rep["stop"]["check"] == "confirmation" and rep["stop"]["numbers"]["next"] == "declined"
    assert not (tmp_path / "cache" / "reads").exists()


def test_ctrl_c_deletes_the_reads_and_keeps_the_decisions_for_the_rerun(tmp_path, monkeypatch):
    an = c.Analyzer(ns(tmp_path, keep_cache=False))
    an.orq = FakeOrq()
    (tmp_path / "cache" / "reads" / "x.json").write_text("{}")

    def interrupt(drawn):
        raise KeyboardInterrupt

    monkeypatch.setattr(an, "pilot_checks", interrupt)
    with pytest.raises(KeyboardInterrupt):
        an.run()
    assert not (tmp_path / "cache" / "reads").exists()
    assert len(list((tmp_path / "cache" / "decisions").glob("*.json"))) == 2


def test_a_stopped_run_keeps_its_decisions_and_a_rerun_does_not_pay_again(tmp_path):
    first = c.Analyzer(ns(tmp_path, keep_cache=False))
    first.orq = FakeOrq()
    first.pilot_checks = lambda drawn: (_ for _ in ()).throw(c.Stop("pilot", {}))
    assert first.run()["status"] == "stopped" and first.orq.decisions == 2
    files = list((tmp_path / "cache" / "decisions").glob("*.json"))
    assert len(files) == 2 and all(EASY_ASK not in f.read_text() and HARD_ASK not in f.read_text() for f in files)

    second = c.Analyzer(ns(tmp_path, keep_cache=False))
    second.orq = FakeOrq()
    assert second.run()["status"] == "ok" and second.orq.decisions == 0
    assert not (tmp_path / "cache").exists()


# ---- filters

FILTER = {"field": "project_id", "op": "eq", "values": ["p1"]}


def test_filters_reach_every_trace_search_and_spend_comes_from_the_matching_rows(tmp_path):
    an = c.Analyzer(ns(tmp_path, filters=[FILTER]))
    an.orq = FakeOrq()
    rep = an.run()
    assert rep["status"] == "ok", rep["stop"]
    searches = [a for a in an.orq.calls if a[:2] == ["traces", "search"]]
    assert len(searches) > 3 and all(FILTER in json.loads(a[a.index("--filters") + 1]) for a in searches)
    assert any("model" in a[a.index("--filters") + 1] for a in searches)  # the draws
    assert not any(a[:2] == ["telemetry", "query"] for a in an.orq.calls)
    s1, s2 = (sol_cost(LEAVES[k]["usage"]) for k in ("s1", "s2"))
    assert rep["spend"]["total"] == pytest.approx(s1 + s2)
    assert rep["spend"]["by_model"] == [{"key": SOL, "spend": pytest.approx(s1 + s2)}]
    assert rep["spend"]["by_agent"] == [{"key": "(not set)", "spend": pytest.approx(s1 + s2)}]
    agg = an.agg[SOL]
    assert (agg.uncached, agg.cache_read, agg.output) == (2000 + 500, 1500, 200)
    assert rep["params"]["filters"] == [FILTER] and rep["params"]["cell_grain"] == {SOL: "hour"}
    assert rep["summary"]["filters"] == [FILTER]
    assert rep["caching"]["threads_scanned"] == 0  # one trace per thread in the rows: none has 3
    assert rep["harness"] == {"error": "not available with --filters"}
    # one hourly cell holds both traces, so a draw is a trace by its cost
    assert all(r["inclusion"] == pytest.approx(r["billed"] / (s1 + s2)) for r in rep["samples"])
    page = c.render(rep, {k: "x" for k in c.SLOTS}, redact=False)
    assert "Scope: project_id eq p1" in page
    jsonschema.validate(rep, SCHEMA)


def test_the_caching_scan_of_a_filtered_run_uses_the_filter_and_the_scanned_threads(tmp_path):
    class Threads(FakeOrq):
        def __call__(self, *args, cache=True):
            out = super().__call__(*args, cache=cache)
            if list(args[:2]) == ["traces", "search"] and json.loads(args[args.index("--filters") + 1]) == [FILTER]:
                out["data"] = out["data"] + [row("s1") | {"trace_id": f"x{i}", "thread_id": "th1"} for i in (1, 2)]
            return out

    an = c.Analyzer(ns(tmp_path, filters=[FILTER]))
    an.orq = Threads()
    rep = an.run()
    assert rep["status"] == "ok", rep["stop"]
    assert rep["caching"]["threads_scanned"] == 1  # th1 has three traces in the rows
    by_thread = [json.loads(a[a.index("--filters") + 1]) for a in an.orq.calls
                 if a[:2] == ["traces", "search"] and "thread_id" in a[a.index("--filters") + 1]]
    assert by_thread == [[{"field": "thread_id", "op": "eq", "values": ["th1"]}, FILTER]]


def test_a_model_whose_rows_have_no_start_time_has_no_cells_instead_of_a_crash(tmp_path):
    class NoTime(FakeOrq):
        def __call__(self, *args, cache=True):
            out = super().__call__(*args, cache=cache)
            if list(args[:2]) == ["traces", "search"] and json.loads(args[args.index("--filters") + 1]) == [FILTER]:
                out["data"] = [{k: v for k, v in r.items() if k != "started_at"} for r in out["data"]]
            return out

    an = c.Analyzer(ns(tmp_path, filters=[FILTER]))
    an.orq = NoTime()
    rep = an.run()
    assert rep["stop"]["check"] == "sample not empty"
    assert {"check": "no cells", "detail": SOL} in rep["checks"]["caveats"]
    assert an.scanned and SOL in an.scanned.agg and SOL not in an.scanned.cells and SOL not in an.scanned.cell_info


def test_without_filters_the_header_has_no_scope_line_and_params_no_filters(tmp_path):
    _, rep = run(tmp_path)
    assert "filters" not in rep["params"] and rep["summary"]["filters"] == []
    assert "Scope:" not in c.render(rep, {k: "x" for k in c.SLOTS}, redact=False)


def test_a_filter_the_server_rejects_stops_the_run_with_its_message(tmp_path):
    class Reject(FakeOrq):
        def __call__(self, *args, cache=True):
            if list(args[:2]) == ["traces", "search"]:
                raise c.OrqError(list(args), 1, 'Error: HTTP 400 filter field not allowed: "nope"')
            return super().__call__(*args, cache=cache)

    an = c.Analyzer(ns(tmp_path, filters=[{"field": "nope", "op": "eq", "values": ["x"]}]))
    an.orq = Reject()
    rep = an.run()
    assert rep["status"] == "stopped" and rep["stop"]["check"] == "filters"
    assert "filter field not allowed" in rep["stop"]["numbers"]["detail"]


def test_filters_are_parsed_as_a_json_list_of_field_op_values():
    assert c._filters(json.dumps([FILTER])) == [FILTER]
    for bad in ("nope", "{}", '[{"field": "x"}]'):
        with pytest.raises(c.argparse.ArgumentTypeError):
            c._filters(bad)


def test_a_rerun_with_other_filters_does_not_reuse_the_window(tmp_path):
    first = c.Analyzer(ns(tmp_path, filters=[FILTER]))
    first.orq = FakeOrq()
    first.run()
    assert c.Analyzer(ns(tmp_path, filters=[FILTER])).previous_window() is not None
    assert c.Analyzer(ns(tmp_path)).previous_window() is None
