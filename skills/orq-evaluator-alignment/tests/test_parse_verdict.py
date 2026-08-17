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

from lib.judge import parse_categorical, parse_numeric, parse_verdict  # noqa: E402


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


# --- categorical canonicalization (RES-978 §4a): casefold + exact one-of-K ---

_LABELS = ['abuse', 'safe', 'spam']


def test_categorical_exact_casefold_match():
    p = parse_categorical('ABUSE', _LABELS)
    assert p.status == 'ok'
    assert p.value == 'abuse'  # returns the DECLARED label, not the raw casing


def test_categorical_non_matching_is_wrong_output_type():
    # No fuzzy/substring: "Abuse (severe)" must NOT merge into "abuse".
    p = parse_categorical('Abuse (severe)', _LABELS)
    assert p.status == 'wrong_output_type'
    assert p.value is None


def test_categorical_json_enum_value():
    p = parse_categorical('{"explanation": "clearly abusive", "value": "abuse"}', _LABELS)
    assert p.status == 'ok'
    assert p.value == 'abuse'


def test_categorical_labelled_freetext():
    p = parse_categorical('Explanation: looks fine.\nValue: Safe', _LABELS)
    assert p.status == 'ok'
    assert p.value == 'safe'


# --- numeric canonicalization (RES-978 §4a): first token, ,/. normalization ---


def test_numeric_plain_int():
    p = parse_numeric('5')
    assert p.status == 'ok'
    assert p.value == 5.0


def test_numeric_dot_decimal():
    p = parse_numeric('3.4')
    assert p.status == 'ok'
    assert p.value == 3.4


def test_numeric_comma_decimal_normalized():
    p = parse_numeric('3,4')
    assert p.status == 'ok'
    assert p.value == 3.4


def test_numeric_first_token_with_trailing_text():
    p = parse_numeric('4,5 out of 10')
    assert p.status == 'ok'
    assert p.value == 4.5


def test_numeric_json_number_value():
    p = parse_numeric('{"explanation": "middling", "value": 4}')
    assert p.status == 'ok'
    assert p.value == 4.0


def test_numeric_non_number_is_wrong_output_type():
    p = parse_numeric('no score in here')
    assert p.status == 'wrong_output_type'
    assert p.value is None


def test_numeric_in_range_ok():
    p = parse_numeric('7', scale=(1.0, 10.0))
    assert p.status == 'ok'
    assert p.value == 7.0


def test_numeric_out_of_range_is_wrong_output_type():
    # A number outside the declared scale is off-contract, not a scored verdict.
    p = parse_numeric('12', scale=(1.0, 10.0))
    assert p.status == 'wrong_output_type'


# --- numeric free-text: the verdict, not the first number in the sentence ---


def test_numeric_freetext_reads_the_verdict_not_the_scale_mention():
    # The P0: `_NUM_TOKEN.search` returned the FIRST number, so this scored 1.0 —
    # inside the declared scale, status ok, and nothing downstream re-checks it.
    # The wrong score then entered mean, stdev and the instability band.
    p = parse_numeric('On a scale of 1 to 5, I give this a 4', (1, 5))
    assert p.status == 'ok'
    assert p.value == 4.0


def test_numeric_freetext_handles_out_of_n_phrasing():
    p = parse_numeric('I give this a 4 out of 5', (1, 5))
    assert p.value == 4.0


def test_numeric_freetext_handles_slash_phrasing():
    p = parse_numeric('Rating: 3/5', (1, 5))
    assert p.value == 3.0


def test_numeric_freetext_reads_the_labelled_verdict_line():
    raw = 'The answer cites 2 of the 3 required sources.\nScore: 4'
    assert parse_numeric(raw, (1, 5)).value == 4.0


def test_numeric_freetext_takes_the_last_line_verdict():
    raw = 'Reasoning about several things.\n5'
    assert parse_numeric(raw, (1, 5)).value == 5.0


def test_numeric_json_value_still_wins():
    p = parse_numeric('{"explanation": "on a scale of 1 to 5", "value": 2}', (1, 5))
    assert p.value == 2.0


def test_numeric_decimal_comma_still_normalises():
    assert parse_numeric('3,4', (1, 5)).value == 3.4


def test_ambiguous_freetext_number_is_surfaced_not_guessed():
    # Two different candidates on the verdict line and no way to tell which is the
    # score. Off-contract is a SIGNAL the human sees; a coin-flip is a wrong score
    # nothing catches.
    p = parse_numeric('The answer covers 2 points and misses 3', (1, 5))
    assert p.status == 'wrong_output_type'
    assert p.value is None


def test_numeric_with_no_number_at_all_is_still_off_contract():
    p = parse_numeric('I cannot score this.', (1, 5))
    assert p.status == 'wrong_output_type'


def test_numeric_out_of_scale_is_still_off_contract():
    assert parse_numeric('Score: 9', (1, 5)).status == 'wrong_output_type'


def test_parse_numeric_accepts_a_quoted_number_in_the_schema():
    # Models quote numbers routinely. Requiring a bare int/float sent these down
    # the free-text path, where the explanation's own digits made the verdict line
    # ambiguous — so a judge that answered correctly in the schema it was handed
    # was recorded as off-contract.
    p = parse_numeric('{"value": "4", "explanation": "2 of the 3 claims are supported"}', (1, 5))
    assert p.status == 'ok'
    assert p.value == 4.0
    assert p.explanation == '2 of the 3 claims are supported'


def test_parse_numeric_quoted_decimal_comma():
    assert parse_numeric('{"value": "3,4"}', (1, 5)).value == pytest.approx(3.4)


def test_parse_numeric_quoted_non_number_is_off_contract():
    p = parse_numeric('{"value": "unscorable", "explanation": "no basis"}', (1, 5))
    assert p.status == 'wrong_output_type'


def test_parse_numeric_quoted_number_still_honours_the_scale():
    assert parse_numeric('{"value": "12"}', (1, 5)).status == 'wrong_output_type'


def test_parse_numeric_does_not_read_a_boolean_as_a_score():
    # True is an int in Python; a boolean verdict is not a score.
    assert parse_numeric('{"value": true}', (0, 1)).status == 'wrong_output_type'
