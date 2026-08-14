"""Tests for the traces.jsonl identity guard (lib.content).

`source_index` is a row's *position* in `traces.jsonl`, and that is what every
human label is keyed by. `fetch_traces` rewrites the file wholesale (dedup
included), so re-fetching after labelling renumbers every row underneath the
labels — and only a *total* miss raised. Partial overlap paired each label against
a different datapoint and produced a clean-looking agreement score for a comparison
that never happened.

Pure stdlib; no orq/evaluatorq import.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import _bootstrap  # noqa: F401,E402

from lib.content import judged_input_key, traces_fingerprint  # noqa: E402


def _rows(*queries: str) -> list[dict]:
    return [{'query': q, 'output': f'o-{q}', 'messages': None} for q in queries]


def test_same_rows_same_fingerprint():
    assert traces_fingerprint(_rows('a', 'b', 'c')) == traces_fingerprint(_rows('a', 'b', 'c'))


def test_reordering_changes_the_fingerprint():
    # Order-sensitive on purpose: a reordering is exactly the failure being guarded
    # against, because the labels are keyed by position.
    assert traces_fingerprint(_rows('a', 'b')) != traces_fingerprint(_rows('b', 'a'))


def test_an_appended_row_changes_the_fingerprint():
    assert traces_fingerprint(_rows('a', 'b')) != traces_fingerprint(_rows('a', 'b', 'c'))


def test_changed_content_changes_the_fingerprint():
    assert traces_fingerprint(_rows('a')) != traces_fingerprint(_rows('a2'))


def test_fingerprint_carries_the_row_count_up_front():
    assert traces_fingerprint(_rows('a', 'b', 'c')).startswith('3:')


def test_empty_is_stable():
    assert traces_fingerprint([]) == traces_fingerprint([])


def test_provenance_fields_do_not_affect_identity():
    # The key is the content the judge scores, not where it came from — the same
    # input captured on two traces is one datapoint (this is also the dedup key).
    a = [{'query': 'q', 'output': 'o', 'messages': None, 'trace_id': 'tr-1'}]
    b = [{'query': 'q', 'output': 'o', 'messages': None, 'trace_id': 'tr-2'}]
    assert traces_fingerprint(a) == traces_fingerprint(b)


def test_judged_input_key_handles_unhashable_messages():
    key = judged_input_key({'query': 'q', 'messages': [{'role': 'user', 'content': 'hi'}]})
    assert isinstance(key, str)
    assert 'hi' in key
