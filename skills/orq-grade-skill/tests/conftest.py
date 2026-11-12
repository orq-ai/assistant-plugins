"""Fixtures shared by the grading tests; the helpers live in support.py."""

from __future__ import annotations

from pathlib import Path

import pytest
from support import Client, make_client


@pytest.fixture
def client(tmp_path: Path) -> Client:
    return make_client(tmp_path)
