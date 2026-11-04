"""Runs the bundle gate's checks and the SKILL.md rubric template in CI."""

import ast
import importlib.util
import re
import sys
from pathlib import Path

import pytest
from evaluatorq.llm_jury import _validate_classify_configuration
from evaluatorq.types import DataPoint, DataPointResult, EvaluationResult, EvaluatorScore, JobResult

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


def test_directory_symlink_says_what_it_is(tmp_path: Path) -> None:
    target = tmp_path.parent / f'{tmp_path.name}_shared'
    target.mkdir()
    (tmp_path / 'shared_docs').symlink_to(target)
    errors = check_bundle.check(tmp_path).errors
    assert any('symlink to a directory' in e for e in errors), errors


def _step5_helpers() -> dict:
    text = (SKILL_DIR / 'SKILL.md').read_text(encoding='utf-8')
    block = re.search(r'```python\n(import math\n.*?)```', text, re.DOTALL)
    assert block, 'step 5 must ship its helpers'
    namespace: dict = {}
    exec(block.group(1), namespace)  # noqa: S102 - our own SKILL.md, tested as written
    return namespace


def _job(name: str, value: float | None = None, *, error: str | None = None, score_error: str | None = None) -> JobResult:
    scores = None
    if value is not None or score_error:
        scores = [
            EvaluatorScore(
                evaluator_name='task-rubric',
                score=EvaluationResult(value=value if value is not None else 0.0),
                error=score_error,
            )
        ]
    return JobResult(job_name=name, output='answer', error=error, evaluator_scores=scores)


def _point(*jobs: JobResult) -> DataPointResult:
    return DataPointResult(data_point=DataPoint(inputs={'text': 'q'}), job_results=list(jobs))


def test_step5_counts_scored_errored_and_skipped() -> None:
    table = _step5_helpers()['rubric_scores']([
        _point(_job('a', 0.9), _job('b', 0.4)),
        _point(_job('a', 0.8), _job('b', error='boom')),
        _point(_job('a', score_error='no levels'), _job('b')),
    ])
    assert (len(table['a']['scored']), table['a']['errored'], table['a']['skipped']) == (2, 1, 0)
    assert (len(table['b']['scored']), table['b']['errored'], table['b']['skipped']) == (1, 1, 1)


def test_step5_gap_is_paired_and_refuses_under_two_shared_datapoints() -> None:
    helpers = _step5_helpers()
    table = helpers['rubric_scores']([
        _point(_job('a', 0.9), _job('b', 0.5)),
        _point(_job('a', 0.7), _job('b', 0.5)),
    ])
    gap, standard_error = helpers['paired_gap'](table['a'], table['b'])
    assert gap == pytest.approx(0.3)
    assert standard_error == pytest.approx(0.1)
    assert helpers['paired_gap']({'scored': {0: 1.0}}, {'scored': {0: 0.5}}) is None


def test_rubric_template_satisfies_the_numeric_judge() -> None:
    text = (SKILL_DIR / 'SKILL.md').read_text(encoding='utf-8')
    block = re.search(r"verdict_kind='numeric',.*?levels=(\[.*?\]),", text, re.DOTALL)
    assert block, 'the rubric template must set levels'
    assert len(ast.literal_eval(block.group(1))) >= 2, 'a score needs at least two anchors'
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
