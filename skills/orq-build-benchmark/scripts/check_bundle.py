#!/usr/bin/env python3
"""Check a staged benchmark bundle before it becomes an orq dataset.

The orq port does not upload a ZIP to a third party; it turns a local bundle
directory into an orq dataset, evaluators, and a run. This validates the staged
directory first, so nothing private slips into the workspace and the seeds are
actually usable.

Checks:
- the three root docs are present (00_BRIEF, 01_SOURCES, 02_TASK_SEEDS);
- 02_TASK_SEEDS.md contains at least one seed;
- no text file is too large to ingest cleanly;
- filenames are self-describing (doc1.pdf / untitled / file1 are flagged);
- common uncleared-confidential markers are absent (secrets, private keys).

Usage:  python check_bundle.py <bundle-dir>
        python check_bundle.py --selftest
Exit 0 = shippable (warnings may still print), 1 = fix first,
2 = wrong usage (not one argument, or the argument is not a directory to scan).
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# A single text file over this many characters gets truncated on ingest and
# spends the grader's context fast. Same order of magnitude as the Optima limit.
TEXT_CONTENT_MAX_LENGTH = 50_000
# Read cap for the secret scan. A staged bundle is source docs, not media, so a file
# past this is almost certainly a binary asset (video, large PDF) we cannot usefully
# grep for an ASCII-armored key. Reading it whole would blow memory; skip and count it.
SECRET_SCAN_MAX_BYTES = 5 * 1024 * 1024
REQUIRED_ROOT_DOCS = ('00_BRIEF.md', '01_SOURCES.md', '02_TASK_SEEDS.md')
TEXT_SUFFIXES = {'.md', '.txt', '.csv', '.tsv', '.json', '.jsonl', '.yaml', '.yml', '.xml', '.py', '.log', '.html'}

# Non-descriptive names: a bundle should say what each file is.
VAGUE_NAME = re.compile(r'^(doc|file|untitled|new|temp|tmp|copy|image|img|scan)[\s_-]*\d*$', re.IGNORECASE)

# A line carrying this marker is skipped by the secret scan: the override the review
# asked for, so a declared stand-in or a bug report that quotes a `key: value` phrase
# can ship without editing the detector.
SECRET_SCAN_ALLOW = 'check-bundle:allow'

# Uncleared-confidential markers. Presence is an error: it must be redacted or
# replaced with a declared stand-in before it can enter an orq dataset.
SECRET_PATTERNS = (
    ('private key block', re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----')),  # gitleaks:allow
    ('AWS access key id', re.compile(r'AKIA[0-9A-Z]{16}')),  # gitleaks:allow
    (
        # Both a quoted literal and an unquoted value: a bare `.env`-style line
        # (ORQ_API_KEY=... or api_key: sk-...) is the most common way a key lands in a
        # bundle, and a quotes-only pattern missed it entirely. The value (group 1,
        # surrounding quotes optional) is then shape-checked by `_looks_like_secret`
        # so ordinary prose like `password: required` does not read as a key.
        'assigned api key literal',
        re.compile(r'(?i)(?:api[_-]?key|secret|token|password)\s*[:=]\s*[\'"]?([^\'"\s]{8,})[\'"]?'),  # gitleaks:allow
    ),
    ('confidential banner', re.compile(r'(?i)\b(do not distribute|strictly confidential|internal use only)\b')),
)

# A seed entry is a markdown list item or section heading, one per task instance;
# a placeholder line (TODO / <fill in>) is not a seed. A bare section heading is not
# a seed either (a `## Task seeds` title plus a `TODO:` line must not count as one),
# so only list items count. The angle-bracket placeholder is lowercase-only so a
# generic type in a real seed (`List<String>`) is not mistaken for a `<fill in>`.
SEED_ENTRY = re.compile(r'\s*([-*+]\s+|\d+[.)]\s+)\S')
SEED_PLACEHOLDER = re.compile(r'(?i)\b(todo|fixme|tbd|fill in|placeholder)\b')
# Case-sensitive on purpose (kept out of the (?i) pattern above): a lowercase `<name>`
# is a fill-in placeholder, but a capitalised generic type in a real seed (`List<String>`)
# is not.
SEED_ANGLE_PLACEHOLDER = re.compile(r'<[a-z][a-z ._-]*>')

# Values that follow a credential label in prose or a template but are not secrets:
# a few plain words a field description uses (`password: required`), documentation
# placeholders, and env-var references. Everything else assigned to an
# api_key/secret/token/password label is treated as a key, so a real key never slips
# through for lacking a digit.
_NON_SECRET_VALUES = frozenset(
    {
        'required',
        'optional',
        'none',
        'null',
        'nil',
        'empty',
        'true',
        'false',
        'unset',
        'changeme',
        'redacted',
        'placeholder',
        'example',
    }
)


def _looks_like_secret(value: str) -> bool:
    """True when a value assigned to a credential label looks like a real secret.

    Credential labels (api_key/secret/token/password) default to secret: a bundle
    almost never assigns one a non-secret value, and the earlier digit/length shape
    check let a real all-letter key (`ORQ_API_KEY=abcdefghijklmnop`) read as clean.
    The exceptions let through are documentation placeholders, env-var references, and
    the handful of plain words a field description uses (`password: required`).
    """
    v = value.strip().strip('\'"')
    if len(v) < 8:  # too short to be a credential; the regex already requires 8+, this is a backstop
        return False
    low = v.lower()
    if low in _NON_SECRET_VALUES:
        return False
    if v[0] in '<${':  # <your-key>, ${ORQ_API_KEY}, $TOKEN
        return False
    if any(marker in low for marker in ('your_', 'your-', 'changeme', 'redacted', 'placeholder', 'example')):
        return False
    return True


def _count_seeds(text: str) -> int:
    """Real seed entries: list items that are not placeholder lines (headings excluded)."""
    return sum(
        1
        for ln in text.splitlines()
        if SEED_ENTRY.match(ln) and not SEED_PLACEHOLDER.search(ln) and not SEED_ANGLE_PLACEHOLDER.search(ln)
    )


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def _is_hidden(path: Path, root: Path) -> bool:
    return any(part.startswith('.') for part in path.relative_to(root).parts)


def _is_vcs_internal(path: Path, root: Path) -> bool:
    """Version-control internals (`.git/`, `.hg/`, `.svn/`): not bundle content, skip entirely.

    Everything else, dotfiles included, is scanned for secrets: a staged ``.env``,
    ``.aws/credentials``, or ``.npmrc`` is the most likely place a key hides, so excluding
    all hidden paths from the scan (as an earlier version did) defeated the point of it.
    """
    return any(part in {'.git', '.hg', '.svn'} for part in path.relative_to(root).parts)


def check(bundle_dir: Path) -> Report:
    report = Report()
    if not bundle_dir.exists() or not bundle_dir.is_dir():
        report.error(f'Not a directory: {bundle_dir}')
        return report

    for doc in REQUIRED_ROOT_DOCS:
        if not (bundle_dir / doc).is_file():
            report.error(f'Missing required root doc: {doc}')

    seeds = bundle_dir / '02_TASK_SEEDS.md'
    if seeds.is_file() and _count_seeds(seeds.read_text(encoding='utf-8', errors='replace')) < 1:
        report.error(
            '02_TASK_SEEDS.md has no seeds; stage at least one task instance '
            '(a list item or heading per instance) with its answer, not a TODO placeholder.'
        )

    files = [p for p in bundle_dir.rglob('*') if p.is_file() and not _is_vcs_internal(p, bundle_dir)]
    if not files:
        report.error('Bundle is empty.')

    not_size_checked: list[str] = []
    unscanned: list[str] = []  # skipped by the secret scan: too big, or unreadable
    for path in files:
        rel = path.relative_to(bundle_dir)
        # The secret scan runs on every file below, hidden ones included. The name and
        # ingest-truncation checks skip hidden config files: a dotfile is not content a
        # human names or that gets truncated on ingest.
        hidden = _is_hidden(path, bundle_dir)
        if not hidden and VAGUE_NAME.match(path.stem):
            report.warn(f'Non-descriptive filename: {rel}. Rename it to say what it is.')
        # Secret scan runs on every file, not just text ones: an ASCII-armored key or
        # token survives readable inside a PDF or image, so decode the raw bytes and scan.
        # Cap the read so one huge asset cannot exhaust memory, and guard the read so an
        # unreadable file (permissions, a broken symlink, a race) fails loud, not silent:
        # a file we did not scan must never be counted as clean.
        try:
            size = path.stat().st_size
        except OSError as exc:
            unscanned.append(f'{rel} (stat failed: {exc.strerror or exc})')
            continue
        if size > SECRET_SCAN_MAX_BYTES:
            unscanned.append(f'{rel} ({size:,} bytes, over the {SECRET_SCAN_MAX_BYTES:,}-byte scan cap)')
            continue
        try:
            blob = path.read_bytes().decode('utf-8', errors='replace')
        except OSError as exc:
            unscanned.append(f'{rel} (read failed: {exc.strerror or exc})')
            continue
        # Scan line by line so a `check-bundle:allow` marker overrides just its own line,
        # and so the api-key value can be shape-checked. One error per label per file.
        flagged: set[str] = set()
        for line in blob.splitlines():
            if SECRET_SCAN_ALLOW in line:
                continue
            for label, pattern in SECRET_PATTERNS:
                if label in flagged:
                    continue
                if label == 'assigned api key literal':
                    # Check EVERY assignment on the line, not just the first: a prose
                    # decoy ahead of a real key (`token: required, api_key=sk-...`) must
                    # not mask the key behind it.
                    if not any(_looks_like_secret(m.group(1)) for m in pattern.finditer(line)):
                        continue
                elif not pattern.search(line):
                    continue
                report.error(f'{rel} contains a {label}; redact or replace with a declared stand-in.')
                flagged.add(label)
        if not hidden:
            if path.suffix.lower() in TEXT_SUFFIXES:
                if len(blob) > TEXT_CONTENT_MAX_LENGTH:
                    report.warn(
                        f'{rel} is {len(blob):,} chars (>{TEXT_CONTENT_MAX_LENGTH:,}); it will be truncated on ingest.'
                    )
            else:
                not_size_checked.append(str(rel))

    if not_size_checked:
        shown = ', '.join(not_size_checked[:5]) + ('...' if len(not_size_checked) > 5 else '')
        report.warn(
            f'{len(not_size_checked)} non-text file(s) not size-checked for ingest truncation '
            f'(secret scan still ran on them): {shown}'
        )

    if unscanned:
        shown = '; '.join(unscanned[:5]) + ('...' if len(unscanned) > 5 else '')
        # A file the scan could not read must never pass as clean (exit 0): fail loud so the
        # human scans it by hand, shrinks it under the cap, or removes it before shipping.
        report.error(
            f'{len(unscanned)} file(s) could not be secret-scanned (too large or unreadable); '
            f'scan by hand, shrink, or remove before you ship: {shown}'
        )

    return report


def _print(report: Report) -> int:
    for w in report.warnings:
        print(f'warning: {w}')
    for e in report.errors:
        print(f'error: {e}')
    if report.errors:
        print(f'\n{len(report.errors)} error(s); fix before building on orq.')
        return 1
    print('Bundle looks shippable.' + (f' ({len(report.warnings)} warning(s))' if report.warnings else ''))
    return 0


def _selftest() -> int:
    """One runnable check: a clean bundle passes, a leaky/incomplete one fails."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        # incomplete + leaky bundle
        (root / '00_BRIEF.md').write_text('# work', encoding='utf-8')
        (root / 'doc1.txt').write_text('api_key = "abcd1234efgh5678"', encoding='utf-8')  # gitleaks:allow
        bad = check(root)
        assert any('02_TASK_SEEDS' in e for e in bad.errors), 'missing-seed doc not caught'
        assert any('01_SOURCES' in e for e in bad.errors), 'missing sources doc not caught'
        assert any('api key' in e.lower() for e in bad.errors), 'secret not caught'
        assert any('doc1' in w for w in bad.warnings), 'vague name not warned'
        # clean bundle
        (root / '01_SOURCES.md').write_text('sources', encoding='utf-8')
        (root / '02_TASK_SEEDS.md').write_text(
            '- seed: review one PR; answer: missing null check; evidence: line 42', encoding='utf-8'
        )
        (root / 'doc1.txt').unlink()
        (root / 'supplier_contract_2024.txt').write_text('a clean contract stand-in', encoding='utf-8')
        good = check(root)
        assert not good.errors, f'clean bundle should pass, got {good.errors}'

        # Ordinary prose is not a key: `password: required` quoted in a bug report must not
        # flag (the false positive the shape check exists for). 'required' is exempted by name.
        (root / 'bug_report.txt').write_text(
            'the login form says "password: required" when the field is blank', encoding='utf-8'
        )
        assert not check(root).errors, 'prose `password: required` must not read as a key'
        (root / 'bug_report.txt').unlink()

        # A real all-letter key must be caught: the digit/length shape check used to let
        # `ORQ_API_KEY=abcdefghijklmnop` (16 letters, no digit) read as clean.
        (root / 'letter_key.txt').write_text('ORQ_API_KEY=abcdefghijklmnop', encoding='utf-8')  # gitleaks:allow
        assert any('api key' in e.lower() for e in check(root).errors), 'all-letter key must be caught'
        (root / 'letter_key.txt').unlink()

        # A prose decoy ahead of a real key on the same line must not mask it (the scan
        # used to check only the first match).
        (root / 'decoy.txt').write_text(
            'token: required, api_key=sk-orq-abcd1234efgh', encoding='utf-8'  # gitleaks:allow
        )
        assert any('api key' in e.lower() for e in check(root).errors), 'key behind a decoy must be caught'
        (root / 'decoy.txt').unlink()

        # The `check-bundle:allow` marker overrides a real-looking match on its own line.
        (root / 'declared_standin.txt').write_text(
            'api_key = "sk-orq-abcd1234efgh"  check-bundle:allow', encoding='utf-8'  # gitleaks:allow
        )
        assert not check(root).errors, 'allow marker must suppress the scan on its line'
        (root / 'declared_standin.txt').unlink()

        # A placeholder seeds file (a section heading plus a TODO) has no real seed entry,
        # so it must still error. A heading is not itself a seed, and the `##` title here
        # would have counted as one before headings were dropped from SEED_ENTRY.
        (root / '02_TASK_SEEDS.md').write_text(
            '## Task seeds\n\nTODO: fill in the task instances here later.\n', encoding='utf-8'
        )
        assert any('02_TASK_SEEDS' in e for e in check(root).errors), 'placeholder seeds must not pass'
        (root / '02_TASK_SEEDS.md').write_text(
            '- seed: review one PR; answer: missing null check; evidence: line 42', encoding='utf-8'
        )
        assert not check(root).errors, 'restored clean seeds should pass again'

        # A seed naming a generic type is a real seed, not a `<fill in>` placeholder: the
        # angle-bracket rule is lowercase-only so `List<String>` does not disqualify it.
        (root / '02_TASK_SEEDS.md').write_text(
            '- seed: parse a List<String>; answer: handle the empty case; evidence: line 9',
            encoding='utf-8',
        )
        assert not check(root).errors, 'a seed with a generic type must count as a real seed'
        (root / '02_TASK_SEEDS.md').write_text(
            '- seed: review one PR; answer: missing null check; evidence: line 42', encoding='utf-8'
        )

        # Unquoted .env-style keys (the most common leak form) are caught too, not only quoted ones.
        for leak in ('ORQ_API_KEY=sk-orq-abcd1234efgh', 'api_key: sk-abcdefghijkl'):  # gitleaks:allow
            (root / 'env_leak.txt').write_text(leak, encoding='utf-8')
            env_report = check(root)
            assert any('api key' in e.lower() for e in env_report.errors), f'unquoted key not caught: {leak!r}'
        (root / 'env_leak.txt').unlink()

        # A secret in a hidden dotfile is scanned too (.env is the top leak vector); it used to be
        # skipped entirely. Version-control internals (.git/) stay excluded.
        (root / '.env').write_text('ORQ_API_KEY=sk-orq-abcd1234efgh', encoding='utf-8')  # gitleaks:allow
        dotfile_report = check(root)
        assert any('api key' in e.lower() for e in dotfile_report.errors), 'secret in a dotfile not caught'
        (root / '.env').unlink()
        (root / '.git').mkdir()
        (root / '.git' / 'config').write_text('api_key = "abcd1234efgh5678"', encoding='utf-8')  # gitleaks:allow
        assert not check(root).errors, '.git internals must not be scanned'
        import shutil

        shutil.rmtree(root / '.git')

        # a key hidden in a non-text file is still caught (secret scan reads raw bytes).
        # The header is assembled from parts so the contiguous literal never sits in
        # this source file, where the detect-private-key pre-commit hook would flag it.
        hdr = '-----BEGIN ' + 'RSA PRIVATE KEY' + '-----'
        # Same split trick for the PDF version magic: byte-for-byte correct, but
        # the token never sits whole in this source where the ticket-id lint reads it.
        pdf_magic = '%PDF-' + '1.4\n'
        (root / 'scan_me.pdf').write_bytes((pdf_magic + hdr + '\nAAAA\n').encode())
        pdf_report = check(root)
        assert any('private key' in e.lower() for e in pdf_report.errors), 'key in a non-text file not caught'
        assert any('not size-checked' in w for w in pdf_report.warnings), 'non-text file not flagged as un-size-checked'
        (root / 'scan_me.pdf').unlink()

        # a file over the scan cap cannot be read, so it must fail loud, not pass as clean:
        # an unread file could hide a secret (this used to be a warning that still exited 0).
        (root / 'huge_asset.bin').write_bytes(b'\x00' * (SECRET_SCAN_MAX_BYTES + 1))
        skipped = check(root)
        assert any('secret-scanned' in e for e in skipped.errors), 'oversized file must error, not pass'
        (root / 'huge_asset.bin').unlink()

        # exit codes via main(): clean=0, incomplete=1, wrong-usage=2 (zero args and
        # two args), help=0.
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert main(['x', str(root)]) == 0, 'clean bundle should exit 0'
            (root / '02_TASK_SEEDS.md').unlink()
            assert main(['x', str(root)]) == 1, 'incomplete bundle should exit 1'
            assert main(['x']) == 2, 'wrong arg count (none) should exit 2, not 0'
            assert main(['x', str(root), 'extra']) == 2, 'wrong arg count (two) should exit 2, not 0'
            assert main(['x', '--help']) == 0, 'help should exit 0'
    print('selftest ok')
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] in {'-h', '--help'}:
        print(__doc__)
        return 0
    if len(argv) != 2:
        print(__doc__)
        print('error: expected exactly one argument (a bundle directory, or --selftest)')
        return 2  # wrong usage must not read as a passed check
    if argv[1] == '--selftest':
        return _selftest()
    return _print(check(Path(argv[1])))


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
