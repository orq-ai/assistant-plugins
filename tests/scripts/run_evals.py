#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# # Pinned exactly: the runner subclasses and patches CodingAgentTarget internals.
# dependencies = ["evaluatorq==1.47.1", "pyyaml"]
# ///
"""Invocation and behavioural evals for the orq skills (RES-1076).

Each case in tests/evals/<skill>/<case>.yaml is a prompt plus what should happen:
which skill fires, which orq tools get called, which must not. Every run starts a
fresh coding agent through `orq launch` (evaluatorq CodingAgentTarget) with this
branch loaded as the `orq` plugin and nothing else from the user's setup, scores
the tool calls with four local scorers, and uploads the results as one orq
experiment.

The agent runs with the caller's ORQ_API_KEY. A run can call only its case's
allow_tools, so it writes to the workspace only if a case allows a write tool.
The experiment upload goes to the `skill-evals` project with the same key.

Usage:
    uv run tests/scripts/run_evals.py --container                          # all cases, all agents
    uv run tests/scripts/run_evals.py --container --skill orq-build-evaluator --agent claude
    uv run tests/scripts/run_evals.py --container --case build-evaluator-asks-first --runs 1 --no-send
    uv run tests/scripts/run_evals.py --unsafe-host --case build-evaluator-fires --runs 1

Agent runs require a container unless --unsafe-host explicitly permits the agent to
read host files. The MCP allowlist does not restrict local Read/Glob/Grep tools.

Exit code: 0 every case passed, 1 a case fell below its threshold, 2 anything
else: run errors, the cost cap stopped runs, or the runner could not start (bad
case, missing key, CLI or image). On 2 before any run, no summary is written.

Summary (--json) fields: cost_usd (traced where orq had the run), cost_estimated_usd
(what the cap counted), cost_traced_runs, cost_estimated_runs, sessions, retries,
models (per agent: model, and source = flag or orq launch default),
runs_skipped_by_cap, exit_code, experiment_url or upload_error, results_file,
orq_version, agents, skills (per-skill counts), and cases. Each case: case, skill,
kind, agent, status (pass, fail, error, skipped, measured), pass_rate, threshold,
flaky, errors, errors_by_kind, cost_usd, runs. Each run: run, status, scores (per
scorer: pass, why), error, error_kind (model, harness, timeout, tool), thread_id,
attempts, cost_usd, cost_source (orq traces or estimate), stopped (done, max_turns,
error), tool_calls. A case with any errored or cost-capped run is error, not pass.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from evaluatorq import DataPoint, EvaluationResult, evaluatorq
from evaluatorq.backends import DockerOptions, coding_agent
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
# Charged per run when the model has no price in the workspace catalogue (see run_estimates).
# The summary replaces it with the traced cost afterwards.
UNPRICED_RUN_COST_USD = 0.25
# What one model call sends, per agent: the system prompt, tool definitions and skill list
# dominate, so it barely moves with the case. Upper figures read off skill-evals traces on
# 2026-09-30: Claude Code sends ~80k prompt tokens a call on Sonnet (~45k elsewhere), OpenCode
# 7-17k. `cached` is the share that was a cache hit where the provider cached (Anthropic,
# OpenAI); DeepSeek and Gemini runs had next to none, so the upper bound assumes no cache.
CALL_TOKENS: dict[str, dict[str, float]] = {
    "claude": {"prompt": 80_000, "cached": 0.8, "output": 1_500},
    "opencode": {"prompt": 18_000, "cached": 0.6, "output": 500},
}
# orq launch prints this whenever the key differs from the `orq auth login` workspace. It is
# harmless for these runs; left in, it hides the real failure.
KEY_NOTE = re.compile(r"Note: ORQ_API_KEY may not belong to the workspace[^\n]*\n?")
# Model errors a second attempt can clear. Any other model error (empty_response, a
# refused request) replays on the same model and prompt, so it is not retried.
TRANSIENT_MODEL_ERROR = re.compile(r"rate.?limit|\b429\b|\b5\d\d\b|overloaded|temporarily|unavailable", re.IGNORECASE)
# Failures before any agent session started: there is no trace to open.
NO_SESSION_CODES = {"cli.not_found", "cli.prompt_too_long", "cli.agent_not_found", "cli.image_missing", "cli.container_start"}
# At its `steps` cap OpenCode appends a "MAXIMUM STEPS REACHED" notice as an assistant message,
# so the request ends on an assistant turn. Gemini rejects that with this error: the run did
# reach its cap, like Claude's error_max_turns, and what it called before is scored. The match
# is on the text alone: OpenCode ends a request on an assistant turn only at the cap.
OPENCODE_STEP_CAP_REJECTED = re.compile(r"Requests ending with a model turn are not supported")
# Model tiers too small to follow the tool-calling rules; a run on one measures the model, not the skill.
WEAK_MODEL = re.compile(r"(?:^|[-/._])(?:lite|nano)(?:$|[-/._])", re.IGNORECASE)
AGENTS: tuple[AgentName, ...] = ("claude", "opencode")
KINDS = ("invocation", "behavioural")

# Nothing is created, changed, deleted or invoked before the user confirms.
DEFAULT_FORBID = ["create_*", "update_*", "delete_*", "invoke_*"]
# Read-only built-ins every Claude run may use; everything else not allowlisted is denied.
CLAUDE_BUILTINS = ["Read", "Glob", "Grep", "Skill"]
CLAUDE_MCP_PREFIX = "mcp__plugin_orq_orq-workspace__"
OPENCODE_MCP_PREFIX = "orq-workspace_"
MCP_URL = "https://my.orq.ai/v2/mcp"
# What a container run does not need from the plugin checkout it copies in. `.claude` holds this
# repo's maintainer skills, which must not load next to the plugin under test.
PLUGIN_COPY_IGNORE = shutil.ignore_patterns(".git", ".venv", ".ruff_cache", "node_modules", "__pycache__", ".claude", "tests", "docs")


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
    expect_question_about: list[str] = field(default_factory=list)
    forbid_tools: list[str] = field(default_factory=lambda: list(DEFAULT_FORBID))
    allow_tools: list[str] = field(default_factory=list)
    runs: int = 5
    pass_threshold: float = 0.8
    # Claude --max-turns; OpenCode agent `steps` (after which it may only answer in text).
    max_turns: int = 6
    # Excluded from pass/fail; reported as a trigger rate.
    borderline: bool = False


CASE_TYPES: dict[str, type | tuple[type, ...]] = {
    "id": str, "skill": str, "kind": str, "prompt": str, "expect_skill": str,
    "expect_tools": list, "expect_question_about": list, "forbid_tools": list, "allow_tools": list,
    "runs": int, "pass_threshold": (int, float), "max_turns": int, "borderline": bool,
}  # fmt: skip

def load_cases(skill_names: set[str]) -> list[Case]:
    """Every tests/evals/<skill>/*.yaml, validated. A malformed case stops the run."""
    cases: list[Case] = []
    problems: list[str] = []
    seen: dict[str, str] = {}
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
        # YAML gives any type; a quoted threshold or a scalar tool list fails deep inside a paid run.
        # bool is an int subclass, so `runs: true` would pass isinstance as 1.
        wrong = [n for n, t in CASE_TYPES.items() if not isinstance(v := getattr(case, n), t) or (t is not bool and isinstance(v, bool))]
        if wrong:
            problems.append(f"{rel}: wrong type for {wrong}")
            continue
        if case.runs < 1 or case.max_turns < 1:
            problems.append(f"{rel}: runs and max_turns must be at least 1")
        if not 0 <= case.pass_threshold <= 1:
            problems.append(f"{rel}: pass_threshold must be between 0 and 1")
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
        # An invocation case is scored by skill_fired alone, which skips `any`: no verdict, so every run fails.
        if case.kind == "invocation" and case.expect_skill == "any":
            problems.append(f"{rel}: an invocation case needs an expected skill or 'none', not 'any'")
        if path.name.endswith("-fires.yaml") and (case.kind != "invocation" or case.expect_skill != case.skill):
            problems.append(f"{rel}: a fires case must assert invocation of its own skill")
        if case.kind != "behavioural" and case.expect_question_about:
            problems.append(f"{rel}: expect_question_about needs a behavioural case")
        if any(not isinstance(term, str) or not term.strip() for term in case.expect_question_about):
            problems.append(f"{rel}: expect_question_about must contain non-empty strings")
        # Claude has no deny layer on top of --allowedTools, so an allowed tool that is also
        # forbidden would really run there. Reject the overlap instead.
        if overlap := [t for t in case.allow_tools if any(fnmatch.fnmatchcase(t, p) for p in case.forbid_tools)]:
            problems.append(f"{rel}: allow_tools {overlap} match forbid_tools")
        # A tool outside the allowlist is denied (Claude) or hidden (OpenCode): the case could never pass.
        if missing := [t for t in case.expect_tools if t not in case.allow_tools]:
            problems.append(f"{rel}: expect_tools {missing} are not in allow_tools")
        # Results are keyed on the id alone, so a duplicate would merge two cases' runs.
        if case.id in seen:
            problems.append(f"{rel}: id '{case.id}' is also used by {seen[case.id]}")
        seen[case.id] = rel
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


def check_key_used(orq: Path, key: str) -> None:
    """Stop when an active `orq auth profile` would override the key.

    orq 11 prefers a profile selected with `orq auth profile use` over ORQ_API_KEY, so
    runs would go to that profile's workspace while the upload goes to the key's.
    """
    out = subprocess.run(
        [str(orq), "launch", "claude", "--dry-run", "--no-mcp", "--no-skills"],
        capture_output=True, text=True, timeout=60, check=False, env={**os.environ, "ORQ_API_KEY": key},
    )
    if "ignoring ORQ_API_KEY" in out.stdout + out.stderr:
        sys.exit("an active orq auth profile overrides ORQ_API_KEY; run `orq auth profile clear` first")
    if out.returncode != 0:
        sys.exit(f"could not verify ORQ_API_KEY use: orq launch --dry-run exited {out.returncode}: "
                 f"{(out.stderr or out.stdout).strip() or 'no output'}")
    if not (out.stdout + out.stderr).strip():
        sys.exit("could not verify ORQ_API_KEY use: orq launch --dry-run produced no output")


def check_orq_version(orq: Path, allow_stale: bool) -> str:
    out = subprocess.run([str(orq), "--version"], capture_output=True, text=True, timeout=30, check=False).stdout
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    version = m.group(0) if m else "unknown"
    if not m or tuple(int(p) for p in m.groups()) < MIN_ORQ_VERSION:
        msg = f"orq {version} is older than {'.'.join(map(str, MIN_ORQ_VERSION))}; run `orq update` first, a stale CLI gives false results"
        if not allow_stale:
            sys.exit(f"{msg} (or pass --allow-stale-orq)")
        print(f"warning: {msg}", file=sys.stderr)
    return version


def resolve_default_model(orq: Path, agent: AgentName, key: str) -> str | None:
    """The model `orq launch <agent>` picks without --model, read from its dry run.

    It runs with the same ORQ_API_KEY the agent runs get, so it resolves against the
    same workspace catalogue (and the same profile precedence) as the runs themselves.
    """
    found = subprocess.run(
        [str(orq), "launch", agent, "--dry-run", "--no-mcp", "--no-skills"],
        capture_output=True, text=True, timeout=60, check=False, env={**os.environ, "ORQ_API_KEY": key},
    )
    out = found.stdout + found.stderr
    if agent == "claude":
        m = re.search(r"^\s*ANTHROPIC_MODEL=(\S+)", out, re.MULTILINE)
        return m.group(1) if m else None
    # OpenCode's config names it as "<provider key>/<model>"; the last "model" key is the top-level one.
    picked = re.findall(r'"model":"([^"]+)"', out)
    return re.sub(r"^orq(?:-openai)?/", "", picked[-1]) if picked else None


def describe_models(models: dict[AgentName, dict[str, str | None]]) -> str:
    return ", ".join(
        f"{a}={m['model'] or 'unresolved'}{' (orq launch default)' if m['source'] != 'flag' else ''}"
        for a, m in models.items()
    )


def check_container_image(image: str) -> None:
    """Stop before any run when Docker or the image is missing; each run would otherwise fail the same way."""
    if shutil.which("docker") is None:
        sys.exit("--container needs Docker on PATH")
    found = subprocess.run(["docker", "image", "inspect", image], capture_output=True, check=False, timeout=60)
    if found.returncode != 0:
        sys.exit(
            f"--container: image {image} not found (or Docker is not running). Build it once with "
            f"`uv run --with evaluatorq=={image.rsplit(':', 1)[-1]} eq coding-agent build-image`"
        )


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
# Actual costs, from orq traces
# ---------------------------------------------------------------------------
# The agents' own cost figures are guesses: OpenCode reports 0 for the orq provider, and
# Claude Code prices every model at Anthropic rates by name, whatever the gateway served.
# The gateway records what each call actually cost, per thread, on the traces.


def orq_json(orq: Path, key: str, command: list[str], body: dict[str, Any] | None = None, jmespath: str | None = None) -> Any:
    """One orq read command's JSON output. A body goes in a file: stdin mangles it on Windows."""
    with tempfile.TemporaryDirectory() as tmp:
        args = [str(orq), *command, "-o", "json"]
        if body is not None:
            path = Path(tmp) / "body.json"
            path.write_text(json.dumps(body), encoding="utf-8")
            args += ["--from-file", str(path)]
        if jmespath:
            args += ["-j", jmespath]
        found = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", timeout=120, check=False,
            env={**os.environ, "ORQ_API_KEY": key},
        )  # fmt: skip
    if found.returncode != 0:
        raise RuntimeError(f"orq {' '.join(command)}: {(found.stderr or found.stdout).strip()[:300]}")
    return json.loads(found.stdout)


def traces_aggregate(orq: Path, key: str, body: dict[str, Any]) -> list[dict[str, Any]]:
    return orq_json(orq, key, ["traces", "aggregate"], body).get("data") or []


def _window(start: datetime, end: datetime) -> dict[str, str]:
    # Traces expire after 30 days; a from past that is a 400.
    start = max(start, datetime.now(timezone.utc) - timedelta(days=29))
    return {"from": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "to": end.strftime("%Y-%m-%dT%H:%M:%SZ")}


async def trace_costs(orq: Path, key: str, thread_ids: list[str], start: datetime) -> dict[str, float]:
    """Gateway cost per thread id. Waits briefly for the last runs' traces to be ingested."""
    costs: dict[str, float] = {}
    for attempt in range(4):
        missing = [t for t in thread_ids if t not in costs]
        end = datetime.now(timezone.utc) + timedelta(minutes=5)
        for i in range(0, len(missing), 100):
            rows = traces_aggregate(orq, key, {
                **_window(start - timedelta(minutes=5), end), "limit": 1000, "group_by": ["session_id"],
                "filters": [{"field": "session_id", "op": "in", "values": missing[i:i + 100]}],
                "compute": [{"metric": "cost.total", "op": "sum"}],
            })  # fmt: skip
            for row in rows:
                thread, cost = (row.get("group") or {}).get("session_id"), (row.get("metrics") or {}).get("cost.total.sum")
                # A null sum is a trace not costed yet (or an unpriced model): missing, not $0.
                if thread and cost is not None:
                    costs[thread] = cost
        if len(costs) == len(thread_ids) or attempt == 3:
            return costs
        await asyncio.sleep(15)
    return costs


def model_prices(orq: Path, key: str) -> dict[str, dict[str, float]]:
    """USD per token (input, cache read, output) per provider/model_id in the workspace catalogue."""
    rows = orq_json(orq, key, ["models", "list"], jmespath=(
        "[].{p: provider, m: model_id, i: metadata.million_tokens_input_cost,"
        " o: metadata.million_tokens_output_cost, cr: metadata.million_tokens_cache_read_cost}"
    ))  # fmt: skip
    prices: dict[str, dict[str, float]] = {}
    for r in rows or []:
        if r.get("p") and r.get("m") and r.get("i") is not None and r.get("o") is not None:
            # A model with no cache price bills cached tokens as ordinary input.
            cached = r["cr"] if r.get("cr") is not None else r["i"]
            prices.setdefault(f"{r['p']}/{r['m']}", {"input": r["i"] / 1e6, "cached": cached / 1e6, "output": r["o"] / 1e6})
    return prices


def run_estimates(
    prices: dict[str, dict[str, float]] | None, models: dict[AgentName, dict[str, str | None]], cases: list[Case]
) -> dict[tuple[str, str], tuple[float, float]]:
    """(likely, upper bound) cost of one run per (agent, case id): every turn used, at catalogue prices.

    A run makes at most max_turns + 1 model calls (the last may only answer in text), each
    about CALL_TOKENS. The likely figure assumes the provider caches the prompt, the bound
    that nothing is cached. Runs that stop early cost less; the catalogue price can also
    differ from the billed one (it does not apply pricing tiers).
    """
    estimates: dict[tuple[str, str], tuple[float, float]] = {}
    for agent, m in models.items():
        price = (prices or {}).get(m["model"] or "")
        t = CALL_TOKENS[agent]
        for case in cases:
            if not price:
                estimates[(agent, case.id)] = (UNPRICED_RUN_COST_USD, UNPRICED_RUN_COST_USD)
                continue
            calls, output = case.max_turns + 1, t["output"] * price["output"]
            cached = t["prompt"] * ((1 - t["cached"]) * price["input"] + t["cached"] * price["cached"]) + output
            uncached = t["prompt"] * price["input"] + output
            estimates[(agent, case.id)] = (calls * cached, calls * uncached)
    return estimates


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


def mcp_name(agent: AgentName, bare: str) -> str:
    return (CLAUDE_MCP_PREFIX if agent == "claude" else OPENCODE_MCP_PREFIX) + bare


def build_target(
    stack: contextlib.ExitStack,
    agent: AgentName,
    case: Case,
    branch: Path,
    key: str,
    run_id: str,
    container: DockerOptions | None = None,
    model: str | None = None,
) -> EvalTarget:
    """One isolated target per run; its temp dirs are removed when `stack` closes.

    ``model`` renders `orq launch --model`; None keeps the default orq launch resolves.

    On the host, empty config dirs stand in for isolation. In a container the agent gets a fresh
    home and sees only its workdir, so those stopgaps are dropped, and anything it needs from the
    host has to be copied into the workdir: a host path does not exist inside.
    """

    def mkdtemp(prefix: str) -> Path:
        # 3.12's TemporaryDirectory also clears Windows read-only files (git objects) on cleanup.
        return Path(stack.enter_context(tempfile.TemporaryDirectory(prefix=prefix, ignore_cleanup_errors=True)))

    temp = mkdtemp(f"skill-evals-{agent}-")
    env = {"ORQ_API_KEY": key}
    if agent == "claude":
        allowed = CLAUDE_BUILTINS + [mcp_name("claude", t) for t in case.allow_tools]
        env |= {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1"}
        workdir: Path | None = None
        if container is None:
            env["CLAUDE_CONFIG_DIR"] = str(temp)
            plugin_dir = str(branch)
        else:
            workdir = temp / "work"
            shutil.copytree(branch, workdir / "plugin", ignore=PLUGIN_COPY_IGNORE, symlinks=True)
            plugin_dir = f"{container.workdir}/plugin"
        target = EvalTarget(
            "claude",
            launcher="orq",
            model=model,
            orq=OrqLaunchOptions(mcp=False, skills=False),
            extra_args=[
                "--plugin-dir", plugin_dir,
                "--allowedTools", ",".join(allowed),
                "--max-turns", str(case.max_turns),
            ],
            env=env,
            workdir=workdir,
            container=container,
            # dontAsk denies every tool --allowedTools does not list. Without it, headless Claude
            # Code runs in auto mode and its classifier approves writes like create_llm_eval, and
            # a container defaults to bypassPermissions; either makes --allowedTools a no-op.
            permission_mode="dontAsk",
        )
        return target

    if container is None:
        # OpenCode: empty XDG dirs hide the user's config, skills and sessions.
        for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "OPENCODE_CONFIG_DIR"):
            d = temp / var.lower()
            d.mkdir()
            env[var] = str(d)
    env |= {
        "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1",
        "OPENCODE_DISABLE_CLAUDE_CODE": "1",
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "OPENCODE_DISABLE_SHARE": "1",
    }
    workdir = mkdtemp("skill-evals-opencode-wd-")
    subprocess.run(["git", "init", "-q"], cwd=workdir, check=True)
    skills_dst = workdir / ".opencode" / "skills"
    skills_dst.mkdir(parents=True)
    for skill in (branch / "skills").iterdir():
        if (skill / "SKILL.md").exists():
            shutil.copytree(skill, skills_dst / skill.name, symlinks=True)
    # --auto grants everything, so deny shell, writes, webfetch (a shell reaches orq through curl,
    # around every MCP-level check) and all orq tools, then allow allow_tools back in: the last
    # matching rule wins, mirroring Claude's --allowedTools. OpenCode hides a denied tool from
    # the model, so unlike Claude a forbidden call is never attempted.
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
        # OpenCode's counterpart of Claude's --max-turns: after `steps` iterations the agent
        # (`opencode run` uses build) must answer in text, so the run stops at its first actions.
        "agent": {"build": {"steps": case.max_turns}},
        # orq launch defines both providers (chat completions and Responses) and picks one.
        "provider": {p: {"options": {"headers": {"X-ORQ-THREAD-ID": run_id}}} for p in ("orq", "orq-openai")},
    }
    (workdir / "opencode.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    target = EvalTarget(
        "opencode",
        launcher="orq",
        model=model,
        orq=OrqLaunchOptions(mcp=False, skills=False),  # only the branch copy and opencode.json's server
        extra_args=["--auto"],
        workdir=workdir,
        env=env,
        container=container,
    )
    return target


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
    """Tool calls from a run that stopped at its turn cap; None for any other failure."""
    events = coding_agent.parse_jsonl(stdout)
    if agent == "claude":
        result = next((e for e in reversed(events) if e.get("type") == "result"), None)
        if not result or result.get("subtype") != "error_max_turns":
            return None
    elif not OPENCODE_STEP_CAP_REJECTED.search(_opencode_error(stdout) or ""):
        return None
    turn = coding_agent.parse_events(agent, events)
    return {
        "tool_calls": [_call_dict(c) for c in turn.tool_calls],
        "text": turn.text or "",
        "session_id": turn.session_id,
        "cost_usd": turn.cost_usd,
        "stopped": "max_turns",
    }


def _opencode_error(stdout: str) -> str | None:
    """OpenCode's own error text. It nests it as error.data.message, where evaluatorq looks
    for error.message and so reports every OpenCode failure as the bare word "error"."""
    for event in reversed(coding_agent.parse_jsonl(stdout)):
        # Same lookup order as evaluatorq: the part's error first, then the event's.
        error = ((event.get("part") or {}).get("error") or event.get("error")) if event.get("type") == "error" else None
        if isinstance(error, dict):
            data = error.get("data") if isinstance(error.get("data"), dict) else {}
            message = data.get("message") or error.get("message")
            if message:
                return f"{error['name']}: {message}" if error.get("name") else str(message)
    return None


def _run_error(agent: AgentName, exc: CodingAgentError, stdout: str) -> dict[str, Any]:
    """A failed run: the agent's own error event first, then orq's stderr tail, and what kind of failure it is.

    On a non-zero exit evaluatorq reports stderr only, but OpenCode (an `error` event) and
    Claude (an is_error result) write the real cause to stdout. A genuine orq failure only
    shows in stderr, so both are kept.
    """
    try:
        turn = coding_agent.parse_events(agent, coding_agent.parse_jsonl(stdout))
    except Exception:  # noqa: BLE001 -- a half-written stdout still leaves stderr to report
        turn = None
    agent_error = turn.agent_error if turn else None
    if agent == "opencode" and agent_error == "error":
        agent_error = _opencode_error(stdout) or agent_error
    stderr = KEY_NOTE.sub("", exc.message).strip()
    if re.fullmatch(r".* exited -?\d+:", stderr):  # only evaluatorq's prefix is left: stderr said nothing else
        stderr = ""
    parts = [f"agent error: {agent_error[:300]}"] if agent_error and agent_error not in stderr else []
    if stderr:
        parts.append(f"stderr: {stderr[-300:]}")
    if exc.code == "cli.timeout":
        kind = "timeout"
    elif agent_error or exc.code in ("cli.agent_error", "cli.no_result"):
        kind = "model"
    else:
        kind = "harness"
    unavailable = isinstance(exc, CodingAgentUnavailableError)  # not found, timeout, prompt too long: a re-run replays it
    return {
        "error": f"{exc.code}: {' | '.join(parts)}",
        "error_kind": kind,
        "retryable": not unavailable and (kind == "harness" or (kind == "model" and bool(TRANSIENT_MODEL_ERROR.search(agent_error or stderr)))),
        "launched": exc.code not in NO_SESSION_CODES,
        "session_id": turn.session_id if turn else None,
        # Not `tool_calls`: an errored run is not scored, but what it did first helps the reader.
        "tool_calls_before_error": [c.name for c in turn.tool_calls] if turn else [],
        "stopped": "error",
    }


def _cost_from_stdout(agent: AgentName, case_id: str, stdout: str, budget: Budget) -> float:
    """The agent's own figure where it can be trusted, else the estimate so the cap still binds; 0 for no output."""
    if not stdout.strip():
        return 0.0
    if agent not in budget.own_cost:
        return budget.estimate(agent, case_id)
    try:
        cost = coding_agent.parse_events(agent, coding_agent.parse_jsonl(stdout)).cost_usd
    except Exception:  # noqa: BLE001 -- cost is best effort; a parse failure must not fail the run
        cost = None
    return cost or budget.estimate(agent, case_id)


@dataclass
class Budget:
    # No lock: admit/add have no await inside, so asyncio cannot interleave them.
    cap: float
    spent: float = 0.0
    reserved: float = 0.0
    breached: bool = False
    # What a run is charged when its own figure is missing or untrusted, per (agent, case id).
    per_run: dict[tuple[str, str], float] = field(default_factory=dict)
    # Agents whose own cost figure is right: Claude Code on an Anthropic model. On any other
    # model it still prices at Anthropic rates, and OpenCode reports 0 for the orq provider.
    own_cost: set[str] = field(default_factory=set)

    def estimate(self, agent: str, case_id: str) -> float:
        return self.per_run.get((agent, case_id), UNPRICED_RUN_COST_USD)

    def admit(self, agent: str, case_id: str) -> bool:
        estimate = self.estimate(agent, case_id)
        if self.spent + self.reserved + estimate > self.cap:
            self.breached = True
            return False
        self.reserved += estimate
        return True

    def add(self, cost: float | None, agent: str, case_id: str) -> None:
        self.reserved -= self.estimate(agent, case_id)
        self.spent += cost or 0.0
        if self.spent > self.cap:
            self.breached = True


async def run_once(
    agent: AgentName,
    case: Case,
    branch: Path,
    key: str,
    run_id: str,
    budget: Budget,
    container: DockerOptions | None,
    model: str | None,
) -> dict[str, Any]:
    with contextlib.ExitStack() as stack:
        target = build_target(stack, agent, case, branch, key, run_id, container, model)
        try:
            response = await target.respond([Message(role="user", content=case.prompt)])
        except CodingAgentError as exc:
            # A timeout leaves no stdout but the agent ran (and billed) until it was killed.
            timed_out = exc.code == "cli.timeout"
            cost = budget.estimate(agent, case.id) if timed_out else _cost_from_stdout(agent, case.id, target.last_stdout, budget)
            # The caller reconciles the reservation even when setup fails.
            recovered = _recover_max_turns(agent, target.last_stdout)
            if recovered is None:
                return _run_error(agent, exc, target.last_stdout) | {"cost_usd": cost}
            return recovered | {"cost_usd": cost}
        finally:
            await target.close()
        cost = _cost_from_stdout(agent, case.id, target.last_stdout, budget)
        return {
            "tool_calls": [_call_dict(o) for o in response.output if isinstance(o, ToolCallOutputItem)],
            "text": next((o.text for o in reversed(response.output) if isinstance(o, TextOutputItem)), ""),
            "session_id": response.response_id,
            "cost_usd": cost,
            "stopped": "done",
        }


def make_job(
    agent: AgentName, branch: Path, key: str, budget: Budget, container: DockerOptions | None, model: str | None
) -> Job:
    async def run(data: DataPoint, row: int) -> dict[str, Any]:
        case = Case(**data.inputs["case"])
        base = {"agent": agent, "case_id": case.id, "run": data.inputs["run"]}
        run_id = f"skill-evals-{case.id}-{agent}-{data.inputs['run']}-{int(time.time())}"
        spent = 0.0
        out: dict[str, Any] = {}
        for attempt in (1, 2):  # a retryable error is re-run once, then reported as an error
            if not budget.admit(agent, case.id):
                if attempt == 1:
                    return {"name": agent, "output": base | {"skipped": "cost cap reached"}, "error": None}
                break  # the cap stopped the retry: report the first attempt's error
            try:
                out = await run_once(agent, case, branch, key, run_id, budget, container, model)
            except Exception as exc:  # noqa: BLE001 -- setup (copy, git init) failed; keep the agent's row
                out = {"error": f"setup: {type(exc).__name__}: {exc}", "error_kind": "harness", "retryable": False,
                       "launched": False, "cost_usd": 0.0}  # fmt: skip
            budget.add(out["cost_usd"], agent, case.id)
            spent += out["cost_usd"] or 0.0
            # OpenCode's thread is the run_id sent as X-ORQ-THREAD-ID; Claude's is its session id.
            thread_id = (out.get("session_id") if agent == "claude" else run_id) if out.pop("launched", True) else None
            # Every attempt is billed, so the run carries the cost of all of them.
            out |= base | {"attempts": attempt, "thread_id": thread_id, "cost_usd": round(spent, 4)}
            if "error" not in out:
                # An expected tool that errored server side is judged in aggregate(), next to the scorers.
                return {"name": agent, "output": out, "error": None}
            if not out.pop("retryable"):
                break
        out.pop("retryable", None)
        return {"name": agent, "output": out, "error": out["error"]}

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


def completed(call: dict[str, Any]) -> bool:
    """Only a completed call counts: `incomplete` is an error or a denial, `in_progress` never returned
    (a Claude run cut off at --max-turns can end on one)."""
    return call.get("status") == "completed"


def split_calls(agent: str, case: Case, calls: list[dict[str, Any]]) -> tuple[set[str | None], set[str | None]]:
    """(orq tools that completed, orq tools that ran and failed server side). A denial is neither."""
    ok = {bare_tool(agent, c["name"]) for c in calls if completed(c)}
    failed: set[str | None] = set()
    for c in calls:
        if c.get("status") != "incomplete":
            continue
        bare = bare_tool(agent, c["name"])
        # An allowlisted tool is never denied locally, so its failure is the server's, even when
        # the text reads like a denial (a 403 from the workspace does).
        if bare in case.allow_tools or not is_denied(c):
            failed.add(bare)
    return ok, failed


def errored_expected_tools(agent: str, case: Case, calls: list[dict[str, Any]]) -> list[str]:
    """Expected tools that were only ever called with an error (a server failure, not a skill regression)."""
    if case.kind != "behavioural":
        return []
    ok, failed = split_calls(agent, case, calls)
    return [t for t in case.expect_tools if t in failed and t not in ok]


def fired_skills(agent: str, calls: list[dict[str, Any]]) -> list[str]:
    """Skills that loaded. A Skill call that errored (unknown skill, load failure) did not fire one."""
    fired: list[str] = []
    for c in calls:
        if not completed(c):
            continue
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
    return result in {"[denied by claude]", "error: permission rejected by user"}


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
    if not case.expect_tools:
        return _skip("no expected tools")
    # A call that errored is not evidence the step worked (a server error scores like a pass otherwise).
    called, failed = split_calls(out["agent"], case, out["tool_calls"])
    missing = [t for t in case.expect_tools if t not in called]
    if missing and all(t in failed for t in missing):
        # The agent took the right step and the server failed it: aggregate() makes the run an
        # error, unless another scorer found a real regression.
        return _skip(f"{missing} called but errored server side")
    why = "; ".join(f"{t} (called but errored)" if t in failed else t for t in missing)
    return _verdict(not missing, not missing, f"missing {why}" if missing else "all expected tools called")


async def asks_user(params: ScorerParameter) -> EvaluationResult:
    usable = _usable(params)
    if usable is None:
        return _skip("no run output")
    case, out = usable
    if not case.expect_question_about:
        return _skip("no question expected")
    # The final assistant text is user-facing; prompts and tool arguments are not evidence.
    # A relevant interrogative sentence checks the case's requested clarification.
    questions = re.findall(
        r"\b(?:what|which|who|when|where|why|how|would|could|can|do|does|is|are|should|will)\b[^.!?\n]*\?",
        out.get("text", ""), re.IGNORECASE,
    )
    relevant = any(re.search(rf"(?<!\w){re.escape(term)}\w*", question, re.IGNORECASE)
                   for question in questions for term in case.expect_question_about)
    return _verdict(relevant, relevant, "asked a relevant question" if relevant else "no user-facing question about the expected topic")


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
    {"name": "asks_user", "scorer": asks_user},
    {"name": "no_forbidden_tools", "scorer": no_forbidden_tools},
]


# ---------------------------------------------------------------------------
# Aggregation and reporting
# ---------------------------------------------------------------------------


def aggregate(results: list[Any], cases: list[Case], expected_agents: list[AgentName] | None = None) -> dict[str, Any]:
    by_case = {c.id: c for c in cases}
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for dp in results:
        case_id = dp.data_point.inputs["case"]["id"]
        for jr in dp.job_results or []:
            out = jr.output if isinstance(jr.output, dict) else {}
            scores = jr.evaluator_scores or []
            verdicts = [sc.score.pass_ for sc in scores if sc.score.pass_ is not None]
            # evaluatorq turns a crashed scorer into an n/a score; it must not read as "nothing to check".
            scorer_errors = [f"scorer {sc.evaluator_name}: {sc.error}" for sc in scores if getattr(sc, "error", None)]
            server_errored = errored_expected_tools(out.get("agent", ""), by_case[case_id], out.get("tool_calls", []))
            error = jr.error or out.get("error") or dp.error or "; ".join(scorer_errors) or None
            error_kind = out.get("error_kind")
            if out.get("skipped"):
                status = "skipped"
            elif error:
                status = "error"
            elif False in verdicts:
                # Checked before the server error: a forbidden attempt or wrong skill in the same run is still a regression.
                status = "fail"
            elif server_errored:
                status, error, error_kind = "error", f"expected tool(s) {server_errored} were called but errored", "tool"
            elif verdicts:
                status = "pass"
            else:
                status, error = "error", "no scorer returned a verdict"
            groups.setdefault((case_id, jr.job_name), []).append(
                {
                    "run": out.get("run"),
                    "status": status,
                    "scores": {sc.evaluator_name: {"pass": sc.score.pass_, "why": sc.score.explanation} for sc in scores},
                    "error": error,
                    "error_kind": error_kind or ("harness" if status == "error" else None),
                    "thread_id": out.get("thread_id"),
                    "attempts": out.get("attempts", 0),
                    "cost_usd": out.get("cost_usd"),
                    "cost_source": out.get("cost_source"),
                    "stopped": out.get("stopped"),
                    "tool_calls": [f"{c['name']}{' (denied)' if is_denied(c) else ''}" for c in out.get("tool_calls", [])]
                    or out.get("tool_calls_before_error", []),
                }
            )
    if expected_agents is not None:
        datapoint_errors = {
            (dp.data_point.inputs["case"]["id"], dp.data_point.inputs["run"]): dp.error
            for dp in results if dp.error
        }
        for case in cases:
            for agent in expected_agents:
                runs = groups.setdefault((case.id, agent), [])
                present = {r["run"] for r in runs}
                for run in range(1, case.runs + 1):
                    if run in present:
                        continue
                    runs.append({
                        "run": run, "status": "error", "scores": {},
                        "error": datapoint_errors.get((case.id, run)) or "missing agent result",
                        "error_kind": "harness", "thread_id": None, "attempts": 0,
                        "cost_usd": None, "cost_source": None, "stopped": None, "tool_calls": [],
                    })
                runs.sort(key=lambda r: r["run"])

    case_rows: list[dict[str, Any]] = []
    for (case_id, agent), runs in sorted(groups.items()):
        case = by_case[case_id]
        scored = [r for r in runs if r["status"] in ("pass", "fail")]
        passes = sum(r["status"] == "pass" for r in scored)
        rate = passes / len(scored) if scored else None
        # No scored run is checked first: a borderline case whose runs all errored measured nothing.
        if rate is None:
            status = "error" if any(r["status"] == "error" for r in runs) else "skipped"
        elif case.borderline:
            # A rate over part of the runs is not a measurement of the case.
            status = "measured" if len(scored) == len(runs) else "error"
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
                # "the model broke 6/6 times" reads differently from "the harness broke"; neither is a verdict.
                "errors_by_kind": {
                    k: sum(r["error_kind"] == k for r in runs if r["status"] == "error")
                    for k in sorted({r["error_kind"] for r in runs if r["status"] == "error"})
                },
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
        by_kind = ", ".join(f"{k} {n}/{len(row['runs'])}" for k, n in row["errors_by_kind"].items())
        errors = f"  errors: {by_kind}" if by_kind else ""
        print(f"  [{icons[row['status']]}] {row['case']} [{row['agent']}] {rate} (need {row['threshold']:.0%}){flaky}  ${row['cost_usd']:.4f}{errors}")
        if row["status"] in ("fail", "error", "measured"):
            for r in row["runs"]:
                failing = {k: v["why"] for k, v in r["scores"].items() if v["pass"] is False}
                detail = r["error"] or failing or ", ".join(r["tool_calls"])
                status = f"error [{r['error_kind']}]" if r["status"] == "error" else r["status"]
                tries = f"  attempts={r['attempts']}" if r["attempts"] > 1 else ""
                print(f"      run {r['run']}: {status}  {detail}  thread={r['thread_id']}{tries}")
    print("\nper skill:")
    for skill, s in sorted(report["skills"].items()):
        print(f"  {skill}: invocation {s['invocation']}, behavioural {s['behavioural']}, errors {s['errors']}, ${s['cost_usd']:.4f}"
              + (f", flaky: {', '.join(s['flaky'])}" if s["flaky"] else ""))


async def apply_trace_costs(results: list[Any], orq: Path | None, key: str, started: datetime) -> int:
    """Replace each run's estimated cost with what orq traced for its thread; returns how many were replaced.

    A retried Claude run keeps its estimate: only its last attempt's session id is known, so
    the trace would miss the first attempt. OpenCode sends one thread id for both.
    """
    outs = [jr.output for dp in results for jr in dp.job_results or [] if isinstance(jr.output, dict)]
    for out in outs:
        out["cost_source"] = "estimate" if out.get("thread_id") else None
    traceable = [
        out for out in outs
        if out.get("thread_id") and not (out.get("agent") == "claude" and out.get("attempts", 1) > 1)
    ]  # fmt: skip
    if not traceable or orq is None:
        return 0
    try:
        costs = await trace_costs(orq, key, sorted({out["thread_id"] for out in traceable}), started)
    except Exception as exc:  # noqa: BLE001 -- best effort; a surprise response must not cost the batch its results
        print(f"warning: could not read run costs from orq traces ({type(exc).__name__}: {exc}); costs are estimates", file=sys.stderr)
        return 0
    for out in traceable:
        if out["thread_id"] in costs:
            out["cost_usd"], out["cost_source"] = round(costs[out["thread_id"]], 6), "orq traces"
    return sum(out["cost_source"] == "orq traces" for out in traceable)


def exit_code(report: dict[str, Any], budget: Budget) -> int:
    statuses = {row["status"] for row in report["cases"]}
    if not report["cases"]:
        return 2
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
    parser.add_argument(
        "--pass-threshold", type=float, help="Override every selected case's pass threshold (0..1; fraction of runs that must pass)"
    )
    parser.add_argument(
        "--claude-model", help="Gateway model for Claude runs (provider/model_id; default: what orq launch resolves)"
    )
    parser.add_argument(
        "--opencode-model", help="Gateway model for OpenCode runs (provider/model_id; default: what orq launch resolves)"
    )
    parser.add_argument("--parallel", type=int, default=2, help="Concurrent agent runs (each is a full agent process)")
    parser.add_argument("--no-send", action="store_true", help="Do not upload the experiment to orq")
    parser.add_argument("--json", dest="json_path", type=Path, help="Write the summary here (default tests/eval-results/<timestamp>.json)")
    parser.add_argument("--branch", type=Path, default=REPO_ROOT, help="Plugin root under test (default: this checkout)")
    parser.add_argument("--max-cost-usd", type=float, default=30.0, help="Stop launching runs once this much is spent")
    parser.add_argument("--list", action="store_true", help="List the selected cases and the run count, then exit")
    parser.add_argument("--allow-stale-orq", action="store_true", help="Run even when orq is older than the minimum")
    parser.add_argument(
        "--container",
        action="store_true",
        help="Run each agent in a Docker container (evaluatorq's image; build it once with `eq coding-agent build-image`)",
    )
    parser.add_argument("--unsafe-host", action="store_true", help="Opt in to running agents on the host with access to host files")
    args = parser.parse_args()

    if args.json_path and not args.list:
        # A fixed --json path outlives the run; delete it before anything can exit, so neither a
        # bad case, an empty selection, a setup failure nor a crash leaves the previous batch's
        # summary to pass for this one.
        args.json_path.unlink(missing_ok=True)
    branch = args.branch.resolve()
    skill_names = {p.name for p in (branch / "skills").iterdir() if (p / "SKILL.md").exists()}
    cases = load_cases(skill_names)
    if args.skill:
        cases = [c for c in cases if c.skill in args.skill]
    if args.case:
        cases = [c for c in cases if c.id in args.case]
    agents: list[AgentName] = args.agent or list(AGENTS)
    if args.runs is not None:
        if args.runs < 1:
            parser.error("--runs must be at least 1")
        for c in cases:
            c.runs = args.runs
    if args.pass_threshold is not None:
        if not 0 <= args.pass_threshold <= 1:
            parser.error("--pass-threshold must be between 0 and 1")
        for c in cases:
            c.pass_threshold = args.pass_threshold
    if not cases:
        print("no eval cases selected", file=sys.stderr)
        return 2
    if not args.list and not args.container and not args.unsafe_host:
        sys.exit("agent runs require --container; --unsafe-host explicitly permits access to host files")

    key = load_dotenv_key("ORQ_API_KEY")
    if not key and not args.list:
        sys.exit("ORQ_API_KEY is not set, refusing to start")
    flag_models: dict[AgentName, str | None] = {"claude": args.claude_model, "opencode": args.opencode_model}
    orq = None if args.container else resolve_orq()
    if orq and key:
        check_key_used(orq, key)
    models = {
        a: {"model": flag_models[a], "source": "flag"}
        if flag_models[a]
        else {"model": resolve_default_model(orq, a, key) if orq and key else None, "source": "orq launch default"}
        for a in agents
    }

    total_runs = sum(c.runs for c in cases) * len(agents)
    print(f"{len(cases)} case(s), {total_runs} agent run(s), agents {agents}, cap ${args.max_cost_usd:.2f}")
    print(f"models: {describe_models(models)}")
    # Container runs need no host orq, but the traces are read from the host when it has one.
    trace_orq = orq
    if trace_orq is None:
        with contextlib.suppress(SystemExit):
            trace_orq = resolve_orq()
    prices = None
    if trace_orq and key:
        try:
            prices = model_prices(trace_orq, key)
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
            print(f"warning: could not read model prices ({exc}); estimating at the flat rate", file=sys.stderr)
    estimates = run_estimates(prices, models, cases)
    # Claude Code's own cost figure is right only on an Anthropic model; elsewhere it prices at
    # Anthropic rates, and OpenCode reports 0 for the orq provider.
    own_cost = {"claude"} if "claude" in models and (models["claude"]["model"] or "").startswith("anthropic/") else set()
    for a in agents:
        priced = bool(prices and models[a]["model"] in prices)
        likely, upper = (sum(estimates[(a, c.id)][i] * c.runs for c in cases) for i in (0, 1))
        basis = "every turn used, catalogue price" if priced else f"flat ${UNPRICED_RUN_COST_USD}/run, model not in the catalogue"
        print(f"  {a}: about ${likely:.4f}, up to ${upper:.4f} for {sum(c.runs for c in cases)} run(s) ({basis})")
    likely, estimate = (sum(estimates[(a, c.id)][i] * c.runs for a in agents for c in cases) for i in (0, 1))
    # Both attempts of a retried run are billed, and at most one retry is made.
    print(f"estimated cost: about ${likely:.4f}, up to ${estimate:.4f} (${2 * estimate:.4f} if every run is retried)")
    # What the cap will count: an agent's own figure where it is trusted (about the likely one), the bound elsewhere.
    charged = sum(estimates[(a, c.id)][0 if a in own_cost else 1] * c.runs for a in agents for c in cases)
    if charged > args.max_cost_usd:
        print(
            f"warning: the cap will count about ${charged:.2f} against --max-cost-usd {args.max_cost_usd:.2f}; "
            "later runs may be skipped. Raise the cap or select fewer cases.",
            file=sys.stderr,
        )
    for a, m in models.items():
        if m["model"] and WEAK_MODEL.search(m["model"]):
            print(
                f"warning: {a} model {m['model']} is a lite/nano tier; such models tend to fail as models "
                "before they reach the skill decision, so a run measures the model, not the skill",
                file=sys.stderr,
            )
    if args.list:
        for c in cases:
            per_run = ", ".join(f"{a} ~${estimates[(a, c.id)][0]:.4f} (<=${estimates[(a, c.id)][1]:.4f})" for a in agents)
            print(
                f"  {c.skill}/{c.id} [{c.kind}] x{c.runs} threshold={c.pass_threshold:g}"
                f"{' (measured, not scored)' if c.borderline else ''}  per run: {per_run}"
            )
        return 0

    container: DockerOptions | None = None
    if args.container:
        container = DockerOptions()
        check_container_image(container.image)
        orq_version = f"from image {container.image}"
    else:
        orq_version = check_orq_version(orq, args.allow_stale_orq)
    print(
        "note: orq launch may warn that ORQ_API_KEY does not belong to your `orq auth login` workspace; "
        "harmless here, and left out of run errors."
    )

    data = [
        # `prompt` duplicates case.prompt on purpose: it is the readable column in the orq experiment.
        DataPoint(inputs={"case": c.__dict__, "run": i + 1, "orq_skills": sorted(skill_names), "prompt": c.prompt})
        for c in cases
        for i in range(c.runs)
    ]
    budget = Budget(
        args.max_cost_usd,
        # The bound, not the likely figure: the cap should stop a batch early rather than late.
        per_run={k: upper for k, (_, upper) in estimates.items()},
        own_cost=own_cost,
    )
    jobs: list[Job] = [make_job(a, branch, key, budget, container, flag_models[a]) for a in agents]

    started = datetime.now(timezone.utc)
    out_path = args.json_path or RESULTS_DIR / f"{started.strftime('%Y%m%dT%H%M%SZ')}.json"
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
    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path = out_path.with_suffix(".results.json")

    def save_raw() -> None:
        raw_path.write_text(
            json.dumps(
                {
                    "started": started.isoformat(),
                    "ended": ended.isoformat(),
                    "description": f"invocation + behavioural evals, branch {branch.name}, models {describe_models(models)}",
                    "results": [r.model_dump(mode="json") for r in results],
                }
            ),
            encoding="utf-8",
        )

    # Saved before the trace lookup, which waits on the network: a paid batch must survive it.
    save_raw()
    traced = await apply_trace_costs(results, trace_orq, key, started)
    if traced:
        save_raw()

    report = aggregate(results, cases, agents)
    print_report(report)
    code = exit_code(report, budget)
    attempts = [r["attempts"] for row in report["cases"] for r in row["runs"]]
    sessions, retries = sum(attempts), sum(max(0, a - 1) for a in attempts)
    summary = {
        "started": started.isoformat(),
        "branch": str(branch),
        "orq_version": orq_version,
        "agents": agents,
        # A null model is a default that could not be read (container mode, or the dry run failed).
        "models": models,
        "experiment_url": None,
        "results_file": str(raw_path),
        # Traced where orq has the run's thread; the rest keep the estimate the cap charged.
        "cost_usd": round(sum(row["cost_usd"] for row in report["cases"]), 4),
        "cost_estimated_usd": round(budget.spent, 4),
        "cost_traced_runs": traced,
        "cost_estimated_runs": sum(r["cost_source"] == "estimate" for row in report["cases"] for r in row["runs"]),
        "sessions": sessions,
        "retries": retries,
        "runs_skipped_by_cap": budget.breached,
        "exit_code": code,
        **report,
    }
    out_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    capped = " (cost cap hit, later runs skipped)" if budget.breached else ""
    untraced = summary["cost_estimated_runs"]
    source = f"{traced} run(s) traced" + (f", {untraced} estimated" if untraced else "")
    print(
        f"\nspent ${summary['cost_usd']:.4f} ({source}; the cap counted ${budget.spent:.2f}) "
        f"({sessions} sessions, {retries} retries){capped}; summary: {out_path}"
    )

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
            print(f"experiment: {url}")
        else:
            summary["upload_error"] = (
                f"{stderr[-300:] or f'exit {child.returncode}'}; retry with: uv run tests/scripts/run_evals.py --upload {raw_path}"
            )
            print(f"warning: experiment upload failed ({summary['upload_error']})", file=sys.stderr)
        out_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
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
    key = load_dotenv_key("ORQ_API_KEY")
    if not key:
        sys.exit("ORQ_API_KEY is not set")
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
    # Exit 1 means a regression, so a setup failure (sys.exit("msg") exits 1) or a crash
    # (an uncaught exception exits 1) is mapped to 2.
    try:
        code = asyncio.run(amain())
    except SystemExit as exc:
        if isinstance(exc.code, str):
            print(exc.code, file=sys.stderr)
            sys.exit(2)
        raise
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
    sys.exit(code)


if __name__ == "__main__":
    main()
