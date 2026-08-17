"""Unit tests for exact-match trace-input dedup in fetch_traces.

`_dedup_rows` is a pure, order-preserving, first-wins deduper; `_judged_input_key`
keys off the content the judge actually scores (query/output/reference/messages),
NOT trace/span provenance — so the same input captured on two traces collapses to
one datapoint and isn't re-judged.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import _bootstrap  # noqa: F401,E402

import json  # noqa: E402

import pytest  # noqa: E402

from fetch_traces import (  # noqa: E402
    _dedup_rows,
    _judged_input_key,
    _resolve_judge_model,
    _guard_foreign_rows,
    _scan_depth_note,
    foreign_rows,
)


def _q(row):
    return row['q']


# --- _resolve_judge_model: the trace-observed model must be normalised to a
# routable refId before it is pinned, else the display alias (openai/gpt-oss-120b)
# reaches the router on the wrong provider path and 403s (RES-978 slug bug). ---


def test_resolve_judge_model_normalizes_observed_alias_to_refid(tmp_path):
    # Spans record the display alias; the registry map routes it to the refId.
    evaluator = {'id': 'e', 'judge_model_id': 'ce490df4-uuid', 'judge_model': 'ce490df4-uuid'}
    rows = [{'judge_model': 'openai/gpt-oss-120b'}] * 5
    model_map = {'openai/gpt-oss-120b': 'groq/openai/gpt-oss-120b'}
    out = _resolve_judge_model(tmp_path, evaluator, rows, model_map=model_map)
    assert out == 'groq/openai/gpt-oss-120b'
    assert evaluator['judge_model'] == 'groq/openai/gpt-oss-120b'
    # the raw observed distribution is preserved honestly, un-normalised
    assert evaluator['judge_models_observed'] == {'openai/gpt-oss-120b': 5}


def test_resolve_judge_model_unknown_observed_falls_back_to_raw(tmp_path):
    # A malformed/absent slug not in the registry map (e.g. a misconfigured
    # production judge) is kept as-is rather than dropped — surfaced, not hidden.
    evaluator = {'id': 'e', 'judge_model_id': 'uuid', 'judge_model': 'uuid'}
    rows = [{'judge_model': 'openai/groq/gpt-oss-120b'}] * 3
    out = _resolve_judge_model(tmp_path, evaluator, rows, model_map={'x': 'y'})
    assert out == 'openai/groq/gpt-oss-120b'


def test_resolve_judge_model_without_map_keeps_observed(tmp_path):
    # Back-compat: no map supplied → behave as before (pin the observed value).
    evaluator = {'id': 'e', 'judge_model_id': 'uuid', 'judge_model': 'uuid'}
    rows = [{'judge_model': 'openai/gpt-4o-mini'}] * 2
    out = _resolve_judge_model(tmp_path, evaluator, rows)
    assert out == 'openai/gpt-4o-mini'


# --- _dedup_rows: first-wins, order-preserving, exact match ---


def test_drops_exact_duplicates_keeping_first():
    rows = [{'q': 'a', 'id': 1}, {'q': 'b', 'id': 2}, {'q': 'a', 'id': 3}]
    out, dropped = _dedup_rows(rows, _q)
    assert [r['id'] for r in out] == [1, 2]  # the later 'a' is the repeat
    assert dropped == 1


def test_preserves_first_seen_order():
    rows = [{'q': c} for c in 'cabca']
    out, dropped = _dedup_rows(rows, _q)
    assert [r['q'] for r in out] == ['c', 'a', 'b']
    assert dropped == 2


def test_all_distinct_are_kept():
    rows = [{'q': 'a'}, {'q': 'b'}, {'q': 'c'}]
    out, dropped = _dedup_rows(rows, _q)
    assert out == rows
    assert dropped == 0


def test_all_identical_collapse_to_one():
    rows = [{'q': 'x'}, {'q': 'x'}, {'q': 'x'}]
    out, dropped = _dedup_rows(rows, _q)
    assert len(out) == 1
    assert dropped == 2


def test_empty_list():
    out, dropped = _dedup_rows([], _q)
    assert out == []
    assert dropped == 0


# --- _judged_input_key: content, not provenance ---


def test_key_ignores_trace_and_span_provenance():
    r1 = {'query': 'q', 'output': 'o', 'reference': '', 'messages': None, 'trace_id': 't1', 'span_id': 's1'}
    r2 = {'query': 'q', 'output': 'o', 'reference': '', 'messages': None, 'trace_id': 't2', 'span_id': 's2'}
    assert _judged_input_key(r1) == _judged_input_key(r2)


def test_key_differs_on_output():
    r1 = {'query': 'q', 'output': 'o1', 'reference': '', 'messages': None}
    r2 = {'query': 'q', 'output': 'o2', 'reference': '', 'messages': None}
    assert _judged_input_key(r1) != _judged_input_key(r2)


def test_key_differs_on_query():
    r1 = {'query': 'q1', 'output': 'o', 'reference': '', 'messages': None}
    r2 = {'query': 'q2', 'output': 'o', 'reference': '', 'messages': None}
    assert _judged_input_key(r1) != _judged_input_key(r2)


def test_key_differs_on_messages():
    r1 = {'query': 'q', 'output': 'o', 'reference': '', 'messages': [{'role': 'user', 'content': 'a'}]}
    r2 = {'query': 'q', 'output': 'o', 'reference': '', 'messages': [{'role': 'user', 'content': 'b'}]}
    assert _judged_input_key(r1) != _judged_input_key(r2)


def test_dedup_rows_with_judged_input_key_collapses_cross_trace_repeats():
    rows = [
        {'query': 'q', 'output': 'o', 'reference': '', 'messages': None, 'trace_id': 't1'},
        {'query': 'q', 'output': 'o', 'reference': '', 'messages': None, 'trace_id': 't2'},
        {'query': 'q2', 'output': 'o', 'reference': '', 'messages': None, 'trace_id': 't3'},
    ]
    out, dropped = _dedup_rows(rows, _judged_input_key)
    assert [r['trace_id'] for r in out] == ['t1', 't3']
    assert dropped == 1


# --- scan depth: the number only means something next to the window it came from ---


def test_scan_depth_flags_a_truncated_scan_as_a_slice():
    # Hitting the cap is the tell that there IS more history, so the deeper scan is
    # worth offering. Without this the count reads as "all this judge has".
    echo = {'limit': 200, 'n_traces_scanned': 200, 'scan_truncated': True}
    note = _scan_depth_note(echo, 18)
    assert '18 datapoint(s) from the 200 most recent' in note
    assert 'SLICE' in note
    assert '--trace_limit 2000' in note


def test_scan_depth_does_not_offer_a_deeper_scan_that_cannot_help():
    # Under the cap the window already held every trace there was; a bigger limit
    # re-scans the same traces. Offering it anyway teaches the user to ignore the ask.
    echo = {'limit': 200, 'n_traces_scanned': 43, 'scan_truncated': False}
    note = _scan_depth_note(echo, 18)
    assert '--trace_limit 2000' not in note
    assert 'trace_start_date' in note


def test_scan_depth_survives_an_echo_without_counts():
    # Older run dirs / the no-traces early return carry no scan counts.
    assert _scan_depth_note({}, 5) == 'Scan: 5 datapoint(s).'


# --- the scan overwrites; the other input sources append ---


def test_foreign_rows_counts_non_trace_sources():
    rows = [
        {'query': 'a', 'trace_id': 't1'},                 # from the scan
        {'query': 'b', 'source': 'dataset:ds-1'},
        {'query': 'c', 'source': 'dataset:ds-1'},
        {'query': 'd', 'synthetic': True},
    ]
    assert foreign_rows(rows) == {'dataset:ds-1': 2, 'synthetic': 1}


def test_foreign_rows_is_empty_for_a_pure_trace_scan():
    assert foreign_rows([{'query': 'a', 'trace_id': 't1'}, {'query': 'b', 'span_id': 's'}]) == {}


def test_scan_refuses_to_delete_rows_another_source_added(tmp_path):
    # fetch_traces rewrites traces.jsonl wholesale while dataset_inputs/seed_inputs
    # append to it, so running the scan second used to silently drop their rows —
    # and the row count afterwards looks perfectly reasonable.
    (tmp_path / 'traces.jsonl').write_text(
        json.dumps({'query': 'a', 'source': 'dataset:ds-1'}) + '\n', encoding='utf-8'
    )
    with pytest.raises(SystemExit) as exc:
        _guard_foreign_rows(tmp_path, replace=False)
    assert 'dataset:ds-1' in str(exc.value)
    assert '--replace' in str(exc.value)


def test_scan_overwrites_when_replace_is_explicit(tmp_path):
    (tmp_path / 'traces.jsonl').write_text(
        json.dumps({'query': 'a', 'synthetic': True}) + '\n', encoding='utf-8'
    )
    _guard_foreign_rows(tmp_path, replace=True)  # no raise


def test_scan_is_unguarded_over_its_own_rows(tmp_path):
    # Re-scanning deeper is the documented step-1a move and must not need a flag.
    (tmp_path / 'traces.jsonl').write_text(
        json.dumps({'query': 'a', 'trace_id': 't1'}) + '\n', encoding='utf-8'
    )
    _guard_foreign_rows(tmp_path, replace=False)


def test_scan_is_unguarded_on_a_fresh_run_dir(tmp_path):
    _guard_foreign_rows(tmp_path, replace=False)
