"""Shared fakes: a fake `orq` CLI (the one external boundary) and SDK client, synthetic data only."""

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("cost_savings_report_e2e", ROOT / "scripts" / "cost_savings_report.py")
c = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = c
SPEC.loader.exec_module(c)

FIX = json.loads((ROOT / "tests" / "fixtures" / "models.json").read_text())
SCHEMA = json.loads((ROOT / "resources" / "report.schema.json").read_text())
SOL, SOL61, TERRA, LUNA, ACME = ("openai/gpt-6-sol", "openai/gpt-6.1-sol", "openai/gpt-6-terra", "openai/gpt-6-luna",
                                  "acme/acme-flash")
EASY_ASK, HARD_ASK = "say hello", "design a sharded database with cross-region failover"


def usage(prompt, cached=0, completion=100):
    return {"prompt_tokens": prompt, "prompt_cached_tokens": cached, "prompt_cache_creation_tokens": 0,
            "completion_tokens": completion}


def sol_cost(u):
    return (u["prompt_tokens"] - u["prompt_cached_tokens"]) * 3e-6 + u["prompt_cached_tokens"] * 0.3e-6 + \
        u["completion_tokens"] * 15e-6


LEAVES = {  # one trace per call: trace id, thread, routing input, usage; the root span is the LLM call
    "s1": {"trace": "tA", "thread": "th1", "at": "2026-11-10T10:00:01Z", "ask": EASY_ASK, "usage": usage(2000)},
    "s2": {"trace": "tB", "thread": "", "at": "2026-11-10T10:00:05Z", "ask": HARD_ASK,
           "usage": usage(2000, cached=1500)},
}
TRACES = {v["trace"]: k for k, v in LEAVES.items()}
# window token totals for sol, priced at its base price to 10.0, the fake's spend on it
AGG = {"gen_ai.client.inference.usage.input_tokens": 2_000_000,
       "gen_ai.client.inference.usage.cache_read.input_tokens": 1_000_000,
       "gen_ai.client.inference.usage.cache_write.input_tokens": 0,
       "gen_ai.client.inference.usage.output_tokens": 446_667}


def row(span):
    leaf = LEAVES[span]
    return {"trace_id": leaf["trace"], "root_span_id": span, "thread_id": leaf["thread"], "project_id": "p1",
            "models": ["gpt-6-sol"], "providers": ["openai"], "started_at": leaf["at"], "operation": "chat", "usage": leaf["usage"],
            "cost": {"total": sol_cost(leaf["usage"])}, "attributes": {}}


class FakeOrq:
    """The CLI the script reads with and the SDK client it reads and decides with. The SDK reads are turned into
    the CLI's argument lists, so one `__call__` (and `calls`) sees every read."""
    last: "FakeOrq"
    retries = 4

    def __init__(self, retention=None, plan="pro", agg=True):
        self.decisions, self.retention, self.plan, self.agg = 0, retention, plan, agg
        self.calls: list[list[str]] = []
        self.router = SimpleNamespace(decisions=SimpleNamespace(create=self.decide))
        self.client = SimpleNamespace(router=self.router, telemetry=SimpleNamespace(query=self.sdk_telemetry),
                                      traces=SimpleNamespace(list_spans=self.sdk_list_spans))
        FakeOrq.last = self

    def cached(self, key, load):
        return load()

    @staticmethod
    def sdk_out(out):
        return SimpleNamespace(model_dump=lambda **_kw: out)

    def sdk_telemetry(self, *, mode, from_, to, compute, group_by=None, filters=None, grain=None,
                      interval_seconds=None, **_kw):
        a = ["telemetry", "query", "--source", "TELEMETRY_SOURCE_TRACES", "--mode", mode, "--from", c.iso(from_),
             "--to", c.iso(to), "--compute", json.dumps(compute), "--limit", "5000"]
        for g in group_by or []:
            a += ["--group-by", g]
        if filters:
            a += ["--filters", json.dumps(filters)]
        if grain:
            a += ["--grain", grain]
        if interval_seconds:
            a += ["--interval-seconds", str(interval_seconds)]
        return self.sdk_out(self(*a))

    def sdk_list_spans(self, *, trace_id, limit, page_token=None, **_kw):
        a = ["traces", "list-spans", trace_id, "--limit", str(limit)] + (["--page-token", page_token] if page_token else [])
        return self.sdk_out(self(*a))

    def decide(self, *, state, **_body):
        self.decisions += 1
        hard = state == HARD_ASK
        probs = {"easy": 0.0 if hard else 1.0, "medium": 0.0, "hard": 1.0 if hard else 0.0}
        return SimpleNamespace(model_dump=lambda: {"answers": {"difficulty": {"probabilities": probs}}})

    def __call__(self, *args, cache=True):
        a = list(args)
        self.calls.append(a)
        if a[:2] == ["telemetry", "query"]:
            return self.telemetry(a)
        if a[:2] == ["workspaces", "get"]:
            if self.plan is None:
                raise c.OrqError(a, 1, "HTTP 403 not authorized")
            ws = {"key": a[2], "subscription": {"type": self.plan, "metadata": {"type": self.plan, "rateLimit": 1500}}, "features": {}}
            if self.retention:
                ws["features"]["retention_period"] = self.retention
            return {"workspace": ws}
        if a[:2] == ["model-catalog", "list"]:
            return {"data": FIX["catalog"]}
        if a[:2] == ["models", "list"]:
            return FIX["cards"]
        if a[:2] == ["traces", "search"]:
            flt = json.loads(a[a.index("--filters") + 1])[0]
            if flt["field"] == "thread_id":
                return {"data": [{"trace_id": "tA", "agent": {"key": "support"},
                                  "attributes": {"orq": {"product": "agents"}}}]}
            return {"data": [row(k) for k in LEAVES]}
        if a[:2] == ["traces", "list-spans"]:
            span = TRACES[a[2]]
            leaf = LEAVES[span]
            return {"data": [{"type": "span.responses", "has_children": False, "provider": "openai",
                              "model": "gpt-6-sol", "span_id": span, "started_at": leaf["at"],
                              "usage": leaf["usage"]}], "has_more": False}
        if a[:2] == ["traces", "get-span"]:
            leaf = LEAVES[a[3]]
            return {"span": {
                "summary": {"provider": "openai", "model": "gpt-6-sol", "started_at": leaf["at"], "operation": "chat",
                            "usage": leaf["usage"], "cost": {"total": sol_cost(leaf["usage"])}},
                "attributes": {"gen_ai": {"input": [{"role": "user", "content": leaf["ask"]}]}}}}
        raise AssertionError(f"unexpected call {a}")

    def telemetry(self, a):
        compute = json.loads(a[a.index("--compute") + 1])[0]
        groups = [a[i + 1] for i, x in enumerate(a) if x == "--group-by"]
        metric = compute["metric"]
        key = f"{metric}:{compute['op']}"
        if not groups:
            return {"data": []}
        if groups == ["model"]:
            per = {"genai.cost": (10.0, 1.0), "genai.requests": (20, 5), "trace_count": (10, 5)}
            if self.agg:
                per |= {m: (v, None) for m, v in AGG.items()}
            if metric not in per:
                return {"data": []}
            sol, emb = per[metric]
            return {"data": [{"group": {"model": SOL}, "metrics": {key: sol}},
                             {"group": {"model": "openai/text-embedding-4"}, "metrics": {key: emb}}]}
        if groups == ["project"]:
            return {"data": [{"group": {"project": "p1"}, "timestamp": f"2026-11-10T{h:02d}:00:00Z",
                              "metrics": {key: 4}} for h in (9, 10, 11)]}
        if groups == ["thread", "project"]:
            return {"data": [{"group": {"thread": "th1", "project": "p1"}, "metrics": {key: 4 if "count" in key
                                                                                       else 2.0}}]}
        if groups == ["trace_framework"]:
            return {"data": [{"group": {"trace_framework": "openai-sdk"}, "metrics": {"orq.gen_ai.cost:sum": 9.0}}]}
        return {"data": []}


REAL_MAKE_CLIENT = c.make_client


@pytest.fixture(autouse=True)
def fake_sdk(monkeypatch):
    monkeypatch.setattr(c, "make_client", lambda workspace: (FakeOrq.last.client, lambda: None))


def ns(tmp_path, **kw):
    d = {"workspace": "demo", "out": str(tmp_path), "sample": 10, "counterfactual_test": False, "cache_threads": 5,
         "seed": 7, "yes": True, "window": 30, "json": False, "keep_cache": True, "filters": None}
    d.update(kw)
    return argparse.Namespace(**d)


def run(tmp_path, **kw):
    an = c.Analyzer(ns(tmp_path, **kw))
    an.orq = FakeOrq()
    return an, an.run()
