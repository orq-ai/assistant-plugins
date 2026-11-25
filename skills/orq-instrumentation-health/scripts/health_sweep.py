# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Instrumentation health sweep for orq-instrumentation-health.

The agent runs every `orq` command; this script only writes request bodies and
reads the saved JSON back. Stdlib only, never calls the network.

Usage:
    uv run --no-project <skill-dir>/scripts/health_sweep.py init <run-dir> [--days N]
    uv run --no-project <skill-dir>/scripts/health_sweep.py plan <run-dir>
    uv run --no-project <skill-dir>/scripts/health_sweep.py sample <run-dir>
    uv run --no-project <skill-dir>/scripts/health_sweep.py pii <run-dir>
    uv run --no-project <skill-dir>/scripts/health_sweep.py score <run-dir> > <run-dir>/scorecard.json

Files the agent saves in <run-dir> (all `-o json`):
    fields.json              orq traces list-fields
    names.json               orq traces list-facet-values name --from F --to T
    count-<check>.json       orq traces aggregate --from-file bodies/count-<check>.json
    search-<n>.json          orq traces search --from-file bodies/search-<n>.json
    spans/<trace>.<p>.json   orq traces list-spans <trace> --limit 100 [--page-token ...], p = 1, 2, ...
    conversation/<trace>.json  orq traces conversation <trace> -o json (content pass only)
    pii/<trace>.json         orq pii detect --from-file pii/<trace>.body.json (content pass only)
"""

import argparse
import codecs
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SAMPLE_PER_GROUP = 20
SAMPLE_TOTAL = 60  # caps the list-spans calls the agent makes across all groups
SEARCH_LIMIT = 200  # search rows are root spans, several can share a trace id
CONTENT_PER_GROUP = 5
NO_AGENT = "no agent"
CLAUDE_CODE_ROOT = "orq.claude_code.session"  # plugins/trace-hooks/src/handlers.js
GENERIC_NAMES = {"default", "trace-1", "llmchain", "runnablesequence", "main"}

# check -> filters. tokens.total is a per-trace rollup and rejects not_exists, so
# "model but no token count" is count-model minus count-model-tokens. Errored
# traces are left out of the token checks: a failed call has no tokens.
MODEL_OK = [{"field": "model", "op": "exists"}, {"field": "status", "op": "neq", "values": ["error"]}]
COUNT_CHECKS = {
    "total": [],
    "model": MODEL_OK,
    "model-tokens": [*MODEL_OK, {"field": "tokens.total", "op": "exists"}],
    "model-tokens-zero": [*MODEL_OK, {"field": "tokens.total", "op": "eq", "values": ["0"]}],
    "user-id": [{"field": "metadata.user_id", "op": "exists"}],
}
SPAN_CHECKS = ("provider_no_model", "type_missing", "tokens_no_cost", "orphan_parent", "flat", "flat_claude_code")


def load(path: Path):
    raw = path.read_bytes()
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return json.loads(raw.decode("utf-16"))
    return json.loads(raw.decode("utf-8-sig"))


def load_obj(path: Path) -> dict | None:
    """A saved CLI response, or None when it is missing, empty, not JSON or not an object."""
    try:
        doc = load(path)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def dump(path: Path, data, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    if private:
        path.chmod(0o600)


def group_key(project_id: str, agent: str | None) -> str:
    return f"{project_id}|{agent or NO_AGENT}"


def cmd_init(run: Path, days: int, now: datetime | None = None) -> dict:
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    window = {
        "from": (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "days": days,
    }
    dump(run / "window.json", window)
    for sub in ("spans", "conversation", "pii"):
        (run / sub).mkdir(parents=True, exist_ok=True)
    dump(
        run / "bodies" / "examples-model-tokens-zero.json",
        {"from": window["from"], "to": window["to"], "limit": 20, "filters": COUNT_CHECKS["model-tokens-zero"]},
    )
    for name, filters in COUNT_CHECKS.items():
        body = {
            "from": window["from"],
            "to": window["to"],
            "group_by": ["project_id", "agent_name"],
            "compute": [{"metric": "trace_id", "op": "count"}],
        }
        if filters:
            body["filters"] = filters
        dump(run / "bodies" / f"count-{name}.json", body)
    return {"window": window, "count_bodies": sorted(f"bodies/count-{n}.json" for n in COUNT_CHECKS)}


def count_rows(run: Path, check: str) -> tuple[dict[str, int] | None, str]:
    """Rows per group, or None and the reason. A group with no row has 0 traces."""
    path = run / f"count-{check}.json"
    if not path.exists():
        return None, f"count-{check}.json not saved"
    doc = load_obj(path)
    if doc is None or not isinstance(doc.get("data"), list):
        return None, f"count-{check}.json has no data list"
    out = {}
    for row in doc["data"]:
        g, metrics = row.get("group") or {}, row.get("metrics") or {}
        if "trace_id.count" not in metrics:
            return None, f"count-{check}.json row without trace_id.count"
        out[group_key(g.get("project_id", ""), g.get("agent_name"))] = int(metrics["trace_id.count"])
    return out, ""


def cmd_plan(run: Path) -> dict:
    window = load(run / "window.json")
    totals, reason = count_rows(run, "total")
    if totals is None:
        raise SystemExit(f"{reason}: rerun the total aggregate")
    groups = []
    for n, key in enumerate(sorted(totals)):
        project_id, agent = key.split("|", 1)
        flt = (
            {"field": "agent_name", "op": "not_exists"}
            if agent == NO_AGENT
            else {"field": "agent_name", "op": "eq", "values": [agent]}
        )
        dump(
            run / "bodies" / f"search-{n}.json",
            {
                "from": window["from"],
                "to": window["to"],
                "limit": SEARCH_LIMIT,
                "filters": [{"field": "project_id", "op": "eq", "values": [project_id]}, flt],
            },
        )
        groups.append({"n": n, "project_id": project_id, "agent": agent, "traces": totals[key]})
    dump(run / "groups.json", groups)
    return {"groups": groups}


def search_rows(run: Path, n: int) -> list[dict]:
    path = run / f"search-{n}.json"
    if not path.exists():
        return []
    data = (load_obj(path) or {}).get("data")
    return data if isinstance(data, list) else []


def cmd_sample(run: Path) -> dict:
    groups = load(run / "groups.json")
    per_group = max(1, min(SAMPLE_PER_GROUP, SAMPLE_TOTAL // max(1, len(groups))))
    sample = {}
    for g in groups:
        picked: dict[str, dict] = {}
        for row in search_rows(run, g["n"]):
            tid = row.get("trace_id")
            if tid and tid not in picked:
                picked[tid] = {
                    "product": row.get("product", ""),
                    "session_id": row.get("session_id", ""),
                }
            if len(picked) == per_group:
                break
        sample[str(g["n"])] = picked
    dump(run / "sample.json", sample)
    return {
        "list_spans": sorted({t for p in sample.values() for t in p}),
        "content_pass": sorted({t for tids in content_traces(run).values() for t in tids}),
    }


def trace_spans(run: Path, tid: str) -> tuple[list[dict], bool]:
    """All saved pages for one trace, and whether they run 1..k with only the last saying has_more false."""
    pages = sorted((run / "spans").glob(f"{tid}.*.json"), key=lambda p: int(p.suffixes[-2][1:]))
    if [int(p.suffixes[-2][1:]) for p in pages] != list(range(1, len(pages) + 1)):
        return [], False
    spans, complete = [], False
    for page in pages:
        data = load_obj(page)
        if data is None or not isinstance(data.get("data"), list) or not isinstance(data.get("has_more"), bool):
            return [], False
        spans.extend(data["data"])
        complete = not data["has_more"]
    return spans, complete and bool(spans)


def span_findings(spans: list[dict], product: str) -> set[str]:
    found = set()
    ids = {s.get("span_id") for s in spans}
    parent = {s.get("span_id"): s.get("parent_span_id") or "" for s in spans}
    has_child = set(parent.values())
    orphans = {sid for sid, p in parent.items() if p and p not in ids}
    for s in spans:
        model, provider = s.get("model") or "", s.get("provider") or ""
        tokens = ((s.get("usage") or {}).get("total_tokens")) or 0
        cost = (s.get("cost") or {}).get("total")
        if provider and not model:
            found.add("provider_no_model")
        if not s.get("type"):
            found.add("type_missing")
        # wrappers (provider "agent", or any span with children) carry rolled-up tokens with cost 0
        wrapper = provider == "agent" or s.get("span_id") in has_child
        if model and tokens > 0 and not cost and not wrapper:
            found.add("tokens_no_cost")
        if s.get("span_id") in orphans:
            found.add("orphan_parent")

    def depth(sid: str) -> int:
        d, seen = 0, set()
        while parent.get(sid) and parent[sid] in ids and sid not in seen:
            seen.add(sid)
            sid, d = parent[sid], d + 1
        return d

    linked = [sid for sid in parent if sid not in orphans]
    if len(linked) >= 3 and product != "router" and max(depth(sid) for sid in linked) <= 1:
        roots = {s.get("name") for s in spans if not s.get("parent_span_id")}
        found.add("flat_claude_code" if CLAUDE_CODE_ROOT in roots else "flat")
    return found


def part_text(part: dict) -> str:
    """`orq traces conversation -o json` parts: {"type": "text", "text": ...} or {"type": "json", "value": ...}."""
    if isinstance(part.get("text"), str):
        return part["text"]
    value = part.get("value")
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value)


def conversation_gap(conv: dict) -> str | None:
    def has_text(role: str) -> bool:
        return any(
            m.get("role") == role and any(part_text(c).strip() for c in m.get("content") or [])
            for m in conv.get("messages") or []
        )

    if not has_text("user"):
        return "no_input"
    if not has_text("assistant"):
        return "no_output"
    return None


def conversation_text(conv: dict) -> str:
    return "\n".join(
        t for m in conv.get("messages") or [] for c in m.get("content") or [] if (t := part_text(c)).strip()
    )


def content_traces(run: Path) -> dict[str, list[str]]:
    sample = load(run / "sample.json")
    return {n: list(picked)[:CONTENT_PER_GROUP] for n, picked in sample.items()}


def where(g: dict) -> dict:
    return {"project_id": g["project_id"], "group": g["agent"]}


def cmd_pii(run: Path) -> dict:
    bodies = []
    for tids in content_traces(run).values():
        for tid in tids:
            conv_path = run / "conversation" / f"{tid}.json"
            if not conv_path.exists():
                continue
            conv = load_obj(conv_path)
            text = conversation_text(conv) if conv else ""
            if not text:  # pii detect rejects empty text
                continue
            body = run / "pii" / f"{tid}.body.json"
            dump(body, {"text": text, "include_entities": True}, private=True)
            bodies.append(str(body.relative_to(run)))
    return {"pii_calls": len(bodies), "bodies": bodies}


def fields_index(run: Path) -> dict[str, list[str]] | None:
    doc = load_obj(run / "fields.json")
    if doc is None or not isinstance(doc.get("data"), list):
        return None
    return {f["name"]: f.get("operators") or [] for f in doc["data"]}


def unobservable_counts(fields: dict[str, list[str]] | None) -> dict[str, str]:
    out = {}
    for check, filters in COUNT_CHECKS.items():
        for f in filters:
            if fields is None:
                reason = "fields.json missing or unreadable"
            elif f["field"] not in fields:
                reason = f"field {f['field']} not in list-fields"
            elif f["op"] not in fields[f["field"]]:
                reason = f"operator {f['op']} not allowed on {f['field']}"
            else:
                continue
            out.setdefault(check, reason)
    return out


def cmd_score(run: Path) -> dict:
    window = load(run / "window.json")
    groups = load(run / "groups.json")
    if not (run / "sample.json").exists():
        raise SystemExit("sample.json is missing: run the sample step first")
    sample = load(run / "sample.json")
    bad = unobservable_counts(fields_index(run))
    counts = {}
    for c in COUNT_CHECKS:
        rows, reason = count_rows(run, c)
        if rows is None:
            bad.setdefault(c, reason)
        counts[c] = None if c in bad else rows
    unobservable = [{"check": c, "reason": r} for c, r in bad.items()]
    content_ids = content_traces(run)
    content_ran = (run / "conversation").is_dir() and any((run / "conversation").glob("*.json"))
    pii_ran = any(not p.name.endswith(".body.json") for p in (run / "pii").glob("*.json"))

    def count(check: str, key: str) -> int | None:
        rows = counts[check]
        return None if rows is None else rows.get(key, 0)

    projects: dict[str, list[dict]] = {}
    for g in groups:
        key = group_key(g["project_id"], None if g["agent"] == NO_AGENT else g["agent"])
        model, with_tokens = count("model", key), count("model-tokens", key)
        out = {
            "agent": g["agent"],
            "traces": g["traces"],
            "counts": {
                "model_tokens_zero": count("model-tokens-zero", key),
                "model_no_tokens": None if model is None or with_tokens is None else model - with_tokens,
                "user_id_set": count("user-id", key),
            },
        }
        picked = sample.get(str(g["n"]), {})
        hits = {c: [] for c in (*SPAN_CHECKS, "no_session_id")}
        incomplete = []
        for tid, meta in picked.items():
            spans, complete = trace_spans(run, tid)
            if not complete:
                incomplete.append(tid)
                continue
            for c in span_findings(spans, meta["product"]):
                hits[c].append(tid)
            if not meta["session_id"] or meta["session_id"] == tid:
                hits["no_session_id"].append(tid)
        out["sampled"] = len(picked) - len(incomplete)
        out["sample"] = {c: {"traces": len(t), "examples": t[:3]} for c, t in hits.items()}
        if g["traces"] and not picked:
            unobservable.append(
                {"check": "sample", **where(g), "reason": f"search-{g['n']}.json missing or empty"}
            )
        if incomplete:
            unobservable.append(
                {"check": "sample", **where(g), "reason": f"spans missing or not fully paged: {incomplete}"}
            )
        content = {}
        for tid in content_ids.get(str(g["n"]), []) if content_ran else []:
            conv_path, pii_path = run / "conversation" / f"{tid}.json", run / "pii" / f"{tid}.json"
            if not conv_path.exists():
                unobservable.append({"check": "content", **where(g), "reason": f"conversation/{tid}.json not saved"})
                continue
            conv = load_obj(conv_path)
            if conv is None:
                unobservable.append({"check": "content", **where(g), "reason": f"conversation/{tid}.json unreadable"})
                continue
            gap = conversation_gap(conv)
            if gap:
                content.setdefault(gap, []).append(tid)
            if pii_ran and (run / "pii" / f"{tid}.body.json").exists() and not pii_path.exists():
                unobservable.append({"check": "pii", **where(g), "reason": f"pii/{tid}.json not saved"})
            if pii_path.exists():
                entities = (load_obj(pii_path) or {}).get("entities") or {}
                if entities:
                    content.setdefault("pii", []).append(
                        {"trace": tid, "entities": {k: int(v) for k, v in entities.items()}}
                    )
        if content:
            out["content"] = content
        projects.setdefault(g["project_id"], []).append(out)

    names = []
    doc = load_obj(run / "names.json")
    if doc is not None:
        for row in doc.get("values") or []:
            if str(row.get("value", "")).strip().lower() in GENERIC_NAMES:
                names.append({"name": row["value"], "count": row.get("count")})
        if doc.get("has_more"):
            unobservable.append({"check": "generic_names", "reason": "names.json is capped (has_more true)"})
    else:
        unobservable.append({"check": "generic_names", "reason": "names.json missing or unreadable"})

    if content_ran:  # the scorecard holds what was found; the raw trace text is not kept
        shutil.rmtree(run / "conversation", ignore_errors=True)
        for body in (run / "pii").glob("*.body.json"):
            body.unlink()

    return {
        "window": window,
        "projects": [{"project_id": p, "groups": gs} for p, gs in projects.items()],
        "generic_names": names,
        "content_pass": ("done" if pii_ran else "no pii") if content_ran else "skipped",
        "unobservable": unobservable,
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["init", "plan", "sample", "pii", "score"])
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args(argv)
    run = args.run_dir
    if args.step == "init":
        result = cmd_init(run, args.days)
    else:
        result = {"plan": cmd_plan, "sample": cmd_sample, "pii": cmd_pii, "score": cmd_score}[args.step](run)
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
