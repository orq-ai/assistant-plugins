"""Unit tests for the win32 TLS-verification policy (RES-1387).

`lib.orq_client.tls_verify()` and `lib.model_backend._tls_verify()` (a
re-export of the same function, see model_backend.py) must never go back to
returning `False` on win32 — that's exactly the bug this covers.

`tests/requirements.txt` declares `truststore` win32-only, matching real
usage (it may still end up installed elsewhere as some other dependency's
transitive pull — this suite doesn't rely on that either way). The tests
that construct a real `truststore.SSLContext` are guarded to skip when it
isn't importable, so they run for real wherever it is — on `windows-latest`
that's a real platform with a real SChannel-backed trust store, which is
what the `skill-tests-windows` CI job exists for. The platform-independent
tests (off-Windows behavior, the missing-dependency error path, the
re-export identity check) always run regardless.
"""

from __future__ import annotations

import importlib.util
import ssl
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import _bootstrap  # noqa: F401,E402

import pytest  # noqa: E402,F401

from lib import model_backend, orq_client  # noqa: E402

# truststore is win32-only in tests/requirements.txt (matching real usage), so
# it's only actually installed on the skill-tests-windows CI job. The three
# tests below construct a real SSLContext and need it importable; skip them
# rather than fail where it's absent by design.
needs_truststore = pytest.mark.skipif(
    importlib.util.find_spec('truststore') is None,
    reason='truststore is win32-only, see tests/requirements.txt',
)


def test_tls_verify_true_off_windows(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, 'platform', 'linux')
    assert orq_client.tls_verify() is True


@needs_truststore
def test_tls_verify_sslcontext_on_windows(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, 'platform', 'win32')
    result = orq_client.tls_verify()
    assert isinstance(result, ssl.SSLContext)
    assert result.check_hostname is True
    assert result.verify_mode == ssl.CERT_REQUIRED


@needs_truststore
def test_tls_verify_never_returns_false_on_windows(monkeypatch: pytest.MonkeyPatch):
    # Pins the regression directly: RES-1387 was `sys.platform != 'win32'`,
    # i.e. `False` on windows. Anything falsy here is the bug back.
    monkeypatch.setattr(sys, 'platform', 'win32')
    assert bool(orq_client.tls_verify()) is not False


def test_model_backend_tls_verify_is_the_same_function():
    # RES-1387 follow-up: model_backend used to carry its own independently
    # broken copy of this policy. Pin that it is now a re-export, not a
    # second implementation that can drift out of sync again.
    assert model_backend._tls_verify is orq_client.tls_verify


@needs_truststore
def test_model_backend_tls_verify_sslcontext_on_windows(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, 'platform', 'win32')
    result = model_backend._tls_verify()
    assert isinstance(result, ssl.SSLContext)
    assert result.check_hostname is True
    assert result.verify_mode == ssl.CERT_REQUIRED


def test_tls_verify_missing_truststore_raises_actionable_error(monkeypatch: pytest.MonkeyPatch):
    # `sys.modules[name] = None` is the standard way to simulate an uninstalled
    # module: the import system raises ImportError for it without touching the
    # real installation. Must still fail loud on win32 — never fall back to
    # verify=False just because truststore is missing.
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setitem(sys.modules, 'truststore', None)
    with pytest.raises(ImportError, match='truststore'):
        orq_client.tls_verify()
