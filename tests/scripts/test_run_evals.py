"""Scoring and aggregation in run_evals.py and skill_test_report.py (RES-1076).

Pure functions on hand-built run outputs: no agent, no network. These decide every
verdict the eval suite reports, so a wrong branch here mis-scores every run.
Run: uv run --no-project --with evaluatorq==1.47.0 --with pyyaml --with pytest python -m pytest tests/scripts/test_run_evals.py -q
"""

from __future__ import annotations

import asyncio
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


def score(scorer: Any, c: r.Case, calls: list[dict[str, Any]], agent: str = "claude") -> Any:
    data = DataPoint(inputs={"case": c.__dict__, "run": 1, "orq_skills": ORQ_SKILLS})
    return asyncio.run(scorer({"data": data, "output": {"agent": agent, "tool_calls": calls}}))


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
    errored = score(r.tools_called, c, [call(CLAUDE + "list_models", status="incomplete")])
    assert errored.pass_ is False
    assert "called but errored" in errored.explanation
    assert score(r.tools_called, case(kind="invocation"), []).pass_ is None


def test_no_forbidden_tools() -> None:
    c = case(allow_tools=["search_entities"])
    assert score(r.no_forbidden_tools, c, [call(CLAUDE + "search_entities")]).pass_ is True
    denied = call(CLAUDE + "create_llm_eval", {"key": "k"}, result="[denied by claude]", status="incomplete")
    assert score(r.no_forbidden_tools, c, [denied]).pass_ is False  # the attempt is the regression
    assert score(r.no_forbidden_tools, c, [call(CLAUDE + "list_models")]).pass_ is False  # outside allow_tools
    denied_read = call(CLAUDE + "list_models", result="[denied by claude]", status="incomplete")
    assert score(r.no_forbidden_tools, c, [denied_read]).pass_ is True


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


def test_aggregate_threshold_partial_and_skipped() -> None:
    c = case(pass_threshold=0.66)
    assert aggregate_one(c, ["pass", "pass", "fail"])["status"] == "pass"
    assert aggregate_one(c, ["pass", "fail", "fail"])["status"] == "fail"
    assert aggregate_one(c, ["pass", "pass", "error"])["status"] == "error"  # a pass on part of the runs is not a pass
    assert aggregate_one(c, ["skipped", "skipped"])["status"] == "skipped"
    row = aggregate_one(c, ["pass", "pass", "fail"])
    assert row["flaky"] is True


def test_aggregate_borderline_errors_are_not_measured() -> None:
    c = case(tags=["borderline"])
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


def test_exit_code() -> None:
    budget = r.Budget(10)
    rows = lambda *statuses: {"cases": [{"status": s} for s in statuses]}
    assert r.exit_code(rows("pass", "measured"), budget) == 0
    assert r.exit_code(rows("pass", "fail", "error"), budget) == 1
    assert r.exit_code(rows("pass", "error"), budget) == 2
    budget.breached = True
    assert r.exit_code(rows("pass"), budget) == 2


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
