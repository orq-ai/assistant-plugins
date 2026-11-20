# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Cost-savings report for one orq.ai workspace.

`analyze` samples leaf LLM spans from the workspace's traces, scores each span's routing input with the Smart Router's
`jev-general-v1` decision, re-prices the span at the model the router would have picked, and scales the per-model
saving rate to the window's spend with a bootstrap CI. It also scans multi-turn threads for missed prompt caching and
writes `report.json` plus `narrative-slots.md`. `render` merges the agent's `narrative.md` into `report.html`.

Every number comes from this script; the agent writes prose only. All reads and the decision calls go through the
`orq` CLI (`-o json`), so the user's CLI login and workspace are reused. Responses are cached under `<out>/cache`,
so a rerun resumes without paying twice. Spec: ../resources/design.md.
"""

from __future__ import annotations

import argparse
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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1"
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
THRESHOLDS = {"cost": (0.59, 1.16), "balanced": (0.11, 0.92), "quality": (0.02, 0.09)}
PROFILES = ("balanced", "cost", "quality")
TIERS = ("easy", "medium", "hard")

LLM_SPAN_TYPES = {"span.chat_completion", "span.responses"}
DECISION_MODEL_RE = re.compile(r"^(typesafe/|orq/decider-)")
NON_CHAT_RE = re.compile(r"embed|rerank|tts|transcri|whisper|moderation|ocr|dall-e|image", re.I)

# Tier table: provider -> [frontier, tier 1, tier 2, tier 3]; regexes on the normalized model name.
TIER_TABLE: dict[str, list[str | None]] = {
    "openai": [r"^gpt-[0-9.]+-astra$", r"^gpt-[0-9.]+-sol$", r"^gpt-[0-9.]+-terra$", r"^gpt-[0-9.]+-luna$"],
    "anthropic": [r"^claude-fable(-[0-9]+)*$", r"^claude-opus(-[0-9]+)*$", r"^claude-sonnet(-[0-9]+)*$",
                  r"^claude-haiku(-[0-9]+)*$"],
    "google": [r"^gemini-4-neon(-preview.*)?$", r"^gemini-4-carbon(-preview.*)?$",
               r"^gemini-4-hydrogen(-preview.*)?$", r"^gemini-3\.8-flash(-preview.*)?$"],
    "zai": [None, r"^glm-[0-9.]+$", r"^glm-[0-9.]+-flash$", None],
    "deepseek": [None, r"^deepseek-v[0-9.]+-pro(-[0-9]+)?$", r"^deepseek(-v[0-9.]+)?-flash(-[0-9]+)?$", None],
}
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


def pool_valid(pool: list[PoolModel]) -> bool:
    return len(pool) >= 2 and all(p.index for p in pool)


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
    if profile == "quality":
        return sorted(cand, key=lambda p: (-p.index, p.id))[0]
    return sorted(cand, key=lambda p: (p.band_price, -p.index, p.id))[0]


# --------------------------------------------------------------------------------------------- model names, tiers


def normalize_name(model_id: str) -> str:
    name = model_id.split("/", 1)[1] if "/" in model_id else model_id
    if "/" in name:  # nested host path, e.g. nebius/moonshotai/Kimi-K3
        name = name.rsplit("/", 1)[1]
    name = REGION_RE.sub("", name)
    name = VENDOR_RE.sub("", name)
    name = re.sub(r"@.*$", "", name)
    name = re.sub(r"-v[0-9]+:[0-9]+$", "", name)
    name = re.sub(r"-global$", "", name)
    name = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", name)
    return name


def region_of(model_id: str) -> str | None:
    name = model_id.split("/", 1)[1] if "/" in model_id else model_id
    m = REGION_RE.match(name)
    if not m or m.group(1) == "global":
        return None
    r = m.group(1)
    return r.split("-", 1)[0] if "-" in r else r


def developer_of(canonical: str) -> str:
    return canonical.split("/", 1)[0]


def table_tier(canonical: str) -> tuple[str | None, int | None]:
    dev = developer_of(canonical)
    row = TIER_TABLE.get(dev)
    if row is None:
        return None, None
    name = normalize_name(canonical)
    for i, rx in enumerate(row):
        if rx and re.match(rx, name):
            return dev, i
    return dev, None


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


def price_tokens(t: Tokens, prices: dict, cache_broken: bool = False) -> float:
    inp, out = prices.get("input", 0.0), prices.get("output", 0.0)
    read_p = prices.get("cache_read", inp)
    w = write_price(prices, t.ttl)
    w = inp if w is None else w
    if cache_broken:
        return t.uncached * inp + (t.cache_read + t.cache_write) * w + t.output * out
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
        dec = self.cards.get(DECISION_MODEL)
        if not dec or not dec.get("enabled"):
            raise Stop("decision model enabled", {"model": DECISION_MODEL,
                                                  "detail": "enable it in the workspace's model garden"})

    def v2(self, m: str) -> dict:
        c = self.cards.get(m) or {}
        return (((c.get("metadata") or {}).get("autorouter") or {}).get("v2")) or {}

    def poolable(self, m: str) -> bool:
        c, v, e = self.cards.get(m), self.v2(m), self.cat.get(m)
        p = (e or {}).get("pricing") or {}
        return bool(c and c.get("enabled") and v.get("intelligence_index") and v.get("price") is not None
                    and "input" in p and "output" in p)

    def canonical(self, m: str) -> str:
        e = self.cat.get(m) or {}
        return e.get("offering_of") or m

    def host(self, m: str) -> str:
        e = self.cat.get(m) or {}
        prov = e.get("provider")
        return (prov.get("id") if isinstance(prov, dict) else prov) or split_model(m)[0]

    def resolve_cell(self, dev: str, idx: int, host: str, region: str | None) -> str | None:
        rx = TIER_TABLE[dev][idx]
        cands = []
        for mid in self.cat:
            if self.host(mid) != host or developer_of(self.canonical(mid)) != dev:
                continue
            if not re.match(rx, normalize_name(self.canonical(mid))) or not self.poolable(mid):
                continue
            r = region_of(mid)
            region_rank = 0 if r == region else (1 if r is None else 2)
            cands.append((region_rank, self.cat[mid].get("created") or "", self.v2(mid).get("price"), mid))
        if not cands:
            return None
        cands.sort(key=lambda c: (c[0], _neg(c[1]), c[2], c[3]))
        return cands[0][3]

    def pool_for(self, m: str) -> dict:
        canon = self.canonical(m)
        dev, idx = table_tier(canon)
        if dev is None:
            return {"pool": None, "reason": f"provider {developer_of(canon)} not in tier table"}
        if idx is None:
            return {"pool": None, "reason": f"{canon} matches no tier"}
        row = TIER_TABLE[dev]
        present = [i for i, rx in enumerate(row) if rx]
        if len(present) == 2:
            if idx != present[0]:
                return {"pool": None, "reason": "under-powered", "tier": idx}
            cells = present
        elif idx == 0:
            cells = [0, 1, 2]
        elif idx == 1:
            cells = [1, 2, 3]
        else:
            return {"pool": None, "reason": "under-powered", "tier": idx}
        host, region = self.host(m), region_of(m)
        ids = []
        for i in cells:
            r = self.resolve_cell(dev, i, host, region)
            if r and r not in ids:
                ids.append(r)
        if m not in ids and self.poolable(m) and table_tier(self.canonical(m))[1] in cells:
            ids = [m if table_tier(self.canonical(x))[1] == idx else x for x in ids]
        pool = [PoolModel(x, float(self.v2(x)["intelligence_index"]), float(self.v2(x)["price"])) for x in ids]
        if not pool_valid(pool):
            return {"pool": None, "reason": "pool has fewer than 2 valid models", "tier": idx}
        return {"pool": pool, "reason": "", "tier": idx}

    def next_stronger(self, m: str) -> str | None:
        dev, host = developer_of(self.canonical(m)), self.host(m)
        mine = self.v2(m).get("intelligence_index") or 0
        region = region_of(m)
        cands = [(self.v2(x)["intelligence_index"], x) for x in self.cat if self.poolable(x)
                 and self.host(x) == host and developer_of(self.canonical(x)) == dev
                 and region_of(x) == region and self.v2(x)["intelligence_index"] > mine]
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

    def previous_turn(self, d: dict) -> dict | None:
        """The leaf LLM span that ran just before this one in its thread (same trace first)."""
        summ = d["span"].get("summary") or {}
        started = summ.get("started_at") or ""
        earlier = sorted([s for s in self.leaf_llm(d["trace_spans"]) if (s.get("started_at") or "") < started],
                         key=lambda s: s.get("started_at") or "")
        if earlier:
            p = earlier[-1]
            return self.get_span(d["trace_id"], p["span_id"], d["project"])
        thread = ((d["span"].get("attributes") or {}).get("orq") or {}).get("thread_id")
        if not thread:
            return None
        traces = self.search(d["project"], parse_ts(self.frm), parse_ts(started),
                             [{"field": "thread_id", "op": "eq", "values": [thread]}])
        traces = [t for t in traces if (t.get("trace_id") or t.get("id")) != d["trace_id"]]
        traces.sort(key=lambda t: t.get("started_at") or t.get("start_time") or "", reverse=True)
        for t in traces[:3]:
            tid = t.get("trace_id") or t.get("id")
            leaves = sorted(self.leaf_llm(self.list_spans(tid, d["project"])), key=lambda s: s.get("started_at") or "")
            leaves = [s for s in leaves if (s.get("started_at") or "") < started]
            if leaves:
                return self.get_span(tid, leaves[-1]["span_id"], d["project"])
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

        # previous turns and routing inputs
        with ThreadPoolExecutor(WORKERS) as ex:
            prevs = list(ex.map(lambda d: self._safe(lambda: self.previous_turn(d)), drawn))
        for d, p in zip(drawn, prevs, strict=True):
            attrs = d["span"].get("attributes") or {}
            d["input"], d["source"] = routing_input(attrs)
            d["prev"] = None
            if p:
                pt, ps = routing_input(p.get("attributes") or {})
                d["prev"] = {"input": pt, "source": ps}
        texts = {t for d in drawn for t in [d["input"], (d["prev"] or {}).get("input")] if t}
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
        pools: dict[str, dict] = {}
        self.samples = []
        for d in drawn:
            m = d["model"]
            if m not in pools:
                pools[m] = self.pool_for(m)
            pinfo = pools[m]
            summ, attrs = d["span"].get("summary") or {}, d["span"].get("attributes") or {}
            env = span_env(summ, attrs, m)
            toks = span_tokens(summ, attrs)
            pricing = (self.cat.get(m) or {}).get("pricing") or {}
            cur = choose_price(pricing, env)
            cur_price = price_tokens(toks, cur.prices)
            billed = float(((summ.get("cost") or {}).get("total") or 0)) - float(
                ((summ.get("cost") or {}).get("web_search") or 0))
            row = {"trace_id": d["trace_id"], "span_id": d["span_id"], "model": m, "project": d["project"],
                   "weight": d["weight"], "cur_price": cur_price, "billed": billed, "source": d["source"],
                   "ask_chars": len(d["input"] or ""), "prompt_tokens": toks.prompt, "flags": [],
                   "picks": {}, "savings": {p: 0.0 for p in PROFILES}, "tier": {}, "fallback": None,
                   "prev_found": d["prev"] is not None, "ask_preview": (d["input"] or "")[:200],
                   "_tokens": toks, "_env": env}
            if cur.flag:
                row["flags"].append(cur.flag)
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
            if row["fallback"] is None and pinfo["pool"]:
                prev_dec = None
                if d["prev"] and d["prev"]["input"]:
                    prev_dec = self.decide(d["prev"]["input"])
                for p in PROFILES:
                    choice = pick(pinfo["pool"], row["tier"][p], p).id
                    row["picks"][p] = choice
                    if d["prev"] is None:
                        prev_pick = None  # previous turn not found
                    elif prev_dec is None or not prev_dec.get("ok"):
                        prev_pick = m  # previous turn fell back to the default, the current model
                    else:
                        prev_pick = pick(pinfo["pool"], tier_of(prev_dec["score"], p), p).id
                    if choice == m and (prev_pick in (m, None)):
                        continue
                    broken = (prev_pick != choice) if prev_pick is not None else (choice != m)
                    pp = choose_price((self.cat.get(choice) or {}).get("pricing") or {}, span_env(summ, attrs, choice))
                    if pp.flag:
                        row["flags"].append(f"{choice}: {pp.flag}")
                    row["savings"][p] = cur_price - price_tokens(toks, pp.prices, cache_broken=broken)
            row["pool"] = [x.id for x in pinfo["pool"]] if pinfo["pool"] else None
            row["pool_reason"] = pinfo["reason"]
            self.samples.append(row)
        self.pools = pools

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
            by[r["model"]].append(Draw(r["model"], r["weight"], r["cur_price"], r["savings"]))
            own[r["model"]][0] += r["cur_price"]
            own[r["model"]][1] += r["billed"]
        strata, covered, not_covered = [], {}, []
        for m, ds in by.items():
            priced, billed = own[m]
            gap = abs(priced - billed) / billed if billed else None
            pinfo = self.pools.get(m) or {}
            reason = ""
            if gap is None or gap > PRICING_GAP:
                reason = f"pricing check off by {gap:.1%}" if gap is not None else "no billed cost"
                self.report["checks"]["caveats"].append({"check": "pricing check", "detail": f"{m}: {reason}"})
            elif any(f.startswith("no ") or ": no " in f for r in self.samples if r["model"] == m for f in r["flags"]):
                reason = "span tier has no price on a pool model"
            if reason:
                not_covered.append({"model": m, "spend": spend.get(m, 0.0), "reason": reason})
            else:
                covered[m] = ds
            strata.append({"model": m, "spend": spend.get(m, 0.0), "n": len(ds), "clusters": len(ds),
                           "pool": [p.id for p in pinfo.get("pool") or []] or None,
                           "pool_reason": pinfo.get("reason", ""), "pricing_gap": gap, "covered": not reason,
                           "rate": stratum_rate(ds, "balanced")})
        for row in self.report["spend"]["by_model"]:
            if row["key"] not in by:
                why = "not sampled" if row["key"] in spend else "not a chat model (decision, embedding or media)"
                not_covered.append({"model": row["key"], "spend": row["spend"], "reason": why})
        cov_spend = {m: spend.get(m, 0.0) for m in covered}
        profiles = {p: estimate(covered, cov_spend, p, random.Random(self.args.seed)) for p in PROFILES}
        for s in strata:
            s["savings"] = profiles["balanced"]["per_stratum"].get(s["model"]) if s["covered"] else None
        head = profiles["balanced"]
        k = 30 / self.days
        self.report["routing"] = {
            "headline": {"savings": head["savings"], "ci90": head["ci90"],
                         "without_top3": without_top(covered, cov_spend, "balanced"),
                         "coverage": (sum(cov_spend.values()) / total) if total else 0.0,
                         "per_month": head["savings"] * k, "ci90_per_month": [x * k for x in head["ci90"]]},
            "profiles": {p: {"savings": v["savings"], "ci90": v["ci90"]} for p, v in profiles.items()},
            "strata": strata, "not_covered": sorted(not_covered, key=lambda r: -r["spend"]),
            "buckets": self.buckets(), "fallbacks": self._count("fallback"),
            "carry_over": {"previous_found": sum(r["prev_found"] for r in self.samples),
                           "previous_missing": sum(not r["prev_found"] for r in self.samples)},
            "moves": self.moves(),
            "underpowered": self.underpowered(), "pareto": self.pareto([s["model"] for s in strata]),
        }

    def moves(self) -> list[dict]:
        """Per (used model, balanced pick): spans, how many cost more, weighted savings."""
        out: dict[tuple, dict] = {}
        for r in self.samples:
            pick = r["picks"].get("balanced") or r["model"]
            row = out.setdefault((r["model"], pick), {"used": r["model"], "pick": pick, "tier": set(), "n": 0,
                                                      "costs_more": 0, "weighted_savings": 0.0})
            row["n"] += 1
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
        out = []
        for m, pinfo in self.pools.items():
            if pinfo.get("reason") != "under-powered":
                continue
            rows = [r for r in self.samples if r["model"] == m and r["tier"]]
            hard = [r for r in rows if r["tier"]["balanced"] == "hard"]
            nxt = self.next_stronger(m)
            extra = None
            den = sum(r["weight"] * r["cur_price"] for r in rows)
            if nxt and hard and den:
                pr = (self.cat.get(nxt) or {}).get("pricing") or {}
                num = sum(r["weight"] * (price_tokens(r["_tokens"], choose_price(pr, r["_env"]).prices)
                                         - r["cur_price"]) for r in hard)
                extra = num / den
            ex = [] if self.args.redact else [r["ask_preview"] for r in hard[:3]]
            out.append({"model": m, "n": len(rows), "share_hard": (len(hard) / len(rows)) if rows else None,
                        "next_model": nxt, "extra_cost_rate": extra, "examples": ex})
        return out

    def pareto(self, models: list[str]) -> list[dict]:
        enabled = [x for x in self.cards if self.poolable(x)]
        out = []
        for m in models:
            v = self.v2(m)
            if not v.get("intelligence_index") or v.get("price") is None:
                out.append({"model": m, "dominated_by": None, "note": "no index or price"})
                continue
            i, p = v["intelligence_index"], v["price"]
            dom = sorted([x for x in enabled if x != m and self.v2(x)["intelligence_index"] >= i
                          and self.v2(x)["price"] <= p
                          and (self.v2(x)["intelligence_index"] > i or self.v2(x)["price"] < p)],
                         key=lambda x: -self.v2(x)["intelligence_index"])
            out.append({"model": m, "dominated_by": dom[:3], "note": ""})
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
            "only from report.json; give every savings figure with its 90% CI and coverage.\n\n" + slots)
        self.log(f"wrote {self.out / 'report.json'}")


def _neg(s: str) -> str:
    """Sort key that orders ISO timestamps newest first inside an ascending sort."""
    return "".join(chr(0x10FFFF - ord(c)) for c in s)


SLOTS = {
    "summary": "two to four sentences: spend, routing savings with CI and coverage, caching savings beside it",
    "routing": "which models move where, the profile columns, flagged buckets, the action",
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
    return "n/a" if x is None else f"${x:,.2f}" if abs(x) >= 1 else f"${x:,.4f}"


def _pct(x: Any) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def _table(rows: list[dict], cols: list[tuple[str, str, Any]]) -> str:
    head = "".join(f"<th>{html.escape(h)}</th>" for h, _, _ in cols)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(fmt(r.get(k))))}</td>" for _, k, fmt in cols) + "</tr>"
                   for r in rows)
    return f"<div class=t><table><tr>{head}</tr>{body}</table></div>"


CSS = """
:root{--bg:#fbfbfa;--fg:#1c1e21;--muted:#5d636b;--line:#dcdfe3;--tile:#f1f3f4;--accent:#2f6f8f}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#15171a;--fg:#e8eaed;--muted:#9aa1a9;
--line:#30343a;--tile:#1d2024;--accent:#7fb7d4;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#15171a;--fg:#e8eaed;--muted:#9aa1a9;--line:#30343a;--tile:#1d2024;--accent:#7fb7d4;
color-scheme:dark}
body{font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--bg);color:var(--fg);margin:0;
padding-block:24px 48px;padding-left:max(16px,env(safe-area-inset-left,0px));
padding-right:max(16px,env(safe-area-inset-right,0px))}
main{max-width:980px;margin:0 auto}
h1,h2,h3{text-wrap:balance;line-height:1.25}h1{font-size:26px;margin:0 0 4px}h2{font-size:19px;margin-top:32px;
border-top:1px solid var(--line);padding-top:16px}h3{font-size:15px;margin-top:20px}
p,li{max-width:70ch}.m{color:var(--muted)}
.t{overflow-x:auto;margin:8px 0}table{border-collapse:collapse;width:100%;font-size:13px;
font-variant-numeric:tabular-nums}td,th{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left;
vertical-align:top}th{color:var(--muted);font-weight:600;white-space:nowrap}
.k{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:16px 0}
.k div{background:var(--tile);padding:12px 14px;border-radius:6px;min-width:0}.k b{display:block;font-size:22px;
color:var(--accent);font-variant-numeric:tabular-nums}
blockquote{border-left:3px solid var(--line);margin:6px 0;padding:2px 10px;color:var(--muted)}
"""


def render(report: dict, narrative: dict[str, str], redact: bool) -> str:
    missing = [s for s in SLOTS if not narrative.get(s)]
    if missing:
        raise SystemExit(f"narrative slots empty: {', '.join(missing)}")
    sp, ro, ca = report.get("spend") or {}, report.get("routing") or {}, report.get("caching") or {}
    h = ro.get("headline") or {}
    ci = h.get("ci90") or [None, None]
    parts = [f"<h1>Cost savings report: {html.escape(str(report.get('workspace')))}</h1>",
             f"<p class=m>{html.escape(report['window']['from'])} to {html.escape(report['window']['to'])} "
             f"({report['window']['days']} days). Policy {POLICY}, balanced profile.</p>",
             "<div class=k>"
             f"<div><b>{_usd(sp.get('total'))}</b>spend in window</div>"
             f"<div><b>{_usd(h.get('savings'))}</b>routing savings, 90% CI {_usd(ci[0])} to {_usd(ci[1])}</div>"
             f"<div><b>{_pct(h.get('coverage'))}</b>of spend covered</div>"
             f"<div><b>{_usd(ca.get('savings_found'))}</b>caching savings in scanned threads</div></div>",
             "<h2>Summary</h2>", _md(narrative["summary"]),
             "<h2>Spend by model</h2>", _table(sp.get("by_model", [])[:15], [("Model", "key", str),
                                                                                ("Spend", "spend", _usd)]),
             "<h2>Routing</h2>", _md(narrative["routing"]),
             _table(ro.get("strata", []), [("Model", "model", str), ("Spend", "spend", _usd), ("n", "n", str),
                                           ("Savings", "savings", _usd), ("Pool", "pool", lambda p: ", ".join(p or [])),
                                           ("Note", "pool_reason", str)]),
             _table([{"profile": k, **v} for k, v in (ro.get("profiles") or {}).items()],
                    [("Profile", "profile", str), ("Savings", "savings", _usd),
                     ("90% CI", "ci90", lambda c: f"{_usd(c[0])} to {_usd(c[1])}")]),
             f"<p class=m>Without the top 3 spans: {_usd(h.get('without_top3'))}.</p>",
             "<h3>Where sampled asks move (balanced)</h3>",
             _table(ro.get("moves", []), [("Used", "used", str), ("Router pick", "pick", str), ("Tier", "tier", str),
                                          ("Spans", "n", str), ("Cost more", "costs_more", str),
                                          ("Weighted saving per span set", "weighted_savings", _usd)])]
    up = ro.get("underpowered") or []
    if up:
        parts += ["<h2>Under-powered asks</h2>",
                  _table(up, [("Model", "model", str), ("n", "n", str), ("Hard share", "share_hard", _pct),
                              ("Next model", "next_model", str), ("Extra cost rate", "extra_cost_rate", _pct)])]
        if not redact:
            for u in up:
                parts += [f"<blockquote>{html.escape(e)}</blockquote>" for e in u.get("examples") or []]
    parts += ["<h2>Caching gaps</h2>", _md(narrative["caching"]),
              _table([r for r in ca.get("rows", []) if r.get("candidate")][:20],
                     [("Thread", "thread", lambda t: "(hidden)" if redact else t), ("Agent", "agent", str),
                      ("Model", "model", str), ("Turns", "turns", str), ("Savings", "savings", _usd),
                      ("Action", "action", str)]),
              "<h2>Harness traffic</h2>",
              _table((report.get("harness") or {}).get("rows", []),
                     [("Framework", "framework", str), ("Spend", "spend", _usd), ("Share", "share", _pct),
                      ("Caching", "coding_harness", lambda x: "not controllable" if x else "")]),
              "<h2>Method and caveats</h2>", _md(narrative["caveats"]),
              _table(report["checks"]["caveats"], [("Check", "check", str), ("Detail", "detail", str)])]
    css = CSS
    return (f"<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            f"<title>Cost savings report</title><style>{css}</style></head><body><main>{''.join(parts)}</main></body>"
            "</html>")


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
    rn.add_argument("--redact", action="store_true")
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
