"""Offline tests for the factual tester: which CLI flags the bootstrap attributes
to which command, and that the MCP schema checks fail when they should.

    uv run --no-project --with pytest --with jsonschema pytest tests/scripts -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bootstrap_factual_tests as bootstrap
import run_factual_tests as runner

FENCE = "```"


def cli_flags(body: str) -> set[tuple[str, str]]:
    rows = bootstrap.Rows()
    bootstrap.extract_cli(body, rows)
    return {(target, flag) for kind, target, flag, _ in rows.as_list() if kind == "cli_flag"}


# --- flag attribution ---


def test_bullet_flag_belongs_to_the_sections_one_command() -> None:
    body = f"## Thread\n\n{FENCE}sh\norq traces thread <id>\n{FENCE}\n\n- `--reasoning=false` drops reasoning.\n"
    assert ("traces thread", "--reasoning") in cli_flags(body)


def test_command_below_the_flag_still_owns_it() -> None:
    body = "## Thread\n\n- `--max-chars` cuts each block.\n\nRun `orq traces thread <id>`.\n"
    assert ("traces thread", "--max-chars") in cli_flags(body)


def test_short_and_long_form_both_become_rows() -> None:
    body = "## Thread\n\n`orq traces thread <id>`\n\n- `-i/--include` renders only these parts.\n"
    assert {("traces thread", "-i"), ("traces thread", "--include")} <= cli_flags(body)


def test_section_with_two_commands_attributes_no_bare_flag() -> None:
    body = "## Traces\n\n`orq traces thread <id>` and `orq traces search`.\n\n- `--max-chars` cuts each block.\n"
    assert not any(flag == "--max-chars" for _, flag in cli_flags(body))


def test_bare_flag_does_not_cross_a_heading() -> None:
    body = "## Thread\n\n`orq traces thread <id>`\n\n## Other\n\n- `--max-chars` cuts each block.\n"
    assert not any(flag == "--max-chars" for _, flag in cli_flags(body))


def test_hash_comment_in_a_code_fence_is_not_a_heading() -> None:
    body = f"## Thread\n\n{FENCE}sh\norq traces thread <id>\n# a shell comment\n{FENCE}\n\n- `--max-chars` cuts each block.\n"
    assert ("traces thread", "--max-chars") in cli_flags(body)


def test_negated_flag_is_skipped() -> None:
    body = "## Search\n\n`orq traces search`\n\nThere is no `-q` flag.\n"
    assert ("traces search", "-q") not in cli_flags(body)


# --- MCP schema checks ---


TOOLS = {
    "no_schema": {"name": "no_schema"},
    "null_schema": {"name": "null_schema", "inputSchema": None},
    "list_skills": {"inputSchema": {"type": "object", "properties": {"limit": {"type": "integer"}}}},
}


def run(monkeypatch: pytest.MonkeyPatch, test_type: str, target: str, assertion: str) -> runner.TestResult:
    factual = runner.FactualTestRunner()
    monkeypatch.setattr(factual, "_mcp_tools", lambda: TOOLS)
    return factual.run_test(runner.TestCase(skill="t", test_type=test_type, target=target, assertion=assertion, description=""))


@pytest.mark.parametrize("test_type", ["mcp_tool_param", "mcp_tool_args"])
@pytest.mark.parametrize("target", ["no_schema", "null_schema"])
def test_missing_or_null_schema_fails_as_drift(monkeypatch: pytest.MonkeyPatch, test_type: str, target: str) -> None:
    assertion = "limit" if test_type == "mcp_tool_param" else "{}"
    result = run(monkeypatch, test_type, target, assertion)
    assert (result.status, result.error) == ("failed", f"{target} declares no inputSchema")


def test_tool_args_with_an_undeclared_name_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    result = run(monkeypatch, "mcp_tool_args", "list_skills", '{"paginated": true}')
    assert (result.status, result.error) == ("failed", "list_skills has no parameter paginated")


def test_tool_args_with_a_wrong_type_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    result = run(monkeypatch, "mcp_tool_args", "list_skills", '{"limit": "ten"}')
    assert result.status == "failed"


def test_valid_tool_args_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    assert run(monkeypatch, "mcp_tool_args", "list_skills", '{"limit": 10}').status == "passed"
