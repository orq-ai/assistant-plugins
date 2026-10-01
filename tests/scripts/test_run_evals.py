"""Scoring and aggregation in run_evals.py and skill_test_report.py (RES-1076).

Pure functions on hand-built run outputs: no agent, no network. These decide every
verdict the eval suite reports, so a wrong branch here mis-scores every run.
Run: uv run --no-project --with evaluatorq==1.47.1 --with pyyaml --with pytest python -m pytest tests/scripts/test_run_evals.py -q
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import run_evals as r
import skill_test_report as report
from evaluatorq import DataPoint

CLAUDE = r.CLAUDE_MCP_PREFIX
ORQ_SKILLS = ["orq-build-evaluator", "orq-run-experiment"]


def call(name: str, arguments: Any = None, *, result: str = "", status: str = "completed") -> dict[str, Any]:
    return {"name": name, "arguments": arguments or {}, "result": result, "status": status}


def case(**overrides: Any) -> r.Case:
    fields = {"id": "c", "skill": "orq-build-evaluator", "kind": "behavioural", "prompt": "p", "expect_skill": "orq-build-evaluator"}
    return r.Case(**(fields | overrides))


def score(scorer: Any, c: r.Case, calls: list[dict[str, Any]], agent: str = "claude", text: str = "") -> Any:
    data = DataPoint(inputs={"case": c.__dict__, "run": 1, "orq_skills": ORQ_SKILLS})
    return asyncio.run(scorer({"data": data, "output": {"agent": agent, "tool_calls": calls, "text": text}}))


# -- tool call parsing ---------------------------------------------------------


def test_fired_skills_strips_prefix_and_ignores_errored_calls() -> None:
    calls = [
        call("Skill", {"skill": "orq:orq-build-evaluator"}),
        call("Skill", {"skill": "orq:orq-run-experiment"}, status="incomplete"),
        call("Read", {"file_path": "x"}),
    ]
    assert r.fired_skills("claude", calls) == ["orq-build-evaluator"]
    assert r.fired_skills("opencode", [call("skill", {"name": "orq-run-experiment"})]) == ["orq-run-experiment"]


def test_bare_tool() -> None:
    assert r.bare_tool("claude", CLAUDE + "list_models") == "list_models"
    assert r.bare_tool("opencode", r.OPENCODE_MCP_PREFIX + "list_models") == "list_models"
    assert r.bare_tool("claude", "Read") is None


# -- scorers ---------------------------------------------------------------------


def test_skill_fired_requires_the_expected_skill_first() -> None:
    c = case(kind="invocation")
    assert score(r.skill_fired, c, [call("Skill", {"skill": "orq-build-evaluator"})]).pass_ is True
    wrong_first = [call("Skill", {"skill": "orq-run-experiment"}), call("Skill", {"skill": "orq-build-evaluator"})]
    assert score(r.skill_fired, c, wrong_first).pass_ is False
    assert score(r.skill_fired, c, [call("Skill", {"skill": "orq-build-evaluator"}, status="incomplete")]).pass_ is False


def test_skill_fired_none_and_any() -> None:
    none = case(kind="invocation", expect_skill="none")
    assert score(r.skill_fired, none, [call("Skill", {"skill": "orq-run-experiment"})]).pass_ is False
    assert score(r.skill_fired, none, [call("Skill", {"skill": "some-other-plugin-skill"})]).pass_ is True
    assert score(r.skill_fired, case(expect_skill="any"), []).pass_ is None


def test_tools_called_counts_only_completed_calls() -> None:
    c = case(expect_tools=["list_models"], allow_tools=["list_models"])
    assert score(r.tools_called, c, [call(CLAUDE + "list_models")]).pass_ is True
    two = case(expect_tools=["list_models", "search_entities"], allow_tools=["list_models", "search_entities"])
    errored = score(r.tools_called, two, [call(CLAUDE + "list_models", status="incomplete")])
    assert errored.pass_ is False  # search_entities was never called: a real miss
    assert "list_models (called but errored)" in errored.explanation
    assert score(r.tools_called, case(kind="invocation"), []).pass_ is None
    # never returned (a Claude run cut off at --max-turns): not evidence the step worked
    assert score(r.tools_called, c, [call(CLAUDE + "list_models", status="in_progress")]).pass_ is False
    # only a server error on the expected tool: no verdict here, aggregate() makes the run an error
    assert score(r.tools_called, c, [call(CLAUDE + "list_models", result="ECONNREFUSED", status="incomplete")]).pass_ is None
    assert score(r.tools_called, case(), []).pass_ is None


def test_asks_user_requires_a_relevant_question_in_assistant_text() -> None:
    c = case(expect_question_about=["ground", "label"])
    assert score(r.asks_user, c, [], text="What counts as a grounded answer?").pass_ is True
    assert score(r.asks_user, c, [], text="What time is it?").pass_ is False
    assert score(r.asks_user, c, [], text="I will check grounding.").pass_ is False
    assert score(r.asks_user, c, [call("AskUserQuestion", {"question": "What counts as grounded?"})]).pass_ is False
    assert score(r.asks_user, case(), [], text="What counts as grounded?").pass_ is None


def test_no_forbidden_tools() -> None:
    c = case(allow_tools=["search_entities"])
    assert score(r.no_forbidden_tools, c, [call(CLAUDE + "search_entities")]).pass_ is True
    denied = call(CLAUDE + "create_llm_eval", {"key": "k"}, result="[denied by claude]", status="incomplete")
    assert score(r.no_forbidden_tools, c, [denied]).pass_ is False  # the attempt is the regression
    assert score(r.no_forbidden_tools, c, [call(CLAUDE + "list_models")]).pass_ is False  # outside allow_tools
    denied_read = call(CLAUDE + "list_models", result="[denied by claude]", status="incomplete")
    assert score(r.no_forbidden_tools, c, [denied_read]).pass_ is True


def test_no_forbidden_tools_on_opencode() -> None:
    c = case(allow_tools=["search_entities"])
    oc = r.OPENCODE_MCP_PREFIX
    assert score(r.no_forbidden_tools, c, [call(oc + "search_entities")], agent="opencode").pass_ is True
    assert score(r.no_forbidden_tools, c, [call(oc + "create_dataset")], agent="opencode").pass_ is False
    rejected = call(oc + "list_models", result="Error: permission rejected by user", status="incomplete")
    assert score(r.no_forbidden_tools, c, [rejected], agent="opencode").pass_ is True


# -- aggregation and exit code ---------------------------------------------------


def run_result(c: r.Case, outcomes: list[str], agent: str = "claude") -> list[Any]:
    """One evaluatorq result per run; outcome is pass, fail, error or skipped."""
    results = []
    for i, outcome in enumerate(outcomes, 1):
        output: dict[str, Any] = {"agent": agent, "run": i, "tool_calls": []}
        scores = []
        if outcome in ("pass", "fail"):
            scores = [SimpleNamespace(evaluator_name="skill_fired", score=SimpleNamespace(pass_=outcome == "pass", explanation=""))]
        elif outcome == "skipped":
            output = {"agent": agent, "run": i, "skipped": "cost cap reached"}
        job = SimpleNamespace(
            job_name=agent,
            output=output,
            error="cli.exit.1: boom" if outcome == "error" else None,
            evaluator_scores=scores,
        )
        results.append(SimpleNamespace(data_point=SimpleNamespace(inputs={"case": c.__dict__}), job_results=[job], error=None))
    return results


def aggregate_one(c: r.Case, outcomes: list[str]) -> dict[str, Any]:
    return r.aggregate(run_result(c, outcomes), [c])["cases"][0]


def scored_run(c: r.Case, calls: list[dict[str, Any]], agent: str = "claude", text: str = "") -> dict[str, Any]:
    """Run the real scorers on one run's calls and aggregate it, as evaluatorq would."""
    names = ("skill_fired", "tools_called", "asks_user", "no_forbidden_tools")
    scores = [
        SimpleNamespace(evaluator_name=n, score=score(getattr(r, n), c, calls, agent, text), error=None) for n in names
    ]
    job = SimpleNamespace(job_name=agent, output={"agent": agent, "run": 1, "tool_calls": calls, "text": text}, error=None, evaluator_scores=scores)
    dp = SimpleNamespace(data_point=SimpleNamespace(inputs={"case": c.__dict__}), job_results=[job], error=None)
    return r.aggregate([dp], [c])["cases"][0]["runs"][0]


def test_asks_first_needs_a_question_and_no_forbidden_attempt() -> None:
    c = case(expect_question_about=["ground"])
    fired = call("Skill", {"skill": "orq-build-evaluator"})
    assert scored_run(c, [fired])["status"] == "fail"
    assert scored_run(c, [fired], text="What counts as grounded?")["status"] == "pass"
    create = call(CLAUDE + "create_llm_eval", result="[denied by claude]", status="incomplete")
    run = scored_run(c, [fired, create], text="What counts as grounded?")
    assert run["status"] == "fail"
    assert run["scores"]["no_forbidden_tools"]["pass"] is False


def test_a_regression_outranks_a_server_error_in_the_same_run() -> None:
    c = case(expect_skill="any", expect_tools=["search_entities"], allow_tools=["search_entities"])
    server_error = call(CLAUDE + "search_entities", result="500 Internal Server Error", status="incomplete")
    create = call(CLAUDE + "create_llm_eval", {"key": "k"}, result="[denied by claude]", status="incomplete")
    assert scored_run(c, [server_error])["status"] == "error"
    run = scored_run(c, [server_error, create])
    assert run["status"] == "fail"
    assert "create_llm_eval" in run["scores"]["no_forbidden_tools"]["why"]


def test_aggregate_scorer_crash_and_no_verdict_are_errors() -> None:
    c = case(kind="invocation")
    crashed = SimpleNamespace(evaluator_name="skill_fired", score=SimpleNamespace(pass_=None, explanation=""), error="KeyError: x")
    job = SimpleNamespace(job_name="claude", output={"agent": "claude", "run": 1, "tool_calls": []}, error=None, evaluator_scores=[crashed])
    dp = SimpleNamespace(data_point=SimpleNamespace(inputs={"case": c.__dict__}), job_results=[job], error=None)
    run = r.aggregate([dp], [c])["cases"][0]["runs"][0]
    assert run["status"] == "error" and "KeyError" in run["error"]
    crashed.error = None
    assert r.aggregate([dp], [c])["cases"][0]["runs"][0]["status"] == "error"  # nothing scored is not a fail


def test_aggregate_threshold_partial_and_skipped() -> None:
    c = case(pass_threshold=0.66)
    assert aggregate_one(c, ["pass", "pass", "fail"])["status"] == "pass"
    assert aggregate_one(c, ["pass", "fail", "fail"])["status"] == "fail"
    assert aggregate_one(c, ["pass", "pass", "error"])["status"] == "error"  # a pass on part of the runs is not a pass
    assert aggregate_one(c, ["skipped", "skipped"])["status"] == "skipped"
    row = aggregate_one(c, ["pass", "pass", "fail"])
    assert row["flaky"] is True


def test_aggregate_borderline_errors_are_not_measured() -> None:
    c = case(borderline=True)
    assert aggregate_one(c, ["error", "error"])["status"] == "error"
    assert aggregate_one(c, ["skipped", "skipped"])["status"] == "skipped"
    assert aggregate_one(c, ["pass", "fail"])["status"] == "measured"


def test_an_expected_tool_that_errored_is_a_run_error_not_a_verdict() -> None:
    c = case(expect_tools=["list_models"], allow_tools=["list_models"])
    server_error = call(CLAUDE + "list_models", result="ECONNREFUSED", status="incomplete")
    assert r.errored_expected_tools("claude", c, [server_error]) == ["list_models"]
    assert r.errored_expected_tools("claude", c, [server_error, call(CLAUDE + "list_models")]) == []  # a retry worked
    assert r.errored_expected_tools("claude", c, []) == []  # never called: a real miss, scored by tools_called
    assert r.errored_expected_tools("claude", case(kind="invocation", expect_tools=["list_models"]), [server_error]) == []


def rows(*statuses: str) -> dict[str, Any]:
    return {"cases": [{"status": s} for s in statuses]}


def test_exit_code() -> None:
    budget = r.Budget(10)
    assert r.exit_code(rows("pass", "measured"), budget) == 0
    assert r.exit_code(rows("pass", "fail", "error"), budget) == 1
    assert r.exit_code(rows("pass", "error"), budget) == 2
    budget.breached = True
    assert r.exit_code(rows("pass"), budget) == 2


def test_budget_and_cost() -> None:
    budget = r.Budget(0.3)
    assert budget.admit()
    budget.add(r._cost_from_stdout("opencode", "c", '{"type": "x"}', budget))  # output but no cost: the flat charge
    budget.add(r._cost_from_stdout("claude", "c", "", budget))  # no output at all: nothing ran
    assert budget.spent == r.UNPRICED_RUN_COST_USD and budget.admit()
    budget.add(0.1)
    assert not budget.admit() and budget.breached


CLAUDE_MAX_TURNS = [
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Skill", "input": {"skill": "orq:orq-build-evaluator"}}]}, "session_id": "s1"},
    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "loaded"}]}, "session_id": "s1"},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t2", "name": CLAUDE + "list_models", "input": {}}]}, "session_id": "s1"},
    {"type": "result", "subtype": "error_max_turns", "total_cost_usd": 0.12, "session_id": "s1"},
]  # fmt: skip


def test_recover_max_turns() -> None:
    """Every invocation case stops at --max-turns; this also guards the evaluatorq parser the pin fixes."""
    stdout = "\n".join(json.dumps(e) for e in CLAUDE_MAX_TURNS)
    out = r._recover_max_turns("claude", stdout)
    assert out is not None and out["stopped"] == "max_turns" and out["cost_usd"] == 0.12
    assert r.fired_skills("claude", out["tool_calls"]) == ["orq-build-evaluator"]
    assert out["tool_calls"][1]["status"] == "in_progress"  # cut off before its result: not a completed call
    assert r._recover_max_turns("opencode", stdout) is None
    assert r._recover_max_turns("claude", stdout.replace("error_max_turns", "error_during_execution")) is None


# -- job retries -----------------------------------------------------------------


def run_job(monkeypatch: pytest.MonkeyPatch, effects: list[Any], budget: r.Budget | None = None) -> tuple[dict[str, Any], int]:
    """make_job's result when run_once returns (or raises, or computes) each effect in turn; plus the call count."""
    calls = 0

    async def fake_run_once(*_: Any) -> dict[str, Any]:
        nonlocal calls
        effect = effects[calls]
        calls += 1
        if callable(effect):
            effect = effect()
        if isinstance(effect, BaseException):
            raise effect
        return dict(effect)

    monkeypatch.setattr(r, "run_once", fake_run_once)
    job = r.make_job("claude", Path("."), "key", budget or r.Budget(10), None, None)
    data = DataPoint(inputs={"case": case().__dict__, "run": 1, "orq_skills": ORQ_SKILLS})
    return asyncio.run(job(data, 0)), calls


def failed_run(msg: str, retryable: bool = True, cost: float = 0.1) -> dict[str, Any]:
    return {"error": msg, "error_kind": "harness", "retryable": retryable, "launched": True, "cost_usd": cost}


def test_make_job_retries_once_then_reports_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    ok = {"tool_calls": [], "session_id": "s", "cost_usd": 0.1}
    result, calls = run_job(monkeypatch, [failed_run("a"), ok])
    assert calls == 2 and result["error"] is None and result["output"]["attempts"] == 2
    assert result["output"]["cost_usd"] == 0.2  # both attempts were billed
    result, calls = run_job(monkeypatch, [failed_run("a"), failed_run("b")])
    assert calls == 2 and result["error"] == "b" and "retryable" not in result["output"]
    result, calls = run_job(monkeypatch, [failed_run("slow", retryable=False)])
    assert calls == 1 and result["error"] == "slow"  # a re-run replays the same outcome


def test_make_job_keeps_the_agent_row_on_a_setup_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    result, calls = run_job(monkeypatch, [OSError("disk full")])
    assert calls == 1 and result["name"] == "claude" and "disk full" in result["error"]
    assert result["output"]["thread_id"] is None  # no session started


def test_make_job_cost_cap_after_an_error_keeps_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    budget = r.Budget(0.1)

    def spend_then_fail() -> dict[str, Any]:
        budget.add(1.0)  # the failed attempt used up the cap
        return failed_run("boom")

    result, calls = run_job(monkeypatch, [spend_then_fail], budget)
    assert calls == 1 and result["error"] == "boom"
    result, calls = run_job(monkeypatch, [], budget)
    assert calls == 0 and result["output"]["skipped"] == "cost cap reached"


def test_opencode_config_mirrors_the_claude_limits() -> None:
    c = case(allow_tools=["search_entities"], max_turns=3)
    with contextlib.ExitStack() as stack:
        target = r.build_target(stack, "opencode", c, r.REPO_ROOT, "key", "run-1")
        config = json.loads((target._source_workdir / "opencode.json").read_text(encoding="utf-8"))
    assert config["agent"]["build"]["steps"] == 3
    perm = config["permission"]
    assert perm["bash"] == "deny" and perm[r.OPENCODE_MCP_PREFIX + "*"] == "deny"
    assert perm[r.OPENCODE_MCP_PREFIX + "search_entities"] == "allow"
    assert perm[r.OPENCODE_MCP_PREFIX + "create_*"] == "deny"
    assert not target._source_workdir.exists()  # the stack removed the temp dirs


# -- case loading ----------------------------------------------------------------


def test_load_cases_rejects_duplicate_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for folder in ("orq-build-evaluator", "orq-run-experiment"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "fires.yaml").write_text(
            f"id: fires\nskill: {folder}\nkind: invocation\nprompt: p\nexpect_skill: {folder}\n", encoding="utf-8"
        )
    monkeypatch.setattr(r, "EVALS_DIR", tmp_path)
    monkeypatch.setattr(r, "REPO_ROOT", tmp_path)
    with pytest.raises(SystemExit, match="is also used by"):
        r.load_cases(set(ORQ_SKILLS))


@pytest.mark.parametrize(
    ("extra", "problem"),
    [
        ("kind: behavioural\nexpect_tools: [list_models]\nallow_tools: [search_entities]\n", "not in allow_tools"),
        ("kind: behavioural\nallow_tools: [create_dataset]\n", "match forbid_tools"),
        ("kind: invocation\nexpect_skill: any\n", "not 'any'"),
        ("kind: invocation\npass_threshold: '0.8'\n", "wrong type for ['pass_threshold']"),
        ("kind: invocation\nexpect_tools: list_models\n", "wrong type for ['expect_tools']"),
        ("kind: invocation\nruns: true\n", "wrong type for ['runs']"),
        ("kind: invocation\nmax_turns: 0\n", "runs and max_turns must be at least 1"),
        ("kind: invocation\npass_threshold: 1.1\n", "pass_threshold must be between 0 and 1"),
        ("kind: invocation\nexpect_question_about: [ground]\n", "expect_question_about needs a behavioural case"),
        ("kind: behavioural\nexpect_question_about: [42]\n", "expect_question_about must contain non-empty strings"),
        ("kind: invocation\nturns: [a]\n", "unknown field"),
    ],
)
def test_load_cases_rejects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: str, problem: str) -> None:
    (tmp_path / "orq-build-evaluator").mkdir()
    base = "id: c\nskill: orq-build-evaluator\nprompt: p\n"
    if "expect_skill" not in extra:
        base += "expect_skill: orq-build-evaluator\n"
    (tmp_path / "orq-build-evaluator" / "c.yaml").write_text(base + extra, encoding="utf-8")
    monkeypatch.setattr(r, "EVALS_DIR", tmp_path)
    monkeypatch.setattr(r, "REPO_ROOT", tmp_path)
    with pytest.raises(SystemExit, match=re.escape(problem)):
        r.load_cases(set(ORQ_SKILLS))


def test_the_shipped_cases_load() -> None:
    skills = {p.name for p in (r.REPO_ROOT / "skills").iterdir() if (p / "SKILL.md").exists()}
    assert r.load_cases(skills)


def test_ci_tests_against_the_evaluatorq_the_runner_pins() -> None:
    """run_evals.py patches evaluatorq internals, so CI must test the version the runner installs."""
    repo = Path(__file__).resolve().parents[2]
    pin = re.compile(r"evaluatorq==([\w.]+)")
    runner = pin.findall((repo / "tests/scripts/run_evals.py").read_text(encoding="utf-8"))
    ci = pin.findall((repo / ".github/workflows/skills-ci.yml").read_text(encoding="utf-8"))
    assert runner and ci and set(runner) == set(ci), f"run_evals.py pins {runner}, skills-ci.yml pins {ci}"


# -- merged report ---------------------------------------------------------------


def test_report_puts_a_skipped_eval_case_in_its_own_bucket() -> None:
    row = {"case": "c", "skill": "orq-build-evaluator", "agent": "claude", "status": "skipped", "flaky": False,
           "pass_rate": None, "threshold": 0.8, "errors": 0, "runs": []}  # fmt: skip
    findings = report.eval_findings({"cases": [row]})
    assert [f["bucket"] for f in findings] == ["skipped"]


def eval_row(status: str, flaky: bool = False, runs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"case": "c", "skill": "orq-build-evaluator", "agent": "claude", "status": status, "flaky": flaky,
            "pass_rate": 0.5, "threshold": 0.8, "errors": 0, "runs": runs or []}  # fmt: skip


def test_report_buckets_and_detail() -> None:
    forbidden = {"status": "fail", "error": None, "thread_id": "t",
                 "scores": {"no_forbidden_tools": {"pass": False, "why": 'create_llm_eval (denied) {"key": "k"}'}}}  # fmt: skip
    rows = [eval_row("fail", flaky=True, runs=[forbidden]), eval_row("error"), eval_row("measured", flaky=True)]
    findings = report.eval_findings({"cases": rows})
    assert [f["bucket"] for f in findings] == ["regression", "flaky", "error", "measured"]
    assert '{"key": "k"}' in findings[0]["detail"]  # the arguments of the forbidden attempt
    doc = {"skill": "orq-build-evaluator", "status": "failed", "test_type": "doc_url", "target": "https://x"}
    assert [f["bucket"] for f in report.factual_findings({"results": [doc]})] == ["advisory"]


@pytest.mark.parametrize(("status", "code"), [("fail", 1), ("error", 2), ("skipped", 2), ("pass", 0)])
def test_report_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str, code: int) -> None:
    evals = tmp_path / "evals.json"
    evals.write_text(json.dumps({"started": "now", "exit_code": 0, "cases": [eval_row(status)]}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["skill_test_report.py", "--evals", str(evals)])
    with pytest.raises(SystemExit) as exc:
        report.main()
    assert exc.value.code == code


def test_report_refuses_an_unfinished_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    evals = tmp_path / "evals.json"
    evals.write_text(json.dumps({"cases": []}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["skill_test_report.py", "--evals", str(evals)])
    with pytest.raises(SystemExit, match="not a finished"):
        report.main()


def test_check_key_used_stops_when_a_profile_overrides_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(stderr: str, returncode: int = 0, stdout: str = "") -> Any:
        return lambda *a, **kw: SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)

    monkeypatch.setattr(r.subprocess, "run", fake('warning: using the API key from profile "x", ignoring ORQ_API_KEY.'))
    with pytest.raises(SystemExit, match="orq auth profile clear"):
        r.check_key_used(Path("orq"), "k")
    monkeypatch.setattr(r.subprocess, "run", fake("", stdout="dry-run configuration"))
    r.check_key_used(Path("orq"), "k")
    monkeypatch.setattr(r.subprocess, "run", fake("", returncode=1))
    with pytest.raises(SystemExit, match="could not verify ORQ_API_KEY use"):
        r.check_key_used(Path("orq"), "k")
    monkeypatch.setattr(r.subprocess, "run", fake(""))
    with pytest.raises(SystemExit, match="produced no output"):
        r.check_key_used(Path("orq"), "k")
