# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pydantic>=2",
#     "evaluatorq>=1.87",
# ]
# ///
"""Harness-independent preparation and baseline gates for skill evaluation."""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path
from statistics import median_low
from typing import Any

import loop
import report as html_report
from contracts import Baseline, GradeBatch
from evaluatorq.common.trace_document import DatasetRef, Outcome, TraceDocument, TraceMetadata
from evaluatorq.formats.atif import AtifAgent, AtifStep, AtifTrajectory
from evaluatorq.insights.transcript import DETAILED, full_conversation_view

RUBRICS = {
    'efficiency': {
        'highly_efficient': 1.0,
        'mostly_efficient': 0.8,
        'mostly_inefficient': 0.4,
        'highly_inefficient': 0.2,
    },
    'quality': {'excellent': 1.0, 'good': 0.8, 'weak': 0.4, 'poor': 0.2},
    'skill_compliance': {'ideal': 5.0, 'good': 4.0, 'mixed': 3.0, 'poor': 2.0, 'failed': 1.0},
}
SCORERS = ('efficiency', 'quality', 'skill-compliance')


def statistics(session: dict[str, Any]) -> dict[str, Any]:
    """Count structured ATIF events; do not guess tool calls from prose."""
    steps = loop.trajectory(session).steps
    calls = [call for step in steps for call in step.tool_calls or []]
    counts = Counter(json.dumps([c.function_name, c.arguments], sort_keys=True) for c in calls)
    results = [r for step in steps if step.observation for r in step.observation.results]
    return {
        'steps': len(steps),
        'characters': sum(len(loop.step_text(step)) for step in steps),
        'sources': dict(Counter(step.source for step in steps)),
        'tool_calls': len(calls),
        'repeated_tool_calls': sum(count - 1 for count in counts.values()),
        'explicit_error_outputs': sum((r.extra or {}).get('is_error') is True for r in results),
        'observed_loads': len(session.get('loads', [])),
    }


TOOL_MAX_CHARS = 500  # tool output shown per call, as Warp skill-doctor's MAX_TOOL_CHARS
VIEW_BUDGET = 75_000  # characters per rendered view, the former evaluatorq conversation_view default
BATCH_SIZE = 8  # sessions per grader
LOAD_WINDOW = (3, 30)  # steps before and after a skill load that graders always see


def view(steps: list[AtifStep], session_id: str, tool_max_chars: int) -> str:
    """evaluatorq's per-call conversation view of these steps, each message prefixed `[step N]` (its own id)."""

    def marked_message(step: AtifStep) -> str:
        calls = step.tool_calls or []
        grouping = ''
        if len(calls) > 1:
            names = ', '.join(f'{name} x{count}' for name, count in Counter(c.function_name for c in calls).items())
            grouping = (
                f'[Tool-call group: {len(calls)} calls share this ATIF step ({names}); '
                'separate tool rows are not separate model turns.] '
            )
        return f'[step {step.step_id}] {grouping}{loop.content_text(step.message)}'

    marked = [
        step.model_copy(update={'step_id': n, 'message': marked_message(step)}) for n, step in enumerate(steps, start=1)
    ]
    document = TraceDocument(
        metadata=TraceMetadata(
            trace_id=session_id,
            dataset=DatasetRef(name='skill-loop', revision='1', row_id=session_id),
            outcome=Outcome(passed=None, source='none', definition='Skill-loop sessions record no task outcome'),
        ),
        trajectory=AtifTrajectory(agent=AtifAgent(name='skill-loop', version='1'), steps=marked),
    )
    return full_conversation_view(
        document, VIEW_BUDGET, DETAILED, tools='per_call', tool_max_chars=tool_max_chars
    )


def transcript_name(session_id: str) -> str:
    return f'grading/sessions/{re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)}.md'


def render_transcript(session: dict[str, Any], tool_max_chars: int = TOOL_MAX_CHARS) -> str:
    """Header plus the per-call view; the steps around each load are repeated when the budget cut them."""
    stats = statistics(session)
    steps = loop.trajectory(session).steps
    loads = [load['step'] for load in session.get('loads', [])]
    lines = [
        f'# Session {session["id"]}',
        f'- cwd: {session.get("cwd")}',
        f'- skill loads: {"steps " + ", ".join(map(str, loads)) if loads else "(none)"}',
        f'- stats: {stats["steps"]} steps, sources {stats["sources"]}, {stats["tool_calls"]} tool calls '
        f'({stats["repeated_tool_calls"]} repeated), {stats["explicit_error_outputs"]} error outputs',
        '',
        '## Conversation',
        '',
    ]
    full = view(steps, session['id'], tool_max_chars)
    lines.append(full)
    for load in loads:
        if f'[step {load}]' in full:
            continue
        # a long session loses its middle to the view budget; never lose the load itself
        first = max(1, load - LOAD_WINDOW[0])
        window = steps[first - 1 : load + LOAD_WINDOW[1]]
        lines += ['', f'## Skill load window (steps {first}-{first + len(window) - 1})', '']
        lines.append(view(window, session['id'], tool_max_chars))
    return '\n'.join(lines) + '\n'


def batches(sessions: list[dict[str, Any]], number: int, size: int) -> list[dict[str, Any]]:
    return [
        {
            'batch_id': f'pass-{number}-{index // size + 1:03d}',
            'output': f'grading/pass-{number}/batch-{index // size + 1:03d}.json',
            'transcripts': [transcript_name(s['id']) for s in sessions[index : index + size]],
        }
        for index in range(0, len(sessions), size)
    ]


def grade_plan(sessions: list[dict[str, Any]], run_id: str, size: int, passes: int) -> dict[str, Any]:
    """Pass 1 keeps collection order; later passes reshuffle batch composition, seeded by run id and pass."""
    sections = []
    for number in range(1, passes + 1):
        order = list(sessions)
        if number > 1:
            random.Random(f'{run_id}:{number}').shuffle(order)
        sections.append({'pass': number, 'batches': batches(order, number, size)})
    return {'schema_version': 1, 'batch_size': size, 'passes': sections}


def plan_batches(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [batch for section in plan['passes'] for batch in section['batches']]


def prepare(
    repo: Path,
    skill: str,
    sessions_path: Path,
    comparisons_path: Path | None,
    run_id: str,
    tool_max_chars: int = TOOL_MAX_CHARS,
    batch_size: int = BATCH_SIZE,
    passes: int = 1,
    *,
    source_session_minimum: int = 10,
) -> Path:
    """Prepare a versioned cohort and its grading passes without modifying skills."""
    if tool_max_chars < 0:
        raise ValueError('tool_max_chars must be >= 0')
    if type(source_session_minimum) is not int or source_session_minimum not in (1, 10):
        raise ValueError('source_session_minimum must be 1 or 10')
    if batch_size < 1 or passes < 1:
        raise ValueError('batch_size and passes must be >= 1')
    comparisons = json.loads(comparisons_path.read_text()) if comparisons_path else []
    if not isinstance(comparisons, list):
        raise TypeError('Comparisons must be a JSON array')
    sessions = json.loads(sessions_path.read_text())
    if not isinstance(sessions, list):
        raise TypeError('Sessions must be a JSON array')
    ids = [session['id'] for session in sessions + comparisons]
    if len(ids) != len(set(ids)):
        raise ValueError('Cohorts need distinct session ids')
    for session in comparisons:
        if session.get('skill_usage_verified') is not True:
            raise ValueError('No-skill comparisons require explicitly verified skill usage')
        loop.trajectory(session)
        if session.get('loads') or session.get('skill_hash'):
            raise ValueError('No-skill comparisons must have no loaded skills or skill hash')
    run = loop.prepare(repo, skill, sessions_path, run_id, allow_empty=True)
    collection_path = sessions_path.parent / 'collection.json'
    collection: dict[str, Any] = (
        json.loads(collection_path.read_text())
        if collection_path.exists()
        else {
            'schema_version': 1,
            'skill': skill,
            'loaded_sessions': len(sessions),
            'complete': False,
            'limitations': ['Imported normalized sessions; caller is responsible for source load/version provenance'],
        }
    )
    if collection.get('skill') != skill or collection.get('loaded_sessions') != len(sessions):
        raise ValueError('Collection record does not describe the supplied selected-skill cohort')
    loop.write_json(run / 'collection.json', collection)
    loop.write_json(run / 'comparisons.json', comparisons)
    grade_input = {
        'schema_version': 1,
        'target_ids': [s['id'] for s in sessions],
        'comparison_ids': [s['id'] for s in comparisons],
        'statistics': {s['id']: statistics(s) for s in sessions + comparisons},
        'limitations': collection.get('limitations', [])
        + ([] if comparisons else ['No no-skill comparison group supplied'])
        + ['Weekly scorer alignment is deferred; no calibration is claimed'],
    }
    loop.write_json(run / 'grade-input.json', grade_input)
    manifest = json.loads((run / 'input.json').read_text())
    if collection.get('skill_hash', manifest['skill_hash']) != manifest['skill_hash']:
        raise ValueError('Collection record belongs to another loaded version')
    manifest.update({
        'comparisons_hash': loop.digest((run / 'comparisons.json').read_text()),
        'grade_input_hash': loop.digest((run / 'grade-input.json').read_text()),
    })
    manifest['collection_hash'] = loop.digest((run / 'collection.json').read_text())
    manifest |= {'tool_max_chars': tool_max_chars, 'batch_size': batch_size, 'passes': passes}
    copies = {f'scorers/{name}.md': loop.SKILL_DIR / 'scorers' / f'{name}.md' for name in SCORERS}
    copies['grade-batch.schema.json'] = loop.SKILL_DIR / 'contracts/grade-batch.schema.json'
    copies['agents/grader.md'] = loop.SKILL_DIR / 'agents/grader.md'
    copies['optimizer-SKILL.md'] = loop.SKILL_DIR / 'SKILL.md'
    manifest['snapshot_hashes'] = {}
    loop.write_json(run / 'source-policy.json', {'source_session_minimum': source_session_minimum})
    manifest['source_session_minimum'] = source_session_minimum
    manifest['snapshot_hashes']['source-policy.json'] = loop.digest((run / 'source-policy.json').read_text())
    for relative, source in copies.items():
        (run / relative).parent.mkdir(parents=True, exist_ok=True)
        (run / relative).write_text(source.read_text())
        manifest['snapshot_hashes'][relative] = loop.digest((run / relative).read_text())
    (run / 'grading/sessions').mkdir(parents=True)
    for session in sessions + comparisons:
        (run / transcript_name(session['id'])).write_text(render_transcript(session, tool_max_chars))
    plan = grade_plan(sessions + comparisons, run_id, batch_size, passes)
    for number in range(1, passes + 1):
        (run / f'grading/pass-{number}').mkdir()
    loop.write_json(run / 'grade-plan.json', plan)
    manifest['grade_plan_hash'] = loop.digest((run / 'grade-plan.json').read_text())
    loop.write_json(run / 'input.json', manifest)
    if len(sessions) < source_session_minimum:
        outcome = {
            'status': 'insufficient_data',
            'run_id': run_id,
            'skill': skill,
            'loaded_sessions': len(sessions),
            'minimum_sessions': source_session_minimum,
            'reason': f'Need at least {source_session_minimum} distinct same-version loaded sessions before grading',
        }
        loop.write_json(run / 'outcome.json', outcome)
    return run


def source_minimum(run: Path, manifest: dict[str, Any]) -> int:
    """Read the frozen regime; historical manifests retain their ten-session bar."""
    if 'source_session_minimum' not in manifest:
        if 'source-policy.json' in manifest.get('snapshot_hashes', {}):
            raise ValueError('Source minimum removed from frozen manifest')
        return 10
    minimum = manifest['source_session_minimum']
    if type(minimum) is not int or minimum not in (1, 10):
        raise ValueError('Invalid frozen source minimum')
    policy = run / 'source-policy.json'
    if (
        policy.is_symlink()
        or loop.digest(policy.read_text()) != manifest.get('snapshot_hashes', {}).get('source-policy.json')
        or json.loads(policy.read_text()) != {'source_session_minimum': minimum}
    ):
        raise ValueError('Frozen source minimum changed')
    return minimum


def inputs(run: Path) -> tuple[dict[str, Any], dict[str, str], dict[str, dict[str, Any]], dict[str, Any]]:
    """Read only intact snapshots and the frozen batch assignment."""
    manifest, originals, sessions = loop.load_inputs(run)
    source_minimum(run, manifest)
    for filename, key in [
        ('comparisons.json', 'comparisons_hash'),
        ('grade-input.json', 'grade_input_hash'),
        ('grade-plan.json', 'grade_plan_hash'),
        ('collection.json', 'collection_hash'),
    ]:
        if loop.digest((run / filename).read_text()) != manifest[key]:
            raise ValueError(f'{filename} snapshot changed')
    comparisons = json.loads((run / 'comparisons.json').read_text())
    sessions.update({s['id']: s for s in comparisons})
    return manifest, originals, sessions, json.loads((run / 'grade-plan.json').read_text())


def check(metric: str, verdict: dict[str, Any]) -> None:
    labels = RUBRICS[metric]
    if verdict['label'] in {'insufficient_evidence', 'not_applicable'}:
        if verdict['score'] is not None:
            raise ValueError('Unavailable scores must be null, not a neutral numeric value')
    elif verdict['label'] not in labels or labels[verdict['label']] != verdict['score']:
        raise ValueError(f'Invalid {metric} rubric label/score')


def low_score(record: dict[str, Any]) -> bool:
    return any(record[k]['score'] is not None and record[k]['score'] < 0.5 for k in ('efficiency', 'quality'))


def normalized_claim(claim: str) -> str:
    """Normalize only case and whitespace so paraphrases remain distinct claims."""
    return ' '.join(claim.casefold().split())


def same_target(a: dict[str, Any], b: dict[str, Any]) -> bool:
    # Keep the line-overlap heuristic for differently sized references to the same claim;
    # adjacent rules sharing one line stay apart.
    x, y = a['instruction'], b['instruction']
    overlap = min(x['end_line'], y['end_line']) - max(x['start_line'], y['start_line']) + 1
    shorter = min(x['end_line'] - x['start_line'], y['end_line'] - y['start_line']) + 1
    return (
        normalized_claim(a['claim']) == normalized_claim(b['claim'])
        and a['cause'] == b['cause']
        and x['path'] == y['path']
        and 2 * overlap >= shorter
    )


def validate_issue_id_consistency(findings_by_pass: list[list[dict[str, Any]]]) -> None:
    """Reject one issue id reused for conflicting claims, causes, or instruction targets."""
    identities: dict[str, dict[str, Any]] = {}
    for findings in findings_by_pass:
        for finding in findings:
            issue = finding.get('issue')
            if not isinstance(issue, dict):
                continue
            issue_id = issue['id']
            prior = identities.get(issue_id)
            if prior is None:
                identities[issue_id] = issue
            elif not same_target(prior, issue):
                raise ValueError(
                    f'Issue id {issue_id!r} is reused for a different normalized claim, cause, or instruction target'
                )


def read_batch(
    run: Path, batch: dict[str, Any], session_ids: dict[str, str], sessions: dict[str, dict[str, Any]]
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """One grader output, validated against its assignment, with quotes resolved to offsets."""
    assigned = {session_ids[t] for t in batch['transcripts']}
    if (run / batch['output']).is_symlink():
        raise ValueError(f'{batch["output"]} is a symlink; graders must write their own output')
    result = GradeBatch.model_validate_json((run / batch['output']).read_text())
    if result.batch_id != batch['batch_id']:
        raise ValueError('Grader result belongs to another batch')
    ids = [s.session_id for s in result.sessions]
    if len(ids) != len(set(ids)) or set(ids) != assigned:
        raise ValueError('Grader must score every assigned session exactly once')
    grades = {}
    for grade in result.sessions:
        record = grade.model_dump()
        for metric in RUBRICS:
            if record[metric] is not None:
                check(metric, record[metric])
        grades[grade.session_id] = record | {'low_score': low_score(record)}
    for finding in result.findings:
        for pointer in finding.issue.evidence if finding.issue else []:
            if pointer.session_id not in assigned:
                raise ValueError('Grader evidence must belong to its assigned batch')
            if pointer.quote is None:
                continue
            steps = loop.trajectory(sessions[pointer.session_id]).steps
            text = loop.step_text(steps[pointer.step - 1]) if pointer.step <= len(steps) else ''
            found = text.find(pointer.quote)
            if found < 0:
                raise ValueError(f'Quote not found in {pointer.session_id} step {pointer.step}: {pointer.quote[:60]!r}')
            pointer.start, pointer.end = found, found + len(pointer.quote)
    return grades, [finding.model_dump() for finding in result.findings]


def validate_batch(run: Path, batch_id: str) -> dict[str, Any]:
    """Preflight one grader handoff against the frozen plan and transcript evidence."""
    _, _, sessions, plan = inputs(run)
    matches = [batch for batch in plan_batches(plan) if batch['batch_id'] == batch_id]
    if len(matches) != 1:
        raise ValueError(f'Expected exactly one frozen grade-plan batch {batch_id!r}; found {len(matches)}')
    session_ids = {transcript_name(session_id): session_id for session_id in sessions}
    grades, findings = read_batch(run, matches[0], session_ids, sessions)
    return {
        'valid': True,
        'batch_id': batch_id,
        'output': matches[0]['output'],
        'sessions': len(grades),
        'findings': len(findings),
        'assignment_source': 'frozen grade-plan.json',
    }


def file_pass(
    grades: dict[str, dict[str, Any]],
    findings: list[dict[str, Any]],
    manifest: dict[str, Any],
    originals: dict[str, str],
    sessions: dict[str, dict[str, Any]],
    target_ids: list[str],
) -> list[dict[str, Any]]:
    """One pass's filing: merge graders' findings by target, then apply the evidence bar."""
    clusters: list[dict[str, Any]] = []
    for finding in findings:
        issue = finding['issue']
        if finding['decision'] != 'file':
            continue
        if issue is None:
            raise ValueError('Filed finding has no issue')
        if issue['skill_hash'] != manifest['skill_hash']:
            raise ValueError('Finding refers to a different skill version')
        instruction = issue['instruction']
        lines = originals[instruction['path']].splitlines()
        if not 1 <= instruction['start_line'] <= instruction['end_line'] <= len(lines):
            raise ValueError('Finding instruction does not resolve')
        for pointer in issue['evidence']:
            session = sessions[pointer['session_id']]
            if pointer['session_id'] not in target_ids:
                raise ValueError('No-skill comparison cannot establish a defect in a loaded version')
            steps = loop.trajectory(session).steps
            step, start, end = pointer['step'], pointer['start'], pointer['end']
            text = loop.step_text(steps[step - 1]) if step <= len(steps) else ''
            if start is None or end is None or not 0 <= start < end <= len(text):
                raise ValueError('Finding evidence pointer does not resolve')
            pointer['quote'] = text[start:end]
        # Graders in different batches never share ids, so merge by cause and overlapping instruction lines.
        previous = next((c for c in clusters if same_target(c, issue)), None)
        if previous:
            issue['id'] = previous['id']
            previous['priority'] = min(previous['priority'], issue['priority'])
            previous['evidence'] += [p for p in issue['evidence'] if p not in previous['evidence']]
        else:
            clusters.append(issue)
    filed = []
    for issue in clusters:
        # An ignored rule is the signal itself; other causes count only low-score sessions
        # (efficiency or quality below 0.5).
        # Uncounted pointers are dropped, not rejected: one stray citation should not fail a whole batch.
        counted = [
            p
            for p in issue['evidence']
            if issue['cause'] == 'ignored_rule' or grades[p['session_id']]['low_score']
        ]
        count = len({p['session_id'] for p in counted})
        if count >= 3 or (count and issue['priority'] in {'p0', 'p1'}):
            filed.append(issue | {'evidence': counted})
        else:
            for finding in findings:
                if finding['issue'] and finding['issue']['id'] == issue['id']:
                    finding['decision'] = 'dont_file'
                    finding['reason'] = 'Fewer than three distinct sessions; ' + finding['reason']
    return filed


def median_grades(
    per_pass: list[dict[str, dict[str, Any]]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, float | None]]]:
    """Per session and metric, the median verdict across passes (median_low keeps a real rubric label)."""
    grades, spread = {}, {}
    for sid in per_pass[0]:
        records = [grades_[sid] for grades_ in per_pass]
        record, spread[sid] = dict(records[0]), {}
        for metric in RUBRICS:
            scored = [r[metric] for r in records if r[metric] and r[metric]['score'] is not None]
            scores = [v['score'] for v in scored]
            if scored:
                record[metric] = next(v for v in scored if v['score'] == median_low(scores))
            spread[sid][metric] = max(scores) - min(scores) if scores else None
        usage = Counter(r['usage'] for r in records).most_common(1)[0][0]
        record |= {'usage': usage, 'usage_reason': next(r['usage_reason'] for r in records if r['usage'] == usage)}
        grades[sid] = record | {'low_score': low_score(record)}
    return grades, spread


def agree(
    filed_by_pass: list[list[dict[str, Any]]], findings_by_pass: list[list[dict[str, Any]]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[list[str]], dict[str, int]]:
    """File a target only when a strict majority of passes filed it; evidence is the union."""
    groups: list[dict[str, Any]] = []
    owner: dict[tuple[int, str], dict[str, Any]] = {}
    for number, filed in enumerate(filed_by_pass, start=1):
        for issue in filed:
            group = next((g for g in groups if same_target(g['issue'], issue)), None)
            if group is None:
                group = {'issue': issue, 'passes': set()}
                if any(g['issue']['id'] == issue['id'] for g in groups):
                    group['issue'] = issue | {'id': f'{issue["id"]}-pass-{number}'}
                groups.append(group)
            else:
                group['issue']['priority'] = min(group['issue']['priority'], issue['priority'])
                group['issue']['evidence'] += [p for p in issue['evidence'] if p not in group['issue']['evidence']]
            group['passes'].add(number)
            owner[number, issue['id']] = group
    passes = len(filed_by_pass)
    findings = []
    for number, pass_findings in enumerate(findings_by_pass, start=1):
        for finding in pass_findings:
            group = owner.get((number, finding['issue']['id'])) if finding['decision'] == 'file' else None
            if group:
                finding['issue']['id'] = group['issue']['id']
                if 2 * len(group['passes']) <= passes:
                    finding['decision'] = 'dont_file'
                    finding['reason'] = f'Filed in {len(group["passes"])} of {passes} passes; ' + finding['reason']
            findings.append(finding)
    filed = [g['issue'] for g in groups if 2 * len(g['passes']) > passes]
    pass_filed_ids = [sorted(g['issue']['id'] for g in groups if n in g['passes']) for n in range(1, passes + 1)]
    return filed, findings, pass_filed_ids, {g['issue']['id']: len(g['passes']) for g in groups}


def baseline(run: Path) -> dict[str, Any]:
    """Validate every pass's grader outputs, file per pass, combine passes, then freeze one baseline."""
    manifest, originals, sessions, plan = inputs(run)
    if manifest.get('baseline_hash'):
        # Repeating the freeze with the same inputs returns the frozen result; anything else is a regrade.
        frozen = run / 'baseline.json'
        if frozen.is_file() and loop.digest(frozen.read_text()) == manifest['baseline_hash']:
            return json.loads(frozen.read_text())
        raise ValueError('Baseline already frozen with different inputs; use a new run id to regrade')
    grade_input = json.loads((run / 'grade-input.json').read_text())
    minimum = source_minimum(run, manifest)
    if len(grade_input['target_ids']) < minimum:
        raise ValueError(f'Need at least {minimum} loaded sessions')
    session_ids = {transcript_name(sid): sid for sid in sessions}
    per_pass, errors = [], []
    for section in plan['passes']:
        grades, findings = {}, []
        for batch in section['batches']:
            try:
                batch_grades, batch_findings = read_batch(run, batch, session_ids, sessions)
            except (ValueError, OSError) as error:
                errors.append(f'{batch["output"]}: {error}')
                continue
            grades |= batch_grades
            findings += batch_findings
        per_pass.append((grades, findings))
    if errors:
        # all batches of all passes at once, so one repair round can fix every grader
        raise ValueError('Grader outputs failed validation:\n' + '\n'.join(errors))
    validate_issue_id_consistency([findings for _, findings in per_pass])
    filed_by_pass = [
        file_pass(grades, findings, manifest, originals, sessions, grade_input['target_ids'])
        for grades, findings in per_pass
    ]
    filed, findings, pass_filed_ids, agreement = agree(filed_by_pass, [f for _, f in per_pass])
    grades, spread = median_grades([g for g, _ in per_pass])

    def averages(ids: list[str]) -> dict[str, Any]:
        values = {}
        for metric in RUBRICS:
            scores = [grades[s][metric]['score'] for s in ids if (grades[s][metric] or {}).get('score') is not None]
            values[metric] = {
                'mean': sum(scores) / len(scores) if scores else None,
                'scored': len(scores),
                'unavailable': len(ids) - len(scores),
            }
        return values

    report = {
        'schema_version': 1,
        'skill': manifest['skill'],
        'skill_hash': manifest['skill_hash'],
        'passes': len(plan['passes']),
        'batch_size': plan['batch_size'],
        'source_hashes': {
            k: manifest[k]
            for k in (
                'sessions_hash',
                'rules_hash',
                'comparisons_hash',
                'grade_plan_hash',
                'grade_input_hash',
                'collection_hash',
            )
        }
        | manifest['snapshot_hashes'],
        'scores': {'loaded': averages(grade_input['target_ids']), 'no_skill': averages(grade_input['comparison_ids'])},
        'statistics': grade_input['statistics'],
        'session_grades': grades,
        'session_spread': spread,
        'findings': findings,
        'filed_issues': filed,
        'pass_filed_ids': pass_filed_ids,
        'agreement': agreement,
        'top_issue_ids': [i['id'] for i in sorted(filed, key=lambda i: (i['priority'], -len(i['evidence'])))[:3]],
        'limitations': grade_input['limitations'],
        'weekly_average': None,
    }
    Baseline.model_validate(report)
    loop.write_json(run / 'baseline.json', report)
    lines = [
        '# Skill baseline',
        '',
        f'Skill: `{manifest["skill"]}` at `{manifest["skill_hash"]}`',
        '',
        f'Passes: {report["passes"]}, batch size {report["batch_size"]}; agreement: {agreement or "none filed"}',
        '',
        'Raw scores (no curve):',
        '',
        '| Cohort | Metric | Mean | Scored | Unavailable |',
        '| --- | --- | --- | --- | --- |',
    ]
    for cohort, metrics in report['scores'].items():
        for metric, value in metrics.items():
            lines.append(f'| {cohort} | {metric} | {value["mean"]} | {value["scored"]} | {value["unavailable"]} |')
    lines += ['', '## Findings', '']
    for finding in findings:
        lines.append(
            f'- {finding["decision"]}: {finding["reason"]} — {finding["owning_surface"]}; rule: {finding["behavioral_rule"]}'
        )
    lines += ['', '## Filed issues', ''] + [
        f'- {i["id"]} ({i["priority"]}): {i["claim"]}' for i in filed
    ]
    lines += ['', '## Limits', ''] + ['- ' + limit for limit in report['limitations']]
    (run / 'baseline.md').write_text('\n'.join(lines) + '\n')
    manifest['baseline_hash'] = loop.digest((run / 'baseline.json').read_text())
    loop.write_json(run / 'input.json', manifest)
    write_report(run)
    return report


def write_report(run: Path) -> Path:
    """RUN/baseline.html from the frozen baseline.json, with each cited instruction's text."""
    data = json.loads((run / 'baseline.json').read_text())
    issues = data['filed_issues'] + [f['issue'] for f in data['findings'] if f['issue']]
    texts = {}
    for issue in issues:
        path = issue['instruction']['path']
        if path not in texts:
            source = loop.safe_path(run / 'original', path)
            texts[path] = source.read_text().splitlines() if source.is_file() else []
    links = {sid: transcript_name(sid) for sid in data['session_grades']}
    page = html_report.render(data, texts, links)
    (run / 'baseline.html').write_text(page)
    return run / 'baseline.html'


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--repo', type=Path, required=True)
    prep.add_argument('--skill', required=True)
    prep.add_argument('--sessions', type=Path, required=True)
    prep.add_argument('--comparisons', type=Path)
    prep.add_argument('--run-id', required=True)
    prep.add_argument('--tool-max-chars', type=int, default=TOOL_MAX_CHARS, help='tool output chars per call')
    prep.add_argument('--batch-size', type=int, default=BATCH_SIZE, help='sessions per grader')
    prep.add_argument('--passes', type=int, default=1, help='independent grading passes, reshuffled batches')
    prep.add_argument('--source-session-minimum', type=int, choices=(1, 10), default=10)
    base = sub.add_parser('baseline')
    base.add_argument('--run', type=Path, required=True)
    sub.add_parser('report').add_argument('--run', type=Path, required=True)
    validate = sub.add_parser('validate-batch', help='validate one grader output against the frozen batch assignment')
    validate.add_argument('--run', type=Path, required=True)
    validate.add_argument('--batch-id', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            run = prepare(
                args.repo,
                args.skill,
                args.sessions,
                args.comparisons,
                args.run_id,
                args.tool_max_chars,
                args.batch_size,
                args.passes,
                source_session_minimum=args.source_session_minimum,
            )
            result = {'run': str(run)}
        elif args.command == 'report':
            result = {'report': str(write_report(args.run.resolve()))}
        elif args.command == 'validate-batch':
            result = validate_batch(args.run.resolve(), args.batch_id)
        else:
            result = baseline(args.run.resolve())
        print(json.dumps(result, indent=2))
    except (TypeError, ValueError, KeyError, OSError) as error:
        print(f'Skill pipeline failed: {error}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
