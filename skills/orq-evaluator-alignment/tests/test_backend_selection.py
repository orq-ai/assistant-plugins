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
    monkeypatch.setattr(mb, 'OrqRouterBackend', lambda model, **_kw: seen.setdefault('model', model))

    mb.get_backend({'backend': 'orq_router', 'backend_model': ''})

    assert seen['model'] == 'deepseek/deepseek-v4-pro'


def test_blank_backend_model_differs_per_backend(monkeypatch):
    seen = {}
    monkeypatch.setattr(mb, 'ClaudeSubagentBackend', lambda model, **_kw: seen.setdefault('model', model))

    mb.get_backend({'backend': 'claude_subagent', 'backend_model': ''})

    assert seen['model'] == 'claude-opus-4-8'


def test_explicit_backend_model_wins(monkeypatch):
    seen = {}
    monkeypatch.setattr(mb, 'OrqRouterBackend', lambda model, **_kw: seen.setdefault('model', model))

    mb.get_backend({'backend': 'orq_router', 'backend_model': 'groq/openai/gpt-oss-120b'})

    assert seen['model'] == 'groq/openai/gpt-oss-120b'


def test_missing_backend_key_falls_back_to_the_default(monkeypatch):
    seen = {}
    monkeypatch.setattr(mb, 'OrqRouterBackend', lambda model, **_kw: seen.setdefault('model', model))

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


# --- endpoint / model / key resolution: flag > config > env > default ---


def test_base_url_falls_back_to_the_hosted_default(monkeypatch):
    monkeypatch.delenv('ORQ_BASE_URL', raising=False)
    assert mb.resolve_base_url('orq_router', {}) == 'https://my.orq.ai'


def test_base_url_reads_the_backend_s_env_var(monkeypatch):
    monkeypatch.setenv('ORQ_BASE_URL', 'https://orq.internal.example.com')
    assert mb.resolve_base_url('orq_router', {}) == 'https://orq.internal.example.com'


def test_config_beats_the_env_var(monkeypatch):
    monkeypatch.setenv('ORQ_BASE_URL', 'https://from-env.example.com')
    cfg = {'backend_base_url': 'https://from-config.example.com'}
    assert mb.resolve_base_url('orq_router', cfg) == 'https://from-config.example.com'


def test_a_trailing_slash_is_normalised(monkeypatch):
    monkeypatch.delenv('ORQ_BASE_URL', raising=False)
    cfg = {'backend_base_url': 'https://orq.internal.example.com/'}
    assert mb.resolve_base_url('orq_router', cfg) == 'https://orq.internal.example.com'


def test_each_backend_reads_its_own_env_var(monkeypatch):
    monkeypatch.setenv('ORQ_BASE_URL', 'https://router.example.com')
    monkeypatch.setenv('ORQ_API_BASE_URL', 'https://api.example.com')
    monkeypatch.setenv('ANTHROPIC_BASE_URL', 'https://anthropic.example.com')
    assert mb.resolve_base_url('orq_router', {}) == 'https://router.example.com'
    assert mb.resolve_base_url('orq_deployment', {}) == 'https://api.example.com'
    assert mb.resolve_base_url('anthropic_api', {}) == 'https://anthropic.example.com'


def test_backends_without_an_endpoint_resolve_to_none():
    assert mb.resolve_base_url('claude_subagent', {}) is None
    assert mb.resolve_base_url('fake', {}) is None


def test_cli_overrides_are_folded_onto_the_config():
    cfg = {'backend': 'claude_subagent', 'backend_model': 'claude-opus-4-8', 'runs_dir': 'runs'}
    merged = mb.apply_backend_overrides(
        cfg, backend='orq_router', backend_model='groq/openai/gpt-oss-120b',
        backend_base_url='https://orq.internal.example.com',
    )
    assert merged['backend'] == 'orq_router'
    assert merged['backend_model'] == 'groq/openai/gpt-oss-120b'
    assert merged['backend_base_url'] == 'https://orq.internal.example.com'
    assert merged['runs_dir'] == 'runs'  # untouched keys ride through


def test_unset_overrides_leave_the_config_alone():
    cfg = {'backend': 'claude_subagent', 'backend_model': 'claude-opus-4-8'}
    assert mb.apply_backend_overrides(cfg) == cfg


def test_overrides_do_not_mutate_the_caller_s_config():
    # The retest imports stability's main in the same process; a flag meant for one
    # call silently changing another's endpoint is a bug that only shows up on
    # someone else's workspace.
    cfg = {'backend': 'claude_subagent'}
    mb.apply_backend_overrides(cfg, backend='orq_router')
    assert cfg == {'backend': 'claude_subagent'}


def test_describe_backend_names_a_custom_endpoint(monkeypatch):
    monkeypatch.delenv('ORQ_BASE_URL', raising=False)
    cfg = {'backend': 'orq_router', 'backend_model': 'deepseek/deepseek-v4-pro',
           'backend_base_url': 'https://orq.internal.example.com'}
    described = mb.describe_backend(cfg)
    assert 'deepseek/deepseek-v4-pro' in described
    assert 'https://orq.internal.example.com' in described


def test_describe_backend_stays_quiet_on_the_default_endpoint(monkeypatch):
    monkeypatch.delenv('ORQ_BASE_URL', raising=False)
    described = mb.describe_backend({'backend': 'orq_router', 'backend_model': 'm'})
    assert described == 'orq_router (m)'


def test_every_backend_has_an_env_table_entry():
    # The table is what the docs and the error messages read; a backend missing
    # from it resolves no endpoint and names no credential.
    for name in ('orq_router', 'claude_subagent', 'anthropic_api', 'orq_deployment', 'fake'):
        assert name in mb.BACKEND_ENV
