# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "fire>=0.7.0",
#     "httpx>=0.27",
#     "loguru>=0.7.3",
#     "python-dotenv>=1.2.1",
# ]
# ///
"""Provision (and tear down) the RAG-groundedness test case in an orq workspace.

Creates one dataset of 24 datapoints plus three judge evaluators (boolean,
categorical, numeric) that all read the same datapoints, then records every
created id in `created.json` so teardown is exact.

    uv run create_testcase.py create --dry_run   # print what would be sent
    uv run create_testcase.py create             # provision for real
    uv run create_testcase.py delete             # remove everything in created.json

Deliberately standalone: it posts to `/v2/evaluators` directly rather than going
through `lib.orq_client.build_create_body`. This fixture exists to exercise the
skill's own create path, so provisioning it must not depend on that path being
correct — a shared builder would let one bug hide itself.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import fire
import httpx
from dotenv import find_dotenv, load_dotenv
from loguru import logger

HERE = Path(__file__).resolve().parent
BASE_URL = 'https://api.orq.ai'
DATASET_NAME = 'RAG Groundedness — evaluator-alignment test case'
DATASET_DESCRIPTION = (
    'TEST CASE for the evaluator-alignment skill. 24 datapoints: 8 stable anchors, '
    '12 grey-zone cases across 4 engineered ambiguities, 4 consistently-wrong traps. '
    'See tests/live/evaluator-alignment/rag-groundedness/answer_key.json. Not production data.'
)

load_dotenv(find_dotenv(usecwd=True))


def _tls_verify() -> bool:
    """Off on Windows only — its bundled OpenSSL aborts the process on some cert
    chains (`OPENSSL_Uplink ... no OPENSSL_Applink`). Mirrors `lib.orq_client.tls_verify`."""
    return sys.platform != 'win32'


def _client() -> httpx.Client:
    key = os.getenv('ORQ_API_KEY')
    if not key:
        raise SystemExit('ORQ_API_KEY is not set (env or a .env on the path).')
    return httpx.Client(
        base_url=BASE_URL,
        headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
        verify=_tls_verify(),
        timeout=120,
    )


def _spec() -> dict[str, Any]:
    return json.loads((HERE / 'evaluators.json').read_text(encoding='utf-8'))


def _datapoints() -> list[dict[str, Any]]:
    """Authoring rows minus the local `_case_id` bookkeeping field."""
    rows = json.loads((HERE / 'datapoints.json').read_text(encoding='utf-8'))
    return [{k: v for k, v in row.items() if not k.startswith('_')} for row in rows]


def _evaluator_body(ev: dict[str, Any], *, model: str, path_prefix: str) -> dict[str, Any]:
    """`POST /v2/evaluators` body for one rubric, per verdict type."""
    body: dict[str, Any] = {
        'type': 'llm_eval',
        'mode': 'single',
        'model': model,
        'prompt': ev['prompt'],
        'output_type': ev['output_type'],
        'path': f"{path_prefix}/{ev['key']}",
        'key': ev['key'],
        'display_name': ev['display_name'],
        'description': ev['description'],
    }
    kind = ev['output_type']
    if kind == 'boolean':
        body['guardrail_config'] = {
            'type': 'boolean',
            'value': ev.get('guardrail_value', True),
            'enabled': True,
            'alert_on_failure': False,
        }
    elif kind == 'categorical':
        labels = ev['categorical_labels']
        body['categorical_labels'] = labels
        body['categories'] = [item['value'] for item in labels]
        body['guardrail_config'] = {
            'type': 'categorical',
            'values': ev.get('guardrail_values') or [item['value'] for item in labels],
            'enabled': True,
            'alert_on_failure': False,
        }
    elif kind == 'number':
        if ev.get('scale'):
            body['scale'] = ev['scale']
    elif kind != 'boolean':
        raise ValueError(f'Unsupported output_type {kind!r}.')
    return body


def _post(client: httpx.Client, path: str, body: Any) -> dict[str, Any]:
    resp = client.post(path, json=body)
    if resp.status_code >= 400:
        logger.error(f'✗ POST {path} [{resp.status_code}]: {resp.text[:600]}')
        resp.raise_for_status()
    payload = resp.json()
    return payload.get('data', payload) if isinstance(payload, dict) else {}


def _save(created: dict[str, Any]) -> None:
    """Persist the manifest after every create, so a mid-run failure still leaves
    an exact teardown record."""
    (HERE / 'created.json').write_text(json.dumps(created, indent=2) + '\n', encoding='utf-8')


def create(dry_run: bool = False, dataset_id: str | None = None) -> None:
    """Create the dataset, its datapoints, and the four evaluators.

    Pass `--dataset_id` to reuse a dataset a previous partial run already created
    (its id is in created.json) instead of leaving an orphan behind.
    """
    spec = _spec()
    rows = _datapoints()
    model = spec['judge_model']
    path_prefix = spec['path_prefix']

    if dry_run:
        logger.info(f'DRY RUN — would create dataset {DATASET_NAME!r} with {len(rows)} datapoints')
        for ev in spec['evaluators']:
            body = _evaluator_body(ev, model=model, path_prefix=path_prefix)
            logger.info(f"  evaluator {ev['key']} ({ev['output_type']}) -> path={body['path']}")
        return

    created: dict[str, Any] = {'dataset': None, 'evaluators': [], 'judge_model': model}
    with _client() as client:
        if dataset_id:
            logger.info(f'reusing dataset {dataset_id}')
        else:
            dataset = _post(
                client,
                '/v2/datasets',
                {'display_name': DATASET_NAME, 'path': path_prefix, 'description': DATASET_DESCRIPTION},
            )
            dataset_id = dataset.get('_id') or dataset.get('id')
            if not dataset_id:
                raise RuntimeError(f'create dataset returned no id; shape: {dataset!r}')
            logger.info(f'✓ dataset {dataset_id}')
        created['dataset'] = {'id': dataset_id, 'display_name': DATASET_NAME}
        _save(created)

        # NOTE: this endpoint takes a BARE ARRAY. `lib.orq_client.build_create_datapoints_body`
        # wraps it as {"datapoints": [...]}, which the API rejects with a 400
        # ("expected array, received object") — a live bug in the skill's save-back path.
        _post(client, f'/v2/datasets/{dataset_id}/datapoints', rows)
        logger.info(f'✓ {len(rows)} datapoints')

        for ev in spec['evaluators']:
            body = _evaluator_body(ev, model=model, path_prefix=path_prefix)
            try:
                data = _post(client, '/v2/evaluators', body)
            except httpx.HTTPStatusError:
                # A verdict type this workspace refuses must not lose the ids
                # already created — record and continue.
                logger.warning(f"⚠ skipped {ev['key']} ({ev['output_type']}) — see error above")
                created['evaluators'].append({'key': ev['key'], 'output_type': ev['output_type'], 'id': None})
                _save(created)
                continue
            ev_id = data.get('_id') or data.get('id')
            created['evaluators'].append({'key': ev['key'], 'output_type': ev['output_type'], 'id': ev_id})
            _save(created)
            logger.info(f"✓ evaluator {ev['output_type']:<11} {ev_id}  {ev['key']}")

    logger.info(f"✓ wrote {HERE / 'created.json'}")
    logger.info('Run the skill against each evaluator id above; see README.md.')


def delete() -> None:
    """Remove everything recorded in created.json."""
    manifest_path = HERE / 'created.json'
    if not manifest_path.exists():
        raise SystemExit('No created.json — nothing recorded to delete.')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    with _client() as client:
        for ev in manifest.get('evaluators', []):
            if not ev.get('id'):
                continue
            resp = client.delete(f"/v2/evaluators/{ev['id']}")
            level = logger.info if resp.status_code < 400 else logger.warning
            level(f"{'✓' if resp.status_code < 400 else '⚠'} evaluator {ev['id']} [{resp.status_code}]")
        dataset = manifest.get('dataset') or {}
        if dataset.get('id'):
            resp = client.delete(f"/v2/datasets/{dataset['id']}")
            level = logger.info if resp.status_code < 400 else logger.warning
            level(f"{'✓' if resp.status_code < 400 else '⚠'} dataset {dataset['id']} [{resp.status_code}]")
    manifest_path.unlink()
    logger.info('✓ removed created.json')


if __name__ == '__main__':
    fire.Fire({'create': create, 'delete': delete})
