"""Make `lib` importable when a step script is run directly.

Every step does `from _bootstrap import SKILL_ROOT` (or just imports it) before
importing `lib.*`, so the scripts run without installing the skill as a package.
"""

from __future__ import annotations

import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

# Windows consoles default to cp1252, which cannot encode the ✓/✗/→ status
# glyphs the step scripts print — a raw `print('✓ ...')` there raises
# UnicodeEncodeError and crashes the process (e.g. serve_annotation.py's
# summary line, which runs *after* labels are already saved). Force UTF-8 on
# the std streams so status output is safe. Every step imports _bootstrap
# before printing, so this one place covers them all.
if sys.platform == 'win32':
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass  # non-TextIOWrapper (redirected/captured) — nothing to do
