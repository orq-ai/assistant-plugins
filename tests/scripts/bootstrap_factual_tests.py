#!/usr/bin/env python3
"""Bootstrap factual test CSVs from SKILL.md files.

Scans each skill's SKILL.md (and resources/) for references to MCP tools,
SDK imports, CLI subcommands, documentation URLs, GitHub repos, and PyPI
packages. Writes one CSV per skill to tests/factual/<skill>.csv.

This is a one-time generation step. Review the output, prune false positives,
then commit the CSVs. Re-running overwrites existing CSVs.

Usage:
    python tests/scripts/bootstrap_factual_tests.py              # generate
    python tests/scripts/bootstrap_factual_tests.py --dry-run    # preview
    python tests/scripts/bootstrap_factual_tests.py --skill X    # one skill
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
FACTUAL_DIR = REPO_ROOT / "tests" / "factual"

# --- Patterns ---

MCP_TOOL_RE = re.compile(r"mcp__[\w-]+__(\w+)")
IMPORT_FROM_RE = re.compile(r"`?from\s+([\w.]+)\s+import\s+([\w*]+)`?")
IMPORT_BARE_RE = re.compile(r"`?import\s+([\w.]+)`?")
METHOD_CHAIN_RE = re.compile(r"(?:orq|Orq\(\))\.(\w+(?:\.\w+)*)\(")
CLIENT_CHAIN_RE = re.compile(r"client\.(\w+(?:\.\w+)*)\(")
CLI_CMD_RE = re.compile(r"`orq[ \t]+([\w][\w-]*(?:[ \t]+[\w][\w-]*)?)`")
CLI_BARE_RE = re.compile(r"(?:^|[|])\s*orq[ \t]+([\w][\w-]*(?:[ \t]+[\w][\w-]*)?)", re.MULTILINE)
URL_RE = re.compile(r"https?://[\w./:%-]+(?:\?[\w=&.%-]*)?")
GITHUB_REPO_RE = re.compile(r"github\.com/([\w-]+/[\w.-]+)")
PYPI_RE = re.compile(r"pip\s+install\s+['\"]?([\w-]+)")

PROSE_NOISE = frozenset(
    {
        "ai",
        "the",
        "a",
        "an",
        "and",
        "or",
        "to",
        "for",
        "in",
        "is",
        "it",
        "not",
        "do",
        "if",
        "of",
        "on",
        "at",
        "by",
        "no",
        "as",
        "so",
        "up",
        "be",
        "CLI",
        "cli",
        "SDK",
        "API",
    }
)

URL_SKIP = frozenset(
    {
        "api.orq.ai",
        "my.orq.ai",
        "localhost",
        "127.0.0.1",
        "example.com",
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


def extract_mcp_tools(frontmatter: dict[str, str]) -> list[TestRow]:
    rows: list[TestRow] = []
    allowed = frontmatter.get("allowed-tools", "")
    seen: set[str] = set()
    for m in MCP_TOOL_RE.finditer(allowed):
        tool = m.group(1)
        if tool not in seen:
            seen.add(tool)
            rows.append(("mcp_tool_exists", tool, "", f"{tool} listed on MCP server"))
    return rows


def extract_sdk_imports(body: str) -> list[TestRow]:
    rows: list[TestRow] = []
    seen: set[str] = set()
    for m in IMPORT_FROM_RE.finditer(body):
        module, name = m.group(1), m.group(2)
        if not module.startswith("orq"):
            continue
        key = f"{module}.{name}"
        if key not in seen:
            seen.add(key)
            rows.append(("sdk_import", key, "", f"{name} importable from {module}"))
    for m in IMPORT_BARE_RE.finditer(body):
        module = m.group(1)
        if module.startswith("orq") and module not in seen:
            seen.add(module)
            rows.append(("sdk_import", module, "", f"{module} importable"))
    return rows


NOT_SDK_METHODS = frozenset({"post", "get", "put", "delete", "patch", "chat", "common"})


def extract_sdk_methods(body: str) -> list[TestRow]:
    rows: list[TestRow] = []
    seen: set[str] = set()

    has_orq_import = "orq_ai_sdk" in body or "from orq" in body.lower()
    patterns = [METHOD_CHAIN_RE]
    if has_orq_import:
        patterns.append(CLIENT_CHAIN_RE)

    for pat in patterns:
        for m in pat.finditer(body):
            start = m.start()
            line_start = body.rfind("\n", 0, start) + 1
            line_end = body.find("\n", start)
            if line_end == -1:
                line_end = len(body)
            context = (body[max(0, start - 80) : start] + body[line_start:line_end]).lower()
            if any(kw in context for kw in ("never", "not", "does not exist", "wrong", "incorrect", "don't", "avoid")):
                continue
            chain = m.group(1)
            top = chain.split(".")[0]
            if top in NOT_SDK_METHODS:
                continue
            key = f"orq_ai_sdk.Orq.{top}"
            if key not in seen:
                seen.add(key)
                rows.append(("sdk_method", "orq_ai_sdk.Orq", top, f"Orq client has .{top}"))
    return rows


def extract_cli_commands(body: str) -> list[TestRow]:
    rows: list[TestRow] = []
    seen: set[str] = set()
    for pat in (CLI_CMD_RE, CLI_BARE_RE):
        for m in pat.finditer(body):
            cmd = m.group(1)
            words = cmd.split()
            first = words[0]
            if first in PROSE_NOISE:
                continue
            if any(w[0].isupper() for w in words):
                continue
            if cmd not in seen:
                seen.add(cmd)
                rows.append(("cli_subcommand", cmd, "", f"orq {cmd} exists"))
    return rows


def extract_doc_urls(body: str) -> list[TestRow]:
    rows: list[TestRow] = []
    seen: set[str] = set()
    for m in URL_RE.finditer(body):
        url = m.group(0).rstrip(".),;'\"")
        if any(skip in url for skip in URL_SKIP):
            continue
        if url.endswith("/package/") or url.endswith("/package"):
            continue
        if url not in seen:
            seen.add(url)
            from urllib.parse import urlparse
            path = urlparse(url).path.rstrip("/")
            slug = path.rsplit("/", 1)[-1] if path else urlparse(url).netloc
            rows.append(("doc_url", url, "", f"{slug} page reachable"))
    return rows


def extract_github_repos(body: str) -> list[TestRow]:
    rows: list[TestRow] = []
    seen: set[str] = set()
    for m in GITHUB_REPO_RE.finditer(body):
        repo = m.group(1).rstrip(".),;")
        if repo not in seen:
            seen.add(repo)
            rows.append(("github_repo", repo, "", f"{repo} exists"))
    return rows


def extract_pypi_packages(body: str) -> list[TestRow]:
    rows: list[TestRow] = []
    seen: set[str] = set()
    for m in PYPI_RE.finditer(body):
        pkg = m.group(1)
        if pkg not in seen:
            seen.add(pkg)
            rows.append(("pypi_package", pkg, "", f"{pkg} on PyPI"))
    return rows


def process_skill(skill_dir: Path) -> list[TestRow]:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return []

    content = skill_md.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(content)

    resources = skill_dir / "resources"
    if resources.exists():
        for f in sorted(resources.glob("*.md")):
            body += "\n" + f.read_text(encoding="utf-8")

    rows: list[TestRow] = []
    rows.extend(extract_mcp_tools(frontmatter))
    rows.extend(extract_sdk_imports(body))
    rows.extend(extract_sdk_methods(body))
    rows.extend(extract_cli_commands(body))
    rows.extend(extract_doc_urls(body))
    rows.extend(extract_github_repos(body))
    rows.extend(extract_pypi_packages(body))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap factual test CSVs")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--skill", help="Process one skill only")
    args = parser.parse_args()

    FACTUAL_DIR.mkdir(parents=True, exist_ok=True)

    total = 0
    skills_written = 0

    dirs = sorted(SKILLS_DIR.iterdir()) if not args.skill else [SKILLS_DIR / args.skill]
    for skill_dir in dirs:
        if not skill_dir.is_dir():
            continue

        rows = process_skill(skill_dir)
        if not rows:
            continue

        name = skill_dir.name
        csv_path = FACTUAL_DIR / f"{name}.csv"

        if args.dry_run:
            print(f"\n{name}: {len(rows)} tests")
            for r in rows:
                print(f"  {r[0]:20s} {r[1]}")
            if r[2]:
                print(f"  {'':20s}   assertion: {r[2]}")
        else:
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["test_type", "target", "assertion", "description"])
                w.writerows(rows)
            print(f"  {name}: {len(rows)} tests -> {csv_path.relative_to(REPO_ROOT)}")

        total += len(rows)
        skills_written += 1

    print(f"\nTotal: {total} test cases across {skills_written} skills")


if __name__ == "__main__":
    main()
