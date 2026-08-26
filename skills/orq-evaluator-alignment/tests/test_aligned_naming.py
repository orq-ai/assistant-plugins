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


# --- the project id the record actually carries ---


def test_resolve_path_reads_domain_id(stub_client):
    # GET /v2/evaluators/{id} runs through normalizeEvalToInternalEvaluator, an
    # explicit field allowlist that emits `domain_id` and never `project_id`. Reading
    # project_id alone resolved nothing, so every aligned copy landed at the
    # workspace root — the exact failure this function exists to prevent.
    stub = stub_client({'proj-123': 'research'})
    ev = {'id': '01ABC', 'raw': {'domain_id': 'proj-123'}}

    path = asyncio.run(create_eval._resolve_path(ev, 'k-aligned-1'))

    assert path == 'research/k-aligned-1'
    assert stub.seen == ['proj-123']


def test_resolve_path_still_accepts_project_id(stub_client):
    # Older records / other fetch paths carry the other spelling.
    stub_client({'proj-9': 'legacy'})
    ev = {'id': '01ABC', 'raw': {'project_id': 'proj-9'}}
    assert asyncio.run(create_eval._resolve_path(ev, 'k')) == 'legacy/k'


def test_domain_id_wins_when_both_are_present(stub_client):
    stub_client({'domain-1': 'current', 'proj-1': 'stale'})
    ev = {'id': '01ABC', 'raw': {'domain_id': 'domain-1', 'project_id': 'proj-1'}}
    assert asyncio.run(create_eval._resolve_path(ev, 'k')) == 'current/k'


def test_unresolvable_project_falls_back_to_the_workspace_root(stub_client):
    stub_client({})
    ev = {'id': '01ABC', 'raw': {'domain_id': 'missing'}}
    assert asyncio.run(create_eval._resolve_path(ev, 'k')) == 'k'


def test_resolve_path_accepts_the_detail_endpoint_shape(stub_client):
    # Probed live 2026-08-14: GET /v2/evaluators/{id} returns domain_id AND
    # project_id carrying the same value.
    stub_client({'27775162-a7b6-4ca6-8f75-e3bc9f8dd440': 'prompt-engineering'})
    ev = {'id': '01ABC', 'raw': {
        'domain_id': '27775162-a7b6-4ca6-8f75-e3bc9f8dd440',
        'project_id': '27775162-a7b6-4ca6-8f75-e3bc9f8dd440',
    }}
    assert asyncio.run(create_eval._resolve_path(ev, 'k')) == 'prompt-engineering/k'


def test_resolve_path_accepts_the_list_endpoint_shape(stub_client):
    # And GET /v2/evaluators (the list) returns project_id with domain_id NULL —
    # the inverse — so neither spelling is safe on its own.
    stub_client({'019dd498-83b4-7000-a154-798f2e531448': 'prompt-learning-vs-dspy'})
    ev = {'id': '01ABC', 'raw': {
        'domain_id': None, 'project_id': '019dd498-83b4-7000-a154-798f2e531448',
    }}
    assert asyncio.run(create_eval._resolve_path(ev, 'k')) == 'prompt-learning-vs-dspy/k'


def test_source_name_uses_key_when_display_name_is_null():
    # The API-created shape, the inverse of the UI one: key set, display_name null.
    ev = {'id': '01KSMFB2S38435YJFY3WRQWKP1', 'key': None,
          'raw': {'display_name': None, 'key': 'constraint-satisfaction-v2-openai-gpt-5-4-mini'}}
    assert create_eval.source_name(ev) == 'constraint-satisfaction-v2-openai-gpt-5-4-mini'
