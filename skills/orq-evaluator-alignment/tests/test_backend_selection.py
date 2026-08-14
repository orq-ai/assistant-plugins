"""Tests for backend selection — which model writes the recommendation + rewrite.

`orq_router` is the default: it reuses ORQ_API_KEY, which the skill already
requires, instead of assuming the `claude` CLI is installed and logged in. These
pin the selection logic and the per-backend model defaults, which exist so that
switching `backend` alone is a valid edit (the old single shared `backend_model`
would hand a Claude model id to the router, or a router slug to `claude -p`).

No network: `get_backend` is exercised with `fake`, and the router branch is
checked through its pure helpers plus a monkeypatched constructor.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import _bootstrap  # noqa: F401,E402

import pytest  # noqa: E402

from lib import model_backend as mb  # noqa: E402


# --- defaults ---


def test_default_backend_is_the_router():
    assert mb.DEFAULT_BACKEND == 'orq_router'


def test_blank_backend_model_takes_the_backends_own_default(monkeypatch):
    seen = {}
    monkeypatch.setattr(mb, 'OrqRouterBackend', lambda model: seen.setdefault('model', model))

    mb.get_backend({'backend': 'orq_router', 'backend_model': ''})

    assert seen['model'] == 'deepseek/deepseek-v4-pro'


def test_blank_backend_model_differs_per_backend(monkeypatch):
    seen = {}
    monkeypatch.setattr(mb, 'ClaudeSubagentBackend', lambda model: seen.setdefault('model', model))

    mb.get_backend({'backend': 'claude_subagent', 'backend_model': ''})

    assert seen['model'] == 'claude-opus-4-8'


def test_explicit_backend_model_wins(monkeypatch):
    seen = {}
    monkeypatch.setattr(mb, 'OrqRouterBackend', lambda model: seen.setdefault('model', model))

    mb.get_backend({'backend': 'orq_router', 'backend_model': 'groq/openai/gpt-oss-120b'})

    assert seen['model'] == 'groq/openai/gpt-oss-120b'


def test_missing_backend_key_falls_back_to_the_default(monkeypatch):
    seen = {}
    monkeypatch.setattr(mb, 'OrqRouterBackend', lambda model: seen.setdefault('model', model))

    mb.get_backend({})

    assert seen['model'] == 'deepseek/deepseek-v4-pro'


def test_unknown_backend_is_rejected():
    with pytest.raises(ValueError, match='Unknown backend'):
        mb.get_backend({'backend': 'telepathy'})


def test_fake_backend_still_resolves():
    assert isinstance(mb.get_backend({'backend': 'fake'}), mb.FakeBackend)


# --- describe_backend: what the step-9 gate tells the user ---


def test_describe_names_backend_and_model():
    assert mb.describe_backend({'backend': 'orq_router', 'backend_model': ''}) == (
        'orq_router (deepseek/deepseek-v4-pro)'
    )


def test_describe_names_the_deployment_for_orq_deployment():
    got = mb.describe_backend({'backend': 'orq_deployment', 'backend_deployment_key': 'my-dep'})
    assert 'my-dep' in got


# --- failure translation: a gate message, not a stack trace ---


class _HttpError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f'HTTP {status}')
        self.status_code = status


def test_403_is_reported_as_not_enabled_with_the_three_ways_out():
    msg = mb._router_failure_reason('deepseek/deepseek-v4-pro', _HttpError(403))

    assert 'not available on this workspace' in msg
    assert 'provider key' in msg
    assert 'different model' in msg
    assert 'Claude subagent' in msg


def test_404_points_at_the_slug_shape():
    # The refId-vs-display-alias trap: a display alias 404s (or routes wrong).
    msg = mb._router_failure_reason('deepseek-v4-pro', _HttpError(404))

    assert 'refId' in msg
    assert 'provider-qualified' in msg


def test_429_is_distinguished_from_an_entitlement_problem():
    msg = mb._router_failure_reason('m', _HttpError(429))

    assert 'rate-limited' in msg
    assert 'not available on this workspace' not in msg


def test_unknown_failure_keeps_the_original_detail():
    msg = mb._router_failure_reason('m', RuntimeError('connection reset'))

    assert 'connection reset' in msg


def test_router_backend_without_an_api_key_is_unavailable(monkeypatch):
    monkeypatch.delenv('ORQ_API_KEY', raising=False)

    with pytest.raises(mb.BackendUnavailable, match='ORQ_API_KEY'):
        mb.OrqRouterBackend(model='deepseek/deepseek-v4-pro')
