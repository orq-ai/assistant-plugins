# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "fire>=0.7.0",
#     "loguru>=0.7.3",
# ]
# ///
"""Step 6/7 — grey-zone assessment glue (RES-980).

The clustering, question generation, and the chat Q&A are done by the *conductor*
(SKILL.md), not here. This CLI only moves data in and out of the run directory
around that conversation, over the pure helpers in `lib/grey_zone.py`:

    uv run scripts/grey_zone.py assemble --run_dir runs/<key>_<ts>
        queue.json → grey_zone_payload.json  (the bounded confuser payload the
        conductor reads into context to open-code grey zones and ask questions)

    uv run scripts/grey_zone.py apply --run_dir runs/<key>_<ts>
        grey_zone_policy.json → aggregated.md (the free-text policy guidance the
        EXISTING rewrite_eval.py already consumes — no rewrite-side change needed)

`grey_zone_policy.json` itself is written by the conductor between these two
steps; `apply` validates it and renders the guidance. Its per-point labels are
read directly by retest.py (grey-zone default, annotations.json fallback).
"""

from __future__ import annotations

import fire
from loguru import logger

import _bootstrap  # noqa: F401
from lib import grey_zone as gzlib
from lib import runner


def _resolve(run_dir: str | None, cfg: dict) -> object:
    out_dir = runner.resolve_run_dir(run_dir) if run_dir else runner.latest_run_dir(cfg.get('runs_dir', 'runs'))
    if out_dir is None:
        raise SystemExit('No run directory. Run build_queue.py first.')
    return out_dir


def assemble(
    run_dir: str | None = None,
    config: str = 'config.toml',
    top_k: int | None = None,
    max_chars: int | None = None,
) -> str:
    """queue.json → grey_zone_payload.json (the conductor's bounded confuser payload).

    `top_k` caps how many (most-unstable-first) confusers enter context; defaults
    to config `grey_zone_top_k` (absent → all). `max_chars` truncates each judged
    input (default config `grey_zone_max_chars` → 600).
    """
    cfg = runner.load_config(config)
    out_dir = _resolve(run_dir, cfg)

    queue = runner.read_json(out_dir / 'queue.json')
    resolved_top_k = top_k if top_k is not None else cfg.get('grey_zone_top_k')
    resolved_max = int(max_chars if max_chars is not None else cfg.get('grey_zone_max_chars', 600))

    payload = gzlib.assemble_payload(queue, top_k=resolved_top_k, max_chars=resolved_max)
    runner.write_json(out_dir / 'grey_zone_payload.json', payload)
    logger.info(
        f'✓ Wrote {out_dir / "grey_zone_payload.json"}: {payload["n_confusers"]} confuser(s) '
        'for grey-zone clustering.'
    )
    print(out_dir)
    return str(out_dir)


def apply(run_dir: str | None = None, config: str = 'config.toml') -> str:
    """grey_zone_policy.json → aggregated.md (guidance rewrite_eval already reads).

    Validates the conductor-written policy first (fail loud on a malformed policy
    or a numeric label missing its tolerance band), then renders the resolved
    grey-zone rules as the free-text guidance.
    """
    cfg = runner.load_config(config)
    out_dir = _resolve(run_dir, cfg)

    policy_path = out_dir / 'grey_zone_policy.json'
    if not policy_path.exists():
        raise SystemExit(
            f'No grey_zone_policy.json in {out_dir}. The conductor writes it from the '
            'grey-zone Q&A before this step.'
        )
    policy = runner.read_json(policy_path)
    gzlib.validate_policy(policy)

    guidance = gzlib.policy_to_guidance(policy)
    (out_dir / 'aggregated.md').write_text(guidance, encoding='utf-8')
    logger.info(
        f'✓ Wrote {out_dir / "aggregated.md"} from {len(policy.get("grey_zones", []))} grey zone(s) '
        f'/ {len(policy.get("labels", []))} policy label(s). rewrite_eval.py can run next.'
    )
    print(out_dir)
    return str(out_dir)


if __name__ == '__main__':
    fire.Fire({'assemble': assemble, 'apply': apply})
