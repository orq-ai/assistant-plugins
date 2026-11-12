"""Render a frozen baseline.json as one self-contained HTML page (stdlib only, system fonts).

Layout follows Warp skill-doctor's report: header, scorecard, then findings. Every string that
comes from a transcript, grader or skill is untrusted and goes through `esc`.
"""

from __future__ import annotations

import html
from typing import Any

METRICS = (('efficiency', 1.0), ('quality', 1.0), ('skill_compliance', 5.0))
# fixed weights and cutoffs, a judgment call; calibrate against human letter grades once some exist
WEIGHTS = {'quality': 0.5, 'efficiency': 0.25, 'skill_compliance': 0.25}
LETTERS = ((0.9, 'A'), (0.8, 'B'), (0.7, 'C'), (0.6, 'D'), (0.0, 'F'))
CAUSES = {
    'skill_missing': 'Missing from the skill',
    'skill_wrong': 'Skill says the wrong thing',
    'skill_ambiguous': 'Skill is unclear',
    'trigger': 'Skill loaded at the wrong time',
    'ignored_rule': 'Agent skipped a correct rule',
}
CAPS = {'p0': 'D', 'p1': 'B'}  # a filed issue at this priority caps the letter

CSS = """
* { box-sizing: border-box; }
body { font-family: ui-sans-serif, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif;
  color: #12302f; background: #fff; max-width: 1040px; margin: 0 auto; padding: 40px 24px; line-height: 1.5;
  font-size: 14px; }
code, pre, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }
h1 { color: #025558; margin: 4px 0; font-size: 30px; }
h2 { color: #025558; margin: 36px 0 8px; font-size: 19px; border-bottom: 2px solid #025558; padding-bottom: 4px; }
h3 { margin: 18px 0 4px; font-size: 15px; }
a { color: #025558; }
.tag { color: #ff8f34; font-weight: 600; text-transform: uppercase; letter-spacing: .12em; font-size: 11px; }
.muted { color: #5b6f6e; font-size: 12px; }
.scorecard { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px; background: #c9d9d8;
  border: 1px solid #c9d9d8; margin-top: 18px; }
.grade { display: flex; gap: 28px; align-items: center; margin-top: 18px; }
.grade .letter { font-size: 96px; font-weight: 800; line-height: 1; color: #fff; background: #025558;
  width: 140px; height: 140px; display: flex; align-items: center; justify-content: center; }
.grade.F .letter, .grade.D .letter { background: #b2440b; }
details > summary { cursor: pointer; color: #025558; font-weight: 600; font-size: 19px; margin-top: 36px; }
.metric, .stat { background: #fff; padding: 16px 20px; }
.metric .num { font-size: 30px; font-weight: 700; color: #025558; }
.track { height: 6px; background: #e6eeee; margin-top: 6px; }
.fill { height: 100%; background: #ff8f34; }
.stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; background: #c9d9d8;
  border: 1px solid #c9d9d8; border-top: none; }
.stat { background: #f3f7f7; } .stat .num { font-size: 22px; font-weight: 700; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { text-align: left; padding: 5px 8px; border-bottom: 1px solid #e0e8e8; vertical-align: top; }
th { color: #025558; font-weight: 600; }
.failed { color: #b2440b; font-weight: 600; }
.card { border: 1px solid #c9d9d8; border-left: 4px solid #025558; padding: 10px 14px; margin: 12px 0; }
.card.dont { border-left-color: #ff8f34; }
pre { background: #f3f7f7; padding: 8px 10px; white-space: pre-wrap; margin: 6px 0; }
blockquote { margin: 4px 0; padding: 2px 10px; border-left: 3px solid #ff8f34; background: #fff8f1; }
"""


def esc(value: Any) -> str:
    return html.escape('' if value is None else str(value))


def number(value: float | None, digits: int = 2) -> str:
    return '-' if value is None else f'{value:.{digits}f}'


def verdict(record: dict[str, Any] | None) -> str:
    if not record:
        return '<span class="muted">-</span>'
    return f'{esc(record["label"])} <span class="muted">{number(record["score"], 1)}</span>'


def instruction(issue: dict[str, Any], texts: dict[str, list[str]]) -> str:
    where = issue['instruction']
    lines = texts.get(where['path'], [])[where['start_line'] - 1 : where['end_line']]
    quoted = f'<pre>{esc(chr(10).join(lines))}</pre>' if lines else ''
    return f'<li>Rule at <span class="mono">{esc(where["path"])}:{where["start_line"]}-{where["end_line"]}</span>{quoted}</li>'


def evidence(issue: dict[str, Any]) -> str:
    """Quotes folded by default; the claim and fix carry the card."""
    quotes = ''.join(
        f'<blockquote>{esc(p["quote"])}<div class="muted">{esc(p["session_id"])} step {p["step"]}</div></blockquote>'
        for p in issue['evidence']
    )
    sessions = len({p['session_id'] for p in issue['evidence']})
    return f'<li><details><summary>Evidence: {sessions} session{"s" * (sessions != 1)}</summary>{quotes}</details></li>'


def name(issue: dict[str, Any]) -> str:
    """The grader's plain title; older baselines have none, so fall back to the id in words."""
    return issue.get('title') or issue['id'].replace('-', ' ').replace('_', ' ').capitalize()


def fixes(rules: list[str]) -> str:
    return ''.join(f'<li><b>Suggested fix</b> {esc(r)}</li>' for r in dict.fromkeys(rules))


def issue_card(
    issue: dict[str, Any],
    texts: dict[str, list[str]],
    reasons: list[str],
    css: str = '',
    rules: list[str] = (),
) -> str:
    """Short description, then the bullets."""
    reason_items = ''.join(f'<li>{esc(r)}</li>' for r in reasons)
    return (
        f'<div class="card{css}"><h3>{esc(name(issue))}</h3><div class="muted">{esc(issue["priority"])} · '
        f'{esc(CAUSES[issue["cause"]])} · <span class="mono">{esc(issue["id"])}</span></div>'
        f'<p>{esc(issue["claim"])}</p><ul>{fixes(list(rules))}{reason_items}'
        f'{instruction(issue, texts)}{evidence(issue)}</ul></div>'
    )


def rank(letter: str) -> tuple[str, int]:
    """Sort key, smaller is better: A+ < A < A- < B+ ..."""
    return letter[0], '+ -'.index(letter[1:] or ' ')


def grade(data: dict[str, Any]) -> tuple[str, float | None, str]:
    """Letter, weighted score in [0, 1], and why; compliance 1-5 maps to (c - 1) / 4."""
    loaded = data['scores']['loaded']
    parts = {}
    for metric, top in METRICS:
        mean = loaded[metric]['mean']
        if mean is not None:
            parts[metric] = (mean - 1) / 4 if top == 5.0 else mean
    if not parts:
        return '-', None, 'No scored sessions.'
    score = sum(WEIGHTS[m] * v for m, v in parts.items()) / sum(WEIGHTS[m] for m in parts)
    cutoff, letter = next((cutoff, name) for cutoff, name in LETTERS if score >= cutoff)
    if letter != 'F':  # thirds of the 0.1 band: bottom -, top +
        third = min(int((score - cutoff) * 30), 2)
        letter += ('-', '', '+')[third]
    why = ' + '.join(f'{WEIGHTS[m]:g} x {m.replace("_", " ")} {number(v)}' for m, v in parts.items())
    priorities = {i['priority'] for i in data['filed_issues']}
    for priority in sorted(priorities & CAPS.keys()):
        if rank(letter) < rank(CAPS[priority]):
            letter, why = CAPS[priority], why + f'; capped at {CAPS[priority]} by a filed {priority} issue'
            break
    return letter, score, why


def grade_block(data: dict[str, Any]) -> str:
    letter, score, why = grade(data)
    cutoffs = ', '.join(f'{name} &ge; {cutoff:g}' for cutoff, name in LETTERS[:-1])
    cap_note = 'A filed p0 caps the grade at D, a filed p1 at B.'
    return (
        f'<div class="grade {letter[0]}"><div class="letter">{letter}</div><div><div class="num" '
        f'style="font-size:22px;font-weight:700">{number(score)}</div><div>{esc(why)}</div>'
        f'<div class="muted">Compliance 1-5 maps to 0-1 as (c - 1) / 4. Cutoffs: {cutoffs}, else F; '
        f'the bottom third of each band gets a minus, the top third a plus. {cap_note}</div></div></div>'
    )


def scorecard(data: dict[str, Any]) -> str:
    loaded = data['scores']['loaded']
    cells = []
    for metric, top in METRICS:
        mean = loaded[metric]['mean']
        width = 0 if mean is None else round(100 * mean / top)
        cells.append(
            f'<div class="metric"><div class="muted">{metric.replace("_", " ")}</div>'
            f'<div class="num">{number(mean)}<span class="muted"> / {top:g}</span></div>'
            f'<div class="track"><div class="fill" style="width:{width}%"></div></div>'
            f'<div class="muted">{loaded[metric]["scored"]} scored, {loaded[metric]["unavailable"]} unavailable</div>'
            '</div>'
        )
    failed = sum(g['grading_failed'] for g in data['session_grades'].values())
    stats = [
        (len(data['session_grades']), 'sessions graded'),
        (failed, 'grading failed'),
        (len(data['filed_issues']), 'issues filed'),
        (sum(f['decision'] == 'dont_file' for f in data['findings']), 'findings not filed'),
    ]
    stat_cells = ''.join(
        f'<div class="stat"><div class="num">{v}</div><div class="muted">{k}</div></div>' for v, k in stats
    )
    return f'<div class="scorecard">{"".join(cells)}</div><div class="stats">{stat_cells}</div>'


def session_table(data: dict[str, Any], links: dict[str, str]) -> str:
    multi = data['passes'] > 1
    head = ['session', 'efficiency', 'quality', 'compliance', 'usage', 'failed'] + (['spread e/q/c'] if multi else [])
    rows = []
    for sid, grade in data['session_grades'].items():
        cells = [
            f'<a class="mono" href="{esc(links.get(sid, ""))}" title="{esc(sid)}">{esc(sid[:12])}</a>',
            verdict(grade['efficiency']),
            verdict(grade['quality']),
            verdict(grade['skill_compliance']),
            esc(grade['usage']),
            '<span class="failed">yes</span>' if grade['grading_failed'] else 'no',
        ]
        if multi:
            spread = data['session_spread'][sid]
            cells.append(' / '.join(number(spread[m], 1) for m, _ in METRICS))
        rows.append('<tr>' + ''.join(f'<td>{c}</td>' for c in cells) + '</tr>')
    return '<table><tr>' + ''.join(f'<th>{h}</th>' for h in head) + '</tr>' + ''.join(rows) + '</table>'


def agreement_table(data: dict[str, Any]) -> str:
    known = {f['issue']['id']: f['issue'] for f in data['findings'] if f['issue']} | {
        i['id']: i for i in data['filed_issues']
    }
    filed = {i['id'] for i in data['filed_issues']}
    rows = ''.join(
        f'<tr><td>{esc(name(known[target]))}</td><td>{esc(CAUSES[known[target]["cause"]])}</td>'
        f'<td class="mono">{esc(known[target]["instruction"]["path"])}:{known[target]["instruction"]["start_line"]}-'
        f'{known[target]["instruction"]["end_line"]}</td><td>{count} of {data["passes"]}</td>'
        f'<td>{"filed" if target in filed else "not filed"}</td></tr>'
        for target, count in data['agreement'].items()
    )
    return (
        '<h2>Agreement across passes</h2><p class="muted">'
        'A target is filed when a strict majority of passes filed it.'
        '</p><table><tr><th>target</th><th>cause</th><th>lines</th><th>passes</th><th>result</th></tr>'
        f'{rows}</table>'
    )


def filed_findings(data: dict[str, Any], issue_id: str) -> list[dict[str, Any]]:
    return [f for f in data['findings'] if f['decision'] == 'file' and f['issue']['id'] == issue_id]


def suggested(finding: dict[str, Any]) -> str:
    return f'{finding["owning_surface"]}: {finding["behavioral_rule"]}'


def render(
    data: dict[str, Any],
    texts: dict[str, list[str]],
    links: dict[str, str],
) -> str:
    """The page for one baseline; `texts` maps skill paths to lines, `links` session ids to transcripts."""
    filed_cards = ''.join(
        issue_card(
            issue,
            texts,
            [f['reason'] for f in filed_findings(data, issue['id'])],
            rules=[suggested(f) for f in filed_findings(data, issue['id'])],
        )
        for issue in data['filed_issues']
    )
    dont_cards = []
    for finding in data['findings']:
        if finding['decision'] != 'dont_file':
            continue
        if finding['issue']:
            dont_cards.append(issue_card(finding['issue'], texts, [finding['reason']], ' dont', [suggested(finding)]))
        else:
            dont_cards.append(
                f'<div class="card dont"><ul>{fixes([suggested(finding)])}<li>{esc(finding["reason"])}</li></ul></div>'
            )
    limits = ''.join(f'<li>{esc(limit)}</li>' for limit in data['limitations'])
    meta = (
        f'version <code>{esc(data["skill_hash"][:12])}</code> · {len(data["session_grades"])} sessions · '
        f'{data["passes"]} pass{"es" if data["passes"] > 1 else ""} · batch size {data["batch_size"]}'
    )
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Skill baseline: {esc(data['skill'])}</title><style>{CSS}</style></head><body>
<div class="tag">Orq skill loop · baseline</div>
<h1>{esc(data['skill'])}</h1>
<div class="muted">{meta}</div>
{grade_block(data)}
{scorecard(data)}
<h2>Filed issues</h2>{filed_cards or '<p class="muted">Nothing cleared the filing bar.</p>'}
<details><summary>References</summary>
<h2>Sessions</h2>{session_table(data, links)}
<h2>Not filed</h2>{''.join(dont_cards) or '<p class="muted">No other findings.</p>'}
{agreement_table(data) if data['passes'] > 1 else ''}
<h2>Limits</h2><ul>{limits}</ul>
</details>
</body></html>
"""
