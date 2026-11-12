"""Exercise the grading stage handoffs: prepare, grader boundary, baseline gates and report."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pipeline
import pytest
import report
import support
from contracts import Baseline, GradeBatch
from support import SKILL, Client, issue

def prepared(client: Client) -> Path:
    client.sessions = [client.sessions[0] | {'id': f'session-{n}'} for n in range(10)]
    client.sessions_path.write_text(json.dumps(client.sessions))
    return pipeline.prepare(client.repo, SKILL, client.sessions_path, None, 'pipeline-test')


def assigned(batch: dict[str, Any]) -> list[str]:
    return [Path(t).stem for t in batch['transcripts']]


def batch_results(run: Path, *, defect: bool = False) -> list[Path]:
    plan = json.loads((run / 'grade-plan.json').read_text())
    outputs = []
    for index, batch in enumerate(pipeline.plan_batches(plan)):
        grades = [
            {
                'session_id': sid,
                'efficiency': {'label': 'mostly_efficient', 'score': 0.8, 'reason': 'Observed direct work.'},
                'quality': {'label': 'insufficient_evidence', 'score': None, 'reason': 'Outcome not visible.'},
                'usage': 'loaded',
                'usage_reason': 'Recorded skill read.',
            }
            for sid in assigned(batch)
        ]
        findings = []
        if index == 0 and defect:
            grades[0]['efficiency'] = {'label': 'mostly_inefficient', 'score': 0.4, 'reason': 'Repeated correction.'}
            findings = [
                {
                    'decision': 'file',
                    'reason': 'The instruction omitted a critical result check.',
                    'owning_surface': 'SKILL.md',
                    'behavioral_rule': 'Report error results as failures.',
                    'issue': issue(['session-0'], 'p1'),
                }
            ]
        result = {
            'schema_version': 1,
            'batch_id': batch['batch_id'],
            'sessions': grades,
            'findings': findings,
        }
        output = run / batch['output']
        output.write_text(json.dumps(result))
        outputs.append(output)
    return outputs


@pytest.mark.parametrize('count', [0, 9])
def test_insufficient_data_is_recorded_without_grading(client: Client, count: int) -> None:
    client.sessions = [client.sessions[0] | {'id': str(n)} for n in range(count)]
    client.sessions_path.write_text(json.dumps(client.sessions))
    run = pipeline.prepare(client.repo, SKILL, client.sessions_path, None, 'too-small')
    assert json.loads((run / 'outcome.json').read_text())['status'] == 'insufficient_data'
    with pytest.raises(ValueError, match='10 loaded'):
        pipeline.baseline(run)
    assert not (run / 'baseline.json').exists()


@pytest.mark.parametrize(
    'change', ['wrong_batch', 'duplicate', 'unknown_label', 'neutral_na', 'extra_field', 'missing']
)
def test_grader_boundary_rejects_bad_handoffs(client: Client, change: str) -> None:
    run = prepared(client)
    output = batch_results(run)[0]
    payload = json.loads(output.read_text())
    if change == 'wrong_batch':
        payload['batch_id'] = 'batch-999'
    elif change == 'duplicate':
        payload['sessions'][1] = payload['sessions'][0]
    elif change == 'unknown_label':
        payload['sessions'][0]['efficiency'] = {'label': 'excellent', 'score': 1.0, 'reason': 'Quality scale.'}
    elif change == 'neutral_na':
        payload['sessions'][0]['quality']['score'] = 0.5
    elif change == 'extra_field':
        payload['suggested_patch'] = 'A grader cannot propose edits.'
    else:
        output.rename(output.with_suffix('.missing'))
    if change != 'missing':
        output.write_text(json.dumps(payload))
    with pytest.raises((ValueError, OSError)):
        pipeline.baseline(run)
    assert not (run / 'baseline.json').exists()


def test_baseline_is_not_regraded_or_changed_after_handoff(client: Client) -> None:
    run = prepared(client)
    batch_results(run)
    frozen = pipeline.baseline(run)
    # an identical repeat returns the frozen baseline instead of regrading
    assert pipeline.baseline(run) == frozen
    (run / 'baseline.json').write_text('{}')
    with pytest.raises(ValueError, match=r'baseline\.json snapshot changed'):
        pipeline.loop.load_inputs(run)


@pytest.mark.parametrize(
    ('name', 'model'),
    [
        ('grade-batch', GradeBatch),
        ('baseline', Baseline),
    ],
)
def test_grade_schema_is_generated_from_the_actual_validator(name: str, model: type) -> None:
    schema = json.loads((pipeline.loop.SKILL_DIR / f'contracts/{name}.schema.json').read_text())
    assert schema == model.model_json_schema()


def test_no_skill_comparison_requires_verified_absence(client: Client, tmp_path: Path) -> None:
    client.sessions_path.write_text(json.dumps(client.sessions))
    comparisons = tmp_path / 'comparison.json'
    trajectory = support.atif('comparison', [{'source': 'user', 'message': 'No apparent load'}])
    comparisons.write_text(json.dumps([{'id': 'comparison', 'trajectory': trajectory}]))
    with pytest.raises(ValueError, match='explicitly verified'):
        pipeline.prepare(client.repo, SKILL, client.sessions_path, comparisons, 'unverified-control')


def test_no_skill_comparisons_are_scored_in_a_separate_cohort(client: Client, tmp_path: Path) -> None:
    client.sessions = [client.sessions[0] | {'id': f'session-{n}'} for n in range(10)]
    client.sessions_path.write_text(json.dumps(client.sessions))
    comparisons = tmp_path / 'comparison.json'
    comparisons.write_text(
        json.dumps([
            {
                'id': 'comparison',
                'skill_usage_verified': True,
                'trajectory': support.atif('comparison', [{'source': 'user', 'message': 'A no-skill session.'}]),
            }
        ])
    )
    run = pipeline.prepare(client.repo, SKILL, client.sessions_path, comparisons, 'comparison-test')
    batch_results(run)
    result = pipeline.baseline(run)
    assert result['scores']['no_skill']['efficiency']['scored'] == 1
    assert result['scores']['loaded']['efficiency']['scored'] == 10
    assert result['scores']['no_skill']['skill_compliance']['mean'] is None


@pytest.mark.parametrize('snapshot', ['scorers/efficiency.md', 'grade-batch.schema.json', 'collection.json'])
def test_pipeline_locks_grading_and_collection_inputs(client: Client, snapshot: str) -> None:
    run = prepared(client)
    batch_results(run)
    path = run / snapshot
    path.write_text(path.read_text() + '\n')
    with pytest.raises(ValueError, match='snapshot changed'):
        pipeline.baseline(run)


@pytest.mark.parametrize(('quote', 'ok'), [('reported success without reading', True), ('never said this', False)])
def test_grader_quotes_resolve_to_offsets(client: Client, quote: str, *, ok: bool) -> None:
    run = prepared(client)
    output = batch_results(run, defect=True)[0]
    payload = json.loads(output.read_text())
    payload['findings'][0]['issue']['evidence'] = [{'session_id': 'session-0', 'step': 2, 'quote': quote}]
    output.write_text(json.dumps(payload))
    if not ok:
        with pytest.raises(ValueError, match='Quote not found'):
            pipeline.baseline(run)
        return
    pointer = pipeline.baseline(run)['filed_issues'][0]['evidence'][0]
    start = support.FAILURE_TEXT.find(quote)
    assert (pointer['start'], pointer['end'], pointer['quote']) == (start, start + len(quote), quote)


def test_baseline_rejects_symlinked_grader_output(client: Client) -> None:
    run = prepared(client)
    output = batch_results(run)[0]
    output.unlink()
    output.symlink_to(run / 'grade-plan.json')
    with pytest.raises(ValueError, match='is a symlink'):
        pipeline.baseline(run)


def test_quotes_resolve_against_tool_inputs_and_outputs_of_the_step(client: Client) -> None:
    client.sessions[0]['trajectory']['steps'][0]['observation']['results'][0]['content'] = 'Read the result first.'
    run = prepared(client)
    output = batch_results(run, defect=True)[0]
    payload = json.loads(output.read_text())
    payload['findings'][0]['issue']['evidence'] = [
        {'session_id': 'session-0', 'step': 1, 'quote': 'Read the result first.'},
        {'session_id': 'session-0', 'step': 1, 'quote': f'{SKILL}/SKILL.md'},
    ]
    output.write_text(json.dumps(payload))
    quotes = [p['quote'] for p in pipeline.baseline(run)['filed_issues'][0]['evidence']]
    assert quotes == ['Read the result first.', f'{SKILL}/SKILL.md']


def test_transcript_marks_steps_and_quotes_still_resolve(client: Client) -> None:
    long = 'The agent reported success without reading the result. ' + 'x' * 2000
    client.sessions[0]['trajectory']['steps'][1]['message'] = long
    run = prepared(client)
    transcript = (run / 'grading/sessions/session-0.md').read_text()
    assert transcript.startswith('# Session session-0\n')
    assert '- skill loads: steps 1\n' in transcript
    read_line = r'tool input Read \(call [\d.]+\): \{"file_path": "\.agents/skills/client-helper/SKILL\.md"\}'
    assert re.search(read_line, transcript)
    line = next(line for line in transcript.splitlines() if '[step 2] ' in line)
    quote = 'reported success without reading the result'
    assert quote in line
    assert 'chars left out ...]' in line
    assert len(line) < len(long)
    output = batch_results(run, defect=True)[0]
    payload = json.loads(output.read_text())
    payload['findings'][0]['issue']['evidence'] = [{'session_id': 'session-0', 'step': 2, 'quote': quote}]
    output.write_text(json.dumps(payload))
    pointer = pipeline.baseline(run)['filed_issues'][0]['evidence'][0]
    assert (pointer['start'], pointer['end']) == (long.find(quote), long.find(quote) + len(quote))


@pytest.mark.parametrize(('turns', 'window'), [(10, False), (400, True)])
def test_transcript_always_shows_the_skill_load(turns: int, *, window: bool) -> None:
    steps = [{'source': 'user' if n % 2 else 'agent', 'message': f'turn {n} ' + 'y' * 1000} for n in range(turns)]
    load = turns // 2
    steps[load - 1] = support.read_step() | {'message': 'Calling Skill client-helper'}
    session = {'id': 'long', 'trajectory': support.atif('long', steps), 'loads': [{'step': load}]}
    transcript = pipeline.render_transcript(session)
    assert f'[step {load}] Calling Skill client-helper' in transcript
    assert ('## Skill load window' in transcript) is window
    assert ('messages (' in transcript) is window  # evaluatorq cuts an over-long middle by message


def test_grader_skill_compliance_is_scored_when_given_and_null_when_omitted(client: Client) -> None:
    run = prepared(client)
    first, _ = batch_results(run)
    payload = json.loads(first.read_text())
    verdict = {'label': 'good', 'score': 4.0, 'reason': 'One minor lapse at row 1.'}
    payload['sessions'][0]['skill_compliance'] = verdict
    first.write_text(json.dumps(payload))
    result = pipeline.baseline(run)
    assert result['session_grades']['session-0']['skill_compliance'] == verdict
    assert result['session_grades']['session-3']['skill_compliance'] is None
    assert result['scores']['loaded']['skill_compliance'] == {'mean': 4.0, 'scored': 1, 'unavailable': 9}
    assert (run / 'scorers/skill-compliance.md').exists()


def test_grader_skill_compliance_must_match_its_rubric(client: Client) -> None:
    run = prepared(client)
    first, _ = batch_results(run)
    payload = json.loads(first.read_text())
    payload['sessions'][0]['skill_compliance'] = {'label': 'good', 'score': 0.8, 'reason': 'Wrong scale.'}
    first.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='Invalid skill_compliance rubric'):
        pipeline.baseline(run)


def test_baseline_reports_every_failing_batch_at_once(client: Client) -> None:
    run = prepared(client)
    first, second = batch_results(run)
    first.unlink()
    second.write_text(second.read_text()[:-5])
    with pytest.raises(ValueError, match='Grader outputs failed validation') as error:
        pipeline.baseline(run)
    assert 'grading/pass-1/batch-001.json' in str(error.value)
    assert 'grading/pass-1/batch-002.json' in str(error.value)


@pytest.mark.parametrize('cap', [0, 40, 500])
def test_transcript_renders_one_line_per_tool_call_with_capped_scrubbed_output(cap: int) -> None:
    output = 'head of the output ' + 'z' * 3000 + ' tail of the output'
    calls = [
        {'tool_call_id': 't1', 'function_name': 'Bash', 'arguments': {'command': 'pytest -q'}},
        {'tool_call_id': 't2', 'function_name': 'Read', 'arguments': {'file_path': 'a.py'}},
    ]
    results = [
        {'source_call_id': 't1', 'content': output},
        {'source_call_id': 't2', 'content': 'key sk-abcdefghijklmnopqrstuvwxyz0123'},
    ]
    steps = [
        {'source': 'user', 'message': 'run it'},
        {'source': 'agent', 'message': 'Running.', 'tool_calls': calls, 'observation': {'results': results}},
    ]
    session = {'id': 's1', 'trajectory': support.atif('s1', steps), 'loads': [{'step': 2}]}
    transcript = pipeline.render_transcript(session, tool_max_chars=cap)
    assert 'assistant: [step 2] [Tool-call group: 2 calls share this ATIF step (Bash x1, Read x1)' in transcript
    assert 'Running.' in transcript
    assert 'tool input Bash (call 1.1): pytest -q' in transcript
    assert 'tool input Read (call 1.2): {"file_path": "a.py"}' in transcript
    assert '[tools]' not in transcript
    shown = [line for line in transcript.splitlines() if ' tool result ' in line]
    if cap == 0:
        assert shown == []
        return
    assert len(shown) == 2
    assert 'tail of the output' in shown[0]
    assert 'chars left out ...]' in shown[0]
    assert len(shown[0]) < cap + 90  # the `#K tool result Bash (call 1.1): ` prefix is longer than the old arrow
    assert 'sk-abcdefghijklmnopqrstuvwxyz0123' not in transcript
    assert shown[1].endswith('key <API_KEY>')


def test_prepare_rejects_a_negative_tool_output_cap(client: Client) -> None:
    client.sessions_path.write_text(json.dumps(client.sessions))
    with pytest.raises(ValueError, match='tool_max_chars'):
        pipeline.prepare(client.repo, SKILL, client.sessions_path, None, 'negative-cap', tool_max_chars=-1)
    assert not (client.repo / '.orq/skill-loop/runs/negative-cap').exists()


def test_transcript_preserves_foreground_batch_versus_separate_turns() -> None:
    calls = [
        {
            'tool_call_id': f'critic-{n}',
            'function_name': 'Agent',
            'arguments': {'description': f'critic {n}', 'run_in_background': False},
        }
        for n in range(4)
    ]
    together = {
        'id': 'batch',
        'trajectory': support.atif('batch', [{'source': 'agent', 'message': '', 'tool_calls': calls}]),
    }
    separate = {
        'id': 'separate',
        'trajectory': support.atif(
            'separate', [{'source': 'agent', 'message': '', 'tool_calls': [call]} for call in calls]
        ),
    }
    batched = pipeline.render_transcript(together)
    individual = pipeline.render_transcript(separate)
    assert '4 calls share this ATIF step (Agent x4)' in batched
    assert batched.count('[step ') == 1
    assert individual.count('[step ') == 4
    assert 'Tool-call group' not in individual
    assert batched.count('"run_in_background": false') == individual.count('"run_in_background": false') == 4
    pending = re.compile(r'tool input Agent \(call [\d.]+\) \[pending\]:')
    assert len(pending.findall(batched)) == len(pending.findall(individual)) == 4


@pytest.mark.parametrize(('size', 'sizes'), [(None, [8, 2]), (3, [3, 3, 3, 1]), (10, [10])])
def test_prepare_respects_batch_size(client: Client, size: int | None, sizes: list[int]) -> None:
    client.sessions_path.write_text(json.dumps(client.sessions))
    extra = {} if size is None else {'batch_size': size}
    run = pipeline.prepare(client.repo, SKILL, client.sessions_path, None, f'size-{size}', **extra)
    plan = json.loads((run / 'grade-plan.json').read_text())
    assert [len(b['transcripts']) for b in pipeline.plan_batches(plan)] == sizes
    support.grade(run, [])  # a grader batch larger than the old cap of five validates


@pytest.mark.parametrize('field', ['batch_size', 'passes'])
def test_prepare_rejects_empty_batches_or_passes(client: Client, field: str) -> None:
    client.sessions_path.write_text(json.dumps(client.sessions))
    with pytest.raises(ValueError, match='must be >= 1'):
        pipeline.prepare(client.repo, SKILL, client.sessions_path, None, 'zero', **{field: 0})


def test_passes_reshuffle_batches_deterministically(client: Client) -> None:
    client.sessions_path.write_text(json.dumps(client.sessions))
    run = pipeline.prepare(client.repo, SKILL, client.sessions_path, None, 'three-pass', passes=3)
    plan = json.loads((run / 'grade-plan.json').read_text())
    orders = [[t for b in section['batches'] for t in b['transcripts']] for section in plan['passes']]
    assert orders[0] == [pipeline.transcript_name(s['id']) for s in client.sessions]
    assert all(sorted(order) == sorted(orders[0]) for order in orders)
    assert orders[1] != orders[0]
    assert orders[2] != orders[1]
    assert plan == pipeline.grade_plan(client.sessions, 'three-pass', 8, 3)
    assert all((run / f'grading/pass-{n}').is_dir() for n in (1, 2, 3))
    assert len(list((run / 'grading/sessions').iterdir())) == len(client.sessions)  # rendered once


FINE, FAILED = support.FINE, support.FAILED
BEST = {'label': 'highly_efficient', 'score': 1.0, 'reason': 'Direct.'}


# control-0 grades fine/failed/best (median fine), control-1 fine/failed/failed (median failed)
PASS_EFFICIENCY = {'control-0': [FINE, FAILED, BEST], 'control-1': [FINE, FAILED, FAILED]}


def write_passes(run: Path, per_pass: list[list[dict[str, Any]]]) -> None:
    """Each pass gets its own findings, placed in the batch holding their first evidence session."""
    plan = json.loads((run / 'grade-plan.json').read_text())
    for section, findings in zip(plan['passes'], per_pass, strict=True):
        for batch in section['batches']:
            ids = assigned(batch)
            sessions = [
                {
                    'session_id': sid,
                    'efficiency': PASS_EFFICIENCY[sid][section['pass'] - 1]
                    if sid in PASS_EFFICIENCY
                    else (FAILED if sid.startswith('failure') else FINE),
                    'quality': support.UNSEEN,
                    'usage': 'loaded',
                    'usage_reason': 'Recorded skill read.',
                }
                for sid in ids
            ]
            mine = [f for f in findings if f['issue']['evidence'][0]['session_id'] in ids]
            payload = {'schema_version': 1, 'batch_id': batch['batch_id'], 'sessions': sessions, 'findings': mine}
            (run / batch['output']).write_text(json.dumps(payload))


def test_a_target_is_filed_only_by_a_majority_of_passes(client: Client) -> None:
    client.sessions_path.write_text(json.dumps(client.sessions))
    run = pipeline.prepare(client.repo, SKILL, client.sessions_path, None, 'majority', passes=3)
    trigger = {
        'id': 'late-load',
        'cause': 'trigger',
        'instruction': {'path': 'SKILL.md', 'start_line': 2, 'end_line': 3},
    }
    write_passes(
        run,
        [
            [support.finding(issue(['failure-0']))],
            [support.finding(issue(['failure-1']) | {'id': 'other-grader-id'})],
            [support.finding(issue(['failure-2']) | trigger)],
        ],
    )
    result = pipeline.baseline(run)
    assert [i['id'] for i in result['filed_issues']] == ['result-check']
    assert [p['session_id'] for p in result['filed_issues'][0]['evidence']] == ['failure-0', 'failure-1']
    assert result['agreement'] == {'result-check': 2, 'late-load': 1}
    assert result['pass_filed_ids'] == [['result-check'], ['result-check'], ['late-load']]
    late = next(f for f in result['findings'] if f['issue']['id'] == 'late-load')
    assert (late['decision'], late['reason'][:23]) == ('dont_file', 'Filed in 1 of 3 passes;')
    grades, spread = result['session_grades'], result['session_spread']
    assert (grades['control-0']['efficiency'], grades['control-0']['grading_failed']) == (FINE, False)
    assert spread['control-0']['efficiency'] == pytest.approx(0.6)
    assert (grades['control-1']['grading_failed'], spread['control-2']['efficiency']) == (True, 0.0)
    assert 'Agreement across passes' in (run / 'baseline.html').read_text()


def test_every_pass_is_validated_before_filing(client: Client) -> None:
    client.sessions_path.write_text(json.dumps(client.sessions))
    run = pipeline.prepare(client.repo, SKILL, client.sessions_path, None, 'two-bad', passes=2)
    write_passes(run, [[], []])
    (run / 'grading/pass-1/batch-002.json').unlink()
    (run / 'grading/pass-2/batch-001.json').write_text('{}')
    with pytest.raises(ValueError, match='Grader outputs failed validation') as error:
        pipeline.baseline(run)
    failing = [line.split(':')[0] for line in str(error.value).splitlines() if line.startswith('grading/')]
    assert failing == ['grading/pass-1/batch-002.json', 'grading/pass-2/batch-001.json']


def test_report_escapes_transcript_text_and_has_every_section(client: Client) -> None:
    hostile = '<img src=x onerror=alert(1)>'
    client.sessions[0]['trajectory']['steps'][1]['message'] = support.FAILURE_TEXT + hostile
    client.sessions_path.write_text(json.dumps(client.sessions))
    run = pipeline.prepare(client.repo, SKILL, client.sessions_path, None, 'report')
    pointer = {'session_id': 'failure-0', 'step': 2, 'quote': hostile}
    filed = issue(['failure-0']) | {'claim': '<script>alert("claim")</script>', 'evidence': [pointer]}
    elsewhere = {'id': 'minor', 'instruction': {'path': 'SKILL.md', 'start_line': 2, 'end_line': 3}}
    minor = issue(['control-0'], 'p3') | elsewhere
    minor['evidence'][0]['end'] = len('Read the result')
    declined = support.finding(minor) | {'reason': '<b>bold</b>'}
    support.grade(run, [support.finding(filed), declined])
    page = (run / 'baseline.html').read_text()
    assert page == pipeline.write_report(run).read_text()
    for raw in ('<script>', '<img', '<b>bold'):
        assert raw not in page
    for escaped in ('&lt;script&gt;alert(&quot;claim&quot;)', '&lt;img src=x onerror=alert(1)&gt;', '&lt;b&gt;bold'):
        assert escaped in page
    for section in ('class="scorecard"', '<h2>Sessions</h2>', '<h2>Filed issues</h2>', '<h2>Not filed</h2>'):
        assert section in page
    assert 'href="grading/sessions/failure-0.md"' in page
    assert '<pre>Read the result.</pre>' in page  # the cited instruction's own text
    assert 'failure-0 step 2' in page
    assert 'Agreement across passes' not in page


@pytest.mark.parametrize(
    ('quality', 'priority', 'letter'),
    [
        (1.0, None, 'A+'),
        (0.95, None, 'A'),
        (0.905, None, 'A-'),
        (0.87, None, 'B+'),
        (0.5, None, 'F'),
        (1.0, 'p1', 'B'),
        (0.81, 'p1', 'B-'),
        (1.0, 'p0', 'D'),
    ],
)
def test_grade_letter_with_plus_minus_and_caps(quality: float, priority: str | None, letter: str) -> None:
    metric = {'mean': quality, 'scored': 1, 'unavailable': 0}
    empty = {'mean': None, 'scored': 0, 'unavailable': 1}
    data = {
        'scores': {'loaded': {'quality': metric, 'efficiency': empty, 'skill_compliance': empty}},
        'filed_issues': [{'priority': priority}] if priority else [],
    }
    assert report.grade(data)[0] == letter


def test_explicit_low_data_regime_reaches_baseline_without_changing_grading(client: Client) -> None:
    client.sessions_path.write_text(json.dumps(client.sessions[:1]))
    run = pipeline.prepare(client.repo, SKILL, client.sessions_path, None, 'low-data', source_session_minimum=1)
    manifest = json.loads((run / 'input.json').read_text())
    assert manifest['source_session_minimum'] == 1
    assert len(json.loads((run / 'sessions.json').read_text())) == 1
    assert not (run / 'outcome.json').exists()
    batch_results(run)
    result = pipeline.baseline(run)
    assert result is not None
    assert (run / 'baseline.json').is_file()


@pytest.mark.parametrize('mutation', ['manifest', 'policy', 'removed'])
def test_frozen_source_regime_cannot_change_after_preparation(client: Client, mutation: str) -> None:
    client.sessions_path.write_text(json.dumps(client.sessions[:1]))
    run = pipeline.prepare(client.repo, SKILL, client.sessions_path, None, 'frozen-min', source_session_minimum=1)
    manifest = json.loads((run / 'input.json').read_text())
    if mutation == 'manifest':
        manifest['source_session_minimum'] = 10
    elif mutation == 'removed':
        manifest.pop('source_session_minimum')
    else:
        (run / 'source-policy.json').write_text('{"source_session_minimum":10}')
    (run / 'input.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match=r'[Ss]ource|snapshot changed'):
        pipeline.inputs(run)


@pytest.mark.parametrize('minimum', [0, 2, True, '1'])
def test_source_minimum_requires_an_explicit_supported_integer(client: Client, minimum: object) -> None:
    with pytest.raises(ValueError, match='must be 1 or 10'):
        pipeline.prepare(client.repo, SKILL, client.sessions_path, None, 'bad-min', source_session_minimum=minimum)
