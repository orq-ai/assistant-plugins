"""Tests for tolerant verdict parsing (lib.judge.parse_verdict).

The audited judge prompt specifies a FREE-TEXT contract ("explanation, value"),
not JSON. Models that honour `response_format: json_schema` return JSON; others
(e.g. glm-5.2 via the orq router) ignore it and follow the prompt literally,
emitting plain text. parse_verdict must accept both so the stability run does
not collapse to 0 usable verdicts on a tool-style model.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import _bootstrap  # noqa: F401,E402

import pytest  # noqa: E402

from lib.judge import parse_verdict  # noqa: E402


def test_strict_json_still_parses():
    p = parse_verdict('{"explanation": "looks fine", "value": false}')
    assert p.value is False
    assert p.explanation == 'looks fine'


def test_freetext_labelled_value_false():
    raw = 'Explanation: The message is benign.\nValue: False'
    p = parse_verdict(raw)
    assert p.value is False
    assert 'benign' in p.explanation


def test_freetext_labelled_value_true():
    raw = 'Explanation: This is a slur directed at the system.\nValue: True'
    p = parse_verdict(raw)
    assert p.value is True
    assert 'slur' in p.explanation


def test_freetext_trailing_token():
    raw = "The latest user message is simply \"No,\" a benign reply.\n\nFalse"
    p = parse_verdict(raw)
    assert p.value is False
    assert 'benign' in p.explanation


def test_uses_last_boolean_token_as_verdict():
    # Explanation mentions "false positive" earlier; the real verdict is the last token.
    raw = 'This is not a false positive; the insult is explicit.\nValue: True'
    p = parse_verdict(raw)
    assert p.value is True


def test_markdown_fenced_json():
    raw = '```json\n{"explanation": "ok", "value": true}\n```'
    p = parse_verdict(raw)
    assert p.value is True
    assert p.explanation == 'ok'


def test_unparseable_raises():
    with pytest.raises(ValueError):
        parse_verdict('the model said something with no verdict at all')
