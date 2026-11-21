# /// script
# requires-python = ">=3.11"
# dependencies = ["evaluatorq>=1.98"]
# ///
"""Cost-savings report for one orq.ai workspace.

`analyze` samples leaf LLM spans from the workspace's traces, scores each span's routing input with the Smart Router's
`jev-general-v1` decision, re-prices the span at the model the router would have picked, and scales the per-model
saving rate to the window's spend with a bootstrap CI. Pools and model switches come from the intelligence/price
frontier in evaluatorq's `model_frontier`, in four scopes: same or any provider, enabled or all available models.
It also scans multi-turn threads for missed prompt caching and writes `report.json` plus `narrative-slots.md`. `render` merges the agent's `narrative.md` into `report.html`.

Every number comes from this script; the agent writes prose only. All reads and the decision calls go through the
`orq` CLI (`-o json`), so the user's CLI login and workspace are reused. Responses are cached under `<out>/cache`,
so a rerun resumes without paying twice. Spec: ../resources/design.md.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import math
import random
import re
import subprocess
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from evaluatorq.common import model_frontier as mf

SCHEMA_VERSION = "1.1"
MAX_SAMPLE = 100
MIN_PER_STRATUM = 5
WINDOW_DAYS = 29
BOOTSTRAP = 1000
WORKERS = 8
PRICING_GAP = 0.02
PROMPT_LIMIT = 65_000

DECISION_MODEL = "typesafe/jev-latest"
DECISION_PRICE_PER_M = 0.042
DECISION_OVERHEAD_TOKENS = 330
POLICY = "jev-general-v1"
QUESTION = {
    "difficulty": {
        "type": "choice",
        "instructions": "How difficult is this prompt for an LLM to complete well?",
        "criteria": {
            "easy": "Trivial change, any model handles it",
            "medium": "Moderate complexity, benefits from a capable model",
            "hard": "Complex task, needs a strong model",
        },
    }
}
THRESHOLDS = {"cost": (0.59, 1.16), "balanced": (0.11, 0.92)}
PROFILES = ("balanced", "cost")
TIERS = ("easy", "medium", "hard")

LLM_SPAN_TYPES = {"span.chat_completion", "span.responses"}
DECISION_MODEL_RE = re.compile(r"^(typesafe/|orq/decider-)")
NON_CHAT_RE = re.compile(r"embed|rerank|tts|transcri|whisper|moderation|ocr|dall-e|image", re.I)

PRERELEASE_RE = re.compile(r"(?:^|[-_.])(preview|beta|alpha|experimental)(?:$|[-_.])", re.IGNORECASE)  # name segment
PROVIDER_FAMILY = {"google-ai": "google"}  # one provider for same-provider scopes; routes keep their own prices
MIN_SAMPLE = 15  # fewer sampled calls on a model than this get a "Small sample" note
SCOPES = ("same_provider:all", "same_provider:enabled", "any_provider:all", "any_provider:enabled")
HEADLINE_SCOPE = "same_provider:all"
REGION_RE = re.compile(r"^(global|us|eu|apac|ap|jp|au|ca|uk|[a-z]{2}-[a-z]+-[0-9]+)\.")
VENDOR_RE = re.compile(r"^(anthropic|openai|google|meta|mistral|amazon|cohere|deepseek|zai|moonshotai)\.")
SR_BLOCK = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)


# --------------------------------------------------------------------------------------------- routing input


def _part_text(part: Any) -> str | None:
    if isinstance(part, str):
        return part
    if not isinstance(part, dict):
        return None
    kind = part.get("type") or part.get("kind")
    if kind not in ("text", "input_text"):
        return None
    for key in ("content", "text"):
        if isinstance(part.get(key), str):
            return part[key]
    return None


def message_text(msg: dict) -> str:
    """Text of one message: text parts cleaned of complete <system-reminder> blocks, trimmed, joined by a space."""
    pieces: list[str] = []
    parts = msg.get("parts")
    if isinstance(parts, list):
        pieces = [t for t in (_part_text(p) for p in parts) if t is not None]
    elif isinstance(msg.get("content"), str):
        pieces = [msg["content"]]
    elif isinstance(msg.get("content"), list):
        pieces = [t for t in (_part_text(p) for p in msg["content"]) if t is not None]
    cleaned = [SR_BLOCK.sub("", p).strip() for p in pieces]
    return " ".join(p for p in cleaned if p)


def _system_text(attrs: dict) -> str:
    gen = attrs.get("gen_ai") or {}
    msgs = ((gen.get("input") or {}).get("messages")) or []
    texts = [message_text(m) for m in msgs if m.get("role") in ("system", "developer")]
    raw = gen.get("system_instructions")
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            texts.append(message_text({"parts": parsed if isinstance(parsed, list) else [parsed]}))
        except json.JSONDecodeError:
            texts.append(SR_BLOCK.sub("", raw).strip())
    return " ".join(t for t in texts if t)


def routing_input(attrs: dict) -> tuple[str | None, str]:
    """(text, source). source: user | system | instructions | empty_input | prompt_limit."""
    msgs = (((attrs.get("gen_ai") or {}).get("input") or {}).get("messages")) or []
    text, source = "", "empty_input"
    for m in reversed(msgs):
        if m.get("role") == "user":
            t = message_text(m)
            if t:
                text, source = t, "user"
                break
    if not text:
        t = _system_text(attrs)
        if t:
            text, source = t, "system"
    if not text:
        instr = (attrs.get("openresponses") or {}).get("instructions")
        if isinstance(instr, str) and SR_BLOCK.sub("", instr).strip():
            text, source = SR_BLOCK.sub("", instr).strip(), "instructions"
    if not text:
        return None, "empty_input"
    if len(text) > PROMPT_LIMIT:
        return None, "prompt_limit"
    return text, source


# --------------------------------------------------------------------------------------------- score, tier, bands


def score(probs: dict) -> float:
    return float(probs.get("easy", 0)) * 0.0 + float(probs.get("medium", 0)) * 1.0 + float(probs.get("hard", 0)) * 2.0


def tier_of(s: float, profile: str) -> str:
    t1, t2 = THRESHOLDS[profile]
    if s <= t1:
        return "easy"
    if s <= t2:
        return "medium"
    return "hard"


@dataclass
class PoolModel:
    id: str
    index: float
    band_price: float


def bands(pool: list[PoolModel]) -> dict[str, list[PoolModel]]:
    ps = sorted(pool, key=lambda p: (-p.index, p.id))
    n_easy = len(ps) // 2
    easy = ps[len(ps) - n_easy:] if n_easy else []
    rest = ps[: len(ps) - n_easy]
    n_hard = len(rest) // 2
    return {"hard": rest[:n_hard], "medium": rest[n_hard:], "easy": easy}


def pick(pool: list[PoolModel], tier: str, profile: str) -> PoolModel:
    b = bands(pool)
    target = tier
    if not b[target]:
        stronger = [t for t in TIERS[TIERS.index(tier) + 1:] if b[t]]
        target = stronger[0] if stronger else next(t for t in reversed(TIERS) if b[t])
    cand = b[target]
    return sorted(cand, key=lambda p: (p.band_price, -p.index, p.id))[0]


# --------------------------------------------------------------------------------------------- frontier pools


def switch_target(incumbent: mf.ScoredModel, cands: list[mf.ScoredModel],
                  cost: Callable[[str], float | None] | None = None) -> mf.ScoredModel | None:
    """The cheapest candidate that dominates the incumbent: at least as intelligent and no more expensive, strictly
    better on one.

    With `cost` (the incumbent's sampled spans re-priced on a model, cache reads and writes included) price is that
    cost; a model `cost` cannot price is never a target. Without it, evaluatorq's 3:1 input/output blend."""
    if incumbent.free:
        return None
    if cost is None:
        better = [m for m in cands if m.router_id != incumbent.router_id and m.dominates(incumbent)]
        return min(better, key=lambda m: (m.price, -m.default_index, m.router_id)) if better else None
    own = cost(incumbent.router_id)
    if own is None:
        return None
    priced = [(c, m) for m in cands if m.router_id != incumbent.router_id and (c := cost(m.router_id)) is not None]
    better = [(c, m) for c, m in priced if m.default_index >= incumbent.default_index and c <= own
              and (m.default_index > incumbent.default_index or c < own)]
    return min(better, key=lambda t: (t[0], -t[1].default_index, t[1].router_id))[1] if better else None


Cost = Callable[[str], "float | None"]


def _pricer(cost: Cost | None) -> Callable[[mf.ScoredModel], float | None]:
    return (lambda m: m.price) if cost is None else (lambda m: cost(m.router_id))


def _beats(a: mf.ScoredModel, b: mf.ScoredModel, price: Callable[[mf.ScoredModel], float | None]) -> bool:
    pa, pb = price(a), price(b)
    if pa is None or pb is None:
        return False
    return a.default_index >= b.default_index and pa <= pb and (a.default_index > b.default_index or pa < pb)


# A seat counts once it is at least half the price of the seat above. evaluatorq lowers its MIN_TIER_RATIO
# from 2.75 to the same 2.0; until that release is the floor here, the skill pins it.
MIN_TIER_RATIO = 2.0


def frontier_pool(top: mf.ScoredModel, cands: list[mf.ScoredModel],
                  cost: Cost | None = None) -> tuple[list[mf.ScoredModel], bool]:
    """(members strongest first, relaxed): `top` plus up to two cheaper frontier models, by the RES-1285 criteria.

    The lower seats are on the candidates' frontier, weaker than `top`, distinct models, and each sits at least
    MIN_TIER_RATIO cheaper than the seat above it; of the trios that pass, the one whose weakest seat is strongest
    wins, ties on total index. With no trio the best pair is returned and flagged relaxed; with no pair, [top].

    Price is `cost` (the incumbent's sampled spans re-priced on the model, cache included) when given, so the
    frontier, the tier gaps and dominance follow this workspace's traffic; else evaluatorq's 3:1 blend."""
    ratio = MIN_TIER_RATIO
    ident = mf.model_identity
    price = _pricer(cost)
    top_p = price(top)
    if top_p is None:
        return [top], True
    priced = [m for m in cands if not m.free and price(m) is not None]
    front = [m for m in priced if not any(_beats(o, m, price) for o in priced if o is not m)]
    lower = [m for m in front if m.default_index < top.default_index
             and ident(m.router_id) != ident(top.router_id) and price(m) * ratio <= top_p]
    best: tuple | None = None
    for mid in lower:
        for low in lower:
            if price(low) * ratio <= price(mid) and ident(low.router_id) != ident(mid.router_id):
                rank = (min(mid.default_index, low.default_index), mid.default_index + low.default_index,
                        mid.router_id, low.router_id)
                if best is None or rank > best[0]:
                    best = (rank, [top, mid, low])
    if best:
        return best[1], False
    if lower:
        return [top, max(lower, key=lambda m: (m.default_index, -price(m), m.router_id))], True
    return [top], True


def has_dominated(members: list[mf.ScoredModel], cost: Cost | None = None) -> bool:
    price = _pricer(cost)
    return any(_beats(a, b, price) for a in members for b in members if a is not b)


def region_of(model_id: str) -> str | None:
    name = model_id.split("/", 1)[1] if "/" in model_id else model_id
    m = REGION_RE.match(name)
    if not m or m.group(1) == "global":
        return None
    r = m.group(1)
    return r.split("-", 1)[0] if "-" in r else r


def developer_of(canonical: str) -> str:
    return canonical.split("/", 1)[0]


# --------------------------------------------------------------------------------------------- pricing


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


MISSING = _Missing()
_TOKEN = re.compile(r'\s*(?:(\d+(?:\.\d+)?)|"([^"]*)"|([A-Za-z_][A-Za-z0-9_]*)|(==|!=|>=|<=|&&|\|\||[<>()+\-*/%]))')


def _tokenize(expr: str) -> list[tuple[str, Any]]:
    out, pos = [], 0
    expr = expr.strip()
    while pos < len(expr):
        m = _TOKEN.match(expr, pos)
        if not m or m.end() == pos:
            raise ValueError(f"bad token in {expr!r} at {pos}")
        num, s, ident, op = m.groups()
        if num is not None:
            out.append(("num", float(num) if "." in num else int(num)))
        elif s is not None:
            out.append(("str", s))
        elif ident is not None:
            out.append(("var", ident) if ident not in ("true", "false") else ("bool", ident == "true"))
        else:
            out.append(("op", op))
        pos = m.end()
    return out


class _Parser:
    """Recursive descent: or > and > comparison > additive > multiplicative > primary."""

    def __init__(self, tokens: list[tuple[str, Any]]):
        self.t, self.i = tokens, 0

    def peek(self) -> tuple[str, Any] | None:
        return self.t[self.i] if self.i < len(self.t) else None

    def eat(self, op: str | None = None) -> tuple[str, Any]:
        tok = self.peek()
        if tok is None or (op is not None and tok != ("op", op)):
            raise ValueError(f"expected {op} at token {self.i}")
        self.i += 1
        return tok

    def parse(self) -> tuple:
        node = self.or_()
        if self.peek() is not None:
            raise ValueError("trailing tokens")
        return node

    def _binary(self, sub, ops):
        node = sub()
        while (tok := self.peek()) and tok[0] == "op" and tok[1] in ops:
            self.i += 1
            node = ("bin", tok[1], node, sub())
        return node

    def or_(self):
        return self._binary(self.and_, ("||",))

    def and_(self):
        return self._binary(self.cmp, ("&&",))

    def cmp(self):
        return self._binary(self.add, ("==", "!=", ">", ">=", "<", "<="))

    def add(self):
        return self._binary(self.mul, ("+", "-"))

    def mul(self):
        return self._binary(self.primary, ("*", "/", "%"))

    def primary(self):
        tok = self.eat()
        if tok == ("op", "("):
            node = self.or_()
            self.eat(")")
            return node
        if tok[0] in ("num", "str", "bool"):
            return ("lit", tok[1])
        if tok[0] == "var":
            return ("var", tok[1])
        raise ValueError(f"unexpected {tok}")


def parse_when(expr: str) -> tuple:
    return _Parser(_tokenize(expr)).parse()


def when_vars(node: tuple) -> set[str]:
    if node[0] == "var":
        return {node[1]}
    if node[0] == "bin":
        return when_vars(node[2]) | when_vars(node[3])
    return set()


def when_conditions(node: tuple) -> int:
    """Number of comparisons in a condition: how much of the span it tests."""
    if node[0] != "bin":
        return 0
    own = 1 if node[1] in ("==", "!=", ">", ">=", "<", "<=") else 0
    return own + when_conditions(node[2]) + when_conditions(node[3])


def eval_when(node: tuple, env: dict) -> Any:
    kind = node[0]
    if kind == "lit":
        return node[1]
    if kind == "var":
        v = env.get(node[1])
        return MISSING if v is None else v
    op, a, b = node[1], eval_when(node[2], env), eval_when(node[3], env)
    if op == "&&":
        return a is True and b is True
    if op == "||":
        return a is True or b is True
    if a is MISSING or b is MISSING:
        return MISSING if op in "+-*/%" else False
    try:
        if op == "==":
            return a == b
        if op == "!=":
            return a != b
        if op in (">", ">=", "<", "<="):
            if isinstance(a, str) != isinstance(b, str):
                return False
            return {">": a > b, ">=": a >= b, "<": a < b, "<=": a <= b}[op]
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            return a // b if isinstance(a, int) and isinstance(b, int) else a / b
        if op == "%":
            return a % b
    except (TypeError, ZeroDivisionError):
        return MISSING if op in "+-*/%" else False
    raise ValueError(op)


def _unit(entry: Any) -> float | None:
    if not isinstance(entry, dict) or entry.get("cost") is None:
        return None
    return float(entry["cost"]) / float(entry.get("per") or 1_000_000)


@dataclass
class PriceChoice:
    prices: dict[str, float]
    variant: str | None
    flag: str | None = None


def choose_price(pricing: dict, env: dict) -> PriceChoice:
    """Base price unless a variant's condition holds on what the span shows.

    Of the matching variants the one with the most comparisons wins (closest to how the client calls the model);
    ties go to the last listed, which is how the catalog orders newer prices after older ones."""
    base = {k: u for k, v in pricing.items() if k != "variants" and (u := _unit(v)) is not None}
    best, best_n, best_when = None, -1, None
    for var in pricing.get("variants") or []:
        when = var.get("when")
        if not when:
            continue
        try:
            node = parse_when(when)
        except ValueError:
            continue
        if eval_when(node, env) is True:
            n = when_conditions(node)
            if n >= best_n:
                best, best_n, best_when = var, n, when
    prices = dict(base)
    if best is not None:
        prices.update({k: u for k, v in best.items() if k != "when" and (u := _unit(v)) is not None})
    flag = None
    tier = env.get("tier")
    if tier in ("batch", "priority", "flex"):
        has = any(f'"{tier}"' in (v.get("when") or "") for v in pricing.get("variants") or [])
        if not has:
            flag = f"no {tier} price"
    return PriceChoice(prices, best_when, flag)


def span_env(summary: dict, attrs: dict, model_id: str) -> dict:
    usage = (attrs.get("gen_ai") or {}).get("usage") or {}
    tier = (summary.get("cost") or {}).get("pricing_tier") or ((attrs.get("openai") or {}).get("response") or {}).get(
        "service_tier")
    if tier in ("default", "auto", ""):
        tier = "standard" if tier else None
    geo = usage.get("inference_geo")
    if geo in (None, "", "global"):
        geo = region_of(model_id)
    started = summary.get("started_at")
    return {
        "request_unix": int(parse_ts(started).timestamp()) if started else None,
        "input_tokens": int((summary.get("usage") or {}).get("prompt_tokens") or 0),
        "tier": tier,
        "inference_geo": geo,
        "speed": usage.get("speed") or None,
        "operation": summary.get("operation") or None,
    }


@dataclass
class Tokens:
    uncached: int
    cache_read: int
    cache_write: int
    output: int
    ttl: str | None
    prompt: int


def span_tokens(summary: dict, attrs: dict) -> Tokens:
    u = summary.get("usage") or {}
    prompt = int(u.get("prompt_tokens") or 0)
    read = int(u.get("prompt_cached_tokens") or 0)
    write = int(u.get("prompt_cache_creation_tokens") or 0)
    details = (((attrs.get("gen_ai") or {}).get("usage") or {}).get("prompt_tokens_details")) or {}
    ttl = None
    for k, v in details.items():
        m = re.match(r"cache_creation_(\w+?)_tokens$", k)
        if m and v:
            ttl = m.group(1)
            break
    return Tokens(max(0, prompt - read - write), read, write, int(u.get("completion_tokens") or 0), ttl, prompt)


def _ttl_seconds(ttl: str) -> float:
    m = re.match(r"(\d+)([smhd])$", ttl)
    return int(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)] if m else math.inf


def write_price(prices: dict, ttl: str | None) -> float | None:
    if ttl and f"cache_write_{ttl}" in prices:
        return prices[f"cache_write_{ttl}"]
    keys = [k for k in prices if k.startswith("cache_write_")]
    if not keys:
        return prices.get("cache_write")
    return prices[min(keys, key=lambda k: _ttl_seconds(k.removeprefix("cache_write_")))]


def price_tokens(t: Tokens, prices: dict) -> float:
    inp, out = prices.get("input", 0.0), prices.get("output", 0.0)
    read_p = prices.get("cache_read", inp)
    w = write_price(prices, t.ttl)
    w = inp if w is None else w
    return t.uncached * inp + t.cache_read * read_p + t.cache_write * w + t.output * out


# --------------------------------------------------------------------------------------------- estimator


@dataclass
class Draw:
    """One sampled span; one draw of a (project, hour) cell."""
    stratum: str
    weight: float
    cur_price: float
    savings: dict[str, float] = field(default_factory=dict)  # per profile


def stratum_rate(draws: list[Draw], profile: str) -> float | None:
    den = sum(d.weight * d.cur_price for d in draws)
    if den <= 0:
        return None
    return sum(d.weight * d.savings.get(profile, 0.0) for d in draws) / den


def allocate(spend: list[tuple[str, float]], n: int, minimum: int = MIN_PER_STRATUM) -> dict[str, int]:
    """Spend-proportional allocation over the largest prefix of models where every model gets >= minimum."""
    ranked = sorted([s for s in spend if s[1] > 0], key=lambda s: -s[1])
    k = 0
    for j in range(1, len(ranked) + 1):
        tot = sum(c for _, c in ranked[:j])
        if n * ranked[j - 1][1] / tot >= minimum:
            k = j
        else:
            break
    if k == 0:
        return {ranked[0][0]: n} if ranked else {}
    top = ranked[:k]
    tot = sum(c for _, c in top)
    raw = [(m, n * c / tot) for m, c in top]
    alloc = {m: max(minimum, int(math.floor(x))) for m, x in raw}
    left = n - sum(alloc.values())
    for m, _x in sorted(raw, key=lambda r: -(r[1] - math.floor(r[1]))):
        if left <= 0:
            break
        alloc[m] += 1
        left -= 1
    return alloc


def estimate(draws_by: dict[str, list[Draw]], spend: dict[str, float], profile: str, rng: random.Random,
             boots: int = BOOTSTRAP) -> dict:
    per, total = {}, 0.0
    for s, ds in draws_by.items():
        r = stratum_rate(ds, profile)
        per[s] = None if r is None else r * spend.get(s, 0.0)
        total += per[s] or 0.0
    samples = []
    for _ in range(boots):
        tot = 0.0
        for s, ds in draws_by.items():
            if not ds:
                continue
            res = [ds[rng.randrange(len(ds))] for _ in ds]
            r = stratum_rate(res, profile)
            tot += (r or 0.0) * spend.get(s, 0.0)
        samples.append(tot)
    samples.sort()
    lo = samples[int(0.05 * (len(samples) - 1))] if samples else total
    hi = samples[int(math.ceil(0.95 * (len(samples) - 1)))] if samples else total
    return {"savings": total, "ci90": [lo, hi], "per_stratum": per}


def without_top(draws_by: dict[str, list[Draw]], spend: dict[str, float], profile: str, k: int = 3) -> float:
    flat = [(s, i, d) for s, ds in draws_by.items() for i, d in enumerate(ds)]
    top = sorted(flat, key=lambda x: -x[2].weight * x[2].savings.get(profile, 0.0))[:k]
    drop = {(s, i) for s, i, _ in top}
    kept = {s: [d for i, d in enumerate(ds) if (s, i) not in drop] for s, ds in draws_by.items()}
    return sum((stratum_rate(ds, profile) or 0.0) * spend.get(s, 0.0) for s, ds in kept.items() if ds)


# --------------------------------------------------------------------------------------------- caching


def caching_savings(turns: list[dict], prices: dict, ttl_s: float) -> dict:
    """turns: [{t (unix), prompt, cached, written}] sorted by time, one (thread, model)."""
    inp, read = prices.get("input", 0.0), prices.get("cache_read")
    write = write_price(prices, None)
    if read is None:
        return {"savings": 0.0, "eligible": False, "reason": "no cache-read price"}
    gain = cost = 0.0
    for k in range(1, len(turns)):
        alive = turns[k]["t"] - turns[k - 1]["t"] <= ttl_s
        read_k = turns[k - 1]["prompt"] if alive else 0
        read_k = min(read_k, turns[k]["prompt"])
        write_k = turns[k]["prompt"] - read_k
        gain += max(0, read_k - turns[k]["cached"]) * (inp - read)
        if write is not None:
            cost += max(0, write_k - turns[k]["written"]) * (write - inp)
    return {"savings": gain - cost, "eligible": True, "reason": ""}


def caching_candidate(turns: list[dict], prices: dict) -> tuple[bool, str]:
    if len(turns) < 3:
        return False, "fewer than 3 turns"
    if prices.get("cache_read") is None:
        return False, "no cache-read price"
    if min(t["prompt"] for t in turns[:-1]) < 1024:
        return False, "prefix under 1,024 tokens"
    tot = sum(t["prompt"] for t in turns)
    if tot and sum(t["cached"] for t in turns) / tot >= 0.20:
        return False, "already caching (read ratio >= 20%)"
    return True, ""


# --------------------------------------------------------------------------------------------- orq CLI


class OrqError(RuntimeError):
    def __init__(self, args: list[str], code: int, stderr: str):
        super().__init__(f"orq {' '.join(args[:3])} -> {code}: {stderr.strip()[:400]}")
        self.stderr = stderr


class Orq:
    def __init__(self, workspace: str | None, cache_dir: Path | None, retries: int = 4):
        self.workspace, self.cache_dir, self.retries = workspace, cache_dir, retries
        self.calls = 0
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def __call__(self, *args: str, body: dict | None = None, cache: bool = True) -> Any:
        argv = list(args) + (["--workspace", self.workspace] if self.workspace else []) + ["-o", "json"]
        key = hashlib.sha256(json.dumps([argv, body], sort_keys=True).encode()).hexdigest()[:24]
        path = self.cache_dir / f"{key}.json" if (self.cache_dir and cache) else None
        if path and path.exists():
            return json.loads(path.read_text())
        tmp = None
        if body is not None:
            tmp = (self.cache_dir or Path(".")) / f"body-{key}.json"
            tmp.write_text(json.dumps(body))
            argv += ["--from-file", str(tmp)]
        try:
            for attempt in range(self.retries + 1):
                self.calls += 1
                p = subprocess.run(["orq", *argv], capture_output=True, text=True, timeout=180)
                if p.returncode == 0:
                    out = json.loads(p.stdout) if p.stdout.strip() else None
                    if path:
                        path.write_text(json.dumps(out))
                    return out
                if re.search(r"HTTP (429|50[0-4])", p.stderr) and attempt < self.retries:
                    time.sleep(2 ** attempt)
                    continue
                raise OrqError(argv, p.returncode, p.stderr)
        finally:
            if tmp:
                tmp.unlink(missing_ok=True)
        raise AssertionError("unreachable")


# --------------------------------------------------------------------------------------------- helpers


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def iso(d: datetime) -> str:
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def model_key(provider: str, model: str) -> str:
    return f"{provider}/{model}" if provider else model


def split_model(m: str) -> tuple[str, str]:
    return tuple(m.split("/", 1)) if "/" in m else ("", m)  # type: ignore[return-value]


def is_chat_model(m: str, cat: dict) -> bool:
    if DECISION_MODEL_RE.match(m) or NON_CHAT_RE.search(m):
        return False
    entry = cat.get(m)
    if entry:
        outs = ((entry.get("modalities") or {}).get("output")) or ["text"]
        return "text" in outs
    return True


def has_auto_router(attrs: dict) -> bool:
    return "auto_router" in json.dumps(list(_keys(attrs)))


def _keys(d: Any, prefix: str = ""):
    if isinstance(d, dict):
        for k, v in d.items():
            yield prefix + k
            yield from _keys(v, prefix + k + ".")


def bucket(value: float, edges: list[tuple[float, str]]) -> str:
    for edge, label in edges:
        if value <= edge:
            return label
    return edges[-1][1]


CODING_HARNESSES = {"claude-code", "codex", "opencode", "kilo-code", "pi", "cursor", "cline", "aider"}

ASK_BUCKETS = [(128, "0-128"), (512, "128-512"), (1024, "512-1024"), (math.inf, ">1024")]
PROMPT_BUCKETS = [(8_000, "0-8k"), (32_000, "8k-32k"), (math.inf, ">32k")]


# --------------------------------------------------------------------------------------------- analyze


class Stop(Exception):
    def __init__(self, check: str, numbers: dict):
        super().__init__(check)
        self.check, self.numbers = check, numbers


class Analyzer:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.out = Path(args.out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.orq = Orq(args.workspace, self.out / "cache")
        self.rng = random.Random(args.seed)
        self.report: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "generated_at": iso(datetime.now(UTC)),
                                       "workspace": args.workspace, "status": "ok", "stop": None,
                                       "params": {"sample": args.sample, "profile": "balanced", "policy": POLICY,
                                                  "seed": args.seed, "counterfactual_test": args.counterfactual_test},
                                       "checks": {"stopping": [], "caveats": []}}
        self.decisions: dict[str, dict] = {}

    def log(self, msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    # ---- telemetry

    def tq(self, compute: list, group_by: list[str], filters: list | None = None, mode: str = "scalar",
           grain: str | None = None) -> list[dict]:
        a = ["telemetry", "query", "--source", "TELEMETRY_SOURCE_TRACES", "--mode", mode, "--from", self.frm,
             "--to", self.to, "--compute", json.dumps(compute), "--limit", "5000"]
        for g in group_by:
            a += ["--group-by", g]
        if filters:
            a += ["--filters", json.dumps(filters)]
        if grain:
            a += ["--grain", grain]
        r = self.orq(*a)
        if r.get("has_more"):
            self.report["checks"]["caveats"].append({"check": "telemetry truncated", "detail": " ".join(group_by)})
        return r.get("data") or []

    def previous_window(self) -> dict | None:
        """The window of an earlier run in this --out dir, so a rerun after `confirmation` hits the read cache."""
        try:
            prev = json.loads((self.out / "report.json").read_text())
        except (OSError, json.JSONDecodeError):
            return None
        w = prev.get("window")
        if not w or prev.get("workspace") != self.args.workspace:
            return None
        if datetime.now(UTC) - parse_ts(w["to"]) > timedelta(hours=24):
            return None
        return w

    def set_window(self) -> None:
        prev = self.previous_window()
        if prev:
            self.frm, self.to, self.days = prev["from"], prev["to"], prev["days"]
            self.report["window"] = dict(prev)
            return
        to = datetime.now(UTC).replace(second=0, microsecond=0)
        days = WINDOW_DAYS
        for _ in range(2):
            self.to, self.frm = iso(to), iso(to - timedelta(days=days))
            try:
                self.tq([{"metric": "genai.cost", "op": "sum"}], [])
                break
            except OrqError as e:
                m = re.search(r"retention period of (\d+) days", e.stderr)
                if not m:
                    raise
                days = max(1, int(m.group(1)) - 1)
        self.days = days
        self.report["window"] = {"from": self.frm, "to": self.to, "days": days}

    # ---- models

    def load_models(self) -> None:
        cat = self.orq("model-catalog", "list")
        cat = cat.get("data", cat) if isinstance(cat, dict) else cat
        self.cat = {m["id"]: m for m in cat}
        cards = self.orq("models", "list")
        cards = cards.get("data", cards) if isinstance(cards, dict) else cards
        self.cards = {model_key(c.get("provider", ""), c.get("model_id", "")): c for c in cards}
        self.garden = mf.garden_from_cards([c for c in cards if isinstance(c, dict)])
        if mf.catalog_scope(cards) == "project":
            self.report["checks"]["caveats"].append(
                {"check": "garden is project-scoped", "detail": "models list has no disabled models, so the "
                 "all-available scopes equal the enabled ones; use a workspace-scoped login"})
        dec = self.cards.get(DECISION_MODEL)
        if not dec or not dec.get("enabled"):
            raise Stop("decision model enabled", {"model": DECISION_MODEL,
                                                  "detail": "enable it in the workspace's model garden"})

    def v2(self, m: str) -> dict:
        c = self.cards.get(m) or {}
        return (((c.get("metadata") or {}).get("autorouter") or {}).get("v2")) or {}

    def poolable(self, m: str, score_ok: bool = True) -> bool:
        """Scored, priced, comparable, seatable and not pre-release (disabled is allowed: the all-available scopes
        recommend it)."""
        if PRERELEASE_RE.search(split_model(m)[1]) or self.discounted_offering(m):
            return False
        g, v, e = self.garden.get(m), self.v2(m), self.cat.get(m)
        p = (e or {}).get("pricing") or {}
        if score_ok and g and g.ranked_on_headline:  # no score at its default effort: the index is not reliable
            return False
        return bool(g and not g.free and mf.unseatable_reason(g) in (None, "disabled in the model catalogue")
                    and v.get("intelligence_index") and v.get("price") is not None and "input" in p and "output" in p)

    def discounted_offering(self, m: str) -> bool:
        """A same-host offering of another catalog model, priced below it (e.g. a `-contributor` offering): the
        catalog does not state its terms, so it is never recommended. Dated snapshots share the base price."""
        e = self.cat.get(m) or {}
        base = e.get("offering_of")
        if not base or base == m or base not in self.cat or self.host(base) != self.host(m):
            return False
        mine, theirs = e.get("pricing") or {}, (self.cat[base].get("pricing") or {})
        units = [(_unit(mine.get(k)), _unit(theirs.get(k))) for k in ("input", "output")]
        return any(a is not None and b is not None and a < b for a, b in units)

    def traffic_cost(self, m: str) -> Callable[[str], float | None] | None:
        """The sampled spans of `m` re-priced on a model, same tokens; None when a span hits a missing tier price."""
        spans = self._traffic.get(m) if hasattr(self, "_traffic") else None
        if not spans:
            return None
        memo: dict[str, float | None] = {}

        def cost(x: str) -> float | None:
            if x not in memo:
                total = 0.0
                for w, summ, attrs, toks in spans:
                    pp = choose_price((self.cat.get(x) or {}).get("pricing") or {}, span_env(summ, attrs, x))
                    if pp.flag:
                        memo[x] = None
                        break
                    total += w * price_tokens(toks, pp.prices)
                else:
                    memo[x] = total
            return memo[x]
        return cost

    def in_scope(self, x: str, m: str, scope: str) -> bool:
        prov, avail = scope.split(":")
        if avail == "enabled" and not (self.cards.get(x) or {}).get("enabled"):
            return False
        if region_of(x) != region_of(m):
            return False
        g, gm = self.garden.get(x), self.garden.get(m)
        if g and gm and not mf.region_compatible(g, gm):
            return False
        return prov == "any_provider" or self.host(x) == self.host(m)

    def candidates(self, m: str, scope: str) -> list[mf.ScoredModel]:
        if not hasattr(self, "_poolable"):
            self._poolable = [x for x in self.garden if self.poolable(x)]
            skipped = sorted(x for x, g in self.garden.items() if g.ranked_on_headline and self.poolable(x, False))
            if skipped:
                self.report["checks"]["caveats"].append(
                    {"check": "no score at default effort", "detail": f"excluded as targets: {', '.join(skipped)}"})
        best: dict[str, mf.ScoredModel] = {}  # one candidate per underlying model: the cheapest route, enabled first
        for x in self._poolable:
            if not self.in_scope(x, m, scope):
                continue
            g, ident = self.garden[x], mf.model_identity(x)
            cur = best.get(ident)
            if cur is None or self._route_rank(g) < self._route_rank(cur):
                best[ident] = g
        return [best[i] for i in best]

    def _route_rank(self, g: mf.ScoredModel) -> tuple:
        return (g.price, not (self.cards.get(g.router_id) or {}).get("enabled"), g.router_id)

    def canonical(self, m: str) -> str:
        e = self.cat.get(m) or {}
        return e.get("offering_of") or m

    def host(self, m: str) -> str:
        e = self.cat.get(m) or {}
        prov = e.get("provider")
        host = (prov.get("id") if isinstance(prov, dict) else prov) or split_model(m)[0]
        return PROVIDER_FAMILY.get(host, host)

    def pool_for(self, m: str, scope: str) -> dict:
        inc = self.garden.get(m)
        if inc is None or inc.free or not self.v2(m).get("intelligence_index") or self.v2(m).get("price") is None:
            return {"pool": None, "reason": "no intelligence index or price", "switch": None, "enable": [],
                    "relaxed": False}
        cands = self.candidates(m, scope)
        cost = self.traffic_cost(m)
        sw = switch_target(inc, cands, cost)
        members, relaxed = frontier_pool(sw or inc, cands, cost)
        assert not has_dominated(members, cost), [x.router_id for x in members]
        ids = [x.router_id for x in members]
        enable = [x for x in ids + ([sw.router_id] if sw else []) if not (self.cards.get(x) or {}).get("enabled")]
        info = {"switch": sw.router_id if sw else None, "enable": sorted(set(enable)), "relaxed": relaxed}
        if len(members) < 2:
            return {"pool": None, "reason": "no frontier model 2x cheaper in scope", **info}
        pool = [PoolModel(x, float(self.v2(x)["intelligence_index"]), float(self.v2(x)["price"])) for x in ids]
        return {"pool": pool, "reason": "two-model pool: no third tier 2x cheaper" if relaxed else "", **info}

    def next_stronger(self, m: str) -> str | None:
        """The weakest model above this one's index, same host and region, enabled or not."""
        mine = self.v2(m).get("intelligence_index") or 0
        cands = [(self.v2(x.router_id)["intelligence_index"], x.router_id)
                 for x in self.candidates(m, HEADLINE_SCOPE) if self.v2(x.router_id)["intelligence_index"] > mine]
        return min(cands)[1] if cands else None

    # ---- sampling

    def spend_by(self, dim: str) -> list[dict]:
        rows = self.tq([{"metric": "genai.cost", "op": "sum"}], [dim])
        return sorted([{"key": r["group"].get(dim) or "(not set)", "spend": r["metrics"].get("genai.cost:sum") or 0.0}
                       for r in rows], key=lambda r: -r["spend"])

    def cells(self, model: str) -> list[tuple[str, str, float]]:
        prov, bare = split_model(model)
        filters = [{"field": "model", "op": "eq", "values": [bare]}]
        if prov:
            filters.append({"field": "provider", "op": "eq", "values": [prov]})
        rows = self.tq([{"metric": "genai.requests", "op": "count"}], ["project"], filters, mode="timeseries",
                       grain="hour")
        return [(r["group"].get("project") or "", r["timestamp"], float(r["metrics"].get("genai.requests:count") or 0))
                for r in rows if r.get("timestamp") and (r["metrics"].get("genai.requests:count") or 0) > 0]

    def search(self, project: str, frm: datetime, to: datetime, filters: list) -> list[dict]:
        a = ["traces", "search", "--from", iso(frm), "--to", iso(to), "--limit", "100", "--filters",
             json.dumps(filters)]
        if project:
            a += ["--project", project]
        return (self.orq(*a) or {}).get("data") or []

    def list_spans(self, trace: str, project: str) -> list[dict]:
        out, tok = [], None
        for _ in range(10):
            a = ["traces", "list-spans", trace, "--limit", "200"] + (["--project", project] if project else [])
            if tok:
                a += ["--page-token", tok]
            r = self.orq(*a) or {}
            out += r.get("data") or []
            tok = r.get("next_page_token")
            if not r.get("has_more") or not tok:
                break
        return out

    def get_span(self, trace: str, span: str, project: str) -> dict:
        a = ["traces", "get-span", trace, span] + (["--project", project] if project else [])
        return (self.orq(*a) or {}).get("span") or {}

    @staticmethod
    def leaf_llm(spans: list[dict], model: str | None = None) -> list[dict]:
        return [s for s in spans if s.get("type") in LLM_SPAN_TYPES and not s.get("has_children")
                and (model is None or model_key(s.get("provider", ""), s.get("model", "")) == model)]

    def draw_one(self, model: str, cells: list[tuple[str, str, float]], seed: int) -> dict | None:
        rng = random.Random(seed)
        total = sum(c[2] for c in cells)
        for _attempt in range(6):
            x, acc, cell = rng.random() * total, 0.0, cells[-1]
            for c in cells:
                acc += c[2]
                if acc >= x:
                    cell = c
                    break
            project, ts, _ = cell
            hour = parse_ts(ts)
            traces: list[dict] = []
            for width in (1, 5, 15, 60):
                start = hour + timedelta(minutes=rng.randrange(0, 61 - width))
                traces = self.search(project, start, start + timedelta(minutes=width),
                                     [{"field": "model", "op": "eq", "values": [model]}])
                if traces:
                    break
            if not traces:
                continue
            t = traces[rng.randrange(len(traces))]
            tid = t.get("trace_id") or t.get("id")
            spans = self.list_spans(tid, project)
            leaves = self.leaf_llm(spans, model)
            if not leaves:
                continue
            s = leaves[rng.randrange(len(leaves))]
            full = self.get_span(tid, s["span_id"], project)
            return {"model": model, "project": project, "cell": ts, "trace_id": tid, "span_id": s["span_id"],
                    "weight": float(len(leaves)), "span": full, "trace_spans": spans}
        return None

    # ---- decisions

    def decide(self, text: str) -> dict:
        key = hashlib.sha256(text.encode()).hexdigest()
        if key in self.decisions:
            return self.decisions[key]
        body = {"model": DECISION_MODEL, "name": "cost-savings-report", "state": text,
                "timeout": {"call_timeout": 1500}, "retry": {"count": 0}, "questions": QUESTION}
        try:
            r = self.orq("decisions", "create", body=body)
            probs = (((r or {}).get("answers") or {}).get("difficulty") or {}).get("probabilities")
            res = {"ok": True, "probs": probs, "score": score(probs)} if probs else {"ok": False, "error": "error"}
        except OrqError as e:
            kind = "decision_timeout" if re.search(r"timeout|timed out|deadline|HTTP 408|HTTP 504", e.stderr,
                                                   re.I) else "error"
            if re.search(r"HTTP 40[13]", e.stderr):
                raise Stop("decision calls allowed", {"detail": e.stderr.strip()[:300]}) from e
            res = {"ok": False, "error": kind}
        self.decisions[key] = res
        return res

    # ---- main flow

    def run(self) -> dict:
        try:
            self._run()
        except Stop as s:
            self.report["status"] = "stopped"
            self.report["stop"] = {"check": s.check, "numbers": s.numbers}
            self.log(f"stopped: {s.check} {json.dumps(s.numbers)}")
        self.write()
        return self.report

    def _run(self) -> None:
        a = self.args
        self.set_window()
        self.log(f"window {self.frm} .. {self.to} ({self.days} days)")
        self.load_models()
        by_model = self.spend_by("model")
        total = sum(r["spend"] for r in by_model)
        per_month = total * 30 / self.days
        self.report["spend"] = {"total": total, "per_month": per_month, "by_model": by_model,
                                "by_agent": self._safe(lambda: self.spend_by("agent")[:50]),
                                "by_deployment": self._safe(lambda: self.spend_by("deployment")[:50])}
        chat = [(r["key"], r["spend"]) for r in by_model if is_chat_model(r["key"], self.cat)]
        alloc = allocate(chat, a.sample)
        self.log(f"strata: {json.dumps(alloc)}")

        # sample spans (free reads)
        jobs = []
        for m, n in alloc.items():
            cells = self.cells(m)
            if not cells:
                self.report["checks"]["caveats"].append({"check": "no cells", "detail": m})
                continue
            jobs += [(m, cells, self.rng.randrange(2**31)) for _ in range(n)]
        est_s = len(jobs) * 12 / WORKERS
        self.log(f"sampling {len(jobs)} spans, about {est_s / 60:.1f} min")
        with ThreadPoolExecutor(WORKERS) as ex:
            drawn = [d for d in ex.map(lambda j: self._safe(lambda: self.draw_one(*j)), jobs) if d]
        empty = {m for m in alloc if not any(d["model"] == m for d in drawn)}
        for m in empty:
            self.report["checks"]["caveats"].append({"check": "stratum search empty", "detail": m})

        # routing inputs
        for d in drawn:
            d["input"], d["source"] = routing_input(d["span"].get("attributes") or {})
        texts = {d["input"] for d in drawn if d["input"]}
        tokens = sum(len(t) / 4 + DECISION_OVERHEAD_TOKENS for t in texts)
        cost = tokens * DECISION_PRICE_PER_M / 1e6
        self.report["routing_decision_cost"] = {"calls": len(texts), "estimated_usd": cost}
        self.log(f"{len(texts)} decision calls, estimated ${cost:.4f}")
        if not a.yes:
            if not sys.stdin.isatty():
                raise Stop("confirmation", {"detail": "rerun with --yes after the user approves the decision cost",
                                            "calls": len(texts), "estimated_usd": cost})
            if input("continue? [y/N] ").strip().lower() != "y":
                raise Stop("confirmation", {"detail": "declined", "calls": len(texts), "estimated_usd": cost})
        with ThreadPoolExecutor(WORKERS) as ex:
            list(ex.map(self.decide, sorted(texts)))

        self.score_samples(drawn)
        self.pilot_checks(drawn)
        if a.counterfactual_test:
            self.counterfactual(drawn)
            return
        self.estimate(drawn, dict(chat), total)
        self.report["caching"] = self._safe(self.caching) or {"error": "caching scan failed"}
        self.report["harness"] = self._safe(self.harness) or {"error": "harness query failed"}

    def _safe(self, fn):
        try:
            return fn()
        except Stop:
            raise
        except Exception as e:  # noqa: BLE001 - one failed read must not end the run
            self.report["checks"]["caveats"].append({"check": "read failed", "detail": str(e)[:300]})
            return None

    def score_samples(self, drawn: list[dict]) -> None:
        """Price every sample under each scenario. Keys: `router|<scope>|<profile>` and `switch|<scope>`.

        `picks`/`savings` per profile are the headline scope's router, kept flat for the tables."""
        pools: dict[str, dict[str, dict]] = {}
        self.samples = []
        self._traffic: dict[str, list] = {}
        for d in drawn:
            summ, attrs = d["span"].get("summary") or {}, d["span"].get("attributes") or {}
            self._traffic.setdefault(d["model"], []).append((d["weight"], summ, attrs, span_tokens(summ, attrs)))
        for d in drawn:
            m = d["model"]
            if m not in pools:
                pools[m] = {sc: self.pool_for(m, sc) for sc in SCOPES}
            summ, attrs = d["span"].get("summary") or {}, d["span"].get("attributes") or {}
            env = span_env(summ, attrs, m)
            toks = span_tokens(summ, attrs)
            cur = choose_price((self.cat.get(m) or {}).get("pricing") or {}, env)
            cur_price = price_tokens(toks, cur.prices)
            billed = float(((summ.get("cost") or {}).get("total") or 0)) - float(
                ((summ.get("cost") or {}).get("web_search") or 0))
            row = {"trace_id": d["trace_id"], "span_id": d["span_id"], "model": m, "project": d["project"],
                   "weight": d["weight"], "cur_price": cur_price, "billed": billed, "source": d["source"],
                   "ask_chars": len(d["input"] or ""), "prompt_tokens": toks.prompt, "flags": [],
                   "picks": {}, "savings": {p: 0.0 for p in PROFILES}, "tier": {}, "fallback": None,
                   "ask_preview": (d["input"] or "")[:200],
                   "scenario_savings": {}, "unpriced": [], "_tokens": toks, "_env": env}
            if cur.flag:
                row["flags"].append(cur.flag)

            ctx = (row, summ, attrs, toks, cur_price)
            for sc in SCOPES:
                sw = pools[m][sc]["switch"]
                key = f"switch|{sc}"
                row["scenario_savings"][key] = self._reprice(ctx, sw, key) if sw else 0.0
            if has_auto_router(attrs):
                row["fallback"] = "already_routed"
            elif d["input"] is None:
                row["fallback"] = d["source"]
            else:
                dec = self.decide(d["input"])
                if not dec["ok"]:
                    row["fallback"] = dec["error"]
                else:
                    row["score"] = dec["score"]
                    row["tier"] = {p: tier_of(dec["score"], p) for p in PROFILES}
            for sc in SCOPES:
                pool = pools[m][sc]["pool"]
                for p in PROFILES:
                    key = f"router|{sc}|{p}"
                    if row["fallback"] == "already_routed":
                        row["scenario_savings"][key] = 0.0
                        continue
                    if not pool:  # no pool to route over: the router serves the top seat, which is the switch
                        row["scenario_savings"][key] = row["scenario_savings"][f"switch|{sc}"]
                        if sc == HEADLINE_SCOPE:
                            row["picks"][p] = pools[m][sc]["switch"] or m
                        continue
                    # an unscored turn goes to the router's default model, the top seat
                    choice = pool[0].id if row["fallback"] is not None else pick(pool, row["tier"][p], p).id
                    row["scenario_savings"][key] = 0.0
                    if sc == HEADLINE_SCOPE:
                        row["picks"][p] = choice
                    if choice != m:
                        row["scenario_savings"][key] = self._reprice(ctx, choice, key)
            row["savings"] = {p: row["scenario_savings"][f"router|{HEADLINE_SCOPE}|{p}"] for p in PROFILES}
            head = pools[m][HEADLINE_SCOPE]
            row["pool"] = [x.id for x in head["pool"]] if head["pool"] else None
            row["pool_reason"] = head["reason"]
            row["flags"] = sorted(set(row["flags"]))
            self.samples.append(row)
        self.pools = pools

    def _reprice(self, ctx: tuple, target: str, key: str) -> float:
        """Saving from serving this span on `target` with the same input, cached and output tokens; a missing tier
        price marks the scenario unpriced."""
        row, summ, attrs, toks, cur_price = ctx
        pp = choose_price((self.cat.get(target) or {}).get("pricing") or {}, span_env(summ, attrs, target))
        if pp.flag:
            row["flags"].append(f"{target}: {pp.flag}")
            row["unpriced"].append(key)
        return cur_price - price_tokens(toks, pp.prices)

    def pilot_checks(self, drawn: list[dict]) -> None:
        rows = self.samples
        n = len(rows)
        if n == 0:
            raise Stop("sample not empty", {"drawn": 0})
        scored = [r for r in rows if r["tier"]]
        fb = sum(1 for r in rows if r["fallback"] in ("decision_timeout", "error", "empty_input", "prompt_limit"))
        err = sum(1 for r in rows if r["fallback"] == "error")
        sysfb = sum(1 for r in rows if r["source"] in ("system", "instructions"))
        leaked = sum(1 for d in drawn if d["input"] and "<system-reminder>" in d["input"])
        tiers = {r["tier"]["balanced"] for r in scored}
        checks = [
            ("tier mix is not a single tier", len(tiers) > 1 or len(scored) < 2, {"tiers": sorted(tiers)}),
            ("fallbacks below 10%", fb / n < 0.10, {"fallbacks": fb, "n": n}),
            ("errors other than timeouts below 2%", err / n < 0.02, {"errors": err, "n": n}),
            ("no system-reminder text in routing inputs", leaked == 0, {"inputs": leaked}),
            ("system-prompt fallback below 10%", sysfb / n < 0.10, {"system_fallback": sysfb, "n": n}),
        ]
        for name, ok, nums in checks:
            self.report["checks"]["stopping"].append({"check": name, "pass": ok, "numbers": nums})
        self.report["samples"] = [{k: v for k, v in r.items() if k != "ask_preview" and not k.startswith("_")}
                                  for r in rows]
        failed = [c for c in self.report["checks"]["stopping"] if not c["pass"]]
        if failed and not self.args.counterfactual_test:
            raise Stop(failed[0]["check"], failed[0]["numbers"])

    def counterfactual(self, drawn: list[dict]) -> None:
        per: dict[str, dict] = defaultdict(lambda: {"n": 0, "differs": 0, "cheaper": 0, "pricier": 0})
        asks = []
        for r in self.samples:
            rec = r["picks"].get("balanced") or r["model"]
            differs = rec != r["model"]
            s = per[r["model"]]
            s["n"] += 1
            if differs:
                s["differs"] += 1
                cheaper = (self.v2(rec).get("price") or 0) < (self.v2(r["model"]).get("price") or 0)
                s["cheaper" if cheaper else "pricier"] += 1
            asks.append({"span_id": r["span_id"], "used": r["model"], "recommended": rec, "differs": differs,
                         "tier": r["tier"].get("balanced"), "fallback": r["fallback"], "pool_reason": r["pool_reason"]})
        self.report["counterfactual_test"] = {"asks": asks, "per_model": dict(per)}

    def estimate(self, drawn: list[dict], spend: dict[str, float], total: float) -> None:
        by: dict[str, list[Draw]] = defaultdict(list)
        own: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        for r in self.samples:
            by[r["model"]].append(Draw(r["model"], r["weight"], r["cur_price"], dict(r["scenario_savings"])))
            own[r["model"]][0] += r["cur_price"]
            own[r["model"]][1] += r["billed"]
        strata, priced_ok, not_covered = [], {}, []
        for m, ds in by.items():
            priced, billed = own[m]
            gap = abs(priced - billed) / billed if billed else None
            reason = ""
            if gap is None or gap > PRICING_GAP:
                reason = f"pricing check off by {gap:.1%}" if gap is not None else "no billed cost"
                self.report["checks"]["caveats"].append({"check": "pricing check", "detail": f"{m}: {reason}"})
                not_covered.append({"model": m, "spend": spend.get(m, 0.0), "reason": reason})
            else:
                priced_ok[m] = ds
            head = self.pools.get(m, {}).get(HEADLINE_SCOPE) or {}
            strata.append({"model": m, "spend": spend.get(m, 0.0), "n": len(ds), "clusters": len(ds),
                           "pool": [p.id for p in head.get("pool") or []] or None,
                           "pool_reason": head.get("reason", ""), "pricing_gap": gap, "covered": not reason})
        for row in self.report["spend"]["by_model"]:
            if row["key"] not in by:
                why = "not sampled" if row["key"] in spend else "not a chat model (decision, embedding or media)"
                not_covered.append({"model": row["key"], "spend": row["spend"], "reason": why})
        k = 30 / self.days

        def scenario(key: str) -> dict:
            unpriced = {r["model"] for r in self.samples if key in r["unpriced"]}
            cov = {m: ds for m, ds in priced_ok.items() if m not in unpriced}
            cov_spend = {m: spend.get(m, 0.0) for m in cov}
            e = estimate(cov, cov_spend, key, random.Random(self.args.seed))
            return {"savings": e["savings"], "ci90": e["ci90"], "per_month": e["savings"] * k,
                    "ci90_per_month": [x * k for x in e["ci90"]], "without_top3": without_top(cov, cov_spend, key),
                    "coverage": (sum(cov_spend.values()) / total) if total else 0.0,
                    "per_stratum": e["per_stratum"], "unpriced_models": sorted(unpriced)}

        scen = {key: scenario(key) for key in
                [f"router|{sc}|{p}" for sc in SCOPES for p in PROFILES] + [f"switch|{sc}" for sc in SCOPES]}
        head = scen[f"router|{HEADLINE_SCOPE}|balanced"]
        for s in strata:
            s["covered"] = s["covered"] and s["model"] not in head["unpriced_models"]
            s["savings"] = head["per_stratum"].get(s["model"]) if s["covered"] else None
            s["rate"] = stratum_rate(by[s["model"]], f"router|{HEADLINE_SCOPE}|balanced")
        for m in head["unpriced_models"]:
            if m in priced_ok:
                not_covered.append({"model": m, "spend": spend.get(m, 0.0),
                                    "reason": "span tier has no price on a pool model"})
        self.report["scenarios"] = [
            {"scope": sc, "provider": sc.split(":")[0], "availability": sc.split(":")[1],
             "headline": sc == HEADLINE_SCOPE,
             "router": {p: dict(scen[f"router|{sc}|{p}"]) for p in PROFILES},
             "switch": dict(scen[f"switch|{sc}"]),
             "models": [{"model": m, **{kk: (([x.id for x in v] if v else None) if kk == "pool" else v)
                                        for kk, v in self.pools[m][sc].items()}} for m in by]}
            for sc in SCOPES]
        self.report["routing"] = {
            "headline": {kk: head[kk] for kk in ("savings", "ci90", "without_top3", "coverage", "per_month",
                                                 "ci90_per_month")},
            "scope": HEADLINE_SCOPE,
            "profiles": {p: {"savings": scen[f"router|{HEADLINE_SCOPE}|{p}"]["savings"],
                             "ci90": scen[f"router|{HEADLINE_SCOPE}|{p}"]["ci90"]} for p in PROFILES},
            "strata": strata, "not_covered": sorted(not_covered, key=lambda r: -r["spend"]),
            "buckets": self.buckets(), "fallbacks": self._count("fallback"),
            "moves": self.moves(),
            "underpowered": self.underpowered(), "pareto": self.pareto([s["model"] for s in strata]),
        }

    def moves(self) -> list[dict]:
        """Per (used model, balanced pick): spans, how many cost more, weighted savings."""
        out: dict[tuple, dict] = {}
        for r in self.samples:
            pick = r["picks"].get("balanced") or r["model"]
            row = out.setdefault((r["model"], pick), {"used": r["model"], "pick": pick, "tier": set(), "n": 0,
                                                      "fallback": 0, "costs_more": 0, "weight": 0.0,
                                                      "weighted_savings": 0.0})
            row["n"] += 1
            row["weight"] += r["weight"]
            row["fallback"] += r["fallback"] is not None
            row["costs_more"] += r["savings"]["balanced"] < 0
            row["weighted_savings"] += r["weight"] * r["savings"]["balanced"]
            row["tier"].add(r["tier"].get("balanced") or f"fallback: {r['fallback']}")
        rows = [dict(v, tier=", ".join(sorted(v["tier"]))) for v in out.values()]
        return sorted(rows, key=lambda v: (v["used"], -v["n"]))

    def _count(self, key: str) -> dict:
        out: dict[str, int] = defaultdict(int)
        for r in self.samples:
            out[str(r[key])] += 1
        return dict(out)

    def buckets(self) -> dict:
        out: dict[str, dict] = {"ask_tokens": defaultdict(lambda: {"n": 0, "weighted_savings": 0.0}),
                                "prompt_tokens": defaultdict(lambda: {"n": 0, "weighted_savings": 0.0})}
        for r in self.samples:
            for name, val, edges in (("ask_tokens", r["ask_chars"] / 4, ASK_BUCKETS),
                                     ("prompt_tokens", r["prompt_tokens"], PROMPT_BUCKETS)):
                b = out[name][bucket(val, edges)]
                b["n"] += 1
                b["weighted_savings"] += r["weight"] * r["savings"]["balanced"]
        return {k: dict(v) for k, v in out.items()}

    def underpowered(self) -> list[dict]:
        """Models where at least half the scored asks are hard: what moving those asks one model up would cost."""
        out = []
        for m in self.pools:
            every = [r for r in self.samples if r["model"] == m]
            rows = [r for r in every if r["tier"]]
            hard = [r for r in rows if r["tier"]["balanced"] == "hard"]
            if not rows or len(hard) / len(rows) < 0.5:
                continue
            nxt = self.next_stronger(m)
            extra = None
            den = sum(r["weight"] * r["cur_price"] for r in rows)
            if nxt and hard and den:
                pr = (self.cat.get(nxt) or {}).get("pricing") or {}
                num = sum(r["weight"] * (price_tokens(r["_tokens"], choose_price(pr, r["_env"]).prices)
                                         - r["cur_price"]) for r in hard)
                extra = num / den
            ex = [] if self.args.redact else [r["ask_preview"] for r in hard[:3]]
            out.append({"model": m, "n": len(every), "n_scored": len(rows), "share_hard": len(hard) / len(rows), "next_model": nxt,
                        "next_enabled": bool((self.cards.get(nxt) or {}).get("enabled")) if nxt else None,
                        "extra_cost_rate": extra, "examples": ex})
        return out

    def pareto(self, models: list[str]) -> list[dict]:
        """Per used model: which models dominate it, enabled first, from any provider in the same region."""
        out = []
        for m in models:
            inc = self.garden.get(m)
            if inc is None or inc.free:
                out.append({"model": m, "dominated_by": None, "note": "no index or price"})
                continue
            cost = self.traffic_cost(m)
            own = cost(m) if cost else None
            if own is None:
                dom = [x for x in self.candidates(m, "any_provider:all") if x.router_id != m and x.dominates(inc)]
            else:  # on this model's sampled traffic, cache prices included
                dom = [x for x in self.candidates(m, "any_provider:all") if x.router_id != m
                       and (c := cost(x.router_id)) is not None and x.default_index >= inc.default_index
                       and c <= own and (x.default_index > inc.default_index or c < own)]
            dom.sort(key=lambda x: (not (self.cards.get(x.router_id) or {}).get("enabled"), -x.default_index))
            out.append({"model": m, "index": inc.default_index, "blend_price": inc.price,
                        "dominated_by": [{"model": x.router_id, "index": x.default_index, "blend_price": x.price,
                                          "enabled": bool((self.cards.get(x.router_id) or {}).get("enabled"))}
                                         for x in dom[:5]], "note": ""})
        return out

    # ---- caching

    def caching(self) -> dict:
        cost_rows = self.tq([{"metric": "genai.cost", "op": "sum"}], ["thread", "project"])
        req_rows = self.tq([{"metric": "genai.requests", "op": "count"}], ["thread", "project"])
        reqs = {(r["group"].get("thread"), r["group"].get("project")): r["metrics"].get("genai.requests:count") or 0
                for r in req_rows}
        threads = [(r["group"].get("thread"), r["group"].get("project"), r["metrics"].get("genai.cost:sum") or 0)
                   for r in cost_rows if r["group"].get("thread")]
        threads = [t for t in threads if reqs.get((t[0], t[1]), 0) >= 3]
        threads.sort(key=lambda t: -t[2])
        threads = threads[: self.args.cache_threads]
        self.log(f"caching: scanning {len(threads)} threads")
        with ThreadPoolExecutor(WORKERS) as ex:
            results = list(ex.map(lambda t: self._safe(lambda: self.scan_thread(*t)), threads))
        cands = [c for res in results if res for c in res]
        found = sum(c["savings"] for c in cands if c["candidate"])
        return {"threads_scanned": len(threads), "label": "found in scanned threads, not scaled to the window",
                "savings_found": found,
                "savings_orq": sum(c["savings"] for c in cands if c["candidate"] and c["product"] in
                                   ("agents", "deployments")),
                "rows": sorted(cands, key=lambda c: -c["savings"])[:50]}

    def scan_thread(self, thread: str, project: str, spend: float) -> list[dict]:
        traces = self.search(project, parse_ts(self.frm), parse_ts(self.to),
                             [{"field": "thread_id", "op": "eq", "values": [thread]}])[:30]
        by_model: dict[str, list[dict]] = defaultdict(list)
        meta: dict[str, Any] = {"agent": None, "product": None}
        for t in traces:
            tid = t.get("trace_id") or t.get("id")
            ag = t.get("agent")
            meta["agent"] = meta["agent"] or (ag.get("key") or ag.get("id") if isinstance(ag, dict) else ag)
            meta["product"] = meta["product"] or (((t.get("attributes") or {}).get("orq") or {}).get("product"))
            for s in self.leaf_llm(self.list_spans(tid, project)):
                u = s.get("usage") or {}
                by_model[model_key(s.get("provider", ""), s.get("model", ""))].append(
                    {"t": parse_ts(s["started_at"]).timestamp(), "prompt": int(u.get("prompt_tokens") or 0),
                     "cached": int(u.get("prompt_cached_tokens") or 0),
                     "written": int(u.get("prompt_cache_creation_tokens") or 0)})
        out = []
        for m, turns in by_model.items():
            turns.sort(key=lambda x: x["t"])
            prices = choose_price((self.cat.get(m) or {}).get("pricing") or {}, {}).prices
            ok, why = caching_candidate(turns, prices)
            ttl = min([_ttl_seconds(k.removeprefix("cache_write_")) for k in prices if k.startswith("cache_write_")]
                      or [300.0])
            res = caching_savings(turns, prices, ttl) if ok else {"savings": 0.0}
            prov = developer_of(self.canonical(m))
            action = {"anthropic": "set cache_control breakpoints", "openai": "keep the prompt prefix stable",
                      "google": "reads-only reporting; no figure"}.get(prov, "check provider caching")
            out.append({"thread": thread, "project": project, "model": m, "turns": len(turns), "candidate": ok,
                        "reason": why, "savings": res["savings"] if ok and prov != "google" else 0.0,
                        "upper_bound": prov == "openai", "action": action, "thread_spend": spend, **meta})
        return out

    def harness(self) -> dict:
        try:
            rows = self.tq([{"metric": "orq.gen_ai.cost", "op": "sum"}], ["trace_framework"])
        except OrqError as e:
            return {"metric": "orq.gen_ai.cost", "rows": [], "error": str(e)[:200]}
        rows = [{"framework": r["group"].get("trace_framework") or "(not set)",
                 "spend": r["metrics"].get("orq.gen_ai.cost:sum") or 0.0} for r in rows]
        tot = sum(r["spend"] for r in rows)
        for r in rows:
            r["share"] = r["spend"] / tot if tot else None
            r["coding_harness"] = r["framework"] in CODING_HARNESSES
        gap = self.report["spend"]["total"] - tot
        if gap < 0:  # the metrics can disagree upward; a negative difference means nothing
            self.report["checks"]["caveats"].append(
                {"check": "harness metric exceeds spend", "detail": f"orq.gen_ai.cost {tot:.2f} > genai.cost "
                                                                    f"{self.report['spend']['total']:.2f}; shares only"})
        return {"metric": "orq.gen_ai.cost", "rows": sorted(rows, key=lambda r: -r["spend"]),
                "unattributed": gap if gap >= 0 else None}

    # ---- output

    def write(self) -> None:
        (self.out / "report.json").write_text(json.dumps(self.report, indent=1, default=str))
        slots = "\n\n".join(f"## {s}\n\n<!-- {h} -->\n" for s, h in SLOTS.items())
        (self.out / "narrative-slots.md").write_text(
            "# Narrative slots\n\nCopy this file to narrative.md and replace each comment with prose. Quote numbers "
            "only from report.json; lead with percent of spend, then dollars per month; no statistics in prose "
            "(the page shows uncertainty in its appendix table).\n\n" + slots)
        self.log(f"wrote {self.out / 'report.json'}")


def _neg(s: str) -> str:
    """Sort key that orders ISO timestamps newest first inside an ascending sort."""
    return "".join(chr(0x10FFFF - ord(c)) for c in s)


SLOTS = {
    "summary": "one line, at most 12 words, no model names: the recommended lever (the larger saving, Smart Router "
               "on ties) with its percent of spend and monthly dollars (for example `Save 38% ($150/mo) with Smart "
               "Router.`); the spend bar shows how much is possible today",
    "routing": "why each model moves where it does: one line per model, plain words, no 'dominated', no raw index "
               "numbers; no action list, the page renders the per-model plan; monthly figures",
    "caching": "which threads or agents miss caching and the action per provider",
    "caveats": "sample size, not-covered spend, failed caveat checks, fallback choice",
}


# --------------------------------------------------------------------------------------------- render


def parse_narrative(text: str) -> dict[str, str]:
    out, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^##\s+(\w+)\s*$", line)
        if m:
            cur = m.group(1).lower()
            out[cur] = ""
            continue
        if cur:
            out[cur] += line + "\n"
    return {k: re.sub(r"<!--.*?-->", "", v, flags=re.S).strip() for k, v in out.items()}


def _md(text: str) -> str:
    blocks, html_out = re.split(r"\n\s*\n", text.strip()), []
    for b in blocks:
        lines = b.splitlines()
        if all(re.match(r"^\s*[-*]\s+", ln) for ln in lines):
            html_out.append("<ul>" + "".join(f"<li>{html.escape(re.sub(r'^\s*[-*]\s+', '', ln))}</li>"
                                              for ln in lines) + "</ul>")
        else:
            html_out.append(f"<p>{html.escape(' '.join(lines))}</p>")
    return "\n".join(html_out)


def _usd(x: Any) -> str:
    if x is None:
        return "n/a"
    if x != 0 and abs(x) < 0.01:
        return "<$0.01" if x > 0 else "-<$0.01"
    return f"${x:,.0f}" if abs(x) >= 1000 else f"${x:,.2f}"


def _usd0(x: Any) -> str:
    return "n/a" if x is None else f"${x:,.0f}"


def _pct(x: Any) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def _pc(x: Any) -> str:
    """A share as a whole percent; '<1%' for a positive share that rounds to zero."""
    if x is None:
        return "n/a"
    return "<1%" if 0 < x < 0.005 else f"{x:.0%}"


def _of(x: Any, base: Any) -> float | None:
    return x / base if x is not None and base else None


def _change(x: Any) -> str:
    """A cost change as a percent, or as a multiple once it passes +1000%."""
    if x is None:
        return "n/a"
    return f"{1 + x:.0f}x the cost" if x > 10 else f"{x:+.0%}"


def _day(iso: str) -> str:
    try:
        d = parse_ts(iso)
    except ValueError:
        return iso
    return f"{d.day} {d:%b %Y}"


def _scrub(text: str) -> str:
    """Drop absolute home paths from an example ask before it is printed."""
    return re.sub(r"(/Users|/home|C:\\Users)/[^\s'\"<>]+", "<path>", text)


def _txt(x: Any) -> str:
    if x is None or x == "":
        return "none"
    if isinstance(x, bool):
        return "Yes" if x else "No"
    return str(x)


def _short(model: str) -> str:
    return model.split("/", 1)[-1]


def _table(rows: list[dict], cols: list[tuple[str, str, Any]], caption: str = "") -> str:
    if not rows:
        return "<p class=m>Nothing to show.</p>"
    cap = f"<caption>{html.escape(caption)}</caption>" if caption else ""
    num = [" class=num" if fmt in NUMERIC else "" for _, _, fmt in cols]
    head = "".join(f"<th scope=col{c}>{html.escape(h)}</th>" for (h, _, _), c in zip(cols, num, strict=True))
    body = "".join("<tr>" + "".join(f"<td{c}>{html.escape(str(fmt(r.get(k))))}</td>"
                                    for (_, k, fmt), c in zip(cols, num, strict=True)) + "</tr>" for r in rows)
    label = html.escape(caption or cols[0][0])
    return (f"<div class=t role=region tabindex=0 aria-label='{label}'><table>{cap}<thead><tr>{head}</tr></thead>"
            f"<tbody>{body}</tbody></table></div>")


THEME_BOOT = ("try{var t=localStorage.getItem('report-theme');"
              "if(t==='light'||t==='dark')document.documentElement.setAttribute('data-theme',t)}catch(e){}"
              "document.documentElement.classList.add('js')")

_SVG = ("<svg class={c} viewBox='0 0 24 24' width=16 height=16 fill=none stroke=currentColor stroke-width=2 "
        "stroke-linecap=round stroke-linejoin=round aria-hidden=true>{d}</svg>")
SUN = _SVG.format(c="sun", d="<circle cx=12 cy=12 r=4></circle><path d='M12 2v2M12 20v2M4.93 4.93l1.41 1.41"
                  "M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41'></path>")
MOON = _SVG.format(c="moon", d="<path d='M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z'></path>")

PAGE_JS = """(function(){var d=document.documentElement;
var mq=window.matchMedia?matchMedia('(prefers-color-scheme: dark)'):null,btn=document.querySelector('.theme');
function isDark(){var t=d.getAttribute('data-theme');return t?t==='dark':!!(mq&&mq.matches)}
function label(){if(btn)btn.setAttribute('aria-label',isDark()?'Switch to light theme':'Switch to dark theme');
var b=document.body;if(b){b.classList.remove('auto','light','dark');b.classList.add(d.getAttribute('data-theme')||'auto')}}
label();if(mq&&mq.addEventListener)mq.addEventListener('change',label);
if(btn)btn.addEventListener('click',function(){var t=isDark()?'light':'dark';d.setAttribute('data-theme',t);
try{localStorage.setItem('report-theme',t)}catch(e){}label()});
})();"""

CSS = """
:root{--bg:#ffffff;--fg:#141319;--fg2:#3c3a48;--muted:#737373;--panel:#fafafa;--strip:#f7f6f5;--card:#ffffff;
--line:#e6e6e6;--shadow:0 1px 2px 0 #14131908;--s1:#299D8F;--s2:#df5325;--kept:#dcdad6;--ns:#ebeae8;--hl:#eef7f5;
--hl2:#7acbc1;--pill:#ffffff;--rec-fg:#1d7a6e;--rec-bg:#e3f3f0;color-scheme:light}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){--bg:#1f1e1d;--fg:#faf9f5;--fg2:#c2c0b6;
--muted:#9c9a92;--panel:#262624;--strip:#1f1e1d;--card:#30302e;--line:#42413d;--shadow:0 1px 2px 0 #00000040;
--s1:#299D8F;--s2:#df5325;--kept:#55544f;--ns:#3a3935;--hl:#2b3a38;--hl2:#7ccdc3;--pill:#42413d;--rec-fg:#7ccdc3;--rec-bg:#2b3a38;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#1f1e1d;--fg:#faf9f5;--fg2:#c2c0b6;--muted:#9c9a92;--panel:#262624;--strip:#1f1e1d;
--card:#30302e;--line:#42413d;--shadow:0 1px 2px 0 #00000040;--s1:#299D8F;--s2:#df5325;--kept:#55544f;--ns:#3a3935;
--hl:#2b3a38;--hl2:#7ccdc3;--pill:#42413d;--rec-fg:#7ccdc3;--rec-bg:#2b3a38;color-scheme:dark}
*{box-sizing:border-box}
body{font:14px/1.5 "Avio Sans",Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
background:var(--bg);color:var(--fg);margin:0;padding-block:28px 56px;
padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
main{max-width:1000px;margin:0 auto}
h1,h2,h3{text-wrap:balance;line-height:1.25;color:var(--fg);font-weight:500}
h1{font-size:24px;margin:0 0 4px;letter-spacing:-0.01em}
h2{font-size:13px;color:var(--muted);margin:0;padding:10px 16px;scroll-margin-top:16px}
h3{font-size:14px;margin:20px 0 6px}
p,li,dd{max-width:68ch;text-wrap:pretty}
.m{color:var(--muted)}
.top{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}.top>div{min-width:0}
.toc{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 16px;margin:20px 0 4px;font-size:13px}
.toc span{color:var(--muted)}
.toc ul{display:flex;flex-wrap:wrap;gap:4px 16px;margin:0;padding:0;list-style:none}
.toc li{max-width:none}
.toc a{color:var(--fg);text-decoration:underline;text-decoration-color:var(--line);text-underline-offset:3px;
transition:text-decoration-color .15s ease-out}
.toc a:hover{text-decoration-color:currentColor}
.toc a:focus-visible{outline:2px solid var(--s1);outline-offset:2px;border-radius:2px}
.theme{display:none;place-items:center;width:32px;height:32px;padding:0;border:1px solid var(--line);border-radius:8px;
background:var(--strip);color:var(--muted);cursor:pointer;transition:color .15s ease-out,background-color .15s ease-out}
.js .theme{display:inline-grid}.theme:hover{color:var(--fg);background:var(--pill)}
.theme:focus-visible{outline:2px solid var(--s1);outline-offset:1px}
.theme .sun{display:none}
:root[data-theme="dark"] .theme .sun{display:block}:root[data-theme="dark"] .theme .moon{display:none}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]) .theme .sun{display:block}
:root:not([data-theme="light"]) .theme .moon{display:none}}
@media (prefers-reduced-motion:reduce){.toc a,.theme{transition:none}}
.card{background:var(--strip);border:1px solid var(--line);border-radius:8px;margin:12px 0;overflow:hidden}
.card>.cb{background:var(--card);border:1px solid var(--line);border-radius:6px;box-shadow:var(--shadow);
margin:0 6px 6px;padding:var(--pad);min-width:0;--pad:16px}
.card>.cb>:first-child{margin-top:0}.card>.cb>:last-child{margin-bottom:0}
.hero{margin:24px 0 0}
.hero .verdict{margin:0 0 12px;font-size:17px;font-weight:500;color:var(--fg);max-width:60ch}
.kpis{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.kpis .card{margin:8px 0 0}
.kh{display:flex;flex-wrap:wrap;align-items:center;gap:4px 8px;padding:10px 16px;font-size:13px;font-weight:500;color:var(--muted)}
.kn{white-space:nowrap}
.rec{margin-left:auto;font-size:11px;font-weight:500;line-height:1.4;padding:1px 6px;border-radius:4px;
color:var(--rec-fg);background:var(--rec-bg)}
.kh i,.dot{width:8px;height:8px;border-radius:50%;display:inline-block;flex:none}
.big{font-size:36px;line-height:1.1;font-weight:500;margin:0;letter-spacing:-0.02em;color:var(--fg)}
.big small{font-size:14px;font-weight:400;color:var(--muted);margin-left:6px;letter-spacing:0}
.kpis .note{margin:8px 0 0;font-size:13px;color:var(--muted)}
.hero .gap{margin:8px 0 24px;font-size:13px;max-width:none}
.bar{display:flex;gap:3px;height:10px}
.bar i{display:block;height:100%;border-radius:5px;min-width:3px}
.bar .b1{background:var(--s1)}.bar .b2{background:var(--hl2)}.bar .b3{background:var(--ns)}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;margin:12px 0 0;font-size:13px;color:var(--fg2);padding:0;
list-style:none}
.legend li{display:flex;align-items:center;gap:6px}
.sw{display:inline-block;width:8px;height:8px;border-radius:50%}
.sr{background:var(--s1)}.sr2{background:var(--hl2)}.kept{background:var(--kept)}
.r{background:var(--s1)}.s{background:var(--s2)}
.caveat{margin:12px 0 0;font-size:13px}
.chart{margin:12px 0 0}
.row{display:grid;grid-template-columns:minmax(0,2.2fr) minmax(0,1fr) 7em;gap:6px 16px;align-items:start;
padding:12px 0;border-bottom:1px solid var(--line)}
.row:first-child{padding-top:0}.row:last-child{border-bottom:0;padding-bottom:0}
.who{min-width:0}
.who>span{display:block;font-size:13px;color:var(--muted);overflow-wrap:anywhere}
.who>span:first-of-type{margin-top:8px}
.flow{display:grid;grid-template-columns:11em auto minmax(0,1fr);align-items:center;min-width:0;
--lv:var(--fg2);--chip-h:25px}
.chip{display:inline-flex;align-items:center;gap:6px;height:var(--chip-h);border:1px solid var(--line);
background:var(--card);border-radius:6px;padding:0 8px;font-size:13px;font-weight:500;line-height:1.3;
color:var(--fg);white-space:nowrap;box-shadow:var(--shadow)}
.chip.src{display:block;justify-self:start;max-width:100%;overflow:hidden;text-overflow:ellipsis;
line-height:calc(var(--chip-h) - 2px);background:var(--panel);color:var(--fg2)}
.chip.new{border-style:dashed;border-color:var(--s1)}
.chip.same{background:var(--panel);color:var(--fg2)}
.chip em{font-style:normal;font-size:11px;font-weight:500;color:var(--rec-fg)}
.chip.same em{color:var(--muted)}
.link{width:18px;height:2px;background:var(--lv);position:relative}
.flow.switch .link{width:26px;margin-right:6px}
.flow.switch .link::after{content:"";position:absolute;right:-6px;top:-4px;border:5px solid transparent;
border-left:6px solid var(--lv);border-right:0}
.to{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px;min-width:0}
.to li{display:flex;flex-wrap:nowrap;position:relative;min-width:0}
.br{display:flex;flex-wrap:wrap;align-items:center;gap:2px 8px;min-width:0}
.meta{display:inline-flex;align-items:center;gap:6px}
.share{display:inline-block;flex:none;width:32px;height:4px;border-radius:2px;
background:linear-gradient(90deg,var(--fg2) calc(var(--p)*100%),var(--ns) 0)}
.to.fork li{padding-left:16px}
.to.fork li::before{content:"";position:absolute;left:0;top:calc(var(--chip-h)/2 - 1px);width:12px;height:2px;
background:var(--lv)}
.to.fork li::after{content:"";position:absolute;left:0;width:2px;top:-3px;bottom:-3px;background:var(--lv)}
.to.fork li:first-child::after{top:calc(var(--chip-h)/2)}
.to.fork li:last-child::after{bottom:auto;height:calc(var(--chip-h)/2 + 3px)}
.to small{font-size:12px;color:var(--muted)}
.lk{display:inline-block;width:16px;height:2px;vertical-align:middle;margin-right:6px;background:var(--fg2);
position:relative}
.lk.arrow::after{content:"";position:absolute;right:-2px;top:-3px;border:4px solid transparent;
border-left:5px solid var(--fg2);border-right:0}
.lk.fork{width:12px;height:10px;background:none;border:solid var(--fg2);border-width:2px 0 2px 2px}
.lk.dash{width:16px;height:10px;background:none;border:1px dashed var(--s1);border-radius:3px}
.val,.kpis,.cell,.legend,.num,td,th{font-variant-numeric:tabular-nums}
.who span.hard{color:var(--muted);font-weight:400}
.track{display:flex;gap:2px;height:10px;align-items:stretch;margin-top:8px}
.track i{display:block;min-width:3px;border-radius:5px}
.track i.kept{min-width:0}
.foot{margin:8px 0 0;font-size:13px}
.foot+.foot{margin-top:2px}
.val{text-align:right}
.val b{display:block;font-size:16px;font-weight:600}
.val span{font-size:12px;color:var(--muted)}
.grid{display:grid;grid-template-columns:5.5em minmax(0,1fr) minmax(0,1fr);gap:8px;margin:12px 0 8px;
align-items:stretch}
.grid .ch,.grid .rh{font-size:13px;font-weight:500;color:var(--muted);margin:0;align-self:end}
.grid .rh{align-self:center}
.cell{border:1px solid var(--line);border-radius:6px;padding:10px 12px;min-width:0;background:var(--panel)}
.cell.hl{background:var(--hl);border-color:var(--s1)}
.cell p{margin:0}
.cell .tag{font-size:12px;font-weight:500;color:var(--rec-fg)}
.cell .f1{font-size:26px;line-height:1.15;font-weight:500;color:var(--s1)}
.cell .f2{font-size:19px;line-height:1.2;font-weight:500;color:var(--s2);margin-top:8px}
.cell .f1 span,.cell .f2 span{font-size:12px;font-weight:400;color:var(--muted);margin-left:6px}
.cell .ci{font-size:12px;color:var(--muted)}
.cell .en{font-size:12px;color:var(--muted);margin-top:6px}
dl.gl{display:grid;grid-template-columns:max-content 1fr;gap:6px 16px;margin:12px 0}
dl.gl dt{font-weight:500}dl.gl dd{margin:0}
.rows{margin:12px calc(-1*var(--pad)) calc(-1*var(--pad))}
details,summary,table,thead,tbody,tr,th,td,caption,dl,dt,dd{background:none;box-shadow:none}
details,summary,td,li,dt,dd{color:var(--fg)}caption{color:var(--muted)}
details{border:0;border-top:1px solid var(--line);border-radius:0;margin:0;padding:2px 16px}
details:first-child{border-top:0}
details[open]{padding-bottom:12px}
summary{cursor:pointer;padding:10px 0;margin:0;border:0;border-radius:0;font-weight:500;color:var(--fg)}
summary span{font-weight:400;color:var(--muted)}
.t{overflow-x:auto;margin:8px 0 12px}.t td:first-child{white-space:nowrap}
@media print{.theme,.toc{display:none}.card,.row,details,tr{break-inside:avoid}body{padding:0}}
table{border-collapse:collapse;width:100%;font-size:13px}
caption{text-align:left;font-weight:500;padding:4px 0 6px}
td,th{border-bottom:1px solid var(--line);padding:8px 8px;text-align:left;vertical-align:top}
td{overflow-wrap:anywhere;min-width:5em}
td.num,th.num{text-align:right}
th{color:var(--muted);font-weight:400;white-space:nowrap}
tbody tr:last-child td{border-bottom:0}
.ex{border:1px solid var(--line);border-radius:6px;padding:8px 10px;margin:6px 0;font-size:13px;color:var(--muted);
white-space:pre-wrap;overflow-wrap:anywhere;max-height:12em;overflow:auto}
:focus-visible{outline:2px solid var(--s1);outline-offset:2px}
@media (max-width:720px){
.grid{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}
.grid .corner{display:none}
.cell .f1 span,.cell .f2 span{display:block;margin:0}
.grid .rh{grid-column:1/-1;margin-top:6px}
.kpis{gap:8px}
.big{font-size:28px}.big small{display:block;margin:2px 0 0}
.hero .verdict{font-size:16px}
.card>.cb{--pad:12px}
.row{grid-template-columns:minmax(0,1fr) auto;padding:12px 0}
.who{grid-column:1/-1}.track{grid-column:1;grid-row:2}.val{grid-row:2}
.flow{grid-template-columns:9.5em auto minmax(0,1fr);--chip-h:23px}
.chip{font-size:12px;padding:0 6px}.chip em{font-size:10px}.to small{font-size:11px}
dl.gl{grid-template-columns:1fr}dl.gl dd{margin-bottom:8px}
}
"""


SCOPE_LABEL = {"same_provider:all": "Same provider, all available", "same_provider:enabled": "Same provider, enabled",
               "any_provider:all": "Any provider, all available", "any_provider:enabled": "Any provider, enabled"}
TOKENIZER_NOTE = ("Any-provider figures reuse the sampled token counts. Other model families tokenize "
                  "differently, so they carry an error the traces cannot measure.")
EXAMPLE_CHARS = 280


def _ci_cell(s: Any) -> str:
    return str(s)


def _ci(c: Any) -> str:
    return f"{_usd(c[0])} to {_usd(c[1])}" if c else "n/a"


NUMERIC = (_usd, _usd0, _pct, _pc, _ci, _ci_cell, _change)


def _scenario_rows(report: dict) -> list[dict]:
    rows = []
    for sc in report.get("scenarios") or []:
        r, w = sc["router"]["balanced"], sc["switch"]
        label = SCOPE_LABEL.get(sc["scope"], sc["scope"])
        rows.append({"scope": label + (" (headline)" if sc["headline"] else ""), "label": label,
                     "headline": sc["headline"], "router": r.get("per_month"), "router_ci": r.get("ci90_per_month"),
                     "switch": w.get("per_month"), "switch_ci": w.get("ci90_per_month"), "coverage": r["coverage"]})
    rows.sort(key=lambda r: not r["headline"])
    return rows


PROVIDER_NAME = {"openai": "OpenAI", "anthropic": "Anthropic", "google-ai": "Google", "google": "Google",
                 "aws": "AWS", "azure": "Azure", "mistral": "Mistral"}


def _lever(info: dict) -> str:
    """'router' when the headline scope routes this model over a pool, 'switch' (change model) when it moves it
    whole, else ''."""
    if len(info.get("pool") or []) > 1:
        return "router"
    return "switch" if info.get("switch") else ""


def _phrase(info: dict) -> str:
    """The move of one model in one scope, lower case: 'route over A and B', 'change to A' or 'route'."""
    pool, switch = info.get("pool") or [], info.get("switch")
    if len(pool) > 1:
        return "route over " + " and ".join(_short(p) for p in pool)
    return f"change to {_short(switch)}" if switch else "route"


def _action_lines(model: str, head: dict, today: dict | None, saved: float, enable: list[str] | None = None) -> list[str]:
    """Plain row text: what to do with this model, today and after enabling models, as short sentences."""
    if saved <= 0:
        prov = PROVIDER_NAME.get(model.split("/")[0], model.split("/")[0])
        return [f"Keep it: no cheaper {prov} model is as strong"]
    enable = head.get("enable") or [] if enable is None else enable
    if not enable:
        p = _phrase(head)
        return [p[0].upper() + p[1:]]
    names = " and ".join(_short(e) for e in enable)
    later = f"After enabling {names}: {_phrase(head)}."
    if today and _lever(today) and not _same_move(today, head):
        return [f"Today: {_phrase(today)}.", later]
    return [later]


def _headline(report: dict) -> dict:
    return next((sc for sc in report.get("scenarios") or [] if sc.get("headline")), {})


def _enabled_scope(report: dict, head: dict) -> dict | None:
    """The same-provider, enabled-models-only scenario (the headline itself when it already is that)."""
    if head.get("availability") == "enabled":
        return head
    want = f"{head.get('provider', 'same_provider')}:enabled"
    return next((sc for sc in report.get("scenarios") or [] if sc.get("scope") == want), None)


def _same_move(a: dict, b: dict) -> bool:
    return (a.get("pool") or None) == (b.get("pool") or None) and a.get("switch") == b.get("switch")


def _as_switch(info: dict) -> dict:
    """A model's move under the change-model lever: its switch target only, enabling only that target."""
    sw = info.get("switch")
    return {"model": info.get("model"), "pool": None, "switch": sw,
            "enable": [e for e in info.get("enable") or [] if e == sw]}


def _plan_figures(report: dict, per_month: float, lever: str = "router") -> dict:
    """The plan as numbers: the enabled-only router figure (today) and the extra from enabling models.

    Today lists the moves of the enabled scope. A move whose target is the same in the headline scope takes the
    headline stratum saving. If exactly one move differs, it takes the residual; with more, their amounts are
    omitted. Without an enabled scope, step 1 falls back to the headline moves that need nothing enabled.
    """
    ro = report.get("routing") or {}
    head = _headline(report)
    sw = lever == "switch"
    conv = _as_switch if sw else (lambda i: i)
    h_info = {m["model"]: conv(m) for m in head.get("models", [])}
    if sw:
        strata = {m: (v or 0) * per_month for m, v in ((head.get("switch") or {}).get("per_stratum") or {}).items()}
        h_pm = (head.get("switch") or {}).get("per_month") or 0
    else:
        strata = {s["model"]: (s.get("savings") or 0) * per_month for s in ro.get("strata") or []}
        h_pm = (ro.get("headline") or {}).get("per_month")
        if h_pm is None:
            h_pm = head.get("router", {}).get("balanced", {}).get("per_month") or 0
    enabled = _enabled_scope(report, head)

    def est(sc: dict) -> dict:
        return (sc.get("switch") if sw else (sc.get("router") or {}).get("balanced")) or {}

    if enabled is not None and enabled is not head:
        e_pm = est(enabled).get("per_month") or 0
        e_info = {m["model"]: conv(m) for m in enabled.get("models", [])}
    else:
        e_info = {m: i for m, i in h_info.items() if not i.get("enable")}
        e_pm = sum(strata.get(m, 0) for m in e_info) if enabled is None else h_pm
    moves = {m: (i, strata.get(m)) for m, i in e_info.items() if _lever(i)}
    diff = [m for m, (i, _) in moves.items() if not _same_move(i, h_info.get(m, {}))]
    amounts = {m: (v if m not in diff else None) for m, (_, v) in moves.items()}
    if len(diff) == 1:
        amounts[diff[0]] = e_pm - sum(v or 0 for m, v in amounts.items() if m != diff[0])
    exact = est(enabled or {}).get("per_stratum")
    if exact is not None:  # the enabled scope's own per-model saving: no reconciliation needed
        amounts = {m: (exact.get(m) or 0.0) * per_month for m in moves}
    now = [(m, i, amounts[m]) for m, (i, _) in moves.items()]
    now.sort(key=lambda x: -(x[2] if x[2] is not None else -1))
    later = [m for m, i in h_info.items() if i.get("enable") and strata.get(m, 0) > 0]
    enable = list(dict.fromkeys(e for m in later for e in h_info[m]["enable"]))
    return {"now": now, "e_pm": e_pm, "h_pm": h_pm, "later": later, "enable": enable, "h_info": h_info,
            "e_info": e_info, "strata": strata}


def _today_amounts(f: dict) -> dict[str, float]:
    """Monthly saving available today per model: the enabled scope's own per-model saving when the report has it.
    Older reports: amounts the reconciliation could not pin down (several moves differ from the headline) split
    what is left of the enabled-scope total by headline saving."""
    known = {m: v for m, _, v in f["now"] if v is not None}
    unknown = [m for m, _, v in f["now"] if v is None]
    if unknown:
        left = max(0.0, f["e_pm"] - sum(known.values()))
        weights = {m: f["strata"].get(m, 0) for m in unknown}
        tot = sum(weights.values())
        known.update({m: left * (w / tot if tot else 1 / len(unknown)) for m, w in weights.items()})
    return known


def _hard_models(ro: dict) -> dict[str, float]:
    """Models where at least half of the sampled calls are hard asks, with that share."""
    return {u["model"]: u["share_hard"] for u in ro.get("underpowered") or [] if (u.get("share_hard") or 0) >= 0.5}


def _plan_rows(report: dict, per_month: float, lever: str = "router") -> list[dict]:
    """One row per sampled model: monthly saving split into today (enabled models) and after enabling.

    `hard` is the share of hard asks on the one flagged row with the largest saving, else None.
    """
    ro = report.get("routing") or {}
    f = _plan_figures(report, per_month, lever)
    today = _today_amounts(f)
    hard = _hard_models(ro)
    rows = []
    for st in sorted(ro.get("strata") or [], key=lambda x: -f["strata"].get(x["model"], 0)):
        m = st["model"]
        saved = max(0.0, f["strata"].get(m, 0))
        now = min(saved, max(0.0, today.get(m, 0.0)))
        n_calls = sum(mv.get("n") or 0 for mv in ro.get("moves") or [] if mv.get("used") == m)
        spend = (st.get("spend") or 0) * per_month
        fl = _flow(m, f["h_info"].get(m, {}), f["e_info"].get(m), ro.get("moves") or [], now, spend)
        rows.append({"model": m, "n": n_calls, "spend": spend, "saved": saved, "today": now,
                     "after": saved - now, "rate": st.get("rate"), "hard": None,
                     "lines": _action_lines(m, f["h_info"].get(m, {}), f["e_info"].get(m), saved, fl["enable"]),
                     **fl})
    flagged = [r for r in rows if r["model"] in hard and r["saved"] > 0]
    if flagged:
        top = max(flagged, key=lambda r: r["saved"])
        top["hard"] = hard[top["model"]]
    return rows


TIERS = ("easy", "medium", "hard")


def _flow(model: str, head: dict, today: dict | None, moves: list[dict], today_saved: float | None = None,
          spend: float | None = None) -> dict:
    """Where this model's calls go in the headline scope: lever, targets with their share of sampled calls (by
    sample weight, over the drawn targets only), the tiers of their scored calls, unscored calls, models to
    enable (only drawn targets, never the model itself), and what to do before enabling when that saves money."""
    lever = _lever(head)
    targets = (head.get("pool") or []) if lever == "router" else [head["switch"]] if lever == "switch" else []
    counts: dict[str, float] = {}
    unscored: dict[str, int] = {}
    tiers: dict[str, set] = {}
    for mv in moves:
        if mv.get("used") == model:
            w = mv.get("weight")
            counts[mv["pick"]] = counts.get(mv["pick"], 0) + (w if w is not None else (mv.get("n") or 0))
            unscored[mv["pick"]] = unscored.get(mv["pick"], 0) + (mv.get("fallback") or 0)
            tiers.setdefault(mv["pick"], set()).update(t.strip() for t in str(mv.get("tier", "")).split(","))
    drawn = [t for t in targets if counts.get(t)] or targets
    total = sum(counts.get(t, 0) for t in drawn)
    enable = [e for e in head.get("enable") or [] if e in drawn and e != model]
    branches = []
    for t in sorted(drawn, key=lambda t: -counts.get(t, 0)):
        tt = [x for x in TIERS if x in tiers.get(t, set())]
        branches.append({"model": t, "share": counts[t] / total if total and t in counts else None,
                         "tiers": tt if 0 < len(tt) < len(TIERS) else [], "unscored": unscored.get(t, 0),
                         "enable": t in enable, "stays": t == model})
    note = ""
    if enable and today and _lever(today) and not _same_move(today, head) and (today_saved or 0) > 0:
        note = f"Before enabling: {_phrase(today)} now, {_pc(_of(today_saved, spend))} saved ({_usd0(today_saved)}/mo)."
    return {"lever": lever, "branches": branches, "note": note, "enable": enable}


def _flow_html(r: dict) -> str:
    """Source model chip, an arrow, and one chip per target: a fork for the router, a single arrow for a change."""
    src = f"<span class='chip src' title='{html.escape(r['model'])}'>{html.escape(_short(r['model']))}</span>"
    items = []
    for b in r["branches"]:
        cls = "chip" + (" new" if b["enable"] else "") + (" same" if b["stays"] else "")
        meta = []
        if len(r["branches"]) > 1:
            if b["share"] is not None:
                meta.append(("<1%" if 0 < b["share"] < 0.005 else f"{b['share']:.0%}") + " of calls")
            if b["tiers"]:
                meta.append(", ".join(t.capitalize() for t in b["tiers"]))
            if b["unscored"]:
                meta.append(f"+{b['unscored']} ungraded")
        tag = "<em>enable</em>" if b["enable"] else "<em>stays</em>" if b["stays"] else ""
        bar = f"<i class=share style='--p:{b['share']:.2f}'></i>" if meta and b["share"] is not None else ""
        text = html.escape(" \u00b7 ".join(meta))
        small = f"<span class=meta>{bar}<small>{text}</small></span>" if meta else ""
        items.append(f"<li><div class=br><span class='{cls}' title='{html.escape(b['model'])}'>"
                     f"{html.escape(_short(b['model']))}{tag}</span>{small}</div></li>")
    kind = "fork" if len(items) > 1 else "one"
    return (f"<div class='flow {r['lever']}' role=img aria-label='{html.escape(' '.join(r['lines']))}'>{src}"
            f"<span class=link></span><ul class='to {kind}'>{''.join(items)}</ul></div>")


def _ranges_overlap(head: dict) -> bool:
    """Whether the 90% ranges of the two levers' monthly savings overlap (a rough, conservative test: both come
    from the same sampled calls, so a paired comparison would separate them sooner)."""
    r = ((head.get("router") or {}).get("balanced") or {}).get("ci90_per_month")
    w = (head.get("switch") or {}).get("ci90_per_month")
    return bool(r and w) and r[0] <= w[1] and w[0] <= r[1]


def _plan_caption(lever: str, pct: float | None, pm: float | None) -> str:
    name = "change model" if lever == "switch" else "Smart Router"
    return (f"<p class=m>Recommended plan ({name}): one move per model. Together: {_pc(pct)} saved "
            f"({_usd0(pm)}/mo). Bar length is the model's monthly spend.</p>")


def _actions_chart(report: dict, per_month: float, lever: str = "router") -> str:
    ro = report.get("routing") or {}
    rows_in = _plan_rows(report, per_month, lever)
    nc = ro.get("not_covered") or []
    nc_spend = sum(x.get("spend") or 0 for x in nc) * per_month
    drawn = [r for r in rows_in if r["saved"] > 0]
    kept = [r["model"] for r in rows_in if r["saved"] <= 0]
    top = max([r["spend"] for r in drawn] + [1e-9])
    rows = []
    for r in drawn:
        seg = [("sr", r["today"]), ("sr2", r["after"]), ("kept", max(0.0, r["spend"] - r["saved"]))]
        bars = "".join(f"<i class={c} style='width:{v / top * 100:.2f}%'></i>" for c, v in seg
                       if v / top > (0.02 if c == "kept" else 0))
        pct = _of(r["saved"], r["spend"])
        tip = (f"{_short(r['model'])}: {_pc(_of(r['today'], r['spend']))} saved today, "
               f"{_pc(_of(r['after'], r['spend']))} more after enabling, of {_usd0(r['spend'])} a month")
        notes = f"<span>{html.escape(r['note'])}</span>" if r["note"] else ""
        if r["n"] < MIN_SAMPLE:
            notes += f"<span>Small sample: {r['n']} call{'s' if r['n'] != 1 else ''}.</span>"
        if r["hard"] is not None:
            notes += "<span class=hard>Mostly hard work: spot-check answers before moving it.</span>"
        rows.append(f"<div class=row><div class=who>{_flow_html(r)}{notes}</div>"
                    f"<div class=track role=img aria-label='{html.escape(tip)}'>{bars}</div>"
                    f"<div class=val><b>{_pc(pct)} saved</b>"
                    f"<span>{_usd0(r['saved'])} of {_usd0(r['spend'])}/mo</span></div></div>")
    legend = "<ul class=legend><li><span class='sw sr'></span>Saved today</li>"
    if any(r["after"] > 0 for r in drawn):
        legend += "<li><span class='sw sr2'></span>Saved after enabling</li>"
    legend += "<li><span class='sw kept'></span>Still spent</li>"
    levers = {r["lever"] for r in drawn}
    if "switch" in levers:
        legend += "<li><span class='lk arrow'></span>Arrow: move all calls</li>"
    if "router" in levers:
        legend += "<li><span class='lk fork'></span>Fork: Smart Router picks a model per call</li>"
    if any(r["enable"] for r in drawn):
        legend += "<li><span class='lk dash'></span>Dashed: enable this model in your orq.ai workspace first</li>"
    foot = "".join(f"<p class='m foot'>{html.escape(_short(m))}: keep, already the cheapest model that is as strong.</p>"
                   for m in kept)
    if nc:
        foot += f"<p class='m foot'>Not analysed: {_usd0(nc_spend)}/mo of spend on {len(nc)} smaller models.</p>"
    return legend + "</ul><div class=chart>" + "".join(rows) + "</div>" + foot


def _ci_short(c: Any) -> str:
    return f"{_usd0(c[0])}\u2013{_usd0(c[1])}" if c else "n/a"


def _scope_cell(sc: dict | None, approx: bool, coverage_note: str, spend_pm: float | None) -> str:
    if not sc:
        return "<div class=cell><p class=m>n/a</p></div>"
    r, w = sc["router"]["balanced"], sc["switch"]
    enable = list(dict.fromkeys(e for m in sc.get("models") or [] for e in m.get("enable") or []))
    out = ["<div class='cell hl'><p class=tag>This report's figure</p>" if sc.get("headline") else "<div class=cell>",
           f"<p class=f1>{_pc(_of(r.get('per_month'), spend_pm))}<span>Smart Router</span></p>",
           f"<p class=ci>{_usd0(r.get('per_month'))}/mo</p>",
           f"<p class=f2>{_pc(_of(w.get('per_month'), spend_pm))}<span>Change model</span></p>",
           f"<p class=ci>{_usd0(w.get('per_month'))}/mo</p>"]
    if enable:
        names = html.escape(", ".join(enable), quote=True)
        out.append(f"<p class=en title='{names}'>enable {len(enable)} model{'s' if len(enable) != 1 else ''}</p>")
    if approx:
        out.append("<p class=en>approximate</p>")
    if coverage_note:
        out.append(f"<p class=en>{coverage_note}</p>")
    return "".join(out) + "</div>"


def _scope_grid(report: dict, spend_pm: float | None = None) -> str:
    """2x2 grid: provider scope in columns, availability in rows; the headline cell is tinted."""
    by = {sc["scope"]: sc for sc in report.get("scenarios") or []}
    covs = {sc["router"]["balanced"].get("coverage") for sc in by.values()}
    out = ["<div class=grid><span class=corner></span><p class=ch>Same provider</p><p class=ch>Any provider</p>"]
    for avail, label in (("all", "All available"), ("enabled", "Enabled only")):
        out.append(f"<p class=rh>{label}</p>")
        for prov in ("same_provider", "any_provider"):
            sc = by.get(f"{prov}:{avail}")
            cov = ""
            if sc and len(covs) > 1:
                cov = f"{_pct(sc['router']['balanced'].get('coverage'))} of spend covered"
            out.append(_scope_cell(sc, prov == "any_provider", cov, spend_pm))
    return "".join(out) + "</div>"


def _uncertainty_rows(report: dict, spend_pm: float | None) -> list[dict]:
    rows = []
    for sc in sorted(report.get("scenarios") or [], key=lambda x: not x.get("headline")):
        label = SCOPE_LABEL.get(sc["scope"], sc["scope"]) + (" (headline)" if sc.get("headline") else "")
        for lever, fig in (("Smart Router", sc["router"]["balanced"]), ("Change model", sc["switch"])):
            ci = fig.get("ci90_per_month")
            rows.append({"scope": label, "lever": lever, "pm": fig.get("per_month"),
                         "pct": _of(fig.get("per_month"), spend_pm),
                         "ci": f"{_usd0(ci[0])} to {_usd0(ci[1])}" if ci else "n/a"})
    return rows


def _pool_tables(report: dict) -> str:
    out = []
    for sc in report.get("scenarios") or []:
        cap = SCOPE_LABEL.get(sc["scope"], sc["scope"]) + (" (headline)" if sc["headline"] else "")
        out.append(_table(sc["models"], [("Model in use", "model", str),
                                         ("Router pool", "pool", lambda p: ", ".join(p or []) or "none"),
                                         ("Change to", "switch", _txt),
                                         ("Enable first", "enable", lambda e: ", ".join(e or []) or "nothing")],
                            cap))
    return "".join(out)


def _card(title: str, body: str, ident: str = "") -> str:
    """Platform chart card: header strip with a small title, inset body."""
    i = f" id={ident}" if ident else ""
    return f"<section class=card><h2{i}>{html.escape(title)}</h2><div class=cb>{body}</div></section>"


def _details(title: str, hint: str, body: str) -> str:
    return f"<details><summary>{html.escape(title)} <span>{html.escape(hint)}</span></summary>{body}</details>"


GLOSSARY = [
    ("Smart Router", "orq's router. For each call it picks the cheapest model in a pool that can handle the ask."),
    ("Change model", "Replace a model with a cheaper, at least as strong model for all of its calls."),
    ("Pool", "The models the router may pick from: one at least as strong as today's, plus cheaper tiers."),
    ("Index", ("A benchmark score for overall model strength. Higher is stronger. Not a measure of fit for "
              "your workload.")),
    ("As strong and cheaper", "Another model scores at least as high on the index and costs no more on your traffic."),
    ("Scope", "Which models are allowed: same provider or any, and enabled models only or all available."),
    ("Coverage", "The share of spend on the sampled models. Spend on other models is not estimated."),
    ("Ask, sampled call", "One LLM call (a span) from this workspace's traces, re-priced on the new model."),
    ("Easy, Medium, Hard", ("How demanding each sampled call is, as graded by the router. Easy and medium calls "
                            "sent to a strong model are where a cheaper model does the same job.")),
    ("Ungraded", "Calls the router could not grade; they go to the strongest model in the pool."),
]


FONT_DIR = Path(__file__).resolve().parent.parent / "resources" / "fonts"


def _font_css() -> str:
    """@font-face rules for the bundled Avio Sans subsets (Latin), inlined so the page stays one file."""
    out = []
    for name, weight in (("Regular", 400), ("Medium", 500), ("SemiBold", 600)):
        f = FONT_DIR / f"AvioSans-{name}.woff2"
        if f.exists():
            data = base64.b64encode(f.read_bytes()).decode()
            out.append(f'@font-face{{font-family:"Avio Sans";font-weight:{weight};font-style:normal;'
                       f'font-display:swap;src:url(data:font/woff2;base64,{data}) format("woff2")}}')
    return "".join(out)


def _moves_rows(ro: dict) -> list[dict]:
    """Moves with a call count that says how many of the calls were fallbacks (no routing decision)."""
    return [dict(mv, calls=f"{mv.get('n', 0)}" + (f" (incl. {mv['fallback']} fallback)" if mv.get("fallback") else ""))
            for mv in ro.get("moves") or []]


def render(report: dict, narrative: dict[str, str], redact: bool) -> str:
    missing = [s for s in SLOTS if not narrative.get(s)]
    if missing:
        raise SystemExit(f"narrative slots empty: {', '.join(missing)}")
    sp, ro, ca = report.get("spend") or {}, report.get("routing") or {}, report.get("caching") or {}
    win = report["window"]
    ws = str(report.get("workspace"))
    total = sp.get("total") or 0
    per_month = (sp["per_month"] / total) if total and sp.get("per_month") else 30 / max(1, win["days"])
    h = ro.get("headline") or {}
    h_pm = h.get("per_month")
    scen = _scenario_rows(report)
    head_row = next((r for r in scen if r["headline"]), {})
    enabled = next((r for r in scen if r["scope"].startswith(SCOPE_LABEL["same_provider:enabled"])), {})
    n = (report.get("params") or {}).get("sample")
    cov = h.get("coverage")
    caching_small = (ca.get("savings_found") or 0) < 1

    spend_pm = sp.get("per_month")
    e_pm = enabled.get("router")
    first = re.split(r"\n\s*\n", narrative["summary"].strip())[0]
    verdict = html.escape(" ".join(first.split()))
    sw_pm = head_row.get("switch")
    p_router, p_switch = _of(h_pm, spend_pm), _of(sw_pm, spend_pm)
    rec = "r" if h_pm is not None and (sw_pm is None or h_pm >= sw_pm) else "s" if sw_pm is not None else ""
    # the spend bar and the plan follow the recommended lever
    plan_pm, plan_e = (sw_pm, enabled.get("switch")) if rec == "s" else (h_pm, e_pm)
    bar = ""
    if spend_pm and plan_pm is not None and plan_e is not None:
        now_v, more_v = max(0.0, plan_e), max(0.0, plan_pm - plan_e)
        left_v = max(0.0, spend_pm - now_v - more_v)
        segs = [("b1", now_v, "sr"), ("b2", more_v, "sr2"), ("b3", left_v, "kept")]
        bars = "".join(f"<i class={c} style='flex:{v / spend_pm:.4f} 1 0'></i>" for c, v, _ in segs if v > 0)
        items = [("sr", f"{_pc(now_v / spend_pm)} saved today", now_v),
                 ("sr2", f"+{_pc(more_v / spend_pm)} after enabling", more_v),
                 ("kept", f"{_pc(left_v / spend_pm)} still spent", left_v)]
        legend = "".join(f"<li><span class='sw {c}'></span>{t} \u00b7 {_usd0(v)}/mo</li>" for c, t, v in items
                         if v > 0 or c == "sr")
        bar = (f"<div class=bar role=img aria-label='{_pc(now_v / spend_pm)} saved today, {_pc(more_v / spend_pm)} "
               f"more after enabling, of {_usd0(spend_pm)} a month spend'>{bars}</div>"
               f"<ul class=legend>{legend}</ul>")
    adds = round((h_pm or 0) - (sw_pm or 0)) if h_pm is not None and sw_pm is not None else None
    pts = round((p_router - p_switch) * 100) if p_router is not None and p_switch is not None else 0
    if adds is None:
        gap = ""
    elif _ranges_overlap(_headline(report)):
        gap = ("<p class='m gap'>Both save about the same: the difference is within the margin of error, "
               "so either works.</p>")
    elif adds > 0:
        more = f"{pts} point{'s' if pts != 1 else ''} more" if pts >= 1 else "slightly more"
        gap = f"<p class='m gap'>Router saves {more} (+${adds:,}/mo).</p>"
    else:
        gap = "<p class='m gap'>Router adds nothing here.</p>"

    def kpi(dot: str, name: str, p: Any, pm: Any) -> str:
        tag = "<span class=rec>Recommended</span>" if dot == rec else ""
        return (f"<section class=card><div class=kh><i class={dot}></i><span class=kn>{name}</span>{tag}</div><div class=cb>"
                f"<p class=big>{_pc(p)}<small>saved, {_usd0(pm)}/mo</small></p></div></section>")

    hero = ("<section class=hero aria-label='Headline'>"
            f"<p class=verdict>{verdict}</p><div class=kpis>"
            f"{kpi('r', 'Smart Router', p_router, h_pm)}{kpi('s', 'Change model', p_switch, sw_pm)}</div>{gap}"
            + _card("Savings against monthly spend",
                    f"{bar}<p class='m caveat'>From {n or 'the'} sampled calls ({_pct(cov)} of spend). "
                    "Test answer quality before moving traffic.</p>")
            + "</section>")

    nav = ("<nav class=toc aria-label='On this page'><span>On this page</span><ul><li><a href='#do'>What to do</a></li>"
           "<li><a href='#scopes'>Other model choices</a></li><li><a href='#details'>Details</a></li></ul></nav>")
    theme = f"<button type=button class=theme aria-label='Switch colour theme'>{SUN}{MOON}</button>"
    to_day = _day(win["to"])
    head = (f"<header class=top><div><h1>LLM cost savings: {html.escape(ws)}</h1>"
            f"<p class=m>{html.escape(_day(win['from']))} to {html.escape(to_day)}, scaled to a month: "
            f"{_usd0(spend_pm)}/mo LLM spend.</p></div>{theme}</header>")
    parts = [_card("What to do", (_plan_caption("switch", p_switch, sw_pm) if rec == "s" else _plan_caption("router", p_router, h_pm))
                   + _actions_chart(report, per_month, "switch" if rec == "s" else "router"), "do"),
             _card("Other model choices", "<p class=m>Share of monthly spend saved, by which models are allowed.</p>"
                   + _scope_grid(report, spend_pm) + f"<p class=m>{html.escape(TOKENIZER_NOTE)}</p>", "scopes")]
    if not caching_small:
        parts.append(_card("Caching", (f"<p><b>Caching:</b> {_usd(ca.get('savings_found'))} in the "
                                       f"{ca.get('threads_scanned', 0)} costliest threads scanned, not scaled to the "
                                       "window.</p>") + _md(narrative["caching"]), "caching"))
    gen = html.escape(_day(report["generated_at"]) if report.get("generated_at") else to_day)
    dets = [_details("Estimates with uncertainty", "90% CI per scope and lever",
                     "<p class=m>The 90% CI covers sampling noise only, not answer quality.</p>" + _table(
                         _uncertainty_rows(report, spend_pm),
                         [("Scope", "scope", str), ("Lever", "lever", str), ("$/mo saved", "pm", _usd),
                          ("% saved", "pct", _pc), ("90% CI ($/mo)", "ci", _ci_cell)], "Estimates with uncertainty")),
            _details("Why each move", "the reasoning per model", _md(narrative["routing"])),
            _details("Method and caveats", "sample, coverage, limits, terms",
                     _md(narrative["caveats"]) + (
                         f"<p class=m>Excluding the 3 largest sampled calls, the headline is "
                         f"{_usd0((h.get('without_top3') or 0) * per_month)}/mo.</p>"
                         "<dl class=gl>" + "".join(f"<dt>{html.escape(t)}</dt><dd>{html.escape(d)}</dd>"
                                                   for t, d in GLOSSARY) + "</dl>")),
            _details("Pools and models to enable", "what each move needs, per scope", _pool_tables(report)),
            _details("Where sampled asks move", "headline scope, balanced profile",
                     _table(_moves_rows(ro), [("Model in use", "used", str), ("Router pick", "pick", str),
                                                  ("Difficulty tiers", "tier", str), ("Sampled calls", "calls", str),
                                                  ("Calls that cost more", "costs_more", str),
                                                  ("Saving on the sampled calls", "weighted_savings", _usd)],
                            "Where sampled asks move"))]
    if not caching_small:
        dets.append(_details("Caching candidates", f"{ca.get('threads_scanned', 0)} threads scanned",
                             _table([r for r in ca.get("rows", []) if r.get("candidate")][:20],
                                    [("Thread", "thread", lambda t: "(hidden)" if redact else t),
                                     ("Agent", "agent", lambda a: a or "no agent"), ("Model", "model", str),
                                     ("Turns", "turns", str), ("Saving", "savings", _usd), ("Action", "action", str)],
                                    "Caching candidates")))
    intro = "<p><b>Caching:</b> nothing worth changing.</p>" if caching_small else ""
    intro += (f"<p class=m>{_usd(total)} spent over the {win['days']}-day window. Covers {_pct(cov)} of spend; "
             f"figures re-price the sampled calls on the new model. Balanced routing profile, generated {gen}.</p>")
    parts.append(_card("Details", intro + "<div class=rows>" + "".join(dets) + "</div>", "details"))
    body = head + hero + nav + "".join(parts)
    title = f"{ws} cost savings, {to_day.split(' ', 1)[-1]}"
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width, initial-scale=1'>"
            f"<title>{html.escape(title)}</title><script>{THEME_BOOT}</script><style>{_font_css()}{CSS}</style></head>"
            f"<body class=auto><main>{body}</main><script>{PAGE_JS}</script></body></html>")


# --------------------------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    an = sub.add_parser("analyze", help="sample, score and estimate; writes report.json")
    an.add_argument("--workspace", help="workspace key (default: the CLI's active workspace)")
    an.add_argument("--out", required=True)
    an.add_argument("--sample", type=int, default=MAX_SAMPLE)
    an.add_argument("--counterfactual-test", action="store_true")
    an.add_argument("--cache-threads", type=int, default=200, help="threads to scan for caching gaps")
    an.add_argument("--seed", type=int, default=1917)
    an.add_argument("--redact", action="store_true", help="leave ask examples out of report.json")
    an.add_argument("--yes", action="store_true", help="skip the decision-cost confirmation")
    rn = sub.add_parser("render", help="merge narrative.md into report.html")
    rn.add_argument("--out", required=True)
    rn.add_argument("--narrative", required=True)
    rn.add_argument("--redact", action="store_true", help="hide thread ids and ask examples")
    a = ap.parse_args(argv)
    if a.cmd == "analyze":
        if not 1 <= a.sample <= MAX_SAMPLE:
            ap.error(f"--sample must be between 1 and {MAX_SAMPLE}")
        rep = Analyzer(a).run()
        return 0 if rep["status"] == "ok" else 2
    report = json.loads((Path(a.out) / "report.json").read_text())
    if report.get("status") != "ok" or "routing" not in report:
        raise SystemExit("report.json has no finished analysis; rerun analyze")
    page = render(report, parse_narrative(Path(a.narrative).read_text()), a.redact)
    (Path(a.out) / "report.html").write_text(page)
    print(Path(a.out) / "report.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
