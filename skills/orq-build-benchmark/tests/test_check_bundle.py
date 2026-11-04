"""Runs the bundle gate's checks and the SKILL.md rubric template in CI."""

import ast
import importlib.util
import re
import sys
from pathlib import Path

import pytest
from evaluatorq.llm_jury import _validate_classify_configuration

SKILL_DIR = Path(__file__).resolve().parent.parent


def _load_check_bundle():
    spec = importlib.util.spec_from_file_location('check_bundle', SKILL_DIR / 'scripts' / 'check_bundle.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules['check_bundle'] = module  # dataclass resolution needs the module registered
    spec.loader.exec_module(module)
    return module


check_bundle = _load_check_bundle()


def test_selftest_passes() -> None:
    assert check_bundle._selftest() == 0


def test_broken_symlink_fails_loud(tmp_path: Path) -> None:
    (tmp_path / 'dangling_source.txt').symlink_to(tmp_path / 'missing.txt')
    errors = check_bundle.check(tmp_path).errors
    assert any('dangling_source.txt' in e for e in errors), errors


def test_rubric_template_satisfies_the_numeric_judge() -> None:
    text = (SKILL_DIR / 'SKILL.md').read_text(encoding='utf-8')
    block = re.search(r"verdict_kind='numeric',.*?levels=(\[.*?\]),", text, re.DOTALL)
    assert block, 'the rubric template must set levels'
    _validate_classify_configuration(
        criteria='criteria',
        verdict_kind='numeric',
        levels=ast.literal_eval(block.group(1)),
        score_range=(0.0, 1.0),
        label_names=None,
        threshold=0.5,
    )
    with pytest.raises(ValueError, match='levels'):
        _validate_classify_configuration(
            criteria='criteria',
            verdict_kind='numeric',
            levels=None,
            score_range=(0.0, 1.0),
            label_names=None,
            threshold=0.5,
        )
