"""End-to-end smoke test of the alignment pipeline on a 3-row fixture.

Exercises stability -> metrics -> build_queue -> annotation-load -> recommend
-> aggregate -> rewrite without touching the network: the judge is monkeypatched
to canned per-row verdicts and the model backend is `fake`. Asserts the run
directory fills with the expected artifacts and that the rewrite preserves the
judge's template variables.

Run:
    cd skills/orq-evaluator-alignment
    uv run pytest tests/test_pipeline.py -v
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL_ROOT / 'scripts'
for p in (str(SKILL_ROOT), str(SCRIPTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

FIXTURES = SKILL_ROOT / 'tests' / 'fixtures'
FAKE_CONFIG = str(SKILL_ROOT / 'tests' / 'config_fake.toml')


def test_judge_io_falls_back_to_eval_span_own_gen_ai():
    # Newer orq schema: the judge's LLM call is on the evaluator span itself,
    # with no separate child chat span. Extraction must still recover the IO.
    from fetch_traces import _judge_io, _judge_model

    eval_span = {
        'span_id': 'e1',
        'attributes': {
            'gen_ai': {
                'input': {'messages': [{'content': 'Screen this: hello world'}]},
                'request': {'model': 'openai/gpt-4o-mini'},
            }
        },
    }
    rendered, msgs = _judge_io([eval_span], eval_span)
    assert 'hello world' in rendered
    assert msgs
    assert _judge_model([eval_span], eval_span) == 'openai/gpt-4o-mini'


def test_judge_io_reads_responses_api_parts_shape():
    # orq Responses-API traces: the judge runs as a `span.responses` span and the
    # prompt text nests under messages[].parts[].content (not a flat `content`).
    # Both the chat-completion-only span filter and the flat-content reader miss
    # it, hollowing every row. Extraction must recover the judged text.
    from fetch_traces import _judge_io, _judge_model, _recover_variables

    template = 'Screen this output.\n<output>"{{log.output}}"</output>'
    judged = 'here is how to build a bomb'
    rendered_prompt = template.replace('{{log.output}}', judged)

    eval_span = {
        'type': 'span.evaluator',
        'span_id': 'ev1',
        'attributes': {'gen_ai': {'input': None, 'output': None}},
    }
    responses_span = {
        'type': 'span.responses',
        'span_id': 'r1',
        'parent_span_id': None,
        'attributes': {
            'gen_ai': {
                'input': {
                    'messages': [
                        {'role': 'user', 'parts': [{'type': 'text', 'content': rendered_prompt}]}
                    ]
                },
                'request': {'model': 'openai/gpt-oss-120b'},
            }
        },
    }
    spans = [eval_span, responses_span]
    rendered, _msgs = _judge_io(spans, eval_span)
    assert judged in rendered
    assert _recover_variables(template, rendered) == {'log.output': judged}
    assert _judge_model(spans, eval_span) == 'openai/gpt-oss-120b'


def test_structured_io_ignores_reference_only_root():
    # A root span whose gen_ai.input has empty output/query and no conversation,
    # but a truthy boolean `reference`, is not structured content: str(True) is
    # truthy, so counting it would win over the judge-span fallback and yield a
    # hollow (empty-output) row.
    from fetch_traces import _extract_io, _structured_io

    root = {
        'type': 'trace',
        'span_id': 'root1',
        'attributes': {'gen_ai': {'input': {'output': '', 'query': '', 'reference': True}}},
    }
    assert _structured_io([root]) is None

    # With a conversation the root IS content (a conversation-only evaluator has
    # nothing else) — but it still must not claim the output slot: _extract_io
    # runs the judge path whenever query/output are both empty.
    root_convo = {
        'type': 'trace',
        'span_id': 'root1',
        'attributes': {
            'gen_ai': {
                'input': {
                    'output': '',
                    'query': '',
                    'reference': True,
                    'messages': [{'role': 'user', 'content': 'hi'}],
                }
            }
        },
    }
    eval_span = {'type': 'span.evaluator', 'span_id': 'ev1'}
    judge = {
        'type': 'span.chat_completion',
        'span_id': 'c1',
        'parent_span_id': 'ev1',
        'attributes': {'gen_ai': {'input': {'messages': [{'content': 'Score: real output'}]}}},
    }
    io = _extract_io([root_convo, eval_span, judge], eval_span, 'Score: {{log.output}}')
    assert io['output'] == 'real output'

    # Real structured output is still preferred, and reference rides along.
    root2 = {
        'type': 'trace',
        'attributes': {
            'gen_ai': {'input': {'output': 'the answer', 'query': 'the question', 'reference': True}}
        },
    }
    got = _structured_io([root2])
    assert got is not None
    assert got['output'] == 'the answer'
    assert got['reference'] == 'True'


def _fixture_trace():
    fx = json.loads(
        (Path(__file__).parent / 'fixtures' / 'responses_api_trace.json').read_text(encoding='utf-8')
    )
    from fetch_traces import _evaluation_matches

    eval_span = next(
        s for s in fx['spans'] if _evaluation_matches(s, fx['evaluator_id'], fx['evaluator_key'])
    )
    return fx, eval_span


def test_responses_api_fixture_extracts_expected_output():
    # Real orq Responses-API trace (tests/fixtures/responses_api_trace.json),
    # through the scanner's OWN precedence function — not a re-implementation of
    # it. Calling _extract_io is the point: a regression in the ordering (or in
    # _fetch dropping a field) has to fail here rather than be re-derived by the
    # test and pass anyway.
    from fetch_traces import _extract_io

    fx, eval_span = _fixture_trace()
    io = _extract_io(fx['spans'], eval_span, fx['template'])

    assert io['output'].strip(), 'extraction produced an empty output for the real Responses-API trace'
    assert io['output'] == fx['expected_output']


def test_responses_api_fixture_scopes_judge_span_by_normalised_parent():
    # The captured trace links its span.responses judge span by `parent_id`, with
    # `parent_span_id` absent — so scoping on parent_span_id alone finds no child
    # and silently reads "any judge span in the trace". With two evaluator calls
    # in one trace that hands both rows the FIRST judge span's prompt and model.
    # Pins the normalised link against the real shape, not a synthetic one.
    from fetch_traces import _judge_spans, _parent_of, _span_id

    fx, eval_span = _fixture_trace()
    judge = next(s for s in fx['spans'] if s['type'] == 'span.responses')

    assert judge.get('parent_span_id') is None  # the field the old scoping used
    assert _parent_of(judge) == _span_id(eval_span)
    assert [_span_id(s) for s in _judge_spans(fx['spans'], eval_span)] == [_span_id(judge)]


def test_judge_spans_refuses_to_guess_between_unparented_candidates():
    # A single unparented judge span is unambiguous — "any judge span" and "this
    # eval span's" are the same span, so keep the fallback. Two are not: picking
    # the first would give the row another evaluator call's prompt, model and
    # content, and the row is non-empty so nothing downstream catches it.
    from fetch_traces import _judge_spans

    eval_span = {'type': 'span.evaluator', 'span_id': 'ev1'}
    one = {'type': 'span.responses', 'span_id': 'r1'}
    two = {'type': 'span.responses', 'span_id': 'r2'}

    assert _judge_spans([eval_span, one], eval_span) == [one]
    assert _judge_spans([eval_span, one, two], eval_span) == []
    # A real parent link still wins over the ambiguity guard.
    parented = {'type': 'span.responses', 'span_id': 'r2', 'parent_id': 'ev1'}
    assert _judge_spans([eval_span, one, parented], eval_span) == [parented]


def test_extract_io_recovers_reference_and_conversation():
    # Two fields the old fallback path threw away.
    # - `reference`: make_replacements maps {{...expected_output}} to row['reference'],
    #   but _assign_io didn't and the path hard-set reference='' — so the retest
    #   compared against a blank ground truth.
    # - `messages`: a root span carrying only the conversation was rejected by the
    #   'output or query' gate, so a conversation-only evaluator hollowed.
    from fetch_traces import _extract_io

    template = 'Compare {{log.output}} with {{log.expected_output}}'
    eval_span = {'type': 'span.evaluator', 'span_id': 'ev1'}
    judge = {
        'type': 'span.chat_completion',
        'span_id': 'c1',
        'parent_span_id': 'ev1',
        'attributes': {
            'gen_ai': {'input': {'messages': [{'content': 'Compare the reply with the gold answer'}]}}
        },
    }
    io = _extract_io([eval_span, judge], eval_span, template)
    assert io['output'] == 'the reply'
    assert io['reference'] == 'the gold answer'

    # Conversation-only root: no query/output anywhere, but the conversation IS
    # the content under evaluation.
    convo = [{'role': 'user', 'content': 'hi'}, {'role': 'assistant', 'content': 'hello'}]
    root = {'type': 'trace', 'span_id': 'root1', 'attributes': {'gen_ai': {'input': {'messages': convo}}}}
    io2 = _extract_io([root, eval_span], eval_span, '{{conversation}}')
    assert io2['messages'] == convo
    assert not io2['output'] and not io2['query']


def test_extract_io_keeps_judge_span_output_when_root_has_only_messages():
    # The messages-count-as-content gate must not short-circuit the judge path:
    # a normal evaluator whose root records the conversation but whose judged
    # output only exists on the judge span would otherwise lose its output.
    from fetch_traces import _extract_io

    convo = [{'role': 'user', 'content': 'hi'}]
    root = {'type': 'trace', 'span_id': 'root1', 'attributes': {'gen_ai': {'input': {'messages': convo}}}}
    eval_span = {'type': 'span.evaluator', 'span_id': 'ev1'}
    judge = {
        'type': 'span.chat_completion',
        'span_id': 'c1',
        'parent_span_id': 'ev1',
        'attributes': {'gen_ai': {'input': {'messages': [{'content': 'Score: the answer'}]}}},
    }
    io = _extract_io([root, eval_span, judge], eval_span, 'Score: {{log.output}}')
    assert io['output'] == 'the answer'
    assert io['messages'] == convo  # the root's conversation rides along


def test_content_source_ids_include_root_and_judge_not_just_eval():
    # The hollow guard must blame a failed detail fetch on the spans the content
    # is READ from (root/judge), not only the eval span. A 429 on the root span
    # hollows the row while the eval span's own fetch succeeds; keying off the
    # eval span id alone would misfile it as a shape gap (empty_extraction).
    from fetch_traces import _content_source_span_ids

    eval_span = {'type': 'span.evaluator', 'span_id': 'ev1'}
    root = {'type': 'trace', 'span_id': 'root1'}
    judge = {'type': 'span.chat_completion', 'span_id': 'chat1', 'parent_span_id': 'ev1'}
    unrelated = {'type': 'span.tool', 'span_id': 'tool1'}

    ids = _content_source_span_ids([eval_span, root, judge, unrelated], eval_span)
    assert ids == {'ev1', 'root1', 'chat1'}  # eval + root + judge, never the tool span


def test_classify_degrade_detail_fetch_on_downgraded_root():
    # Pins the real call-site decision (not just the helper set): a hollow row
    # whose ROOT span — a _structured_io content source — had its detail fetch
    # 429'd is a fetch failure, even though the eval span's own fetch succeeded.
    from fetch_traces import _classify_degrade

    eval_span = {'type': 'span.evaluator', 'span_id': 'ev1'}
    root = {'type': 'trace', 'span_id': 'root1'}
    judge = {'type': 'span.chat_completion', 'span_id': 'chat1', 'parent_span_id': 'ev1'}
    full = [eval_span, root, judge]

    # Root downgraded + hollow row → detail_fetch (root is in the source set).
    assert _classify_degrade('ev1', '', '', full, eval_span, {'root1'}) == 'detail_fetch'
    # Nothing downgraded + hollow → genuine shape gap.
    assert _classify_degrade('ev1', '', '', full, eval_span, set()) == 'empty_extraction'
    # Eval span itself downgraded → detail_fetch (short-circuit, before the set check).
    assert _classify_degrade('ev1', '', '', full, eval_span, {'ev1'}) == 'detail_fetch'
    # A non-hollow row is a clean datapoint regardless of downgrades.
    assert _classify_degrade('ev1', 'q', 'o', full, eval_span, {'root1'}) is None


def test_classify_degrade_does_not_borrow_another_eval_spans_judge():
    # Two evaluator calls in one trace. Row A parses hollow; A's own judge span
    # fetched fine, but a DIFFERENT eval span B's judge span 429'd. A must be
    # filed as a shape gap (empty_extraction), not detail_fetch — the source set
    # is scoped to A's own judge span, mirroring _judge_io. (Fails on the old
    # take-all-judge-spans scoping.)
    from fetch_traces import _classify_degrade

    eval_a = {'type': 'span.evaluator', 'span_id': 'evA'}
    judge_a = {'type': 'span.chat_completion', 'span_id': 'chatA', 'parent_span_id': 'evA'}
    eval_b = {'type': 'span.evaluator', 'span_id': 'evB'}
    judge_b = {'type': 'span.chat_completion', 'span_id': 'chatB', 'parent_span_id': 'evB'}
    root = {'type': 'trace', 'span_id': 'root1'}
    full = [eval_a, judge_a, eval_b, judge_b, root]

    # Only B's judge downgraded → classifying A is a shape gap, not A's fetch failure.
    assert _classify_degrade('evA', '', '', full, eval_a, {'chatB'}) == 'empty_extraction'
    # Sanity: A's OWN judge span downgrading IS a detail fetch failure for A.
    assert _classify_degrade('evA', '', '', full, eval_a, {'chatA'}) == 'detail_fetch'


def test_classify_degrade_keeps_conversation_only_rows():
    # A conversation-only evaluator (template judges on {{messages}}/{{conversation}})
    # yields a row with empty query/output but a populated `messages` — that IS the
    # content under evaluation, so the row must NOT be degraded. Before messages
    # counted, such rows were filed empty_extraction and stability.py dropped valid
    # conversation data with a false "empty output" reason.
    from fetch_traces import _classify_degrade

    eval_span = {'type': 'span.evaluator', 'span_id': 'ev1'}
    root = {'type': 'trace', 'span_id': 'root1'}
    full = [eval_span, root]
    convo = [{'role': 'user', 'content': 'hi'}, {'role': 'assistant', 'content': 'hello'}]

    # Populated messages → clean datapoint, even with empty query/output.
    assert _classify_degrade('ev1', '', '', full, eval_span, set(), messages=convo) is None
    # Truly empty (no query/output/messages) is still a genuine shape gap.
    assert _classify_degrade('ev1', '', '', full, eval_span, set(), messages=None) == 'empty_extraction'
    # An empty messages list is not content — still a shape gap.
    assert _classify_degrade('ev1', '', '', full, eval_span, set(), messages=[]) == 'empty_extraction'


def test_guard_hollow_aborts_over_threshold():
    from fetch_traces import _guard_hollow

    # 3/4 hollow (75%) with a 20% threshold and no --force → abort.
    with pytest.raises(SystemExit):
        _guard_hollow(n_detail=3, n_empty=0, n_rows=4, abort_ratio=0.2, force=False)
    # --force persists anyway; a small fraction under threshold is fine; empty is a no-op.
    _guard_hollow(n_detail=3, n_empty=0, n_rows=4, abort_ratio=0.2, force=True)
    _guard_hollow(n_detail=1, n_empty=0, n_rows=100, abort_ratio=0.2, force=False)
    _guard_hollow(n_detail=0, n_empty=0, n_rows=0, abort_ratio=0.2, force=False)


def test_guard_hollow_diagnoses_shape_gap_vs_auth():
    from fetch_traces import _guard_hollow

    # All-empty-extraction, no detail failures → the message must call it a
    # shape/extraction gap (NOT auth) and must not steer toward --force.
    with pytest.raises(SystemExit) as shape:
        _guard_hollow(n_detail=0, n_empty=20, n_rows=20, abort_ratio=0.2, force=False,
                      debug_path='runs/x/hollow_debug.json')
    msg = str(shape.value)
    assert 'shape' in msg.lower() and 'hollow_debug.json' in msg
    assert 'succeeded' in msg.lower()  # names that the fetch itself worked

    # All-detail-fetch failures → auth/rate-limit diagnosis instead.
    with pytest.raises(SystemExit) as auth:
        _guard_hollow(n_detail=20, n_empty=0, n_rows=20, abort_ratio=0.2, force=False)
    assert 'ORQ_API_KEY' in str(auth.value)


def test_force_does_not_override_a_shape_gap_abort():
    # --force is the auth/rate-limit override, and the shape-gap message tells the
    # operator not to force it (forcing writes empty rows that read as perfectly
    # stable datapoints). It must not silently suppress that abort as well.
    from fetch_traces import _guard_hollow, _should_abort_hollow

    assert _should_abort_hollow(n_detail=20, n_empty=0, n_rows=20, abort_ratio=0.2, force=True) is False
    assert _should_abort_hollow(n_detail=0, n_empty=20, n_rows=20, abort_ratio=0.2, force=True) is True
    with pytest.raises(SystemExit):
        _guard_hollow(n_detail=0, n_empty=20, n_rows=20, abort_ratio=0.2, force=True)
    # Mixed: forcing discounts the fetch half, so the shape half alone decides.
    assert _should_abort_hollow(n_detail=18, n_empty=1, n_rows=20, abort_ratio=0.2, force=True) is False
    assert _should_abort_hollow(n_detail=18, n_empty=1, n_rows=20, abort_ratio=0.2, force=False) is True


def test_make_replacements_reads_parts_shaped_conversation():
    # The scanner stores the Responses-API messages verbatim (parts[].content).
    # A content-only renderer turns each turn into a bare "user:", re-judging the
    # conversation blank — the same silent loss the extraction fix exists to stop.
    from lib.content import stringify_messages
    from lib.judge import make_replacements

    convo = [
        {'role': 'user', 'parts': [{'type': 'text', 'content': 'where is my order'}]},
        {'role': 'assistant', 'content': 'it shipped'},
    ]
    rendered = stringify_messages(convo)
    assert 'where is my order' in rendered and 'it shipped' in rendered

    repl = make_replacements(['log.conversation', 'log.expected_output'], {
        'messages': convo,
        'reference': 'gold',
    })
    assert 'where is my order' in repl['log.conversation']
    assert repl['log.expected_output'] == 'gold'

# Canned per-row verdicts keyed by a substring of the judged input.
_CANNED = {
    'useless': [True, False, True, False, True],   # flips: 3T/2F, mode True
    'tokyo': [False, False, False, False, False],  # unanimous False
    'hate you': [True, True, True, True, True],     # unanimous True
}


async def _fake_run_jury_for_row(spec, judge_model, *, client, repetitions):
    text = (spec.replacements.get('log.input') or '').lower()
    reps = next((v for k, v in _CANNED.items() if k in text), [True] * repetitions)
    reps = reps[:repetitions]
    n_true = sum(reps)
    value = n_true >= (len(reps) - n_true)
    return {
        'success': True,
        'repetitions': reps,
        'repetitions_failed': 0,
        'value': value,
        'explanation': 'canned judge rationale',
    }


@pytest.fixture()
def run_dir(tmp_path, monkeypatch):
    d = tmp_path / 'fixture_run'
    d.mkdir()
    shutil.copy(FIXTURES / 'evaluator.json', d / 'evaluator.json')
    shutil.copy(FIXTURES / 'traces.jsonl', d / 'traces.jsonl')

    import stability

    monkeypatch.setattr(stability, 'run_jury_for_row', _fake_run_jury_for_row)
    monkeypatch.setattr(stability, 'make_judge_client', lambda: object())
    return d


def test_stability_source_index_survives_the_degraded_filter(run_dir):
    # source_index is the row's identity across artifacts — annotations.json keys
    # off it. Enumerating the FILTERED list renumbers the rows, so a rerun under a
    # different --include_degraded lands last run's labels on different datapoints.
    import stability

    rows = [json.loads(ln) for ln in (run_dir / 'traces.jsonl').read_text(encoding='utf-8').splitlines() if ln]
    rows[0]['degraded'] = True
    rows[0]['degrade_reason'] = 'detail_fetch'
    (run_dir / 'traces.jsonl').write_text(
        '\n'.join(json.dumps(r) for r in rows) + '\n', encoding='utf-8'
    )

    stability.main(run_dir=str(run_dir), config=FAKE_CONFIG, metrics=False)
    stab = json.loads((run_dir / 'stability.json').read_text(encoding='utf-8'))
    # Rows 1 and 2 of traces.jsonl kept their traces.jsonl indices, not 0 and 1.
    assert [r['source_index'] for r in stab['rows']] == [1, 2]


def test_stability_names_the_filter_not_the_fetch_when_every_row_is_degraded(run_dir):
    # traces.jsonl is fine and full; the degraded skip emptied it. Telling the
    # operator to "run fetch_traces.py first" sends them to re-fetch data they
    # already have instead of at the extraction that produced hollow rows.
    import stability

    rows = [json.loads(ln) for ln in (run_dir / 'traces.jsonl').read_text(encoding='utf-8').splitlines() if ln]
    for r in rows:
        r['degraded'] = True
        r['degrade_reason'] = 'empty_extraction'
    (run_dir / 'traces.jsonl').write_text(
        '\n'.join(json.dumps(r) for r in rows) + '\n', encoding='utf-8'
    )

    with pytest.raises(RuntimeError) as exc:
        stability.main(run_dir=str(run_dir), config=FAKE_CONFIG, metrics=False)
    msg = str(exc.value)
    assert 'include_degraded' in msg
    assert 'run fetch_traces.py first' not in msg


def test_pipeline_end_to_end(run_dir):
    import aggregate
    import build_queue
    import metrics  # noqa: F401  (invoked via stability)
    import recommend
    import rewrite_eval
    import stability

    # Step 4 (+5 metrics auto): stability over the fixture.
    stability.main(run_dir=str(run_dir), config=FAKE_CONFIG)
    stab = json.loads((run_dir / 'stability.json').read_text(encoding='utf-8'))
    assert len(stab['rows']) == 3
    mx = json.loads((run_dir / 'metrics.json').read_text(encoding='utf-8'))
    assert mx['scores']['n_flipped'] == 1  # only the "useless" row flips
    assert mx['scores']['num_rows'] == 3

    # Step 6: queue = 1 flipped + 1 low-flip sanity item.
    build_queue.main(run_dir=str(run_dir), config=FAKE_CONFIG, count=-1)
    queue = json.loads((run_dir / 'queue.json').read_text(encoding='utf-8'))
    assert queue['meta']['n_flipped_items'] == 1
    assert queue['meta']['n_low_flip_sample'] == 1
    assert queue['meta']['n_items'] == 2

    # Step 7 (simulated): write human labels for both queue items. Force a
    # disagreement on the flipped row (human=False vs judge mode True).
    annotations = {}
    for it in queue['items']:
        idx = it['source_index']
        human = False if not it['low_flip_sample'] else bool(it['ambiguity']['mode_value'])
        annotations[str(idx)] = {
            'status': 'labeled',
            'value': human,
            'explanation': 'test label',
            'provenance': {'low_flip_sample': it['low_flip_sample']},
        }
    (run_dir / 'annotations.json').write_text(json.dumps(annotations), encoding='utf-8')

    # Step 8: recommend (fake backend -> canned recommendation JSON) + aggregate.
    recommend.main(run_dir=str(run_dir), config=FAKE_CONFIG)
    recs = json.loads((run_dir / 'recommendations.json').read_text(encoding='utf-8'))
    assert recs['metadata']['n_ok'] == 2
    assert all(r['success'] for r in recs['recommendations'])

    aggregate.main(run_dir=str(run_dir), config=FAKE_CONFIG)
    aggregated = (run_dir / 'aggregated.md').read_text(encoding='utf-8')
    assert 'Changes to make' in aggregated and 'Strengths to preserve' in aggregated

    # Step 9a: PO2 rewrite (fake backend echoes the prompt -> identity stub).
    rewrite_eval.main(run_dir=str(run_dir), config=FAKE_CONFIG)
    status = json.loads((run_dir / 'rewrite_status.json').read_text(encoding='utf-8'))
    assert status['var_check_passed'] is True
    assert set(status['source_vars']) == {'log.input', 'log.output'}
    new_prompt = (run_dir / 'new_prompt.md').read_text(encoding='utf-8')
    assert '{{log.input}}' in new_prompt and '{{log.output}}' in new_prompt
