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

from fetch_traces import _dedup_rows, _judged_input_key  # noqa: E402


def _q(row):
    return row['q']


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
