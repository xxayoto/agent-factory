"""Статичный HTML-дашборд по runs/index.jsonl и runs/judge_evals.jsonl: python3 -m factory dashboard"""
import html
import json
import os

from .runlog import load_index

CSS = """
:root{--bg:#fafaf8;--fg:#1d1d1b;--mut:#6b6b66;--line:#e4e2dc;--acc:#b8862b;--bad:#c4473a;--ok:#3f8a5a}
@media (prefers-color-scheme:dark){:root{--bg:#151514;--fg:#ecebe6;--mut:#9a9890;--line:#2c2b28;--acc:#d9a648;--bad:#e06a5d;--ok:#62b37f}}
body{background:var(--bg);color:var(--fg);font:15px/1.45 -apple-system,system-ui,sans-serif;margin:0;padding:24px 16px;max-width:1100px;margin:auto}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 8px}.mut{color:var(--mut)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:16px 0}
.tile{border:1px solid var(--line);border-radius:10px;padding:10px 12px}.tile b{display:block;font-size:22px}
.wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
td,th{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}td:first-child,th:first-child{text-align:left}
th{color:var(--mut);font-weight:500}.bar{display:inline-block;height:8px;background:var(--acc);border-radius:2px;vertical-align:middle;margin-left:6px}
.live{color:var(--ok);font-weight:600}
"""


def _pct(v):
    return '—' if v is None else f'{v:.0%}'


def render(runs_dir):
    runs = load_index(runs_dir)
    ev_path = os.path.join(runs_dir, 'judge_evals.jsonl')
    evals = [json.loads(l) for l in open(ev_path)] if os.path.exists(ev_path) else []
    live = [m for m in runs if m['mode'] == 'live']
    frames = sum(m['frames_generated'] for m in runs)
    rej = sum(m['frames_rejected'] for m in runs)
    tiles = [('прогонов', len(runs)), ('из них live', len(live)), ('кадров', frames),
             ('брак', _pct(rej / frames) if frames else '—'),
             ('потрачено реально', f"${sum(m['cost_usd'] for m in live):.2f}"), ('замеров судьи', len(evals))]
    h = [f'<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">'
         f'<title>Agent factory runs</title><style>{CSS}</style>',
         '<h1>agent-factory — прогоны</h1><div class=mut>mock-прогоны — симуляция затрат; live — реальные деньги</div>',
         '<div class=tiles>' + ''.join(f'<div class=tile><span class=mut>{k}</span><b>{v}</b></div>' for k, v in tiles) + '</div>',
         '<h2>Прогоны</h2><div class=wrap><table><tr><th>run</th><th>режим</th><th>судья</th><th>шотов</th><th>кадров</th>'
         '<th>брак</th><th>с 1-й</th><th>правил+</th><th>вердикт судьи</th><th>$</th><th>сек</th></tr>']
    for m in reversed(runs):
        mode = f"<span class=live>live</span>" if m['mode'] == 'live' else 'mock'
        h.append(f"<tr><td>{html.escape(m['run_id'])}</td><td>{mode}</td><td>{m.get('judge', 'mock')}</td><td>{m['shots']}</td>"
                 f"<td>{m['frames_generated']}</td><td>{_pct(m['reject_rate'])}<span class=bar style='width:{m['reject_rate'] * 60:.0f}px'></span></td>"
                 f"<td>{_pct(m['first_pass_yield'])}</td><td>{m['rules_learned']}</td><td>{_pct(m.get('judge_verdict_accuracy'))}</td>"
                 f"<td>{m['cost_usd']:.3f}</td><td>{m['wall_sec']}</td></tr>")
    h.append('</table></div><h2>Замеры судьи</h2><div class=wrap><table><tr><th>замер</th><th>модель</th><th>кадров</th>'
             '<th>атрибуты</th><th>recall брака</th><th>ложный брак</th><th>с/кадр</th></tr>')
    for e in reversed(evals):
        h.append(f"<tr><td>{e['eval_id']}</td><td>{e['judge']} {e.get('model') or ''}</td><td>{e['frames']}</td>"
                 f"<td>{_pct(e['attribute_accuracy'])}</td><td>{_pct(e['defect_recall'])}</td><td>{_pct(e['false_reject_rate'])}</td>"
                 f"<td>{e['sec_per_frame']}</td></tr>")
    h.append('</table></div>')
    out = os.path.join(runs_dir, 'dashboard.html')
    with open(out, 'w') as f:
        f.write('\n'.join(h))
    return out
