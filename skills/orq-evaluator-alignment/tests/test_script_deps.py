"""Every step script's PEP 723 block must cover what the script imports.

`uv run scripts/<step>.py` builds an isolated environment from the inline
`# /// script` metadata and nothing else, so a third-party import missing from
that block is a `ModuleNotFoundError` at launch — on the *documented* command,
not on some edge path. `serve_annotation.py` shipped declaring only `fire` while
importing `loguru`, which meant the annotation UI (the fallback the grey-zone
flow falls back TO) could not start at all.

The rest of the suite never catches this: pytest runs with every dependency
installed, so the declared set is unverified in exactly the environment that
matters. This test reads the block and the imports and compares them, per script.

Pure stdlib; no orq/evaluatorq import.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_ROOT / 'scripts'

sys.path.insert(0, str(SCRIPTS_DIR))

# Modules that need no declaration: the stdlib, the skill's own `lib.*` (reached
# through `_bootstrap`), and any sibling step script — `retest.py` importing
# `stability` is a file next to it, not a distribution.
_FIRST_PARTY = frozenset({'_bootstrap', 'lib'}) | {p.stem for p in SCRIPTS_DIR.glob('*.py')}

# Distribution name -> the module it actually installs, where they differ.
_MODULE_BY_DIST = {'python-dotenv': 'dotenv'}

_PEP723 = re.compile(r'^# /// script\s*$(.*?)^# ///\s*$', re.MULTILINE | re.DOTALL)


def _script_paths() -> list[Path]:
    return sorted(p for p in SCRIPTS_DIR.glob('*.py') if p.name != '_bootstrap.py')


def _declared_modules(source: str) -> set[str] | None:
    """Module names the script's PEP 723 block makes importable, or None if absent."""
    match = _PEP723.search(source)
    if match is None:
        return None
    body = ''.join(line.removeprefix('# ').removeprefix('#') for line in match.group(1).splitlines(keepends=True))
    meta = tomllib.loads(body)
    modules = set()
    for spec in meta.get('dependencies', []):
        dist = re.split(r'[<>=!~\[ ]', spec, maxsplit=1)[0].strip()
        modules.add(_MODULE_BY_DIST.get(dist, dist.replace('-', '_')))
    return modules


def _imported_modules(source: str) -> set[str]:
    """Top-level module names the script imports at module scope or inside functions."""
    modules = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module.split('.')[0])
    return modules


@pytest.mark.parametrize('path', _script_paths(), ids=lambda p: p.name)
def test_script_declares_every_third_party_import(path: Path):
    source = path.read_text(encoding='utf-8')
    declared = _declared_modules(source)
    assert declared is not None, f'{path.name} has no PEP 723 block, so `uv run` cannot resolve its deps'

    third_party = {
        name for name in _imported_modules(source)
        if name not in _FIRST_PARTY and name not in sys.stdlib_module_names
    }
    missing = sorted(third_party - declared)
    assert not missing, (
        f'{path.name} imports {missing} but does not declare them in its PEP 723 block — '
        f'`uv run scripts/{path.name}` will fail with ModuleNotFoundError. Declared: {sorted(declared)}'
    )


def test_the_guard_sees_a_missing_declaration():
    # Pins the check itself: strip loguru from a real script's block and the
    # comparison must notice. Without this the test above passes vacuously if the
    # PEP 723 parser ever stops matching.
    source = (SCRIPTS_DIR / 'metrics.py').read_text(encoding='utf-8')
    stripped = source.replace('#     "loguru>=0.7.3",\n', '')
    assert 'loguru' in _imported_modules(stripped)
    assert 'loguru' not in _declared_modules(stripped)
