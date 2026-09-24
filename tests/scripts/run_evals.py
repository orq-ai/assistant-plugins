#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# # Pinned exactly: the runner subclasses and patches CodingAgentTarget internals.
# dependencies = ["evaluatorq==1.47.0", "pyyaml"]
# ///
"""Invocation and behavioural evals for the orq skills (RES-1076).

Each case in tests/evals/<skill>/<case>.yaml is a prompt plus what should happen:
which skill fires, which orq tools get called, which must not. Every run starts a
fresh coding agent through `orq launch` (evaluatorq CodingAgentTarget) with this
branch loaded as the `orq` plugin and nothing else from the user's setup, scores
the tool calls with three local scorers, and uploads the results as one orq
experiment.

The agent runs with ORQ_SKILL_EVALS_KEY as its ORQ_API_KEY: a key scoped to the
`skill-evals` project, so a stray create cannot land anywhere else. The experiment
upload uses the same key: a key scoped to another project cannot write there.

Usage:
    uv run tests/scripts/run_evals.py                          # all cases, all agents
    uv run tests/scripts/run_evals.py --skill orq-build-evaluator --agent claude
    uv run tests/scripts/run_evals.py --case build-evaluator-asks-first --runs 1 --no-send

Exit code: 0 every case passed, 1 a case fell below its threshold, 2 errors only
or the cost cap was hit.
"""

from __future__ import annotations

import argparse
import asyncio
import fnmatch
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from evaluatorq import DataPoint, EvaluationResult, evaluatorq
from evaluatorq.backends import coding_agent
from evaluatorq.backends.coding_agent import (
    AgentName,
    CodingAgentError,
    CodingAgentTarget,
    CodingAgentUnavailableError,
    OrqLaunchOptions,
)
from evaluatorq.contracts import Message, TextOutputItem, ToolCallOutputItem
from evaluatorq.types import Evaluator, Job, ScorerParameter

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
# tests/evals/<skill>/ per skill; folders starting with _ hold cross-skill cases
# (_no-skill for prompts that must not fire any orq skill, _general for the rest).
EVALS_DIR = REPO_ROOT / "tests" / "evals"
RESULTS_DIR = REPO_ROOT / "tests" / "eval-results"
MIN_ORQ_VERSION = (10, 3, 1)
# Charged against --max-cost-usd for a run that reports no cost. OpenCode gets no
# pricing for the orq provider and reports 0; a Claude run costs about this much.
UNPRICED_RUN_COST_USD = 0.25
AGENTS: tuple[AgentName, ...] = ("claude", "opencode")
KINDS = ("invocation", "behavioural")

# Nothing is created, changed, deleted or invoked before the user confirms.
DEFAULT_FORBID = ["create_*", "update_*", "delete_*", "invoke_*"]
# Read-only built-ins every Claude run may use; everything else not allowlisted is denied.
CLAUDE_BUILTINS = ["Read", "Glob", "Grep", "Skill"]
CLAUDE_MCP_PREFIX = "mcp__plugin_orq_orq-workspace__"
OPENCODE_MCP_PREFIX = "orq-workspace_"
MCP_URL = "https://my.orq.ai/v2/mcp"


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


@dataclass
class Case:
    id: str
    skill: str
    kind: str
    prompt: str
    expect_skill: str
    expect_tools: list[str] = field(default_factory=list)
    forbid_tools: list[str] = field(default_factory=lambda: list(DEFAULT_FORBID))
    allow_tools: list[str] = field(default_factory=list)
    runs: int = 5
    pass_threshold: float = 0.8
    max_turns: int = 6
    agents: list[str] = field(default_factory=lambda: list(AGENTS))
    expect_tools_by_agent: dict[str, list[str]] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    turns: list[Any] = field(default_factory=list)

    @property
    def borderline(self) -> bool:
        return "borderline" in self.tags

    def expected_tools(self, agent: str) -> list[str]:
        return self.expect_tools_by_agent.get(agent, self.expect_tools)


def load_cases(skill_names: set[str]) -> list[Case]:
    """Every tests/evals/<skill>/*.yaml, validated. A malformed case stops the run."""
    cases: list[Case] = []
    problems: list[str] = []
    known = set(Case.__dataclass_fields__)
    for path in sorted(EVALS_DIR.glob("*/*.yaml")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        unknown = set(raw) - known
        if unknown:
            problems.append(f"{rel}: unknown field(s) {sorted(unknown)}")
            continue
        try:
            case = Case(**raw)
        except TypeError as exc:
            problems.append(f"{rel}: {exc}")
            continue
        if case.skill != path.parent.name:
            problems.append(f"{rel}: skill '{case.skill}' does not match its folder")
        if case.id != path.stem:
            problems.append(f"{rel}: id '{case.id}' does not match its file name")
        if case.kind not in KINDS:
            problems.append(f"{rel}: kind must be one of {KINDS}")
        if not case.skill.startswith("_") and case.skill not in skill_names:
            problems.append(f"{rel}: '{case.skill}' is not a skill in skills/ (cross-skill folders start with _)")
        if case.expect_skill not in ("none", "any") and case.expect_skill not in skill_names:
            problems.append(f"{rel}: expect_skill '{case.expect_skill}' is not a skill in skills/")
        if bad := set(case.agents) - set(AGENTS):
            problems.append(f"{rel}: unknown agent(s) {sorted(bad)}")
        # Claude has no deny layer on top of --allowedTools, so an allowed tool that is also
        # forbidden would really run there. Reject the overlap instead.
        if overlap := [t for t in case.allow_tools if any(fnmatch.fnmatchcase(t, p) for p in case.forbid_tools)]:
            problems.append(f"{rel}: allow_tools {overlap} match forbid_tools")
        if case.turns:
            problems.append(f"{rel}: multi-turn cases (turns:) are not supported yet")
        cases.append(case)
    if problems:
        sys.exit("invalid eval cases:\n  " + "\n  ".join(problems))
    return cases


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


def resolve_orq() -> Path:
    """Put the real orq binary first on PATH and return it.

    CodingAgentTarget runs bare `orq` through create_subprocess_exec, which does not
    resolve the npm `.cmd` shim on Windows (cli.not_found). The native exe ships
    inside the npm package.
    """
    if os.name == "nt":
        shim = shutil.which("orq")
        if shim is None:
            sys.exit("orq CLI not found on PATH; install it with `npm i -g @orq-ai/cli`")
        exe = next(Path(shim).parent.glob("node_modules/@orq-ai/cli/node_modules/@orq-ai/cli-win32-*/bin/orq.exe"), None)
        if exe is None:
            sys.exit(f"could not find the native orq.exe next to {shim}")
        os.environ["PATH"] = f"{exe.parent}{os.pathsep}{os.environ['PATH']}"
        return exe
    found = shutil.which("orq")
    if found is None:
        sys.exit("orq CLI not found on PATH")
    return Path(found)


def check_orq_version(orq: Path) -> str:
    out = subprocess.run([str(orq), "--version"], capture_output=True, text=True, timeout=30, check=False).stdout
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    version = m.group(0) if m else "unknown"
    if not m or tuple(int(p) for p in m.groups()) < MIN_ORQ_VERSION:
        print(
            f"warning: orq {version} is older than {'.'.join(map(str, MIN_ORQ_VERSION))}; "
            "run `orq update` first, a stale CLI gives false results",
            file=sys.stderr,
        )
    return version


def load_dotenv_key(name: str) -> str | None:
    """The variable from the environment, else from a gitignored .env at the repo root."""
    if os.environ.get(name):
        return os.environ[name]
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


# ---------------------------------------------------------------------------
# Agent targets
# ---------------------------------------------------------------------------

_original_render_prompt = coding_agent.render_prompt


def _render_single_turn_raw(messages: list[Message], *, system_prompt: str | None, inline_system: bool) -> str:
    """Send a single user message verbatim.

    CodingAgentTarget wraps every prompt, even one user turn, in a "You are
    continuing the conversation below" JSON block. Skill triggering depends on the
    exact wording a user types, so single-turn cases send the prompt as is.
    """
    if len(messages) == 1 and messages[0].role == "user" and not system_prompt and isinstance(messages[0].content, str):
        return messages[0].content
    return _original_render_prompt(messages, system_prompt=system_prompt, inline_system=inline_system)


coding_agent.render_prompt = _render_single_turn_raw

if os.name == "nt":
    # os.killpg does not exist on Windows; the upstream helper only reaches it on timeout or
    # cancel. The process is orq.exe, so kill its tree: the agent it launched keeps billing otherwise.
    def _kill_proc(proc: asyncio.subprocess.Process, *, force: bool = False) -> None:
        if proc.returncode is None or force:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False)

    coding_agent.kill_group = _kill_proc


class EvalTarget(CodingAgentTarget):
    """CodingAgentTarget that keeps the raw stdout of its last run.

    A run that stops at --max-turns is a normal outcome here (we only want the
    first actions), but Claude reports it as an error, orq exits 1, and respond()
    raises without the tool calls. The raw stdout lets the runner recover them,
    and carries the run cost, which AgentResponse does not.
    """

    last_stdout: str = ""

    async def _run(
        self, argv: list[str], stdin_text: str | None, *, cwd: Path, env: dict[str, str]
    ) -> tuple[int, str, str]:
        # OpenCode finds its project (opencode.json, .opencode/skills) from $PWD, not the
        # process cwd. Inherited, PWD names the directory the runner started in, so the
        # agent would load the caller's checkout and work in it instead of the private copy.
        env = {**env, "PWD": str(cwd)}
        returncode, stdout, stderr = await super()._run(argv, stdin_text, cwd=cwd, env=env)
        self.last_stdout = stdout
        return returncode, stdout, stderr


def remove_tree(path: Path) -> None:
    """rmtree that also clears Windows read-only files (git objects), and warns on what it leaves."""

    def clear_readonly(func: Any, target: str, _exc: BaseException) -> None:
        os.chmod(target, stat.S_IWRITE)
        func(target)

    try:
        shutil.rmtree(path, onexc=clear_readonly)
    except OSError as exc:
        print(f"warning: could not remove temp dir {path}: {exc}", file=sys.stderr)


def mcp_name(agent: AgentName, bare: str) -> str:
    return (CLAUDE_MCP_PREFIX if agent == "claude" else OPENCODE_MCP_PREFIX) + bare


def build_target(agent: AgentName, case: Case, branch: Path, key: str, run_id: str) -> tuple[EvalTarget, list[Path]]:
    """One isolated target per run, plus the temp dirs to remove afterwards."""
    temp = [Path(tempfile.mkdtemp(prefix=f"skill-evals-{agent}-"))]
    env = {"ORQ_API_KEY": key}
    if agent == "claude":
        allowed = CLAUDE_BUILTINS + [mcp_name("claude", t) for t in case.allow_tools]
        env |= {"CLAUDE_CONFIG_DIR": str(temp[0]), "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1"}
        target = EvalTarget(
            "claude",
            launcher="orq",
            orq=OrqLaunchOptions(mcp=False, skills=False),
            extra_args=[
                "--plugin-dir", str(branch),
                "--allowedTools", ",".join(allowed),
                "--max-turns", str(case.max_turns),
            ],
            env=env,
        )
        return target, temp

    # OpenCode: empty XDG dirs hide the user's config, skills and sessions.
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "OPENCODE_CONFIG_DIR"):
        d = temp[0] / var.lower()
        d.mkdir()
        env[var] = str(d)
    env |= {
        "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1",
        "OPENCODE_DISABLE_CLAUDE_CODE": "1",
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "OPENCODE_DISABLE_SHARE": "1",
    }
    workdir = Path(tempfile.mkdtemp(prefix="skill-evals-opencode-wd-"))
    temp.append(workdir)
    subprocess.run(["git", "init", "-q"], cwd=workdir, check=True)
    skills_dst = workdir / ".opencode" / "skills"
    skills_dst.mkdir(parents=True)
    for skill in (branch / "skills").iterdir():
        if (skill / "SKILL.md").exists():
            shutil.copytree(skill, skills_dst / skill.name)
    # --auto grants every permission, so anything not allowed is denied here instead.
    # Shell and file writes stay off as for Claude: there is no sandbox on Windows, and
    # an unscoped shell reaches orq through curl, around every MCP-level check.
    # The last matching rule wins, which mirrors Claude's --allowedTools: every orq tool is
    # denied and allow_tools are let back in (load_cases rejects an allow that is also
    # forbidden, so the forbid rules below only restate that). OpenCode
    # hides a denied tool from the model, so unlike Claude a forbidden call is never attempted.
    permission = {"bash": "deny", "edit": "deny", "write": "deny", "webfetch": "deny", mcp_name("opencode", "*"): "deny"}
    permission |= {mcp_name("opencode", t): "allow" for t in case.allow_tools}
    permission |= {mcp_name("opencode", p): "deny" for p in case.forbid_tools}
    config = {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {
            "orq-workspace": {
                "type": "remote",
                "url": MCP_URL,
                "headers": {"Authorization": "Bearer {env:ORQ_API_KEY}"},
            }
        },
        "permission": permission,
        # orq launch defines both providers (chat completions and Responses) and picks one.
        "provider": {p: {"options": {"headers": {"X-ORQ-THREAD-ID": run_id}}} for p in ("orq", "orq-openai")},
    }
    (workdir / "opencode.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    target = EvalTarget(
        "opencode",
        launcher="orq",
        orq=OrqLaunchOptions(mcp=False, skills=False),  # only the branch copy and opencode.json's server
        extra_args=["--auto"],
        workdir=workdir,
        env=env,
    )
    return target, temp


# ---------------------------------------------------------------------------
# Running one case
# ---------------------------------------------------------------------------


def _call_dict(call: ToolCallOutputItem) -> dict[str, Any]:
    try:
        arguments = json.loads(call.arguments)
    except (TypeError, json.JSONDecodeError):
        arguments = call.arguments
    status = getattr(call.status, "value", call.status)
    return {"name": call.name, "arguments": arguments, "result": (call.result or "")[:500], "status": str(status)}


def _recover_max_turns(agent: AgentName, stdout: str) -> dict[str, Any] | None:
    """Tool calls from a Claude run that stopped at --max-turns; None for any other failure."""
    events = coding_agent.parse_jsonl(stdout)
    result = next((e for e in reversed(events) if e.get("type") == "result"), None)
    if agent != "claude" or not result or result.get("subtype") != "error_max_turns":
        return None
    turn = coding_agent.parse_events(agent, events)
    return {
        "tool_calls": [_call_dict(c) for c in turn.tool_calls],
        "text": turn.text or "",
        "session_id": turn.session_id,
        "cost_usd": turn.cost_usd,
        "stopped": "max_turns",
    }


def _cost_from_stdout(agent: AgentName, stdout: str) -> tuple[float, bool]:
    """(cost, estimated): the agent's own figure, else the flat estimate so the cap still binds."""
    try:
        cost = coding_agent.parse_events(agent, coding_agent.parse_jsonl(stdout)).cost_usd
    except Exception:  # noqa: BLE001 -- cost is best effort; a parse failure must not fail the run
        cost = None
    if not cost and stdout.strip():
        return UNPRICED_RUN_COST_USD, True
    return cost or 0.0, False


class Budget:
    def __init__(self, cap: float) -> None:
        self.cap = cap
        self.spent = 0.0
        self.breached = False
        self._lock = asyncio.Lock()

    async def admit(self) -> bool:
        async with self._lock:
            if self.spent >= self.cap:
                self.breached = True
                return False
            return True

    async def add(self, cost: float | None) -> None:
        async with self._lock:
            self.spent += cost or 0.0


async def run_once(agent: AgentName, case: Case, branch: Path, key: str, run_id: str, budget: Budget) -> dict[str, Any]:
    target, temp = build_target(agent, case, branch, key, run_id)
    try:
        try:
            response = await target.respond([Message(role="user", content=case.prompt)])
        except CodingAgentError as exc:
            recovered = _recover_max_turns(agent, target.last_stdout)
            # A timeout leaves no stdout but the agent ran (and billed) until it was killed.
            timed_out = exc.code == "cli.timeout"
            await budget.add(UNPRICED_RUN_COST_USD if timed_out else _cost_from_stdout(agent, target.last_stdout)[0])
            if recovered is None:
                raise
            return recovered
        cost, estimated = _cost_from_stdout(agent, target.last_stdout)
        await budget.add(cost)
        return {
            "tool_calls": [_call_dict(o) for o in response.output if isinstance(o, ToolCallOutputItem)],
            "text": next((o.text for o in response.output if isinstance(o, TextOutputItem)), ""),
            "session_id": response.response_id,
            "cost_usd": cost,
            "cost_estimated": estimated,
            "stopped": "done",
        }
    finally:
        await target.close()
        for d in temp:
            remove_tree(d)


def make_job(agent: AgentName, branch: Path, key: str, budget: Budget) -> Job:
    async def run(data: DataPoint, row: int) -> dict[str, Any]:
        case = Case(**data.inputs["case"])
        base = {"agent": agent, "case_id": case.id, "run": data.inputs["run"]}
        if agent not in case.agents:
            return {"name": agent, "output": base | {"skipped": "agent not enabled for this case"}, "error": None}
        run_id = f"skill-evals-{case.id}-{agent}-{data.inputs['run']}-{int(time.time())}"
        last_error = ""
        for attempt in (1, 2):  # an errored run is re-run once, then reported as an error
            if not await budget.admit():
                return {"name": agent, "output": base | {"skipped": "cost cap reached"}, "error": None}
            try:
                out = await run_once(agent, case, branch, key, run_id, budget)
                out |= base | {"attempts": attempt, "thread_id": out.get("session_id") if agent == "claude" else run_id}
                return {"name": agent, "output": out, "error": None}
            except CodingAgentError as exc:
                last_error = f"{exc.code}: {exc.message[-300:]}"  # the tail holds the actual failure
                if isinstance(exc, CodingAgentUnavailableError):
                    break  # not found, timeout, prompt too long: a re-run replays the same outcome
        return {"name": agent, "output": base | {"error": last_error}, "error": last_error}

    # Not wrapped in evaluatorq's job(): that would nest our JobReturn inside `output`,
    # and a handled error has to reach evaluatorq as the top-level `error`.
    return run


# ---------------------------------------------------------------------------
# Scorers
# ---------------------------------------------------------------------------


def bare_tool(agent: str, name: str) -> str | None:
    """The orq MCP tool name without its per-agent prefix; None for non-orq tools."""
    for prefix in (CLAUDE_MCP_PREFIX, OPENCODE_MCP_PREFIX):
        if name.startswith(prefix):
            return name[len(prefix):]
    return None


def fired_skills(agent: str, calls: list[dict[str, Any]]) -> list[str]:
    fired: list[str] = []
    for c in calls:
        args = c["arguments"] if isinstance(c["arguments"], dict) else {}
        if agent == "claude" and c["name"] == "Skill":
            name = str(args.get("skill", ""))
        elif agent == "opencode" and c["name"] == "skill":
            name = str(args.get("name", ""))
        else:
            continue
        fired.append(name.removeprefix("orq:"))
    return fired


def is_denied(call: dict[str, Any]) -> bool:
    result = (call.get("result") or "").lower()
    return result == "[denied by claude]" or ("permission" in result and ("denied" in result or "rejected" in result))


def _verdict(value: str | bool, ok: bool | None, why: str) -> EvaluationResult:
    # `pass` is a keyword, so the field is set through its alias.
    return EvaluationResult.model_validate({"value": value, "pass": ok, "explanation": why})


def _skip(reason: str) -> EvaluationResult:
    return _verdict("n/a", None, reason)


def _usable(params: ScorerParameter) -> tuple[Case, dict[str, Any]] | None:
    output = params["output"]
    if not isinstance(output, dict) or "tool_calls" not in output:
        return None
    return Case(**params["data"].inputs["case"]), output


async def skill_fired(params: ScorerParameter) -> EvaluationResult:
    usable = _usable(params)
    if usable is None:
        return _skip("no run output")
    case, out = usable
    orq_skills = set(params["data"].inputs["orq_skills"])
    fired = fired_skills(out["agent"], out["tool_calls"])
    fired_orq = [s for s in fired if s in orq_skills]
    if case.expect_skill == "any":
        return _skip(f"any skill may fire; fired {fired or 'nothing'}")
    if case.expect_skill == "none":
        ok = not fired_orq
    else:
        ok = bool(fired_orq) and fired_orq[0] == case.expect_skill
    return _verdict(ok, ok, f"expected {case.expect_skill}; fired {fired or 'nothing'}")


async def tools_called(params: ScorerParameter) -> EvaluationResult:
    usable = _usable(params)
    if usable is None:
        return _skip("no run output")
    case, out = usable
    if case.kind != "behavioural":
        return _skip("invocation case")
    called = {bare_tool(out["agent"], c["name"]) for c in out["tool_calls"]}
    missing = [t for t in case.expected_tools(out["agent"]) if t not in called]
    return _verdict(not missing, not missing, f"missing {missing}" if missing else "all expected tools called")


async def no_forbidden_tools(params: ScorerParameter) -> EvaluationResult:
    usable = _usable(params)
    if usable is None:
        return _skip("no run output")
    case, out = usable
    if case.kind != "behavioural":
        return _skip("invocation case")
    problems: list[str] = []
    for c in out["tool_calls"]:
        bare = bare_tool(out["agent"], c["name"])
        label = bare or c["name"]
        denied = is_denied(c)
        if any(fnmatch.fnmatchcase(label, p) for p in case.forbid_tools):
            # The attempt is the regression, denied or not; its arguments show what it tried.
            problems.append(f"{label}{' (denied)' if denied else ''} {json.dumps(c['arguments'])[:300]}")
        elif bare and bare not in case.allow_tools and not denied:
            problems.append(f"{label} ran outside allow_tools")
    return _verdict(not problems, not problems, "; ".join(problems) or "none")


SCORERS: list[Evaluator] = [
    {"name": "skill_fired", "scorer": skill_fired},
    {"name": "tools_called", "scorer": tools_called},
    {"name": "no_forbidden_tools", "scorer": no_forbidden_tools},
]


# ---------------------------------------------------------------------------
# Aggregation and reporting
# ---------------------------------------------------------------------------


def aggregate(results: list[Any], cases: list[Case]) -> dict[str, Any]:
    by_case = {c.id: c for c in cases}
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for dp in results:
        case_id = dp.data_point.inputs["case"]["id"]
        for jr in dp.job_results or []:
            out = jr.output if isinstance(jr.output, dict) else {}
            if out.get("skipped") == "agent not enabled for this case":
                continue
            scores = {s.evaluator_name: s.score.pass_ for s in jr.evaluator_scores or []}
            verdicts = [v for v in scores.values() if v is not None]
            if out.get("skipped"):
                status = "skipped"
            elif jr.error or out.get("error") or dp.error:
                status = "error"
            else:
                status = "pass" if verdicts and all(verdicts) else "fail"
            groups.setdefault((case_id, jr.job_name), []).append(
                {
                    "run": out.get("run"),
                    "status": status,
                    "scores": {s.evaluator_name: {"pass": s.score.pass_, "why": s.score.explanation} for s in jr.evaluator_scores or []},
                    "error": jr.error or out.get("error") or dp.error,
                    "thread_id": out.get("thread_id"),
                    "cost_usd": out.get("cost_usd"),
                    "stopped": out.get("stopped"),
                    "tool_calls": [f"{c['name']}{' (denied)' if is_denied(c) else ''}" for c in out.get("tool_calls", [])],
                    "tool_call_details": [
                        {"name": c["name"], "denied": is_denied(c), "arguments": json.dumps(c["arguments"])[:400]}
                        for c in out.get("tool_calls", [])
                    ],
                }
            )

    case_rows: list[dict[str, Any]] = []
    for (case_id, agent), runs in sorted(groups.items()):
        case = by_case[case_id]
        scored = [r for r in runs if r["status"] in ("pass", "fail")]
        passes = sum(r["status"] == "pass" for r in scored)
        rate = passes / len(scored) if scored else None
        if case.borderline:
            status = "measured"
        elif rate is None:
            status = "error" if any(r["status"] == "error" for r in runs) else "skipped"
        else:
            status = "pass" if rate >= case.pass_threshold else "fail"
            if status == "pass" and len(scored) < len(runs):
                # Errored or cost-capped runs are not in the rate; a pass on part of the runs is not a pass.
                status = "error"
        case_rows.append(
            {
                "case": case_id,
                "skill": case.skill,
                "kind": case.kind,
                "agent": agent,
                "status": status,
                "pass_rate": rate,
                "threshold": case.pass_threshold,
                "flaky": rate is not None and 0 < rate < 1,
                "errors": sum(r["status"] == "error" for r in runs),
                "cost_usd": round(sum(r["cost_usd"] or 0 for r in runs), 4),
                "runs": runs,
            }
        )

    skills: dict[str, dict[str, Any]] = {}
    for row in case_rows:
        s = skills.setdefault(row["skill"], {"invocation": [], "behavioural": [], "flaky": [], "errors": 0, "cost_usd": 0.0})
        if row["status"] in ("pass", "fail"):
            s[row["kind"]].append(row["status"] == "pass")
        if row["flaky"]:
            s["flaky"].append(f"{row['case']} ({row['agent']})")
        s["errors"] += row["errors"]
        s["cost_usd"] = round(s["cost_usd"] + row["cost_usd"], 4)
    for s in skills.values():
        for kind in KINDS:
            verdicts = s[kind]
            s[kind] = f"{sum(verdicts)}/{len(verdicts)}" if verdicts else "-"
    return {"cases": case_rows, "skills": skills}


def print_report(report: dict[str, Any]) -> None:
    icons = {"pass": "+", "fail": "x", "error": "!", "skipped": "o", "measured": "~"}
    current = None
    for row in report["cases"]:
        if row["skill"] != current:
            current = row["skill"]
            print(f"\n{current}")
        rate = "-" if row["pass_rate"] is None else f"{row['pass_rate']:.0%}"
        flaky = " flaky" if row["flaky"] else ""
        print(f"  [{icons[row['status']]}] {row['case']} [{row['agent']}] {rate} (need {row['threshold']:.0%}){flaky}  ${row['cost_usd']:.2f}")
        if row["status"] in ("fail", "error", "measured"):
            for r in row["runs"]:
                failing = {k: v["why"] for k, v in r["scores"].items() if v["pass"] is False}
                detail = r["error"] or failing or ", ".join(r["tool_calls"])
                print(f"      run {r['run']}: {r['status']}  {detail}  thread={r['thread_id']}")
    print("\nper skill:")
    for skill, s in sorted(report["skills"].items()):
        print(f"  {skill}: invocation {s['invocation']}, behavioural {s['behavioural']}, errors {s['errors']}, ${s['cost_usd']:.2f}"
              + (f", flaky: {', '.join(s['flaky'])}" if s["flaky"] else ""))


# Everything an agent could create through the MCP server; experiments are left out
# because the runner's own upload is one.
ENTITY_TYPES = ("dataset", "prompt", "agent", "knowledge", "memory_store", "deployment", "evaluator", "skill")


def report_project_entities(key: str, since: datetime) -> dict[str, Any] | None:
    """What the agents' key can see in skill-evals: totals per type, and what appeared during this batch.

    search_entities keeps showing a deleted dataset for a while, so this is a
    pointer for a human, not a verdict. It is advisory: failures only warn.
    """
    sys.path.insert(0, str(Path(__file__).parent))
    from run_factual_tests import MCPClient  # sibling script, not a package

    client = MCPClient(MCP_URL, key)
    totals: dict[str, int] = {}
    new: list[str] = []
    for entity_type in ENTITY_TYPES:
        try:
            result = client.call_tool("search_entities", {"type": entity_type, "limit": 100})
        except Exception as exc:  # noqa: BLE001 -- the entity listing is advisory
            print(f"warning: could not list skill-evals {entity_type}s: {exc}", file=sys.stderr)
            return None
        rows = (result.get("structuredContent") or {}).get("data") or []
        totals[entity_type] = len(rows)
        for row in rows:
            try:
                created = datetime.fromisoformat(str(row.get("created_at")).replace("Z", "+00:00"))
            except ValueError:
                continue
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created >= since:
                new.append(f"{entity_type} {row.get('key') or row.get('display_name')} ({row.get('_id')})")
    return {"totals": {t: n for t, n in totals.items() if n}, "created_during_batch": new}


def exit_code(report: dict[str, Any], budget: Budget) -> int:
    statuses = {row["status"] for row in report["cases"]}
    if "fail" in statuses:
        return 1
    if "error" in statuses or budget.breached:
        return 2
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def amain() -> int:
    parser = argparse.ArgumentParser(description="Run invocation and behavioural evals for the orq skills")
    parser.add_argument("--skill", action="append", help="Only this skill (repeatable)")
    parser.add_argument("--case", action="append", help="Only this case id (repeatable)")
    parser.add_argument("--agent", action="append", choices=AGENTS, help="Only this agent (repeatable)")
    parser.add_argument("--runs", type=int, help="Override runs per case")
    parser.add_argument("--parallel", type=int, default=2, help="Concurrent agent runs (each is a full agent process)")
    parser.add_argument("--no-send", action="store_true", help="Do not upload the experiment to orq")
    parser.add_argument("--json", dest="json_path", type=Path, help="Write the summary here (default tests/eval-results/<timestamp>.json)")
    parser.add_argument("--branch", type=Path, default=REPO_ROOT, help="Plugin root under test (default: this checkout)")
    parser.add_argument("--max-cost-usd", type=float, default=10.0, help="Stop launching runs once this much is spent")
    parser.add_argument("--list", action="store_true", help="List the selected cases and the run count, then exit")
    args = parser.parse_args()

    branch = args.branch.resolve()
    skill_names = {p.name for p in (branch / "skills").iterdir() if (p / "SKILL.md").exists()}
    cases = load_cases(skill_names)
    if args.skill:
        cases = [c for c in cases if c.skill in args.skill]
    if args.case:
        cases = [c for c in cases if c.id in args.case]
    agents: list[AgentName] = args.agent or list(AGENTS)
    if args.runs:
        for c in cases:
            c.runs = args.runs
    if not cases:
        print("no eval cases selected", file=sys.stderr)
        return 2
    total_runs = sum(c.runs * len(set(c.agents) & set(agents)) for c in cases)
    print(f"{len(cases)} case(s), {total_runs} agent run(s), agents {agents}, cap ${args.max_cost_usd:.2f}")
    if args.list:
        for c in cases:
            print(f"  {c.skill}/{c.id} [{c.kind}] x{c.runs} agents={sorted(set(c.agents) & set(agents))}")
        return 0

    key = load_dotenv_key("ORQ_SKILL_EVALS_KEY")
    if not key:
        sys.exit("ORQ_SKILL_EVALS_KEY is not set: agent runs must use the skill-evals project key, refusing to start")
    orq = resolve_orq()
    orq_version = check_orq_version(orq)

    data = [
        DataPoint(inputs={"case": c.__dict__, "run": i + 1, "orq_skills": sorted(skill_names), "prompt": c.prompt})
        for c in cases
        for i in range(c.runs)
    ]
    budget = Budget(args.max_cost_usd)
    jobs: list[Job] = [make_job(a, branch, key, budget) for a in agents]

    started = datetime.now(timezone.utc)
    # Upload later, from disk and in a child process: evaluatorq uploads at the very end,
    # and on Windows its default-SSL httpx client can abort the whole process
    # (OPENSSL_Applink), which would take every result of a paid batch with it.
    results = await evaluatorq(
        "skill-evals",
        data=data,
        jobs=jobs,
        evaluators=SCORERS,
        datapoint_parallelism=max(1, args.parallel // len(agents)) if len(agents) > 1 else args.parallel,
        print_results=False,
        _send_results=False,
    )
    ended = datetime.now(timezone.utc)

    out_path = args.json_path or RESULTS_DIR / f"{started.strftime('%Y%m%dT%H%M%SZ')}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path = out_path.with_suffix(".results.json")
    raw_path.write_text(
        json.dumps(
            {
                "started": started.isoformat(),
                "ended": ended.isoformat(),
                "description": f"invocation + behavioural evals, branch {branch.name}, agents {agents}",
                "results": [r.model_dump(mode="json") for r in results],
            }
        ),
        encoding="utf-8",
    )

    report = aggregate(results, cases)
    print_report(report)
    entities = report_project_entities(key, started)
    if entities:
        print(f"\nskill-evals project: {entities['totals'] or 'empty'} (search index may lag deletes)")
        for line in entities["created_during_batch"]:
            print(f"  created during this batch: {line}")

    code = exit_code(report, budget)
    summary = {
        "started": started.isoformat(),
        "branch": str(branch),
        "orq_version": orq_version,
        "agents": agents,
        "experiment_url": None,
        "results_file": str(raw_path),
        "cost_usd": round(budget.spent, 4),
        "cost_cap_hit": budget.breached,
        "exit_code": code,
        "skill_evals_project": entities,
        **report,
    }
    out_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\nspent ${budget.spent:.2f}{' (cost cap hit, later runs skipped)' if budget.breached else ''}; summary: {out_path}")

    if not args.no_send:
        child = await asyncio.create_subprocess_exec(
            sys.executable, __file__, "--upload", str(raw_path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await child.communicate()
        stdout, stderr = stdout_b.decode(errors="replace").strip(), stderr_b.decode(errors="replace").strip()
        url = stdout.splitlines()[-1] if child.returncode == 0 and stdout else None
        if url:
            summary["experiment_url"] = url
            out_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
            print(f"experiment: {url}")
        else:
            print(
                f"warning: experiment upload failed ({stderr[-300:] or f'exit {child.returncode}'}); "
                f"results are saved, retry with: uv run tests/scripts/run_evals.py --upload {raw_path}",
                file=sys.stderr,
            )
    return code


async def upload_results(raw_path: Path) -> str:
    """Upload a saved batch as one orq experiment in project skill-evals; returns its URL."""
    # Only the upload child needs these.
    import httpx
    from evaluatorq import send_results
    from evaluatorq.types import DataPointResult

    if os.environ.get("SSL_VERIFY", "1") == "0":
        # Same opt-in as the factual runner, for TLS-intercepting antivirus on Windows.
        client = httpx.AsyncClient
        httpx.AsyncClient = lambda **kw: client(verify=False, **kw)  # type: ignore[assignment]
    key = load_dotenv_key("ORQ_SKILL_EVALS_KEY")
    if not key:
        sys.exit("ORQ_SKILL_EVALS_KEY is not set")
    saved = json.loads(raw_path.read_text(encoding="utf-8"))
    response = await send_results.send_results_to_orq(
        key,
        "skill-evals",
        saved["description"],
        None,
        [DataPointResult.model_validate(r) for r in saved["results"]],
        datetime.fromisoformat(saved["started"]),
        datetime.fromisoformat(saved["ended"]),
        path="skill-evals",
        raise_on_error=True,
    )
    if response is None or not response.experiment_url:
        sys.exit("upload returned no experiment URL")
    return response.experiment_url


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--upload":
        print(asyncio.run(upload_results(Path(sys.argv[2]))))
        return
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
