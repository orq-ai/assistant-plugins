#!/usr/bin/env python3
"""Factual test runner for orq skills.

Reads CSV test fixtures from tests/factual/<skill>.csv and executes
deterministic checks against the live environment. Outputs structured
JSON for CI integration.

Phase 1 test types (auth-free):
  mcp_tool_exists    MCP server lists the named tool
  mcp_tool_param     MCP tool schema includes the named parameter
  sdk_import         Python import succeeds
  sdk_method         Python object has the named attribute
  cli_subcommand     `orq <subcommand> --help` exits 0
  cli_flag           `orq <subcommand> --help` output mentions the flag
  doc_url            curl returns 2xx/3xx
  github_repo        `gh repo view` exits 0
  pypi_package       PyPI JSON API returns 200

Usage:
    python tests/scripts/run_factual_tests.py                    # all skills
    python tests/scripts/run_factual_tests.py --skill orq-cli    # one skill
    python tests/scripts/run_factual_tests.py --type sdk_import  # one type
    python tests/scripts/run_factual_tests.py --json             # CI output
"""

from __future__ import annotations

import argparse
import csv
import json
import os
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
    }
)


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
    environments. Falls back to urllib if curl is unavailable.
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
            "-H", f"Authorization: Bearer {self.api_key}",
            "-D", "-",
            "-d", data,
        ]
        if self.session_id:
            cmd.extend(["-H", f"Mcp-Session-Id: {self.session_id}"])
        if os.environ.get("SSL_VERIFY", "1") == "0":
            cmd.append("-k")

        result = subprocess.run(cmd, capture_output=True, timeout=30, text=True)
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

        if "text/event-stream" in headers_block.lower():
            for line in reversed(body_text.split("\n")):
                if line.startswith("data:"):
                    return json.loads(line[5:].strip())
            return None

        return json.loads(body_text)

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

        resp = self._post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = (resp or {}).get("result", {}).get("tools", [])
        self._tools = {t["name"]: t for t in tools}
        return self._tools


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _which(name: str) -> str | None:
    """Resolve a command name to its full path (handles .cmd/.bat on Windows)."""
    return shutil.which(name)


class FactualTestRunner:
    def __init__(self, verbose: bool = False) -> None:
        self.verbose = verbose
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
                for row in csv.DictReader(f):
                    if row["test_type"] not in PHASE1_TYPES:
                        continue
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
        handler = getattr(self, f"_check_{tc.test_type}", None)
        if handler is None:
            return TestResult(**vars(tc), status="skipped", error=f"unknown type: {tc.test_type}")
        try:
            passed, error = handler(tc.target, tc.assertion)
            status = "passed" if passed else "failed"
        except Exception as e:
            status, error = "error", str(e)
        duration = (time.monotonic() - start) * 1000
        return TestResult(**vars(tc), status=status, duration_ms=round(duration, 1), error=error if status != "passed" else None)

    # -- test type implementations --

    def _check_mcp_tool_exists(self, target: str, _assertion: str) -> tuple[bool, str | None]:
        client = self.mcp
        if client is None:
            raise RuntimeError(f"MCP unavailable: {self._mcp_err}")
        tools = client.list_tools()
        if target in tools:
            return True, None
        available = ", ".join(sorted(tools)[:15])
        return False, f"tool '{target}' not found (have: {available})"

    def _check_mcp_tool_param(self, target: str, assertion: str) -> tuple[bool, str | None]:
        client = self.mcp
        if client is None:
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
        return False, last_line

    def _check_sdk_method(self, target: str, assertion: str) -> tuple[bool, str | None]:
        parts = target.rsplit(".", 1)
        if len(parts) != 2:
            return False, f"bad target '{target}' (want module.Class)"
        module, cls = parts
        stmt = (
            f"from {module} import {cls}; "
            f"assert hasattr({cls}, '{assertion}') or '{assertion}' in getattr({cls}, '__annotations__', {{}})"
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

    def _check_cli_subcommand(self, target: str, _assertion: str) -> tuple[bool, str | None]:
        orq = self._resolve("orq")
        if orq is None:
            return False, "orq CLI not on PATH"
        cmd = [orq, *target.split(), "--help"]
        result = subprocess.run(cmd, capture_output=True, timeout=30, text=True)
        if result.returncode == 0:
            return True, None
        return False, f"exit {result.returncode}: {result.stderr.strip()[:200]}"

    def _check_cli_flag(self, target: str, assertion: str) -> tuple[bool, str | None]:
        orq = self._resolve("orq")
        if orq is None:
            return False, "orq CLI not on PATH"
        cmd = [orq, *target.split(), "--help"]
        result = subprocess.run(cmd, capture_output=True, timeout=30, text=True)
        if result.returncode != 0:
            return False, f"subcommand exit {result.returncode}"
        flag = assertion.lstrip("-")
        if f"--{flag}" in result.stdout or f"-{flag}" in result.stdout:
            return True, None
        return False, f"'{assertion}' not in help output"

    def _check_doc_url(self, target: str, _assertion: str) -> tuple[bool, str | None]:
        cmd = ["curl", "-sL", "-o", os.devnull, "-w", "%{http_code}", target]
        if os.environ.get("SSL_VERIFY", "1") == "0":
            cmd.insert(1, "-k")
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=15, text=True)
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
            return False, "gh CLI not on PATH"
        result = subprocess.run(
            [gh, "repo", "view", target, "--json", "name"],
            capture_output=True,
            timeout=30,
            text=True,
        )
        if result.returncode == 0:
            return True, None
        return False, result.stderr.strip()[:200]

    def _check_pypi_package(self, target: str, _assertion: str) -> tuple[bool, str | None]:
        url = f"https://pypi.org/pypi/{target}/json"
        cmd = ["curl", "-s", "-o", os.devnull, "-w", "%{http_code}", url]
        if os.environ.get("SSL_VERIFY", "1") == "0":
            cmd.insert(1, "-k")
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=15, text=True)
            if result.returncode != 0:
                return False, f"curl exit {result.returncode}"
            code = int(result.stdout.strip())
            if code == 200:
                return True, None
            if code == 404:
                return False, f"'{target}' not on PyPI"
            return False, f"HTTP {code}"
        except FileNotFoundError:
            return False, "curl not on PATH"
        except ValueError:
            return False, f"unexpected curl output: {result.stdout.strip()[:50]}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Run factual tests for orq skills")
    parser.add_argument("--skill", help="Test one skill only")
    parser.add_argument("--type", dest="test_type", help="Run one test type only")
    parser.add_argument("--json", dest="json_output", action="store_true", help="JSON output")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--parallel", type=int, default=4, help="Max parallel workers")
    args = parser.parse_args()

    runner = FactualTestRunner(verbose=args.verbose)
    tests = runner.load_tests(skill=args.skill)
    if args.test_type:
        tests = [t for t in tests if t.test_type == args.test_type]
    if not tests:
        print("No tests found.", file=sys.stderr)
        sys.exit(1)

    results: list[TestResult] = []
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futures = {pool.submit(runner.run_test, tc): tc for tc in tests}
        for fut in as_completed(futures):
            results.append(fut.result())

    results.sort(key=lambda r: (r.skill, r.test_type, r.target))

    summary = {
        "total": len(results),
        "passed": sum(1 for r in results if r.status == "passed"),
        "failed": sum(1 for r in results if r.status == "failed"),
        "skipped": sum(1 for r in results if r.status == "skipped"),
        "errors": sum(1 for r in results if r.status == "error"),
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
            label = r.description if r.description != r.target else r.description
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
        print()

    sys.exit(1 if summary["failed"] or summary["errors"] else 0)


if __name__ == "__main__":
    main()
