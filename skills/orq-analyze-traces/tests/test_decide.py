import argparse
import asyncio
import importlib.util
import json
import math
import sys
from pathlib import Path

import httpx
import pytest

SPEC = importlib.util.spec_from_file_location(
    "trace_decide", Path(__file__).parents[1] / "scripts" / "decide.py"
)
d = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = d
SPEC.loader.exec_module(d)


@pytest.fixture
def run(tmp_path):
    q = tmp_path / "q.json"
    q.write_text(json.dumps({"bad": {"type": "noul", "instructions": "Did it fail?"}}))
    ids = tmp_path / "ids.txt"
    ids.write_text("a\nb\nc\n")
    return argparse.Namespace(
        questions=q,
        run_dir=tmp_path,
        short_model=d.SHORT_MODEL,
        long_model=d.LONG_MODEL,
        trace_ids=ids,
        search_body=None,
        max_traces=None,
        concurrency=1,
        budget_usd=1,
        agent_labels=tmp_path / "coded.json",
        read_ids=None,
        question=None,
        seed=0,
        flagged=30,
        random=50,
    )


def label(trace_id, positive=True, route="short"):
    return {
        "trace_id": trace_id,
        "route": route,
        "answers": {"bad": {"type": "noul", "noul": float(positive)}},
    }


def save(run, rows):
    path = d.run_file(run, d.load_questions(run.questions))
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    d.meta_path(path).write_text(json.dumps({"ids": [row["trace_id"] for row in rows]}))
    return path


def test_missing_positive_cannot_clear(run, capsys):
    save(
        run,
        [
            label(str(i), i < 8) if i < 10 else {"trace_id": str(i), "error": "fetch"}
            for i in range(15)
        ],
    )
    run.agent_labels.write_text(json.dumps({str(i): "bad" for i in range(15)}))
    d.cmd_agreement(run)
    mode = json.loads(capsys.readouterr().out)["modes"]["bad"]
    assert mode["all"]["agent_positives"] == 15
    assert mode["all"]["recall"] == pytest.approx(8 / 15, abs=0.001)
    assert mode["clears"].startswith("no:")


@pytest.mark.parametrize(
    "answer",
    [
        None,
        {},
        {"type": "refusal"},
        {"type": "noul", "noul": "yes"},
        {"type": "noul", "noul": math.nan},
        {"type": "noul", "noul": -1},
        {"type": "noul", "noul": 1.1},
        {"type": "noul", "noul": True},
    ],
)
def test_unreadable_answers_are_unknown(run, answer):
    assert (
        d.verdicts({"answers": {"bad": answer}}, d.load_questions(run.questions))
        is None
    )


def test_probability_boundary_and_choice(run):
    q = d.load_questions(run.questions)
    assert d.verdicts({"answers": {"bad": {"type": "noul", "noul": 0.5}}}, q) == {"bad"}
    assert d.verdicts(label("a", False), q) == set()
    choice = d.Questions(
        {"kind": {"type": "choice", "criteria": {"bad": "Failure", "none": "Pass"}}},
        {},
        run.questions,
    )
    assert d.verdicts(
        {"answers": {"kind": {"type": "choice", "choice": "bad"}}}, choice
    ) == {"bad"}
    assert (
        d.verdicts({"answers": {"kind": {"type": "choice", "choice": "other"}}}, choice)
        is None
    )


@pytest.mark.parametrize("limit", [math.nan, math.inf, -math.inf, 0, -1])
def test_budget_rejects_invalid_limits(limit):
    with pytest.raises(ValueError):
        d.Budget(limit)


def test_budget_reserves_concurrent_calls():
    async def scenario():
        budget = d.Budget(1)

        async def call():
            if not budget.reserve(0.3):
                return
            await asyncio.sleep(0)
            budget.settle(0.3, 0.3)

        await asyncio.gather(*(call() for _ in range(50)))
        assert budget.spent == pytest.approx(0.9)
        assert budget.held == pytest.approx(0)
        assert not budget.reserve(
            0.01
        )  # first refusal stops this pass, including cheaper calls

    asyncio.run(scenario())


def test_wilson_known_values():
    assert d.wilson(50, 100) == [0.4038, 0.5962]
    assert d.wilson(0, 0) == [0, 0]
    assert d.wilson(0, 10)[0] == 0
    assert d.wilson(10, 10)[1] == 1


def test_kappa_known_values():
    assert d.kappa(5, 0, 0, 5) == 1
    assert d.kappa(0, 5, 5, 0) == -1
    assert d.kappa(5, 5, 5, 5) == 0
    assert d.kappa(0, 0, 0, 0) is None


def test_empty_answers_are_retried(run, monkeypatch, capsys):
    save(run, [{"trace_id": "a", "answers": {}}])
    calls = []

    async def one(http, trace_id, order, args, wire, budget):
        calls.append(trace_id)
        return label(trace_id)

    monkeypatch.setenv("ORQ_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(d, "label_one", one)
    asyncio.run(d.cmd_label(run))
    report = json.loads(capsys.readouterr().out)
    assert set(calls) == {"a", "b", "c"}
    assert report["labelled"] == 3


def test_metadata_exists_before_first_call(run, monkeypatch, capsys):
    async def one(http, trace_id, order, args, wire, budget):
        path = d.run_file(args, d.load_questions(args.questions))
        assert d.load_meta(path)["ids"] == ["a", "b", "c"]
        return label(trace_id)

    monkeypatch.setenv("ORQ_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(d, "label_one", one)
    asyncio.run(d.cmd_label(run))
    report = json.loads(capsys.readouterr().out)
    assert report["labelled"] == 3
    assert not report["this_run"].get("error")


def test_stop_before_fetch_when_budget_exhausted(run, monkeypatch, capsys):
    calls = []

    async def one(http, trace_id, order, args, wire, budget):
        calls.append(trace_id)
        assert budget.reserve(1)
        budget.settle(1, 1)
        return label(trace_id)

    monkeypatch.setenv("ORQ_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(d, "label_one", one)
    asyncio.run(d.cmd_label(run))
    assert calls == ["a"]
    assert json.loads(capsys.readouterr().out)["this_run"]["not_started"] == 2


def test_wire_contract_and_refusal():
    async def scenario():
        def handle(req):
            assert req.url.path == "/v3/router/decisions"
            assert json.loads(req.content)["model"] == d.SHORT_MODEL
            return httpx.Response(
                200,
                json={
                    "model": d.SHORT_MODEL,
                    "answers": {"probe": {"type": "noul", "noul": 0.9}},
                    "usage": {"total_cost": 0.00001},
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handle), base_url="https://example.test"
        ) as http:
            result = await d.decide(
                http, d.SHORT_MODEL, d.PROBE_STATE, d.PROBE_QUESTION
            )
            assert result["answers"]["probe"]["noul"] == 0.9
            assert result["usage"]["total_cost"] == 0.00001

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("positives", "caught", "clears"),
    [
        (9, 9, False),
        (10, 7, True),
        (100, 69, False),
        (100, 70, True),
        (10000, 6999, False),
    ],
)
def test_gate_boundaries(run, capsys, positives, caught, clears):
    save(run, [label(str(i), i < caught) for i in range(positives)])
    run.agent_labels.write_text(json.dumps({str(i): "bad" for i in range(positives)}))
    d.cmd_agreement(run)
    mode = json.loads(capsys.readouterr().out)["modes"]["bad"]
    assert (mode["clears"] == "yes") == clears


def test_random_and_route_breakdown_includes_missing_positive(run, capsys):
    save(run, [label("a"), {"trace_id": "b", "route": "long", "error": "timeout"}])
    picks = run.run_dir / "picks"
    picks.mkdir()
    (picks / "random_ids.txt").write_text("b\n")
    run.agent_labels.write_text(json.dumps({"a": "bad", "b": "bad"}))
    d.cmd_agreement(run)
    mode = json.loads(capsys.readouterr().out)["modes"]["bad"]
    assert mode["random"]["recall"] == 0
    assert mode["long"]["missing_positives"] == 1
    assert mode["all"]["agent_positives"] == 2


def test_window_reporting_excludes_cached_other_window(run, capsys):
    path = save(run, [label("old"), label("a", False)])
    d.meta_path(path).write_text(json.dumps({"ids": ["a", "not_started"]}))
    d.cmd_rates(run)
    report = json.loads(capsys.readouterr().out)
    assert report["labelled"] == 1
    assert report["not_started"] == 1
    assert report["modes"]["bad"]["count"] == 0
    d.cmd_pick(run)
    report = json.loads(capsys.readouterr().out)
    assert report["flagged"] == 0
    assert set(report["picks"]["random"]) == {"a", "not_started"}


@pytest.mark.parametrize("command", ["cmd_pick", "cmd_rates"])
def test_missing_metadata_refuses_window_report(run, command):
    path = save(run, [label("a")])
    d.meta_path(path).unlink()
    with pytest.raises(SystemExit, match="window ids unavailable"):
        getattr(d, command)(run)


def test_latest_rows_keeps_readable_success_else_latest_error(run):
    q = d.load_questions(run.questions)
    path = save(
        run,
        [
            label("a"),
            {"trace_id": "a", "answers": {}},
            {"trace_id": "b", "error": "first"},
            {"trace_id": "b", "error": "last"},
        ],
    )
    rows = d.latest_rows(path, q)
    assert d.verdicts(rows["a"], q) == {"bad"}
    assert rows["b"]["error"] == "last"


def test_pick_never_promotes_partial_answers(run, capsys):
    run.questions.write_text(
        json.dumps({"bad": {"type": "noul"}, "other": {"type": "noul"}})
    )
    save(run, [label("a")])
    d.cmd_pick(run)
    report = json.loads(capsys.readouterr().out)
    assert report["labelled"] == report["flagged"] == 0
    assert report["picks"]["random"] == ["a"]


def test_torn_row_does_not_corrupt_resumed_success(run, monkeypatch, capsys):
    path = save(run, [label("a")])
    with path.open("a") as f:
        f.write('{"trace_id":')

    async def one(http, trace_id, order, args, wire, budget):
        return label(trace_id)

    monkeypatch.setenv("ORQ_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(d, "label_one", one)
    asyncio.run(d.cmd_label(run))
    assert json.loads(capsys.readouterr().out)["labelled"] == 3


@pytest.mark.parametrize("status", [401, 403])
def test_fetch_auth_error_aborts_whole_run(run, monkeypatch, capsys, status):
    requests = []

    def handle(req):
        requests.append(req.url.path)
        return httpx.Response(status, text="denied")

    monkeypatch.setattr(
        d,
        "client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handle), base_url="https://example.test"
        ),
    )
    with pytest.raises(SystemExit, match="authorisation"):
        asyncio.run(d.cmd_label(run))
    report = json.loads(capsys.readouterr().out)
    assert report["labelled"] == 0
    assert len(requests) == 1


@pytest.mark.parametrize("billed", [None, math.nan, -1, "unknown", 0.2])
def test_missing_invalid_and_reported_costs(run, billed):
    async def scenario():
        def handle(req):
            if req.method == "GET":
                return httpx.Response(
                    200,
                    json={
                        "items": [{"type": "message", "role": "user", "content": "hi"}]
                    },
                )
            return httpx.Response(
                200,
                text=json.dumps(
                    {"answers": label("a")["answers"], "usage": {"total_cost": billed}}
                ),
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handle), base_url="https://example.test"
        ) as http:
            budget = d.Budget(1)
            row = await d.label_one(
                http, "a", 0, run, d.load_questions(run.questions).wire, budget
            )
            assert math.isfinite(budget.spent) and budget.spent > 0
            assert budget.held == 0
            assert row["cost_estimated"] == (billed != 0.2)
            assert row["cost"] == budget.spent

    asyncio.run(scenario())


def test_overflow_budget_refusal_retains_attempt(run):
    async def scenario():
        wire = d.load_questions(run.questions).wire
        items = [{"type": "message", "role": "user", "content": "hi"}]
        estimate = d.estimated_cost(
            d.SHORT_MODEL, d.state_tokens(d.build_state(items), wire)
        )
        budget = d.Budget(estimate * 1.1)
        calls = []

        def handle(req):
            if req.method == "GET":
                return httpx.Response(200, json={"items": items})
            calls.append(req)
            return httpx.Response(400, text=d.OVERFLOW)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handle), base_url="https://example.test"
        ) as http:
            row = await d.label_one(http, "a", 0, run, wire, budget)
            assert row["error_kind"] == "budget_retry"
            assert row["cost"] == pytest.approx(estimate)
            assert budget.stopped and len(calls) == 1

    asyncio.run(scenario())


def test_unknown_agent_labels_block_gate(run, capsys):
    save(run, [label(str(i)) for i in range(10)])
    run.agent_labels.write_text(
        json.dumps({**{str(i): "bad" for i in range(10)}, "typo": "bda"})
    )
    d.cmd_agreement(run)
    report = json.loads(capsys.readouterr().out)
    assert report["modes"]["bad"]["clears"].startswith("no:")


def test_host_and_threshold_changes_invalidate_cache(run, monkeypatch):
    old = d.run_file(run, d.load_questions(run.questions))
    run.questions.write_text(
        json.dumps(
            {"bad": {"type": "noul", "instructions": "Did it fail?", "threshold": 0.8}}
        )
    )
    assert old != d.run_file(run, d.load_questions(run.questions))
    monkeypatch.setenv("ORQ_API_BASE_URL", "https://staging.example.test")
    assert old != d.run_file(run, d.load_questions(run.questions))


@pytest.mark.parametrize(
    "answers",
    [{}, {"probe": {"type": "refusal"}}, {"probe": {"type": "noul", "noul": "yes"}}],
)
def test_probe_unreadable_response_reports_unavailable(
    run, monkeypatch, capsys, answers
):
    async def decide(*args):
        return {"answers": answers, "usage": {}}

    monkeypatch.setenv("ORQ_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(d, "decide", decide)
    with pytest.raises(SystemExit) as exc:
        asyncio.run(d.cmd_probe(run))
    assert exc.value.code == 1
    reports = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(reports) == 2
    assert all("unavailable" in row["status"] for row in reports)


def test_final_overflow_refusal_reports_budget_stop(run, monkeypatch, capsys):
    run.trace_ids.write_text("a\n")

    async def one(http, trace_id, order, args, wire, budget):
        assert not budget.reserve(2)
        return {
            "trace_id": trace_id,
            "error": "budget refused overflow retry",
            "error_kind": "budget_retry",
        }

    monkeypatch.setenv("ORQ_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(d, "label_one", one)
    asyncio.run(d.cmd_label(run))
    assert "budget" in json.loads(capsys.readouterr().out)["stopped"]


def test_flagged_reading_list_follows_current_window_order(run, capsys):
    path = save(
        run, [{**label("old_first"), "order": 0}, {**label("now_first"), "order": 1}]
    )
    d.meta_path(path).write_text(json.dumps({"ids": ["now_first", "old_first"]}))
    d.cmd_rates(run)
    mode = json.loads(capsys.readouterr().out)["modes"]["bad"]
    assert mode["flagged_not_read"] == ["now_first", "old_first"]
