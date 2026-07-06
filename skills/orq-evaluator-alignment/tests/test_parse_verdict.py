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


def test_labelled_explanation_has_no_scaffolding():
    # Label at the very start must not leak "Value: true" into the explanation.
    p = parse_verdict('Value: true\nsome trailing note')
    assert p.value is True
    assert 'Value:' not in p.explanation
    assert 'true' not in p.explanation.lower()
    assert 'trailing note' in p.explanation


def test_label_without_following_boolean_falls_back():
    # A bare/empty trailing label with the verdict stated in prose above must
    # still recover the boolean, not raise.
    p = parse_verdict('It is true.\nValue:')
    assert p.value is True


def test_multiple_label_words_do_not_break_parse():
    # 'answer:' appears in prose after the real verdict; the verdict must still
    # be recovered from the labelled boolean rather than raising.
    p = parse_verdict('Verdict: true\nanswer: this is a longer note')
    assert p.value is True


def test_verdict_label_variant():
    p = parse_verdict('Reasoning: clearly a violation.\nVerdict: True')
    assert p.value is True
    assert 'violation' in p.explanation


def test_fence_inside_explanation_not_truncated():
    # A ``` inside the explanation string must not truncate the JSON (this is the
    # bug the naive regex fence-strip had; _strip_code_fences handles it).
    raw = '{"explanation": "see ``` code ``` block", "value": true}'
    p = parse_verdict(raw)
    assert p.value is True
    assert 'code' in p.explanation
