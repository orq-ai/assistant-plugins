"""Unit tests for the win32 TLS-verification policy (RES-1387).

`lib.orq_client.tls_verify()` and `lib.model_backend._tls_verify()` (a
re-export of the same function, see model_backend.py) must never go back to
returning `False` on win32 — that's exactly the bug this covers. CI runs
`ubuntu-latest` only, so these tests monkeypatch `sys.platform` rather than
requiring a real Windows runner; `truststore` dispatches on the *real*
platform internally (not on `sys.platform`), so a `truststore.SSLContext`
built here is backed by this machine's actual OS trust store, not a fake
Windows one — but its type and its `check_hostname`/`verify_mode` invariants
hold on every backend truststore supports, which is what these tests assert.

Not pure stdlib: needs `truststore`, unconditionally installed for the test
suite (see tests/requirements.txt) precisely so this file can run anywhere.
"""

from __future__ import annotations

import ssl
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import _bootstrap  # noqa: F401,E402

import pytest  # noqa: E402,F401

from lib import model_backend, orq_client  # noqa: E402


def test_tls_verify_true_off_windows(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, 'platform', 'linux')
    assert orq_client.tls_verify() is True


def test_tls_verify_sslcontext_on_windows(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, 'platform', 'win32')
    result = orq_client.tls_verify()
    assert isinstance(result, ssl.SSLContext)
    assert result.check_hostname is True
    assert result.verify_mode == ssl.CERT_REQUIRED


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
