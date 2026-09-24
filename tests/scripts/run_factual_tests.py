#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["orq-ai-sdk", "evaluatorq"]
# ///
"""Factual test runner for orq skills.

Reads CSV test fixtures from tests/factual/<skill>.csv and executes
deterministic checks against the live environment. Outputs structured
JSON for CI integration.

Phase 1 test types (the two mcp_* types need ORQ_API_KEY and skip without it):
  mcp_tool_exists    MCP server lists the named tool
  mcp_tool_param     MCP tool schema includes the named parameter
  sdk_import         Python import succeeds
  sdk_method         Python object has the named attribute
  cli_subcommand     `orq <subcommand> --help` resolves to that subcommand
  cli_flag           `orq <subcommand> --help` output mentions the flag
  doc_url            curl returns 2xx/3xx (non-gating: reported, never fails the run)
  github_repo        `gh repo view` finds the repo
  pypi_package       PyPI JSON API returns 200
  pypi_extra         PyPI metadata lists the extra (target package, assertion extra)
  npm_package        npm registry returns 200

For github_repo and the package types only "not found" is drift and fails the
run; an outage, rate limit or auth error skips the row with its reason.

Usage (uv installs the latest orq-ai-sdk and evaluatorq, so SDK checks run against the
current release rather than whatever the caller's environment holds):
    uv run tests/scripts/run_factual_tests.py                    # all skills
    uv run tests/scripts/run_factual_tests.py --skill orq-cli    # one skill
    uv run tests/scripts/run_factual_tests.py --type sdk_import  # one type
    uv run tests/scripts/run_factual_tests.py --json             # CI output
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FACTUAL_DIR = REPO_ROOT / "tests" / "factual"
MCP_URL = "https://my.orq.ai/v2/mcp"

PHASE1_TYPES = frozenset(
    {
        "mcp_tool_exists",
        "mcp_tool_param",
        "sdk_import",
        "sdk_method",
        "cli_subcommand",
        "cli_flag",
        "doc_url",
        "github_repo",
        "pypi_package",
        "pypi_extra",
        "npm_package",
    }
)

# Docs pages move and rate-limit too often for any failure to count as drift.
# The other third-party checks gate on "not found" only (see SkipCheck).
NON_GATING_TYPES = frozenset({"doc_url"})
# Types whose check reads the assertion; a blank one would pass vacuously.
ASSERTION_TYPES = frozenset({"cli_flag", "mcp_tool_param", "pypi_extra", "sdk_method"})
NETWORK_RETRIES = 2
REQUIRED_COLUMNS = ("test_type", "target", "assertion", "description")


class SkipCheck(Exception):
    """The check could not run (no ORQ_API_KEY on a fork PR, a host outage), not drift."""


@dataclass
class TestCase:
    skill: str
    test_type: str
    target: str
    assertion: str
    description: str


@dataclass
class TestResult:
    skill: str
    test_type: str
    target: str
    assertion: str
    description: str
    status: str  # passed | failed | skipped | error
    duration_ms: float = 0.0
    error: str | None = None


# ---------------------------------------------------------------------------
# Minimal MCP client (Streamable HTTP)
# ---------------------------------------------------------------------------


class MCPClient:
    """List tools from an MCP server over Streamable HTTP.

    Uses curl subprocess to avoid OpenSSL DLL conflicts on some Windows
    environments.
    """

    def __init__(self, url: str, api_key: str) -> None:
        self.url = url
        self.api_key = api_key
        self.session_id: str | None = None
        self._tools: dict[str, dict] | None = None

    def _post(self, body: dict) -> dict | None:
        data = json.dumps(body)
        cmd = [
            "curl", "-s", "-X", "POST", self.url,
            "-H", "Content-Type: application/json",
            "-H", "Accept: application/json, text/event-stream",
            # The key goes in a config read from stdin, so it never shows in the process list.
            "-K", "-",
            "-D", "-",
            "-d", data,
        ]
        if self.session_id:
            cmd.extend(["-H", f"Mcp-Session-Id: {self.session_id}"])
        if os.environ.get("SSL_VERIFY", "1") == "0":
            cmd.append("-k")

        config = f'header = "Authorization: Bearer {self.api_key}"\n'
        result = subprocess.run(cmd, input=config, capture_output=True, timeout=30, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"curl failed: {result.stderr.strip()[:200]}")

        raw = result.stdout
        headers_end = raw.find("\r\n\r\n")
        if headers_end == -1:
            headers_end = raw.find("\n\n")
        if headers_end == -1:
            return None

        headers_block = raw[:headers_end]
        body_text = raw[headers_end:].strip()

        for line in headers_block.split("\n"):
            if line.lower().startswith("mcp-session-id:"):
                self.session_id = line.split(":", 1)[1].strip()

        if not body_text:
            return None

        status_parts = headers_block.split("\n", 1)[0].split()
        status = status_parts[1] if len(status_parts) > 1 else "?"
        if not status.startswith("2"):
            raise RuntimeError(f"MCP HTTP {status}: {body_text[:200]}")

        if "text/event-stream" in headers_block.lower():
            payload = None
            for line in reversed(body_text.split("\n")):
                if line.startswith("data:"):
                    payload = json.loads(line[5:].strip())
                    break
        else:
            payload = json.loads(body_text)

        if isinstance(payload, dict) and payload.get("error"):
            raise RuntimeError(f"MCP error: {payload['error']}")
        return payload

    def list_tools(self) -> dict[str, dict]:
        if self._tools is not None:
            return self._tools

        self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "factual-test-runner", "version": "1.0.0"},
                },
            }
        )
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

        # Follow nextCursor: a tool past the first page is not drift.
        tools: list[dict] = []
        params: dict = {}
        for request_id in range(2, 102):
            resp = self._post({"jsonrpc": "2.0", "id": request_id, "method": "tools/list", "params": params})
            result = (resp or {}).get("result", {})
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                break
            params = {"cursor": cursor}
        self._tools = {t["name"]: t for t in tools}
        return self._tools


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _usage_token_accepts(usage_tok: str, word: str, first: bool) -> bool:
    """Does one Usage-line token accept the word the skill wrote in that position?

    Exact name; a fixed choice (`[json|yaml|table]`); or, past the first word, a
    positional placeholder (`trace-id`, `<id>`) that takes a literal value. The
    placeholders `[command]` and `[flags]` mean the CLI did not resolve the word.
    """
    if usage_tok == word:
        return True
    if "|" in usage_tok:
        return word in usage_tok.strip("[]<>").split("|")
    return not first and usage_tok not in ("[command]", "[flags]") and not usage_tok.startswith("[")


# argv: module, name. Exits 0 if the module's source defines or imports the name at
# top level, without executing the module.
_STATIC_NAME_CHECK = """
import ast, importlib.util, sys
module, name = sys.argv[1:3]
spec = importlib.util.find_spec(module)
if spec is None or not spec.origin or not spec.origin.endswith(".py"):
    sys.exit(f"module {module} not found")
tree = ast.parse(open(spec.origin, encoding="utf-8").read())
names = set()
for node in tree.body:
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        names.add(node.name)
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        names.update(a.asname or a.name.split(".")[0] for a in node.names)
    elif isinstance(node, ast.Assign):
        names.update(t.id for t in node.targets if isinstance(t, ast.Name))
sys.exit(0 if name in names else f"{name} not defined in {module}")
"""


def _which(name: str) -> str | None:
    """Resolve a command name to its full path (handles .cmd/.bat on Windows)."""
    return shutil.which(name)


class FactualTestRunner:
    def __init__(self) -> None:
        self._mcp: MCPClient | None = None
        self._mcp_err: str | None = None
        self._tool_cache: dict[str, str | None] = {}

    @property
    def mcp(self) -> MCPClient | None:
        if self._mcp is not None:
            return self._mcp
        if self._mcp_err is not None:
            return None
        api_key = os.environ.get("ORQ_API_KEY")
        if not api_key:
            self._mcp_err = "ORQ_API_KEY not set"
            return None
        try:
            client = MCPClient(MCP_URL, api_key)
            client.list_tools()
            self._mcp = client
            return client
        except Exception as e:
            self._mcp_err = str(e)
            return None

    # -- loading --

    def load_tests(self, skill: str | None = None) -> list[TestCase]:
        pattern = f"{skill}.csv" if skill else "*.csv"
        tests: list[TestCase] = []
        for csv_path in sorted(FACTUAL_DIR.glob(pattern)):
            skill_name = csv_path.stem
            with open(csv_path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
                if missing:
                    raise ValueError(f"{csv_path.name}: missing column(s) {', '.join(missing)}")
                for row in reader:
                    tests.append(
                        TestCase(
                            skill=skill_name,
                            test_type=row["test_type"],
                            target=row["target"],
                            assertion=row.get("assertion", ""),
                            description=row.get("description", ""),
                        )
                    )
        return tests

    # -- dispatcher --

    def run_test(self, tc: TestCase) -> TestResult:
        start = time.monotonic()
        # A mistyped test_type is a broken reviewed row: report it rather than drop it.
        if tc.test_type not in PHASE1_TYPES:
            return TestResult(**vars(tc), status="error", error=f"unknown test_type '{tc.test_type}'")
        if tc.test_type in ASSERTION_TYPES and not tc.assertion.strip():
            return TestResult(**vars(tc), status="error", error=f"{tc.test_type} row has an empty assertion")
        handler = getattr(self, f"_check_{tc.test_type}")
        try:
            passed, error = handler(tc.target, tc.assertion)
            status = "passed" if passed else "failed"
        except SkipCheck as e:
            status, error = "skipped", str(e)
        except Exception as e:
            status, error = "error", str(e)
        duration = (time.monotonic() - start) * 1000
        return TestResult(**vars(tc), status=status, duration_ms=round(duration, 1), error=error if status != "passed" else None)

    # -- test type implementations --

    def _check_mcp_tool_exists(self, target: str, _assertion: str) -> tuple[bool, str | None]:
        client = self.mcp
        if client is None:
            if not os.environ.get("ORQ_API_KEY"):
                raise SkipCheck("ORQ_API_KEY not set")
            raise RuntimeError(f"MCP unavailable: {self._mcp_err}")
        tools = client.list_tools()
        if target in tools:
            return True, None
        available = ", ".join(sorted(tools)[:15])
        return False, f"tool '{target}' not found (have: {available})"

    def _check_mcp_tool_param(self, target: str, assertion: str) -> tuple[bool, str | None]:
        client = self.mcp
        if client is None:
            if not os.environ.get("ORQ_API_KEY"):
                raise SkipCheck("ORQ_API_KEY not set")
            raise RuntimeError(f"MCP unavailable: {self._mcp_err}")
        tools = client.list_tools()
        if target not in tools:
            return False, f"tool '{target}' not found"
        props = tools[target].get("inputSchema", {}).get("properties", {})
        if assertion in props:
            return True, None
        return False, f"param '{assertion}' not in {target} (have: {', '.join(sorted(props))})"

    def _check_sdk_import(self, target: str, _assertion: str) -> tuple[bool, str | None]:
        parts = target.rsplit(".", 1)
        stmt = f"from {parts[0]} import {parts[1]}" if len(parts) == 2 else f"import {parts[0]}"
        result = subprocess.run([sys.executable, "-c", stmt], capture_output=True, timeout=30, text=True)
        if result.returncode == 0:
            return True, None
        last_line = result.stderr.strip().split("\n")[-1] if result.stderr else "unknown error"
        if len(parts) == 2 and ("ModuleNotFoundError" in last_line or "ImportError" in last_line):
            # An integration module behind an extra (`evaluatorq[langgraph]`) imports a
            # framework we do not install; read its source instead of importing it.
            static = subprocess.run([sys.executable, "-c", _STATIC_NAME_CHECK, *parts], capture_output=True, timeout=30, text=True)
            if static.returncode == 0:
                return True, None
            return False, f"{last_line} (static check: {static.stderr.strip().splitlines()[-1] if static.stderr else 'failed'})"
        return False, last_line

    def _check_sdk_method(self, target: str, assertion: str) -> tuple[bool, str | None]:
        parts = target.rsplit(".", 1)
        if len(parts) != 2:
            return False, f"bad target '{target}' (want module.Class)"
        module, cls = parts
        stmt = (
            f"from {module} import {cls}; "
            f"assert hasattr({cls}, '{assertion}') or any("
            f"'{assertion}' in vars(c).get('__annotations__', {{}}) for c in {cls}.__mro__)"
        )
        result = subprocess.run([sys.executable, "-c", stmt], capture_output=True, timeout=30, text=True)
        if result.returncode == 0:
            return True, None
        last_line = result.stderr.strip().split("\n")[-1] if result.stderr else "unknown error"
        return False, last_line

    def _resolve(self, name: str) -> str | None:
        if name not in self._tool_cache:
            self._tool_cache[name] = _which(name)
        return self._tool_cache[name]

    def _orq_help(self, target: str) -> tuple[str | None, str | None]:
        """Return (help text, None) if `orq <target>` resolves, else (None, error).

        The CLI prints the parent's help and exits 0 for an unknown child
        (`orq traces bogus --help`), so the exit code proves nothing. The
        Usage line (`orq traces get trace-id [flags]`) starts with the path
        that actually resolved, under its canonical name; the target may use
        an alias listed under `Aliases:` for its last word.
        """
        orq = self._resolve("orq")
        if orq is None:
            return None, "orq CLI not on PATH"
        words = target.split()
        result = subprocess.run([orq, *words, "--help"], capture_output=True, timeout=30, text=True)
        if result.returncode != 0:
            return None, f"exit {result.returncode}: {result.stderr.strip()[:200]}"
        lines = result.stdout.splitlines()
        usage = next((lines[i + 1].split() for i, line in enumerate(lines[:-1]) if line.strip() == "Usage:"), None)
        if not usage:
            return None, "no Usage line in help output"
        resolved = usage[1 : 1 + len(words)]
        if len(resolved) == len(words) and all(
            _usage_token_accepts(u, w, first=i == 0) for i, (u, w) in enumerate(zip(resolved, words))
        ):
            return result.stdout, None
        aliases = next(
            (lines[i + 1].replace(",", " ").split() for i, line in enumerate(lines[:-1]) if line.strip() == "Aliases:"),
            [],
        )
        if resolved[:-1] == words[:-1] and words[-1] in aliases:
            return result.stdout, None
        return None, f"not a subcommand, CLI resolved `{' '.join(usage[1:])}`"

    def _check_cli_subcommand(self, target: str, _assertion: str) -> tuple[bool, str | None]:
        help_text, error = self._orq_help(target)
        return help_text is not None, error

    def _check_cli_flag(self, target: str, assertion: str) -> tuple[bool, str | None]:
        help_text, error = self._orq_help(target)
        if help_text is None:
            return False, error
        if re.search(rf"(?<![\w-]){re.escape(assertion)}(?![\w-])", help_text):
            return True, None
        return False, f"'{assertion}' not in help output"

    def _check_doc_url(self, target: str, _assertion: str) -> tuple[bool, str | None]:
        cmd = ["curl", "-sL", "--retry", str(NETWORK_RETRIES), "--max-time", "10", "-o", os.devnull, "-w", "%{http_code}", target]
        if os.environ.get("SSL_VERIFY", "1") == "0":
            cmd.insert(1, "-k")
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=45, text=True)
            if result.returncode != 0:
                return False, f"curl exit {result.returncode}: {result.stderr.strip()[:200]}"
            code = int(result.stdout.strip())
            if code < 400:
                return True, None
            return False, f"HTTP {code}"
        except FileNotFoundError:
            return False, "curl not on PATH"
        except ValueError:
            return False, f"unexpected curl output: {result.stdout.strip()[:50]}"

    def _check_github_repo(self, target: str, _assertion: str) -> tuple[bool, str | None]:
        gh = self._resolve("gh")
        if gh is None:
            raise SkipCheck("gh CLI not on PATH")
        result = subprocess.run(
            [gh, "repo", "view", target, "--json", "name"],
            capture_output=True,
            timeout=30,
            text=True,
        )
        if result.returncode == 0:
            return True, None
        error = result.stderr.strip()[:200]
        if "Could not resolve to a Repository" in error:
            return False, error
        raise SkipCheck(error)

    def _registry_get(self, url: str) -> tuple[int, str]:
        """GET a registry URL: (status, body). Anything but 200 or 404 is not an
        answer about the package, so it skips the row instead of failing it."""
        cmd = ["curl", "-s", "--retry", str(NETWORK_RETRIES), "--max-time", "10", "-w", "\n%{http_code}", url]
        if os.environ.get("SSL_VERIFY", "1") == "0":
            cmd.insert(1, "-k")
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=45, text=True, encoding="utf-8")
        except FileNotFoundError:
            raise SkipCheck("curl not on PATH") from None
        if result.returncode != 0:
            raise SkipCheck(f"curl exit {result.returncode}: {result.stderr.strip()[:200]}")
        body, _, code = result.stdout.rpartition("\n")
        if code not in ("200", "404"):
            raise SkipCheck(f"{url}: HTTP {code}")
        return int(code), body

    def _check_pypi_extra(self, target: str, assertion: str) -> tuple[bool, str | None]:
        code, body = self._registry_get(f"https://pypi.org/pypi/{target}/json")
        if code == 404:
            return False, f"'{target}' not on PyPI"
        extras = json.loads(body)["info"].get("provides_extra") or []
        if assertion in extras:
            return True, None
        return False, f"extra '{assertion}' not in {target} (have: {', '.join(extras)})"

    def _check_npm_package(self, target: str, _assertion: str) -> tuple[bool, str | None]:
        code, _ = self._registry_get(f"https://registry.npmjs.org/{target}")
        return (True, None) if code == 200 else (False, f"'{target}' not on npm")

    def _check_pypi_package(self, target: str, _assertion: str) -> tuple[bool, str | None]:
        code, _ = self._registry_get(f"https://pypi.org/pypi/{target}/json")
        return (True, None) if code == 200 else (False, f"'{target}' not on PyPI")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Run factual tests for orq skills")
    parser.add_argument("--skill", help="Test one skill only")
    parser.add_argument("--type", dest="test_type", help="Run one test type only")
    parser.add_argument("--json", dest="json_output", action="store_true", help="JSON output")
    parser.add_argument("--parallel", type=int, default=4, help="Max parallel workers")
    args = parser.parse_args()

    runner = FactualTestRunner()
    # A header-only CSV is a reviewed "nothing to check" and passes; only a
    # skill with no CSV at all (or a typo in --skill) is an error.
    if args.skill and not (FACTUAL_DIR / f"{args.skill}.csv").exists():
        print(f"No tests/factual/{args.skill}.csv.", file=sys.stderr)
        sys.exit(1)
    tests = runner.load_tests(skill=args.skill)
    if args.test_type:
        tests = [t for t in tests if t.test_type == args.test_type]
    # Connect once before fanning out, so worker threads share one client
    # instead of racing to create it.
    if any(t.test_type.startswith("mcp_") for t in tests):
        _ = runner.mcp

    results: list[TestResult] = []
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futures = {pool.submit(runner.run_test, tc): tc for tc in tests}
        for fut in as_completed(futures):
            results.append(fut.result())

    results.sort(key=lambda r: (r.skill, r.test_type, r.target))

    gating = [r for r in results if r.test_type not in NON_GATING_TYPES]
    summary = {
        "total": len(results),
        "passed": sum(1 for r in results if r.status == "passed"),
        "failed": sum(1 for r in gating if r.status == "failed"),
        "errors": sum(1 for r in gating if r.status == "error"),
        "non_gating_failed": sum(
            1 for r in results if r.test_type in NON_GATING_TYPES and r.status in ("failed", "error")
        ),
        "skipped": sum(1 for r in results if r.status == "skipped"),
    }

    if args.json_output:
        print(json.dumps({"summary": summary, "results": [asdict(r) for r in results]}, indent=2))
    else:
        cur_skill = None
        for r in results:
            if r.skill != cur_skill:
                cur_skill = r.skill
                print(f"\n{cur_skill}")
            icon = {"passed": "+", "failed": "x", "skipped": "o", "error": "!"}[r.status]
            label = r.description
            if r.target and r.target not in label:
                label = f"{label} [{r.target}]"
            print(f"  [{icon}] {r.test_type}: {label} ({r.duration_ms:.0f}ms)")
            if r.error:
                print(f"      {r.error}")
        print(f"\n{summary['passed']}/{summary['total']} passed", end="")
        if summary["failed"]:
            print(f", {summary['failed']} failed", end="")
        if summary["skipped"]:
            print(f", {summary['skipped']} skipped", end="")
        if summary["errors"]:
            print(f", {summary['errors']} errors", end="")
        if summary["non_gating_failed"]:
            print(f", {summary['non_gating_failed']} non-gating ({', '.join(sorted(NON_GATING_TYPES))})", end="")
        print()

    # Skipped rows measured nothing: say so loudly, so a missing secret or a
    # host outage does not hide behind a green run.
    if summary["skipped"]:
        reasons: dict[str, int] = {}
        for r in results:
            if r.status == "skipped":
                reasons[r.error or "?"] = reasons.get(r.error or "?", 0) + 1
        print(f"warning: {summary['skipped']} of {summary['total']} rows skipped, not checked:", file=sys.stderr)
        for reason, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"  {n:4d}  {reason}", file=sys.stderr)

    # A full run that loads nothing means tests/factual/ is gone or unreadable.
    if not results and not args.skill and not args.test_type:
        print("error: no factual rows loaded from tests/factual/", file=sys.stderr)
        sys.exit(1)
    sys.exit(1 if summary["failed"] or summary["errors"] else 0)


if __name__ == "__main__":
    main()
