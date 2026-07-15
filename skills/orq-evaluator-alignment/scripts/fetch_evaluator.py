# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "evaluatorq>=1.4.0",
#     "fire>=0.7.0",
#     "httpx>=0.27",
#     "loguru>=0.7.3",
#     "python-dotenv>=1.2.1",
#     "tenacity>=8.0",
# ]
# ///
"""Step 1 — fetch the evaluator under audit and pin its config.

GET /v2/evaluators/{id}, assert it is a boolean single-judge evaluator (V1
scope), extract the judge prompt, judge model, output type, and declared
`{{...}}` variables, and write `evaluator.json` into a fresh run directory.
The declared variable set is stored so step 9a can enforce variable
preservation on the rewrite.

Usage:
    cd skills/orq-evaluator-alignment
    uv run scripts/fetch_evaluator.py --evaluator_id <24-hex-id>
    # if the judge model can't be auto-resolved (run dir shows model-unknown):
    uv run scripts/fetch_evaluator.py --evaluator_id <id> --judge_model mistral-large-latest
"""

from __future__ import annotations

import asyncio
import re

import fire
from dotenv import load_dotenv
from loguru import logger

import _bootstrap  # noqa: F401  (path setup)
from lib import runner
from lib.orq_client import EvaluatorNotFound, OrqClient

load_dotenv()

_UUID_RE = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.IGNORECASE)


async def _fetch(evaluator_id: str, judge_model_override: str | None = None) -> dict:
    async with OrqClient() as client:
        cfg = await client.get_evaluator(evaluator_id)
        # Resolve a routable judge-model slug up front. Priority:
        #   1. explicit --judge_model override
        #   2. the config model id resolved via GET /v2/models (registry UUID
        #      -> slug), so stability can run even when production spans don't
        #      record the model (step 2 refines this from traces if they do)
        #   3. the raw config value (opaque UUID) as a last resort
        judge_model = judge_model_override or cfg.judge_model
        if not judge_model_override and cfg.judge_model and _UUID_RE.fullmatch(cfg.judge_model):
            slug = await client.resolve_model_slug(cfg.judge_model)
            if slug:
                judge_model = slug
    return {
        'id': cfg.id,
        'key': cfg.key,
        'prompt': cfg.prompt,
        # `judge_model_id` preserves the opaque config id; `judge_model` is the
        # routable slug (override > registry-resolved > opaque). Step 2
        # (fetch_traces) overwrites it with the model seen on production spans
        # when those record one; otherwise this resolved slug is kept.
        'judge_model': judge_model,
        'judge_model_id': cfg.judge_model,
        'output_type': cfg.output_type,
        'variables': cfg.variables,
        'raw': cfg.raw,
    }


def main(
    evaluator_id: str | None = None,
    config: str = 'config.toml',
    run_dir: str | None = None,
    with_traces: bool = True,
    trace_limit: int = 200,
    judge_model: str | None = None,
) -> str:
    """Fetch an evaluator and create its run directory.

    By default this also chains straight into the trace fetch (``with_traces``),
    scanning the most recent ``trace_limit`` traces, so a single command confirms
    the evaluator in one shot: its declared variables and judge prompt (this
    step) plus the candidate datapoint count and the *real* judge model resolved
    from the production spans (the trace step). The cost/setup GATE still comes
    afterwards (step 3), so nothing expensive runs here — trace fetch is
    read-only. Pass ``--no-with_traces`` to fetch only the evaluator, or rerun
    ``fetch_traces.py --trace_limit <N>`` later to pull more data.

    Args:
        evaluator_id: orq evaluator id (24-hex). Falls back to `evaluator_id`
            in the config when omitted.
        config: Path to the TOML config (relative to skill/ or absolute).
        run_dir: Optional existing run directory to write into. Omit to create
            a fresh `runs/<key>_<ts>/`.
        with_traces: Auto-run the trace fetch after writing evaluator.json
            (default True). The evaluator is already saved if the trace fetch
            fails, so you can retry traces without re-fetching the evaluator.
        trace_limit: Scan depth for the chained trace fetch (default 200).
        judge_model: Explicit judge-model slug override (e.g.
            ``mistral-large-latest``). Use this when the evaluator's config
            model can't be resolved to a routable slug AND production spans
            don't record one — otherwise ``judge_model`` stays an opaque id and
            step 4 (stability) can't route the judge. See "Judge-model
            resolution" below. Normally unnecessary: the config UUID is resolved
            automatically via GET /v2/models.

    Judge-model resolution (why this can be empty/opaque):
        The stability run must re-invoke the judge, which needs a routable model
        slug. That slug is sourced in priority order:
          1. ``--judge_model`` override (this arg).
          2. The evaluator's config model id resolved via GET /v2/models
             (workspace registry UUID -> slug). Done automatically here.
          3. The model seen on the production judge spans
             (``gen_ai.request.model``), read in step 2.
        A judge model comes out EMPTY/opaque only when ALL three miss: the
        config stores a registry UUID that isn't in /v2/models (deleted or
        cross-workspace model), and the evaluator's spans don't stamp
        ``gen_ai.request.model`` (common — evaluator spans record the judge's
        input/output but not always the resolved model). In that case rerun with
        ``--judge_model <slug>``, or set ``evaluator.json["judge_model"]`` to a
        real slug before step 4.

    Returns:
        The run directory path (printed for the conductor / next step).
    """
    cfg = runner.load_config(config)
    evaluator_id = evaluator_id or cfg.get('evaluator_id') or ''
    if not evaluator_id:
        raise SystemExit(
            'No evaluator id. Pass --evaluator_id <id> or set `evaluator_id` in config.toml.'
        )

    try:
        evaluator = asyncio.run(_fetch(evaluator_id, judge_model_override=judge_model))
    except EvaluatorNotFound as exc:
        raise SystemExit(str(exc)) from exc

    output_type = (evaluator['output_type'] or '').lower()
    if output_type != 'boolean':
        raise SystemExit(
            f'Evaluator {evaluator_id} has output_type={evaluator["output_type"]!r}. '
            'V1 supports boolean Pass/Fail judges only (design §1, §8). Stopping.'
        )
    if not evaluator['prompt']:
        raise SystemExit(
            f'Evaluator {evaluator_id} returned an empty judge prompt — nothing to audit. '
            'Confirm this is an LLM-judge (llm_eval) evaluator.'
        )

    key = evaluator['key'] or runner.slugify(evaluator_id)
    out_dir = (
        runner.resolve_run_dir(run_dir)
        if run_dir
        else runner.new_run_dir(key, cfg.get('runs_dir', 'runs'))
    )
    runner.write_json(out_dir / 'evaluator.json', evaluator)

    jm, jm_id = evaluator['judge_model'], evaluator['judge_model_id']
    logger.info(f'✓ Evaluator {evaluator["id"]} (key={key!r})')
    if jm and jm != jm_id:
        logger.info(f'  judge model:    {jm} (resolved from config id {jm_id})')
    else:
        logger.warning(
            f'  judge model:    UNRESOLVED (config id {jm_id or "<none>"}). Step 2 will try the '
            'production spans; if that also misses, rerun with --judge_model <slug>.'
        )
    logger.info(f'  variables:   {evaluator["variables"]}')
    logger.info(f'  run dir:     {out_dir}')

    if with_traces:
        # Chain straight into the trace fetch so one command confirms the
        # evaluator, the candidate datapoint count, and the resolved judge
        # model. evaluator.json is already on disk, so a trace-fetch failure
        # (empty scan window, network) leaves the run recoverable — the
        # operator just reruns fetch_traces.py with a wider --trace_limit.
        import fetch_traces  # sibling script; sys.path set by _bootstrap

        logger.info(f'→ Fetching up to {trace_limit} recent traces for this evaluator...')
        try:
            # fetch_traces renames the run dir to embed the model + set size once
            # both are known, so adopt its returned path for the final print.
            renamed = fetch_traces.main(run_dir=str(out_dir), config=config, trace_limit=trace_limit)
            out_dir = runner.resolve_run_dir(renamed)
        except SystemExit as exc:  # fetch_traces raises this on an empty result
            logger.warning(
                f'⚠ Trace fetch found nothing: {exc}\n'
                '  The evaluator is saved. Rerun with a wider window once ready:\n'
                f'    uv run scripts/fetch_traces.py --run_dir {out_dir} --trace_limit 2000'
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                '✗ Trace fetch failed (the evaluator is still saved). Retry with:\n'
                f'    uv run scripts/fetch_traces.py --run_dir {out_dir}'
            )

    print(out_dir)
    return str(out_dir)


if __name__ == '__main__':
    fire.Fire(main)
