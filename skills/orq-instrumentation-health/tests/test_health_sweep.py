import codecs
import importlib.util
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "health_sweep", Path(__file__).parents[1] / "scripts" / "health_sweep.py"
)
h = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = h
SPEC.loader.exec_module(h)

FIXTURES = Path(__file__).parent / "fixtures" / "run"
PROJECT = "019b0000-0000-7000-8000-000000000001"


@pytest.fixture
def run(tmp_path):
    """The recorded research-workspace run (24/11/2026), replayed through every step."""
    shutil.copytree(FIXTURES, tmp_path, dirs_exist_ok=True)
    h.cmd_plan(tmp_path)
    h.cmd_sample(tmp_path)
    return tmp_path


def group(card, agent):
    return next(g for p in card["projects"] for g in p["groups"] if g["agent"] == agent)


def span(sid, parent="", **kw):
    base = {"span_id": sid, "parent_span_id": parent, "type": "span.generic", "model": "", "provider": ""}
    return {**base, **kw}


def llm(sid, parent, tokens=10, cost=0.001, **kw):
    fields = {"type": "span.chat_completion", "model": "m", "provider": "openai",
              "usage": {"total_tokens": tokens}, "cost": {"total": cost}}
    return span(sid, parent, **{**fields, **kw})


def test_init_writes_window_and_count_bodies(tmp_path):
    now = datetime(2026, 11, 24, 12, 0, 0, tzinfo=timezone.utc)
    out = h.cmd_init(tmp_path, 7, now)
    assert out["window"] == {"from": "2026-11-17T12:00:00Z", "to": "2026-11-24T12:00:00Z", "days": 7}
    body = json.loads((tmp_path / "bodies" / "count-model-tokens-zero.json").read_text())
    assert body["from"] == "2026-11-17T12:00:00Z" and body["to"] == "2026-11-24T12:00:00Z"
    assert body["group_by"] == ["project_id", "agent_name"]
    assert {"field": "status", "op": "neq", "values": ["error"]} in body["filters"]
    assert "filters" not in json.loads((tmp_path / "bodies" / "count-total.json").read_text())


def test_plan_has_one_search_per_group_with_the_no_agent_bucket(run):
    groups = json.loads((run / "groups.json").read_text())
    assert {g["agent"] for g in groups} == {
        "eq-throwaway-11171454", "no agent", "pizza-faq-agent", "res1933-tagtest"
    }
    no_agent = next(g for g in groups if g["agent"] == h.NO_AGENT)
    assert no_agent["traces"] == 2112
    body = json.loads((run / "bodies" / f"search-{no_agent['n']}.json").read_text())
    assert body["filters"] == [{"field": "agent_name", "op": "not_exists"}]
    assert body["limit"] == h.SEARCH_LIMIT


def test_sample_dedupes_root_spans_by_trace(run):
    sample = json.loads((run / "sample.json").read_text())
    pizza = next(g for g in json.loads((run / "groups.json").read_text()) if g["agent"] == "pizza-faq-agent")
    # search-2.json has dozens of root-span rows for these two trace ids
    assert len(sample[str(pizza["n"])]) == 2


def test_recorded_run_scorecard(run):
    card = h.cmd_score(run)
    assert card["unobservable"] == []
    assert [p["project_id"] for p in card["projects"]] == [PROJECT]
    no_agent = group(card, h.NO_AGENT)
    assert no_agent["counts"] == {"model_tokens_zero": 0, "model_no_tokens": 0, "user_id_set": 0}
    assert no_agent["sample"]["no_session_id"]["traces"] == 3
    assert no_agent["sample"]["flat"]["traces"] == 0  # router traces are exempt
    pizza = group(card, "pizza-faq-agent")
    assert pizza["sampled"] == 2  # both traces paged to the end (4 and 2 pages)
    assert pizza["sample"]["tokens_no_cost"]["traces"] == 0  # agent wrapper spans are not unpriced models
    assert pizza["content"]["pii"][0]["entities"]["IBAN_CODE"] == 1
    assert card["generic_names"] == [{"name": "default", "count": 3}]


def test_unpaged_trace_is_unobservable_not_healthy(run):
    pizza_trace = "d9bdf3b32599079220af79dd5378289b"
    (run / "spans" / f"{pizza_trace}.2.json").unlink()
    card = h.cmd_score(run)
    assert group(card, "pizza-faq-agent")["sampled"] == 1
    assert any(pizza_trace in u["reason"] for u in card["unobservable"])


def test_missing_operator_makes_the_check_unobservable(run):
    fields = json.loads((run / "fields.json").read_text())
    for f in fields["data"]:
        if f["name"] == "tokens.total":
            f["operators"].remove("exists")
    (run / "fields.json").write_text(json.dumps(fields))
    card = h.cmd_score(run)
    assert {"check": "model-tokens", "reason": "operator exists not allowed on tokens.total"} in card["unobservable"]


def test_missing_count_file_is_unobservable_and_null(run):
    (run / "count-user-id.json").unlink()
    card = h.cmd_score(run)
    assert group(card, h.NO_AGENT)["counts"]["user_id_set"] is None
    assert {"check": "user-id", "reason": "count-user-id.json not saved"} in card["unobservable"]


@pytest.mark.parametrize(
    ("spans", "product", "expected"),
    [
        ([span("r"), span("a", "r", provider="openai")], "", {"provider_no_model"}),
        ([span("r"), span("a", "r", type="")], "", {"type_missing"}),
        ([span("r"), llm("a", "r", cost=0)], "", {"tokens_no_cost"}),
        ([span("r"), llm("a", "r", cost=None)], "", {"tokens_no_cost"}),
        # an agent wrapper with rolled-up tokens and cost 0, with or without children
        ([span("r"), llm("w", "r", cost=0, provider="agent")], "", set()),
        ([span("r"), llm("w", "r", cost=0), llm("c", "w")], "", set()),
        ([span("r"), span("a", "gone")], "", {"orphan_parent"}),
        ([span("r"), span("a", "r"), span("b", "r")], "", {"flat"}),
        ([span("r"), span("a", "r"), span("b", "r")], "router", set()),
        ([span("r"), span("a", "r"), span("b", "a")], "", set()),
        ([span("r"), span("a", "r")], "", set()),  # two spans is too few to call flat
    ],
)
def test_span_findings(spans, product, expected):
    assert h.span_findings(spans, product) == expected


def test_conversation_gaps_read_text_and_json_parts():
    def conv(*msgs):
        return {"messages": [{"role": r, "content": c} for r, c in msgs]}

    assert h.conversation_gap(conv(("user", [{"type": "text", "text": "hi"}]),
                                   ("assistant", [{"type": "json", "value": {"a": 1}}]))) is None
    assert h.conversation_gap(conv(("system", [{"type": "text", "text": "x"}]),
                                   ("assistant", [{"type": "text", "text": "y"}]))) == "no_input"
    assert h.conversation_gap(conv(("user", [{"type": "text", "text": "hi"}]),
                                   ("assistant", [{"type": "text", "text": "  "}]))) == "no_output"


def test_pii_bodies_skip_empty_conversations(run):
    out = h.cmd_pii(run)
    assert out["pii_calls"] == len(out["bodies"]) == 9
    tid = Path(out["bodies"][0]).name.removesuffix(".body.json")
    body = json.loads((run / "pii" / f"{tid}.body.json").read_text())
    assert body["include_entities"] is True and body["text"].startswith("synthetic")
    (run / "conversation" / f"{tid}.json").write_text(json.dumps({"messages": []}))
    assert h.cmd_pii(run)["pii_calls"] == 8


def test_load_reads_utf16_from_powershell(tmp_path):
    p = tmp_path / "x.json"
    p.write_bytes(codecs.BOM_UTF16_LE + json.dumps({"data": []}).encode("utf-16-le"))
    assert h.load(p) == {"data": []}
