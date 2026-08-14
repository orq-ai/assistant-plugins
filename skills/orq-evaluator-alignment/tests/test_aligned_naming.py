"""Tests for how the aligned copy is named and where it is created.

Both were driven by the same gap in the orq record: `GET /v2/evaluators/{id}` returns
neither a populated `key` nor a `path` — only `display_name` and `project_id`. The old
code fell back to the evaluator id for both, so an aligned copy came out named
`01kzxt86ac82d1fvzw3s8wfv83-aligned-<ts>` and landed in a folder named after that id
instead of in the project its source lives in.

Pure-function tests; no network. `_resolve_path` is exercised against a stub client.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import _bootstrap  # noqa: F401,E402

import pytest  # noqa: E402

import create_eval  # noqa: E402


# --- source_name: the fallback chain over an inconsistent record shape ---


def test_source_name_prefers_the_top_level_key():
    ev = {'id': '01ABC', 'key': 'groundedness-bool', 'raw': {'display_name': 'Other'}}
    assert create_eval.source_name(ev) == 'groundedness-bool'


def test_source_name_falls_back_to_display_name_when_key_is_empty():
    # The real shape for evaluators created through the current orq UI: key comes
    # back as '' and display_name carries the name the user actually sees.
    ev = {'id': '01KZXT86AC82D1FVZW3S8WFV83', 'key': '',
          'raw': {'display_name': 'groundedness-bool-testcase'}}
    assert create_eval.source_name(ev) == 'groundedness-bool-testcase'


def test_source_name_falls_back_to_raw_key():
    ev = {'id': '01ABC', 'key': '', 'raw': {'display_name': None, 'key': 'legacy-key'}}
    assert create_eval.source_name(ev) == 'legacy-key'


def test_source_name_last_resort_is_the_id():
    ev = {'id': '01ABC', 'key': '', 'raw': {}}
    assert create_eval.source_name(ev) == '01ABC'


def test_source_name_ignores_whitespace_only_names():
    ev = {'id': '01ABC', 'key': '   ', 'raw': {'display_name': 'real-name'}}
    assert create_eval.source_name(ev) == 'real-name'


# --- aligned_key: mirrors the source name, keeps the run timestamp ---


def test_aligned_key_mirrors_the_source_name():
    ev = {'id': '01KZXT86AC82D1FVZW3S8WFV83', 'key': '',
          'raw': {'display_name': 'groundedness-bool-testcase'}}
    assert (
        create_eval.aligned_key(ev, '20260814_072001')
        == 'groundedness-bool-testcase-aligned-20260814_072001'
    )


def test_aligned_key_slugifies_a_human_display_name():
    ev = {'id': '01ABC', 'key': '', 'raw': {'display_name': 'RAG Groundedness (v2)'}}
    assert create_eval.aligned_key(ev, '20260814_072001') == 'rag-groundedness-v2-aligned-20260814_072001'


def test_aligned_key_is_unique_per_run():
    # Two alignment runs on one evaluator must not collide.
    ev = {'id': '01ABC', 'key': 'my-eval', 'raw': {}}
    assert create_eval.aligned_key(ev, '20260814_072001') != create_eval.aligned_key(ev, '20260814_143355')


# --- _resolve_path: co-locate with the source's project ---


class _StubClient:
    """Stands in for OrqClient as an async context manager."""

    def __init__(self, mapping: dict[str, str | None]) -> None:
        self.mapping = mapping
        self.seen: list[str] = []

    async def __aenter__(self) -> '_StubClient':
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def resolve_project_key(self, project_id: str) -> str | None:
        self.seen.append(project_id)
        return self.mapping.get(project_id)


@pytest.fixture
def stub_client(monkeypatch):
    def _install(mapping):
        stub = _StubClient(mapping)
        monkeypatch.setattr(create_eval, 'OrqClient', lambda *a, **k: stub)
        return stub
    return _install


def test_resolve_path_prefixes_the_source_project(stub_client):
    stub = stub_client({'019dd498-83b4-7000-a154-798f2e531448': 'prompt-learning-vs-dspy'})
    ev = {'id': '01ABC', 'raw': {'project_id': '019dd498-83b4-7000-a154-798f2e531448'}}

    path = asyncio.run(create_eval._resolve_path(ev, 'my-eval-aligned-20260814_072001'))

    assert path == 'prompt-learning-vs-dspy/my-eval-aligned-20260814_072001'
    assert stub.seen == ['019dd498-83b4-7000-a154-798f2e531448']


def test_resolve_path_falls_back_to_a_bare_key_when_the_project_is_unknown(stub_client):
    # Unresolvable project → workspace root, not a folder guessed from the id.
    stub_client({})
    ev = {'id': '01ABC', 'raw': {'project_id': 'missing-project'}}

    assert asyncio.run(create_eval._resolve_path(ev, 'my-eval-aligned-ts')) == 'my-eval-aligned-ts'


def test_resolve_path_handles_a_record_with_no_project_id(stub_client):
    stub_client({})
    ev = {'id': '01ABC', 'raw': {}}

    assert asyncio.run(create_eval._resolve_path(ev, 'my-eval-aligned-ts')) == 'my-eval-aligned-ts'
