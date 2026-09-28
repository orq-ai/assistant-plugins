#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Merge a factual run and an eval run into one per-skill report (RES-1076).

Deterministic: reads the two JSON outputs, sorts every finding into one bucket per
skill, and points at the SKILL.md line that names the failing target where one
does. The skill-tests skill launches the runners and presents this; it does not
reinterpret the results.

Buckets:
  drift       factual check failed: the skill names a tool, command or URL that is gone
  regression  eval case below its pass threshold
  flaky       eval case that passed some runs and failed others
  error       factual or eval run that could not complete (not a verdict)
  measured    borderline eval case, trigger rate only
  skipped     eval case with no run scored (cost cap hit); measured nothing, so not clean

Usage:
    uv run tests/scripts/run_factual_tests.py --json > factual.json
    uv run tests/scripts/run_evals.py --json evals.json
    uv run tests/scripts/skill_test_report.py --factual factual.json --evals evals.json [--json out.json]

Either input may be omitted. Exit code: 1 when any skill has drift or a regression, else 0.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BUCKETS = ("drift", "regression", "flaky", "error", "measured", "skipped")


def skill_line(skill: str, needle: str) -> str | None:
    """`skills/<skill>/SKILL.md:<line>` of the first body line naming the needle, if any.

    Whole-token match, frontmatter skipped: a pointer to an unrelated line is worse than none.
    """
    path = REPO_ROOT / "skills" / skill / "SKILL.md"
    needle = needle.strip()
    if len(needle) < 3 or not path.exists():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    body_start = 0
    if lines and lines[0].strip() == "---":
        body_start = next((i + 1 for i in range(1, len(lines)) if lines[i].strip() == "---"), 0)
    pattern = re.compile(rf"(?<![\w-]){re.escape(needle)}(?![\w-])")
    for i in range(body_start, len(lines)):
        if pattern.search(lines[i]):
            return f"skills/{skill}/SKILL.md:{i + 1}"
    return None


def factual_findings(data: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    for r in data.get("results", []):
        if r["status"] == "failed" and r["test_type"] != "doc_url":
            bucket = "drift"
        elif r["status"] == "error":
            bucket = "error"
        else:
            continue
        findings.append(
            {
                "skill": r["skill"],
                "bucket": bucket,
                "source": "factual",
                "what": f"{r['test_type']} {r['target']}" + (f" ({r['assertion']})" if r.get("assertion") else ""),
                "detail": r.get("error") or r.get("description"),
                "where": skill_line(r["skill"], r.get("target") or ""),
            }
        )
    return findings


def _eval_pointer(skill: str, row: dict[str, Any]) -> str | None:
    """The SKILL.md line naming a tool the failing runs called or were forbidden to call."""
    for run in row["runs"]:
        for score in run["scores"].values():
            if score["pass"] is False:
                for tool in re.findall(r"\b((?:create|update|delete|invoke|list|get|search)_[a-z_]+)\b", score["why"] or ""):
                    if where := skill_line(skill, tool):
                        return where
    return None


def eval_findings(data: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    for row in data.get("cases", []):
        buckets = []
        if row["status"] == "fail":
            buckets.append("regression")
        elif row["status"] == "error":
            buckets.append("error")
        elif row["status"] == "measured":
            buckets.append("measured")
        elif row["status"] == "skipped":
            buckets.append("skipped")
        if row["flaky"] and row["status"] != "measured":
            buckets.append("flaky")
        rate = "-" if row["pass_rate"] is None else f"{row['pass_rate']:.0%}"
        failing = sorted(
            {k for r in row["runs"] for k, v in r["scores"].items() if v["pass"] is False}
            | ({"run error"} if row["errors"] else set())
        )
        for bucket in buckets:
            findings.append(
                {
                    "skill": row["skill"],
                    "bucket": bucket,
                    "source": "evals",
                    "what": f"{row['case']} [{row['agent']}] {rate} (need {row['threshold']:.0%})",
                    "detail": ", ".join(failing) or None,
                    "where": _eval_pointer(row["skill"], row) if bucket in ("regression", "flaky") else None,
                    "threads": [r["thread_id"] for r in row["runs"] if r["status"] != "pass" and r.get("thread_id")],
                }
            )
    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge factual and eval results per skill")
    parser.add_argument("--factual", type=Path, help="run_factual_tests.py --json output")
    parser.add_argument("--evals", type=Path, help="run_evals.py --json output")
    parser.add_argument("--json", dest="json_path", type=Path, help="Also write the merged report here")
    args = parser.parse_args()
    if not args.factual and not args.evals:
        sys.exit("pass --factual and/or --evals")

    findings: list[dict[str, Any]] = []
    skills: set[str] = set()
    # Skipped rows measured nothing (no ORQ_API_KEY, a host outage); a skill whose checks
    # were all skipped must not read as clean.
    skipped: dict[str, int] = {}
    if args.factual:
        factual = json.loads(args.factual.read_text(encoding="utf-8"))
        findings += factual_findings(factual)
        skills |= {r["skill"] for r in factual.get("results", [])}
        for r in factual.get("results", []):
            if r["status"] == "skipped":
                skipped[r["skill"]] = skipped.get(r["skill"], 0) + 1
    evals: dict[str, Any] = {}
    if args.evals:
        evals = json.loads(args.evals.read_text(encoding="utf-8"))
        findings += eval_findings(evals)
        skills |= {row["skill"] for row in evals.get("cases", [])}

    report: dict[str, Any] = {}
    for skill in sorted(skills):
        mine = [f for f in findings if f["skill"] == skill]
        report[skill] = {b: [f for f in mine if f["bucket"] == b] for b in BUCKETS}
        report[skill]["factual_skipped"] = skipped.get(skill, 0)
        if skill in evals.get("skills", {}):
            report[skill]["eval_summary"] = evals["skills"][skill]

    for skill, buckets in report.items():
        parts = [f"{len(buckets[b])} {b}" for b in BUCKETS if buckets[b]]
        if buckets["factual_skipped"]:
            parts.append(f"{buckets['factual_skipped']} factual skipped (not checked)")
        counts = ", ".join(parts) or "clean"
        print(f"\n{skill}: {counts}")
        for b in BUCKETS:
            for f in buckets[b]:
                where = f"  -> {f['where']}" if f.get("where") else ""
                print(f"  [{b}] {f['what']}: {f['detail']}{where}")
                for t in f.get("threads", [])[:3]:
                    print(f"      thread {t}")
    if evals.get("experiment_url"):
        print(f"\nexperiment: {evals['experiment_url']}")
    if evals:
        print(f"eval cost: ${evals.get('cost_usd', 0):.2f}" + (" (cost cap stopped some runs)" if evals.get("runs_skipped_by_cap") else ""))

    if args.json_path:
        args.json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    bad = any(buckets["drift"] or buckets["regression"] for buckets in report.values())
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
