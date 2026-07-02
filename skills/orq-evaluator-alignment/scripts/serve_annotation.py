# /// script
# requires-python = ">=3.11"
# dependencies = ["fire>=0.7.0"]
# ///
"""Step 7 — serve the annotation UI and persist human labels.

stdlib `http.server` only (no Flask, no pip installs — keeps it runnable on
Windows where importing heavier deps can abort the process). Serves
`annotation/annotate.html`, the run's `queue.json`, and an annotations API that
writes every label straight to `annotations.json` the moment it is made (true
auto-save: a reload or crash resumes exactly where you left off).

The label space is the judge's own boolean verdict (True / False), so a human
correction is directly comparable to the judge's value in step 8 — no Pass/Fail
remapping to get wrong. Each record follows ADR-14's `human_review` shape plus a
`provenance` block. `parent_annotation_id` is intentionally absent until this
runs against live spans (design §1, RES-843).

Usage:
    cd skills/orq-evaluator-alignment
    uv run scripts/serve_annotation.py --run_dir runs/<key>_<ts>
    # then open http://localhost:8765
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import fire

import _bootstrap  # noqa: F401
from lib import runner

HERE = Path(__file__).resolve().parent
HTML = HERE.parent / 'annotation' / 'annotate.html'

ACTOR_ID = os.getenv('ALIGNMENT_ACTOR_ID', 'chiel@orq.ai')

# Module-level state populated by main(); read by the handler.
QUEUE_PATH: Path
ANNOTATIONS_PATH: Path
_meta: dict[str, Any] = {}
_index_by_source: dict[int, dict[str, Any]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_annotations() -> dict[str, Any]:
    if ANNOTATIONS_PATH.exists():
        return json.loads(ANNOTATIONS_PATH.read_text(encoding='utf-8'))
    return {}


def _atomic_write(data: dict[str, Any]) -> None:
    tmp = ANNOTATIONS_PATH.with_suffix(ANNOTATIONS_PATH.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(ANNOTATIONS_PATH)


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
            self._json(200, _load_annotations())
        else:
            self._json(404, {'error': 'not found', 'path': path})

    def do_POST(self) -> None:
        path = self.path.split('?', 1)[0]
        if path == '/api/done':
            # The UI's "Done" button: ack, then shut the server down from a
            # separate thread (shutdown() deadlocks if called on the serving
            # thread). serve_forever() returns, main() prints a summary, and the
            # conductor proceeds to the next step (compare / rewrite).
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
        status = payload.get('status', 'labeled')
        value = payload.get('value')
        if status == 'labeled' and not isinstance(value, bool):
            self._json(400, {'error': 'value must be true or false when status=labeled'})
            return

        item = _index_by_source.get(int(idx), {})
        with getattr(self.server, '_lock'):
            store = _load_annotations()
            key = str(idx)
            prev = store.get(key, {})
            store[key] = {
                '_id': prev.get('_id') or f'ann_{uuid.uuid4().hex}',
                'evaluation_type': 'human_review',
                'source': 'platform',
                'output_schema': 'boolean',
                'value': value if status == 'labeled' else None,
                'explanation': payload.get('explanation', ''),
                'status': status,
                'annotator': {'kind': 'human', 'actor_id': ACTOR_ID},
                'provenance': {
                    'source_index': idx,
                    'rank': item.get('rank'),
                    'low_flip_sample': item.get('low_flip_sample', False),
                    'evaluator_id': _meta.get('evaluator_id'),
                    'evaluator_key': _meta.get('evaluator_key'),
                    'judge_model': _meta.get('judge_model'),
                },
                'created_at': prev.get('created_at') or _now(),
                'updated_at': _now(),
            }
            _atomic_write(store)
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
    _index_by_source = {int(it['source_index']): it for it in queue.get('items', []) if it.get('source_index') is not None}

    httpd = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    setattr(httpd, '_lock', threading.Lock())

    existing = len(_load_annotations())
    print(f'Annotation server -> http://localhost:{port}')
    print(f'  queue:       {QUEUE_PATH} ({_meta.get("n_items")} items, judge={_meta.get("judge_model")})')
    print(f'  annotations: {ANNOTATIONS_PATH.name} ({existing} already saved)')
    print('  Click "Done" in the UI (or Ctrl-C) to stop. Labels auto-persist on every action.')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\nStopped.')
    finally:
        httpd.server_close()
        final = _load_annotations()
        labeled = sum(1 for a in final.values() if a.get('status') == 'labeled')
        deferred = sum(1 for a in final.values() if a.get('status') == 'deferred')
        print(f'✓ Annotation finished: {labeled} labeled, {deferred} deferred -> {ANNOTATIONS_PATH.name}')


if __name__ == '__main__':
    fire.Fire(main)
