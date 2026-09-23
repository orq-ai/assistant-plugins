#!/usr/bin/env python3
"""Bootstrap factual test CSVs from SKILL.md files.

Scans each skill's SKILL.md (and resources/) for references to MCP tools and
their parameters, SDK imports and methods, CLI subcommands and flags,
documentation URLs, GitHub repos, and PyPI/npm packages. Writes one CSV per
skill to tests/factual/<skill>.csv.

Extraction casts a wide net on purpose: a false positive costs a reviewer one
deleted row, a false negative is drift nobody checks. Review the output, prune
false positives, then commit. Existing CSVs hold that review, so they are only
overwritten with --force; check `git diff tests/factual` afterwards.

Bare MCP tool names are confirmed against the live server, so set ORQ_API_KEY.
Names the server does not list are printed, not written: add one by hand only
when the skill really means an orq MCP tool (that is drift).

Usage:
    uv run --no-project python tests/scripts/bootstrap_factual_tests.py --skill X          # new skill
    uv run --no-project python tests/scripts/bootstrap_factual_tests.py --dry-run          # preview all
    uv run --no-project python tests/scripts/bootstrap_factual_tests.py --force            # regenerate all
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
FACTUAL_DIR = REPO_ROOT / "tests" / "factual"

# --- Patterns ---

MCP_TOOL_RE = re.compile(r"mcp__[\w-]+__(\w+)")
# orq MCP tools are verb_noun; a backticked or called identifier with one of
# these verbs is a candidate even when the skill never writes the mcp__ prefix.
MCP_VERBS = ("create", "get", "list", "search", "update", "delete", "invoke", "query", "retrieve", "find")
MCP_NAME = rf"(?:{'|'.join(MCP_VERBS)})_[a-z][a-z_]*[a-z]"
MCP_BACKTICK_RE = re.compile(rf"(?<![\w.])`({MCP_NAME})(?:\(\))?`")
MCP_CALL_RE = re.compile(rf"(?<![\w.`])({MCP_NAME})\(([^()\n]*)\)")
# `search_entities` ... (`type: "evaluator"`) on one line
MCP_BACKTICK_PARAM_RE = re.compile(rf"`({MCP_NAME})`[^\n`]{{0,60}}`([a-z_][a-z0-9_]*)\s*:")
KWARG_RE = re.compile(r"(?:^|,)\s*([a-z_][a-z0-9_]*)\s*=")

IMPORT_FROM_RE = re.compile(r"\bfrom\s+([\w.]+)\s+import\s+\(?([\w*, \t]+)\)?")
IMPORT_BARE_RE = re.compile(r"(?m)^\s*import\s+([\w.]+)")
SDK_MODULE_PREFIXES = ("orq", "evaluatorq")
# The lookbehind keeps `evaluatorq.ranking.fit_bt(` from reading as `orq.ranking`.
METHOD_CHAIN_RE = re.compile(r"(?<![\w.])(?:orq|Orq\(\))\.(\w+(?:\.\w+)*)\(")
CLIENT_CHAIN_RE = re.compile(r"client\.(\w+(?:\.\w+)*)\(")

# `orq ...` inside backticks, at a line start, after a table pipe, or after a
# shell prompt/operator. The rest of the line is parsed into words and flags.
CLI_LINE_RE = re.compile(r"(?:^|[`|]|\$|&&|;|\()[ \t]*orq[ \t]+([^`\n]*)", re.MULTILINE)
CLI_WORD_RE = re.compile(r"^[a-z][a-z-]*$")
CLI_FLAG_RE = re.compile(r"^(--[a-z][a-z0-9-]*|-[a-zA-Z])(?:=.*)?$")
MAX_CLI_WORDS = 3

URL_RE = re.compile(r"https?://[\w./:%-]+(?:\?[\w=&.%-]*)?")
GITHUB_REPO_RE = re.compile(r"github\.com/([\w-]+/[\w.-]+?)(?:\.git)?(?![\w.-])")
PIP_LINE_RE = re.compile(r"(?:pip install|uv pip install|uv add|pip3 install)\s+([^\n`#|]+)")
PIP_SPEC_RE = re.compile(r"^['\"]?([A-Za-z0-9][\w.-]*)(?:\[([\w,-]+)\])?")
NPM_RE = re.compile(r"npm (?:i|install|add)(?:\s+-[gD]|\s+--save-dev)*\s+((?:@[\w-]+/)?[\w.-]+)")

# An example the next line shows failing: `orq traces search -q` / `# Error: unknown shorthand flag`.
FAILS_NEXT_LINE = ("error:", "unknown ")
NEGATIVE_CONTEXT = ("never", "not ", "does not exist", "doesn't exist", "no such", "there is no", "wrong", "incorrect", "don't", "avoid", "removed", "deprecated")

PROSE_NOISE = frozenset(
    {
        "ai", "the", "a", "an", "and", "or", "to", "for", "in", "is", "it", "not", "do", "if",
        "of", "on", "at", "by", "no", "as", "so", "up", "be", "cli", "sdk", "api",
        "platform", "workspace-level", "will", "can", "has", "was", "install", "npm",
    }
)

URL_SKIP = frozenset(
    {
        "api.orq.ai",
        "my.orq.ai",
        "localhost",
        "127.0.0.1",
        "example.com",
        ".internal",
        "semver.org",
        "agentskills.io",
        "agentplugins",
        "api.anthropic.com",
        "api.openai.com",
        "staging.orq.ai",
    }
)


def parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """Extract YAML frontmatter key-value pairs and the body."""
    if not content.startswith("---"):
        return {}, content
    end = content.find("\n---", 3)
    if end == -1:
        return {}, content

    fm: dict[str, str] = {}
    cur_key: str | None = None
    cur_parts: list[str] = []

    for line in content[4:end].split("\n"):
        if line and not line[0].isspace() and ":" in line:
            if cur_key is not None:
                fm[cur_key] = " ".join(cur_parts).strip()
            key, _, val = line.partition(":")
            cur_key = key.strip()
            val = val.strip()
            cur_parts = [] if val in (">", "|", ">-", "|-") else [val]
        elif cur_key and line.strip():
            cur_parts.append(line.strip())

    if cur_key is not None:
        fm[cur_key] = " ".join(cur_parts).strip()

    return fm, content[end + 4 :]


TestRow = tuple[str, str, str, str]  # test_type, target, assertion, description


class Rows:
    """Ordered, de-duplicated rows keyed on (type, target, assertion)."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, str], str] = {}

    def add(self, test_type: str, target: str, assertion: str, description: str) -> None:
        self._rows.setdefault((test_type, target, assertion), description)

    def as_list(self) -> list[TestRow]:
        return [(t, tg, a, d) for (t, tg, a), d in self._rows.items()]


def _line_at(body: str, pos: int) -> str:
    start = body.rfind("\n", 0, pos) + 1
    end = body.find("\n", pos)
    return body[start : end if end != -1 else len(body)]


def _negated(body: str, pos: int) -> bool:
    context = (body[max(0, pos - 80) : pos] + _line_at(body, pos)).lower()
    return any(kw in context for kw in NEGATIVE_CONTEXT)


def live_mcp_tools() -> set[str] | None:
    """Tool names the orq MCP server lists now, or None without ORQ_API_KEY."""
    api_key = os.environ.get("ORQ_API_KEY")
    if not api_key:
        return None
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from run_factual_tests import MCP_URL, MCPClient

    return set(MCPClient(MCP_URL, api_key).list_tools())


def extract_mcp(frontmatter: dict[str, str], body: str, rows: Rows, live: set[str] | None, unknown: set[str]) -> None:
    """A `mcp__server__tool` name is a claim and always becomes a row. A bare
    `verb_noun` name is only a candidate: agent built-in tools, framework
    functions and REST calls share the shape, so it becomes a row only when the
    live server lists it, and lands in `unknown` for review otherwise."""
    for text in (frontmatter.get("allowed-tools", ""), body):
        for m in MCP_TOOL_RE.finditer(text):
            rows.add("mcp_tool_exists", m.group(1), "", f"{m.group(1)} listed on MCP server")

    def candidate(tool: str) -> bool:
        if live is not None and tool in live:
            rows.add("mcp_tool_exists", tool, "", f"{tool} listed on MCP server")
            return True
        unknown.add(tool)
        return False

    for m in MCP_BACKTICK_RE.finditer(body):
        candidate(m.group(1))
    for m in MCP_CALL_RE.finditer(body):
        if candidate(m.group(1)):
            for kw in KWARG_RE.finditer(m.group(2)):
                rows.add("mcp_tool_param", m.group(1), kw.group(1), f"{m.group(1)} takes {kw.group(1)}")
    for m in MCP_BACKTICK_PARAM_RE.finditer(body):
        if candidate(m.group(1)):
            rows.add("mcp_tool_param", m.group(1), m.group(2), f"{m.group(1)} takes {m.group(2)}")


def extract_sdk_imports(body: str, rows: Rows) -> None:
    for m in IMPORT_FROM_RE.finditer(body):
        module = m.group(1)
        if not module.startswith(SDK_MODULE_PREFIXES):
            continue
        for name in (n.strip() for n in m.group(2).split(",")):
            if name and name != "*":
                rows.add("sdk_import", f"{module}.{name}", "", f"{name} importable from {module}")
    for m in IMPORT_BARE_RE.finditer(body):
        module = m.group(1)
        if module.startswith(SDK_MODULE_PREFIXES):
            rows.add("sdk_import", module, "", f"{module} importable")


NOT_SDK_METHODS = frozenset({"post", "get", "put", "delete", "patch", "chat", "common"})


def extract_sdk_methods(body: str, rows: Rows) -> None:
    patterns = [METHOD_CHAIN_RE]
    if "orq_ai_sdk" in body or "from orq" in body.lower():
        patterns.append(CLIENT_CHAIN_RE)
    for pat in patterns:
        for m in pat.finditer(body):
            if _negated(body, m.start()):
                continue
            top = m.group(1).split(".")[0]
            if top not in NOT_SDK_METHODS:
                rows.add("sdk_method", "orq_ai_sdk.Orq", top, f"Orq client has .{top}")


def _fails_next_line(body: str, pos: int) -> bool:
    end = body.find("\n", pos)
    return end != -1 and any(kw in _line_at(body, end + 1).lower() for kw in FAILS_NEXT_LINE)


def extract_cli(body: str, rows: Rows) -> None:
    for m in CLI_LINE_RE.finditer(body):
        if _fails_next_line(body, m.start()):
            continue
        tokens = m.group(1).split()
        words: list[str] = []
        for tok in tokens:
            if len(words) == MAX_CLI_WORDS or not CLI_WORD_RE.match(tok):
                break
            words.append(tok)
        if not words or words[0] in PROSE_NOISE:
            continue
        cmd = " ".join(words)
        rows.add("cli_subcommand", cmd, "", f"orq {cmd} exists")
        for tok in tokens[len(words) :]:
            if tok in ("|", ">", "<", "&&", ";", "\\"):
                break
            flag = CLI_FLAG_RE.match(tok)
            if flag:
                rows.add("cli_flag", cmd, flag.group(1), f"orq {cmd} accepts {flag.group(1)}")


def extract_doc_urls(body: str, rows: Rows) -> None:
    for m in URL_RE.finditer(body):
        url = m.group(0).rstrip(".),;'\"")
        if any(skip in url for skip in URL_SKIP):
            continue
        if url.endswith(("/package/", "/package")):
            continue
        path = urlparse(url).path.rstrip("/")
        slug = path.rsplit("/", 1)[-1] if path else urlparse(url).netloc
        rows.add("doc_url", url, "", f"{slug} page reachable")


def extract_github_repos(body: str, rows: Rows) -> None:
    for m in GITHUB_REPO_RE.finditer(body):
        repo = m.group(1).rstrip(".),;")
        rows.add("github_repo", repo, "", f"{repo} exists")


def extract_packages(body: str, rows: Rows) -> None:
    for m in PIP_LINE_RE.finditer(body):
        for spec in m.group(1).split():
            if spec.startswith("-"):
                continue
            parsed = PIP_SPEC_RE.match(spec)
            if not parsed or parsed.group(1).lower() in PROSE_NOISE or "/" in spec:
                continue
            pkg, extras = parsed.group(1), parsed.group(2)
            rows.add("pypi_package", pkg, "", f"{pkg} on PyPI")
            for extra in (extras or "").split(","):
                if extra:
                    rows.add("pypi_extra", pkg, extra, f"{pkg} has extra [{extra}]")
    for m in NPM_RE.finditer(body):
        pkg = re.sub(r"(?<=.)@[\w.-]+$", "", m.group(1))
        rows.add("npm_package", pkg, "", f"{pkg} on npm")


def process_skill(skill_dir: Path, live: set[str] | None, unknown: set[str]) -> list[TestRow]:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return []

    content = skill_md.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(content)

    resources = skill_dir / "resources"
    if resources.exists():
        for f in sorted(resources.rglob("*.md")):
            body += "\n" + f.read_text(encoding="utf-8")

    rows = Rows()
    extract_mcp(frontmatter, body, rows, live, unknown)
    extract_sdk_imports(body, rows)
    extract_sdk_methods(body, rows)
    extract_cli(body, rows)
    extract_doc_urls(body, rows)
    extract_github_repos(body, rows)
    extract_packages(body, rows)
    return rows.as_list()


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap factual test CSVs")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--skill", help="Process one skill only")
    parser.add_argument("--force", action="store_true", help="Overwrite existing (reviewed) CSVs")
    args = parser.parse_args()

    FACTUAL_DIR.mkdir(parents=True, exist_ok=True)

    live = live_mcp_tools()
    if live is None:
        print("ORQ_API_KEY not set: bare MCP tool names cannot be confirmed and are all listed for review", file=sys.stderr)

    total = 0
    skills_written = 0
    kept: list[str] = []

    dirs = sorted(SKILLS_DIR.iterdir()) if not args.skill else [SKILLS_DIR / args.skill]
    if args.skill and not dirs[0].is_dir():
        sys.exit(f"No skills/{args.skill} directory.")
    for skill_dir in dirs:
        if not skill_dir.is_dir():
            continue

        unknown: set[str] = set()
        rows = process_skill(skill_dir, live, unknown)
        name = skill_dir.name
        if unknown:
            print(f"{name}: not on the MCP server, add by hand only if it is drift: {', '.join(sorted(unknown))}", file=sys.stderr)
        csv_path = FACTUAL_DIR / f"{name}.csv"

        if args.dry_run:
            print(f"\n{name}: {len(rows)} tests")
            for r in rows:
                print(f"  {r[0]:20s} {r[1]}" + (f"  [{r[2]}]" if r[2] else ""))
            total += len(rows)
            skills_written += 1
            continue

        if csv_path.exists() and not args.force:
            kept.append(name)
            continue

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["test_type", "target", "assertion", "description"])
            w.writerows(rows)
        print(f"  {name}: {len(rows)} tests -> {csv_path.relative_to(REPO_ROOT)}")
        total += len(rows)
        skills_written += 1

    print(f"\nTotal: {total} test cases across {skills_written} skills")
    if kept:
        print(f"Kept {len(kept)} existing reviewed CSV(s), pass --force to regenerate: {', '.join(kept)}", file=sys.stderr)


if __name__ == "__main__":
    main()
