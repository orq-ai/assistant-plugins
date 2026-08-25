# /// script
# requires-python = ">=3.11"
# dependencies = ["fire>=0.7.0", "loguru>=0.7.3"]
# ///
"""Step 7 — serve the per-type annotation UI and persist human labels.

stdlib `http.server` only (no Flask, no pip installs — keeps it runnable on
Windows where importing heavier deps can abort the process). Serves
`annotation/annotate.html`, the run's `queue.json`, and an annotations API that
writes every label straight to `annotations.json` the moment it is made (true
auto-save: a reload or crash resumes exactly where you left off).

Each queue item carries its own `verdict_space` (Part 1 → Part 2 boundary, §5.6),
so the UI renders a **type-native** input per item — boolean → Pass/Fail,
categorical → one button per declared label, numeric → a bounded number input —
plus one optional one-line "why". The human's typed value is therefore directly
comparable to the judge's verdicts in the recommend/aggregate step (§2.2) with no
Pass/Fail remapping to get wrong.

`annotations.json` contract (RES-978 Part 2, §2.1) — a JSON object keyed by
`source_index` (string):

    {"<source_index>": {"status": "labeled",
                        "value":  <bool | str | number>,   # follows output_type
                        "reason": <str, may be "">,
                        "low_flip_sample": <bool>}}

The pure bits (verdict_space → widget, value coercion/validation, record build,
load/write round-trip) live as module functions so the HTTP handler stays thin
and everything is unit-testable without a browser.

Usage:
    cd skills/orq-evaluator-alignment
    uv run scripts/serve_annotation.py --run_dir runs/<key>_<ts>
    # then open http://localhost:8765
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import fire
from loguru import logger

import _bootstrap  # noqa: F401
from lib import runner

HERE = Path(__file__).resolve().parent
HTML = HERE.parent / 'annotation' / 'annotate.html'

# Module-level state populated by main(); read by the handler.
QUEUE_PATH: Path
ANNOTATIONS_PATH: Path
_meta: dict[str, Any] = {}
_index_by_source: dict[int, dict[str, Any]] = {}


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested in tests/test_serve_annotation.py)                 #
# --------------------------------------------------------------------------- #

_NUMBER_TYPES = {'number', 'numeric'}


def _normalize_scale(scale: Any) -> list[float] | None:
    """Coerce a `[min, max]` verdict-space scale to a float pair, or None.

    Numeric evaluators can lack a scale entirely (fetch keeps it override-only),
    so None is a first-class value — the widget just renders unbounded.
    """
    if isinstance(scale, (list, tuple)) and len(scale) == 2:
        try:
            return [float(scale[0]), float(scale[1])]
        except (TypeError, ValueError):
            return None
    return None


def input_type_for(verdict_space: dict[str, Any] | None) -> dict[str, Any]:
    """Map a queue item's `verdict_space` to the widget the UI should render.

    Returns a small spec the front-end consumes directly:
      - boolean     -> {'type': 'boolean'}
      - categorical -> {'type': 'categorical', 'labels': [...]}
      - number      -> {'type': 'number', 'scale': [min, max] | None}

    Anything missing/unknown falls back to boolean (the safe default and the
    historical behaviour of this UI).
    """
    vs = verdict_space or {}
    vtype = str(vs.get('type', 'boolean')).strip().lower()
    if vtype == 'categorical':
        return {'type': 'categorical', 'labels': list(vs.get('labels') or [])}
    if vtype in _NUMBER_TYPES:
        return {'type': 'number', 'scale': _normalize_scale(vs.get('scale'))}
    return {'type': 'boolean'}


def coerce_value(verdict_space: dict[str, Any] | None, value: Any) -> bool | str | float:
    """Turn a raw posted value into the typed `annotations.json` value.

    Type follows the output type: boolean -> bool, categorical -> one of the
    declared labels (str), numeric -> float within scale (if a scale is set).
    Raises ValueError on a value that does not fit the verdict space so a bad
    label or out-of-range score is rejected at the door rather than silently
    persisted.
    """
    spec = input_type_for(verdict_space)
    vtype = spec['type']

    if vtype == 'boolean':
        if not isinstance(value, bool):
            raise ValueError(f'boolean value must be true/false, got {value!r}')
        return value

    if vtype == 'categorical':
        labels = spec['labels']
        sval = value if isinstance(value, str) else str(value)
        if labels and sval not in labels:
            raise ValueError(f'{sval!r} is not a declared label {labels}')
        return sval

    # numeric
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'numeric value must be a number, got {value!r}') from None
    scale = spec['scale']
    if scale is not None:
        lo, hi = scale
        if not (lo <= num <= hi):
            raise ValueError(f'{num} is outside scale [{lo}, {hi}]')
    return num


def build_annotation_record(
    space: dict[str, Any] | None,
    value: Any,
    reason: str | None,
    low_flip_sample: bool,
) -> dict[str, Any]:
    """Build one `annotations.json` entry for a `labeled` item (pinned contract).

    `value` is coerced/validated against the verdict space; `reason` defaults to
    "" (the optional "why" field). This is exactly the shape a reload reads back.
    """
    return {
        'status': 'labeled',
        'value': coerce_value(space, value),
        'reason': reason or '',
        'low_flip_sample': bool(low_flip_sample),
    }


def upsert_annotation(store: dict[str, Any], source_index: Any, record: dict[str, Any]) -> dict[str, Any]:
    """Insert/replace an entry, keyed by `source_index` as a string (the contract
    key). Mutates and returns `store`."""
    store[str(source_index)] = record
    return store


def read_annotations(path: Path) -> dict[str, Any]:
    """Load `annotations.json` (empty dict if the file does not exist yet)."""
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    return {}


def write_annotations(path: Path, store: dict[str, Any]) -> None:
    """Atomically write `annotations.json` (write-tmp-then-replace)."""
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


# --------------------------------------------------------------------------- #
# HTTP server (thin: delegates all type logic to the pure helpers above)       #
# --------------------------------------------------------------------------- #


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:  # quiet console
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: Any) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')

    def do_GET(self) -> None:
        path = self.path.split('?', 1)[0]
        if path in ('/', '/index.html', '/annotate.html'):
            self._send(200, HTML.read_bytes(), 'text/html; charset=utf-8')
        elif path == '/queue.json':
            self._send(200, QUEUE_PATH.read_bytes(), 'application/json; charset=utf-8')
        elif path == '/api/annotations':
            self._json(200, read_annotations(ANNOTATIONS_PATH))
        else:
            self._json(404, {'error': 'not found', 'path': path})

    def do_POST(self) -> None:
        path = self.path.split('?', 1)[0]
        if path == '/api/done':
            # The UI's "Done" button: ack, then shut the server down from a
            # separate thread (shutdown() deadlocks if called on the serving
            # thread). serve_forever() returns, main() prints a summary, and the
            # conductor proceeds to the next step (recommend / aggregate).
            self._json(200, {'ok': True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if path != '/api/annotations':
            self._json(404, {'error': 'not found', 'path': path})
            return
        try:
            length = int(self.headers.get('Content-Length', 0))
            payload = json.loads(self.rfile.read(length) or b'{}')
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {'error': f'bad json: {exc}'})
            return

        idx = payload.get('source_index')
        if idx is None:
            self._json(400, {'error': 'source_index required'})
            return

        item = _index_by_source.get(int(idx), {})
        space = item.get('verdict_space') or _meta.get('verdict_space')
        low_flip = bool(item.get('low_flip_sample', False))
        status = payload.get('status', 'labeled')
        reason = payload.get('reason', payload.get('explanation', '')) or ''

        with getattr(self.server, '_lock'):
            store = read_annotations(ANNOTATIONS_PATH)
            if status == 'labeled':
                try:
                    record = build_annotation_record(
                        space=space, value=payload.get('value'), reason=reason, low_flip_sample=low_flip
                    )
                except ValueError as exc:
                    self._json(400, {'error': str(exc)})
                    return
            else:
                # defer / clear: keep the item resume-able but unlabeled.
                record = {'status': status, 'value': None, 'reason': reason, 'low_flip_sample': low_flip}
            upsert_annotation(store, idx, record)
            write_annotations(ANNOTATIONS_PATH, store)
        self._json(200, {'ok': True})


def main(
    run_dir: str | None = None,
    config: str = 'config.toml',
    port: int = 8765,
) -> None:
    """Serve the annotation UI for a run directory's queue.json."""
    global QUEUE_PATH, ANNOTATIONS_PATH, _meta, _index_by_source

    cfg = runner.load_config(config)
    out_dir = runner.resolve_run_dir(run_dir) if run_dir else runner.latest_run_dir(cfg.get('runs_dir', 'runs'))
    if out_dir is None:
        raise SystemExit('No run directory. Run build_queue.py first.')

    QUEUE_PATH = out_dir / 'queue.json'
    ANNOTATIONS_PATH = out_dir / 'annotations.json'
    if not QUEUE_PATH.exists():
        raise SystemExit(f'queue.json not found in {out_dir} — run build_queue.py first.')
    if not HTML.exists():
        raise SystemExit(f'annotate.html missing: {HTML}')

    queue = json.loads(QUEUE_PATH.read_text(encoding='utf-8'))
    _meta = queue.get('meta', {})
    _index_by_source = {
        int(it['source_index']): it for it in queue.get('items', []) if it.get('source_index') is not None
    }

    httpd = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    setattr(httpd, '_lock', threading.Lock())

    existing = len(read_annotations(ANNOTATIONS_PATH))
    vs = _meta.get('verdict_space', {})
    logger.info(f'Annotation server -> http://localhost:{port}')
    logger.info(f'  queue:       {QUEUE_PATH} ({_meta.get("n_items")} items, judge={_meta.get("judge_model")})')
    logger.info(f'  verdict:     {vs.get("type", "boolean")} (type-native scoring UI)')
    logger.info(f'  annotations: {ANNOTATIONS_PATH.name} ({existing} already saved)')
    logger.info('  Click "Done" in the UI (or Ctrl-C) to stop. Labels auto-persist on every action.')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info('Stopped.')
    finally:
        httpd.server_close()
        final = read_annotations(ANNOTATIONS_PATH)
        labeled = sum(1 for a in final.values() if a.get('status') == 'labeled')
        deferred = sum(1 for a in final.values() if a.get('status') == 'deferred')
        print(f'✓ Annotation finished: {labeled} labeled, {deferred} deferred -> {ANNOTATIONS_PATH.name}')


if __name__ == '__main__':
    fire.Fire(main)
