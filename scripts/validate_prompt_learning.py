#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Validate the prompt-learning skill integration.

Checks:
1. Frontmatter matches existing skill patterns
2. All companion skill references point to existing skills
3. AGENTS.md includes the new skill entry
4. resources/meta-prompt.md is properly referenced in SKILL.md steps
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "skills" / "prompt-learning"
SKILL_MD = SKILL_DIR / "SKILL.md"
META_PROMPT = SKILL_DIR / "resources" / "meta-prompt.md"
AGENTS_MD = ROOT / "agents" / "AGENTS.md"

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"


def parse_frontmatter(text: str) -> dict[str, str]:
    match = re.search(r"^---\s*\n(.*?)\n---\s*", text, re.DOTALL)
    if not match:
        return {}
    data: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip()
    return data


def collect_existing_skills() -> set[str]:
    """Discover all skills that have a SKILL.md file."""
    skills = set()
    for skill_md in ROOT.glob("skills/*/SKILL.md"):
        meta = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        name = meta.get("name")
        if name:
            skills.add(name)
    return skills


def test_frontmatter() -> list[str]:
    """Check 1: Frontmatter matches existing skill patterns."""
    errors = []

    if not SKILL_MD.exists():
        return [f"SKILL.md not found at {SKILL_MD}"]

    text = SKILL_MD.read_text(encoding="utf-8")
    meta = parse_frontmatter(text)

    # Required fields
    for field in ("name", "description", "allowed-tools"):
        if field not in meta:
            errors.append(f"Missing frontmatter field: {field}")

    # Name should match directory
    if meta.get("name") != "prompt-learning":
        errors.append(
            f"Frontmatter name '{meta.get('name')}' doesn't match "
            f"directory 'prompt-learning'"
        )

    # Description should be non-empty
    if not meta.get("description"):
        errors.append("Frontmatter description is empty")

    # allowed-tools should contain core tools present in all skills
    allowed = meta.get("allowed-tools", "")
    for tool in ("Bash", "Read", "Write", "Edit", "Grep", "Glob", "AskUserQuestion"):
        if tool not in allowed:
            errors.append(f"allowed-tools missing core tool: {tool}")

    # Cross-check: compare against another skill's frontmatter pattern
    reference_skill = ROOT / "skills" / "optimize-prompt" / "SKILL.md"
    if reference_skill.exists():
        ref_meta = parse_frontmatter(
            reference_skill.read_text(encoding="utf-8")
        )
        ref_fields = set(ref_meta.keys())
        our_fields = set(meta.keys())
        missing = ref_fields - our_fields
        if missing:
            errors.append(
                f"Frontmatter missing fields present in optimize-prompt: {missing}"
            )

    return errors


def test_companion_skills() -> list[str]:
    """Check 2: All companion skill references point to existing skills."""
    errors = []

    if not SKILL_MD.exists():
        return [f"SKILL.md not found at {SKILL_MD}"]

    text = SKILL_MD.read_text(encoding="utf-8")
    existing = collect_existing_skills()

    # Extract companion skill names from backtick references after "Companion skills:"
    companion_section = re.search(
        r"\*\*Companion skills:\*\*\s*\n((?:- .*\n)*)", text
    )
    if not companion_section:
        errors.append("No 'Companion skills' section found")
        return errors

    companion_names = re.findall(r"`([^`]+)`", companion_section.group(1))
    if not companion_names:
        errors.append("No companion skills listed")
        return errors

    for name in companion_names:
        if name not in existing:
            errors.append(f"Companion skill '{name}' does not exist as a skill")

    # Check bidirectional: do companion skills reference us back?
    for name in companion_names:
        companion_path = ROOT / "skills" / name / "SKILL.md"
        if companion_path.exists():
            companion_text = companion_path.read_text(encoding="utf-8")
            if "prompt-learning" not in companion_text:
                errors.append(
                    f"Companion '{name}' does not reference 'prompt-learning' back"
                )

    return errors


def test_agents_md() -> list[str]:
    """Check 3: AGENTS.md has correct formatting with new entry."""
    errors = []

    if not AGENTS_MD.exists():
        return [f"AGENTS.md not found at {AGENTS_MD}"]

    text = AGENTS_MD.read_text(encoding="utf-8")

    # Check skill path entry
    expected_path = 'prompt-learning -> "skills/prompt-learning/SKILL.md"'
    if expected_path not in text:
        errors.append(f"AGENTS.md missing path entry: {expected_path}")

    # Check description entry in available_skills
    if "prompt-learning:" not in text:
        errors.append("AGENTS.md missing description entry for prompt-learning")

    # Check alphabetical ordering in the skills list
    path_entries = re.findall(
        r" - (\S+) -> ", text
    )
    if path_entries:
        sorted_entries = sorted(path_entries, key=str.lower)
        if path_entries != sorted_entries:
            errors.append("AGENTS.md skill list is not alphabetically sorted")

    # Check alphabetical ordering in available_skills
    desc_entries = re.findall(r"^(\S+):", text, re.MULTILINE)
    # Filter to only skill entries (those that have backtick descriptions)
    skill_descs = [
        e for e in desc_entries
        if f"{e}:" in text and "`" in text.split(f"{e}:")[1].split("\n")[0]
    ]
    if skill_descs:
        sorted_descs = sorted(skill_descs, key=str.lower)
        if skill_descs != sorted_descs:
            errors.append(
                "AGENTS.md available_skills descriptions not alphabetically sorted"
            )

    return errors


def test_meta_prompt_reference() -> list[str]:
    """Check 4: resources/meta-prompt.md exists and is referenced in SKILL.md."""
    errors = []

    if not META_PROMPT.exists():
        errors.append(f"Meta-prompt not found at {META_PROMPT}")
        return errors

    if not SKILL_MD.exists():
        errors.append(f"SKILL.md not found at {SKILL_MD}")
        return errors

    skill_text = SKILL_MD.read_text(encoding="utf-8")

    # Check that SKILL.md references the meta-prompt file
    if "resources/meta-prompt.md" not in skill_text:
        errors.append(
            "SKILL.md does not reference 'resources/meta-prompt.md'"
        )

    # Check that meta-prompt contains key structural elements
    meta_text = META_PROMPT.read_text(encoding="utf-8")

    required_sections = [
        "GOAL",
        "INPUTS",
        "PROCESS",
        "OUTPUT FORMAT",
        "EXAMPLE",
        "ISSUE TAXONOMY",
        "FAILURE_EXAMPLES",
        "POSITIVE_EXAMPLES",
        "RULES_TO_APPEND",
        "ITERATION_GUIDANCE",
    ]
    for section in required_sections:
        if section not in meta_text:
            errors.append(f"Meta-prompt missing required section: {section}")

    # Check that the meta-prompt has the "If [TRIGGER] then [ACTION]" rule format
    if "If [TRIGGER]" not in meta_text:
        errors.append(
            "Meta-prompt missing rule format: 'If [TRIGGER], then [ACTION]'"
        )

    # Check that LEARNED_RULES section is referenced
    if "LEARNED_RULES" not in meta_text:
        errors.append("Meta-prompt missing LEARNED_RULES reference")

    return errors


def test_cross_file_consistency() -> list[str]:
    """Check 5: Parameters and taxonomy are consistent between SKILL.md and meta-prompt.md."""
    errors = []

    if not SKILL_MD.exists() or not META_PROMPT.exists():
        return ["Cannot check consistency — files missing"]

    skill_text = SKILL_MD.read_text(encoding="utf-8")
    meta_text = META_PROMPT.read_text(encoding="utf-8")

    # Check that all taxonomy tags mentioned in meta-prompt are referenced in SKILL.md
    # Only match tags in the ISSUE TAXONOMY section (between "ISSUE TAXONOMY:" and next empty line)
    taxonomy_match = re.search(
        r"ISSUE TAXONOMY:\n((?:- \w+:.*\n)+)", meta_text
    )
    taxonomy_tags = (
        re.findall(r"^- (\w+):", taxonomy_match.group(1), re.MULTILINE)
        if taxonomy_match
        else []
    )
    for tag in taxonomy_tags:
        if tag not in skill_text:
            errors.append(
                f"Taxonomy tag '{tag}' in meta-prompt.md not referenced in SKILL.md"
            )

    # Check that SKILL.md taxonomy references match meta-prompt taxonomy
    skill_tags = re.findall(r"`(\w+)`", skill_text.split("Issue Taxonomy")[1].split("##")[0]) if "Issue Taxonomy" in skill_text else []
    meta_tags = set(taxonomy_tags)
    for tag in skill_tags:
        if tag in (
            "accuracy", "missing_requirement", "policy", "safety",
            "formatting", "verbosity", "tone", "tool_use", "reasoning",
            "hallucination",
        ) and tag not in meta_tags:
            errors.append(
                f"Taxonomy tag '{tag}' in SKILL.md not found in meta-prompt.md"
            )

    # Check that key structural elements referenced in SKILL.md steps exist in meta-prompt
    # (e.g., output sections A-F)
    for section_label in ("PATTERN_ANALYSIS", "ANCHOR_CHECK", "RULES_TO_APPEND", "REGRESSION_TESTS", "ITERATION_GUIDANCE"):
        if section_label in skill_text and section_label not in meta_text:
            errors.append(
                f"SKILL.md references output section '{section_label}' not found in meta-prompt.md"
            )

    # Check that the LEARNED_RULES format is consistent
    skill_has_learned_rules = "### LEARNED_RULES" in skill_text
    meta_has_learned_rules = "LEARNED_RULES" in meta_text
    if skill_has_learned_rules and not meta_has_learned_rules:
        errors.append("SKILL.md references LEARNED_RULES but meta-prompt.md does not")
    if meta_has_learned_rules and not skill_has_learned_rules:
        errors.append("meta-prompt.md references LEARNED_RULES but SKILL.md does not")

    return errors


def main() -> None:
    checks = [
        ("Frontmatter matches existing skill patterns", test_frontmatter),
        ("Companion skill references are valid", test_companion_skills),
        ("AGENTS.md includes prompt-learning correctly", test_agents_md),
        ("Meta-prompt is referenced and well-structured", test_meta_prompt_reference),
        ("Cross-file consistency (SKILL.md ↔ meta-prompt.md)", test_cross_file_consistency),
    ]

    total_errors = 0
    for label, check_fn in checks:
        errors = check_fn()
        if errors:
            print(f"{FAIL} {label}")
            for err in errors:
                print(f"    - {err}")
            total_errors += len(errors)
        else:
            print(f"{PASS} {label}")

    print()
    if total_errors:
        print(f"{total_errors} error(s) found.")
        sys.exit(1)
    else:
        print("All checks passed.")


if __name__ == "__main__":
    main()
