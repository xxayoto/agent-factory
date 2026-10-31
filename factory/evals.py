"""Замер судьи на кадрах с известной истиной: mock рисует дефекты сам, поэтому правильный ответ известен.

Метрики: точность по атрибутам; на уровне кадра — recall (поймал брак), false-reject (зря забраковал хороший),
время и стоимость по прайсу API на кадр.
"""
import datetime as dt
import json
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor

from . import backends
from .backends.mock import MockFrames, wrong_value


def make_set(brief, n, out_dir, seed=0, defect_share=0.5):
    """n кадров: доля defect_share с одним искажённым атрибутом, остальные чистые."""
    rng = random.Random(seed)
    gen = MockFrames(seed=seed, defect_rate=0.0)
    shots = brief.shots
    items = []
    for i in range(n):
        shot = shots[i % len(shots)]
        prod = brief.products[shot.product]
        truth = dict(prod.attributes)
        if rng.random() < defect_share:
            a = rng.choice(list(truth))
            truth[a] = wrong_value(a, truth[a])
        ref = brief.references.get(shot.reference)
        req = {'shot': f'eval{i:02d}', 'attempt': 1, 'prompt': '', 'constraints': [], 'photos': prod.photos,
               'reference': ref and ref['path'], 'attributes': prod.attributes, 'force_observed': truth,
               'width': brief.width, 'height': brief.height}
        path = os.path.join(out_dir, f'eval{i:02d}.png')
        gen.generate(req, path)
        items.append({'path': path, 'product': prod, 'truth': truth, 'defect': truth != prod.attributes})
    return items


def run(brief, judge, n, runs_dir, seed=0, workers=3, **judge_kw):
    stamp = f'{dt.datetime.now():%Y%m%d-%H%M%S}'
    out_dir = os.path.join(runs_dir, f'judge-eval-{stamp}')
    os.makedirs(out_dir, exist_ok=True)
    items = make_set(brief, n, out_dir, seed)
    insp = backends.judge_inspector(judge, **judge_kw)

    def one(it):
        t = time.time()
        try:
            got = insp.inspect(it['path'], it['product'])
            err = None
        except Exception as e:  # ошибка судьи — тоже результат замера
            got, err = {}, str(e)[:200]
        last = dict(getattr(insp, 'last', {}) or {})
        return {**{k: it[k] for k in ('truth', 'defect')}, 'path': os.path.basename(it['path']), 'got': got, 'error': err,
                'sec': round(time.time() - t, 2), 'list_usd': last.get('list_usd'), 'notes': last.get('notes')}

    with ThreadPoolExecutor(workers if judge != 'manual' else 1) as pool:
        rows = list(pool.map(one, items))

    attr_total = attr_ok = tp = fn = fp = tn = errors = 0
    for r, it in zip(rows, items):
        if r['error']:
            errors += 1
            continue
        expected = it['product'].attributes
        for a in expected:
            attr_total += 1
            attr_ok += r['got'].get(a) == r['truth'][a]
        flagged = any(r['got'].get(a) != expected[a] for a in expected)
        tp += flagged and r['defect']
        fn += (not flagged) and r['defect']
        fp += flagged and not r['defect']
        tn += (not flagged) and not r['defect']
    usd = [r['list_usd'] for r in rows if r['list_usd'] is not None]
    m = {
        'eval_id': f'judge-eval-{stamp}', 'judge': judge, 'model': judge_kw.get('model'), 'frames': n, 'errors': errors,
        'defective': tp + fn, 'clean': fp + tn,
        'attribute_accuracy': round(attr_ok / attr_total, 3) if attr_total else None,
        'defect_recall': round(tp / (tp + fn), 3) if tp + fn else None,
        'false_reject_rate': round(fp / (fp + tn), 3) if fp + tn else None,
        'sec_per_frame': round(sum(r['sec'] for r in rows) / n, 2),
        'list_usd_per_frame': round(sum(usd) / len(usd), 4) if usd else None,
    }
    with open(os.path.join(out_dir, 'eval.json'), 'w') as f:
        json.dump({'metrics': m, 'rows': rows}, f, ensure_ascii=False, indent=2)
    with open(os.path.join(runs_dir, 'judge_evals.jsonl'), 'a') as f:
        f.write(json.dumps(m, ensure_ascii=False) + '\n')
    return m, rows, out_dir


def rejudge(brief, run_dir, judge='claude', **judge_kw):
    """Перепроверка уже оплаченных кадров прогона текущим судьёй — бесплатно для генерации.
    Нужна, когда судья улучшился: видно, что новый судья поймал бы на старых кадрах."""
    from .agents.judge import JudgeAgent
    from .runlog import RunLog
    runs_dir, run_id = os.path.split(os.path.abspath(run_dir))
    old = RunLog(runs_dir, run_id, 'rejudge', resume=True).previous
    finals = {}
    for e in old:
        if e['event'] in ('approved', 'rejected'):
            finals[e['task']] = e
    stamp = f'{dt.datetime.now():%Y%m%d-%H%M%S}'
    log = RunLog(os.path.join(runs_dir, run_id), f'rejudge-{stamp}', 'rejudge')
    agent = JudgeAgent(log, backends.judge_inspector(judge, **judge_kw), brief)
    shots = {s.id: s for s in brief.shots}
    rows = []
    for task, e in finals.items():
        shot = shots[task]
        product = brief.products[shot.product]
        ok, tech, mism, extra = agent.review(shot, product, e['path'], None, [], e['attempt'])
        rows.append({'shot': task, 'was': e['event'], 'now': 'approved' if ok else 'rejected', 'mismatches': mism,
                     'scene_ok': extra.get('scene_ok'), 'scene_issue': extra.get('scene_issue'),
                     'color_delta_e': extra.get('color_delta_e'), 'bbox': extra.get('bbox')})
    log._f.close()
    return rows, log.dir


def run_labeled(brief, labels_path, runs_dir, workers=3, ids=None, **judge_kw):
    """Замер судьи на реальных кадрах с ручной разметкой: ok — верный ли товар на кадре по мнению человека."""
    import yaml
    from .color import delta_e, stone_lab
    with open(labels_path) as f:
        labels = yaml.safe_load(f)['items']
    if ids:
        labels = [it for it in labels if it.get('id') in ids]
    stamp = f'{dt.datetime.now():%Y%m%d-%H%M%S}'
    out_dir = os.path.join(runs_dir, f'judge-labeled-{stamp}')
    os.makedirs(out_dir, exist_ok=True)
    lim = brief.judge.get('max_delta_e')

    def one(it):
        prod = brief.products[it['product']]
        insp = backends.judge_inspector('claude', **judge_kw)
        t = time.time()
        try:
            got, err = insp.inspect(it['path'], prod), None
        except Exception as e:
            got, err = {}, str(e)[:200]
        last = dict(insp.last)
        disputed = last.get('disputed') or {}
        wrong = {a: got.get(a) for a, v in prod.attributes.items() if got.get(a) != v and a not in disputed}
        de = None
        if not err and prod.color_box and last.get('bbox'):
            b = last['bbox']
            de = round(delta_e(stone_lab(prod.photos[0], prod.color_box), stone_lab(it['path'], [b[k] for k in ('x0', 'y0', 'x1', 'y1')])), 1)
        if lim is not None and de is not None and de > lim and not wrong:
            disputed = {**disputed, 'stone_color': [f'ΔE {de}', 'модель: камень верный']}  # см. JudgeAgent.review
        flagged = bool(wrong)
        return {'id': it.get('id'), 'product': it['product'], 'label_ok': it['ok'], 'judge_ok': not flagged, 'wrong': wrong,
                'zoomed': last.get('zoomed', False), 'first_pass': last.get('first_pass'), 'disputed': disputed,
                'delta_e': de, 'notes': last.get('notes'), 'label_note': it.get('note', ''), 'error': err,
                'sec': round(time.time() - t, 1), 'list_usd': last.get('list_usd')}

    with ThreadPoolExecutor(workers) as pool:
        rows = list(pool.map(one, labels))
    ok_rows = [r for r in rows if not r['error']]
    pos = [r for r in ok_rows if r['label_ok']]
    neg = [r for r in ok_rows if not r['label_ok']]
    usd = [r['list_usd'] for r in ok_rows if r['list_usd'] is not None]
    m = {
        'eval_id': f'judge-labeled-{stamp}', 'judge': 'claude', 'model': judge_kw.get('model'), 'labels': os.path.basename(labels_path),
        'subset': sorted(ids) if ids else None, 'zoomed': sum(r.get('zoomed', False) for r in rows), 'max_delta_e': lim,
        'needs_review': sum(bool(r.get('disputed')) for r in rows),
        'frames': len(rows), 'errors': len(rows) - len(ok_rows), 'defective': len(neg), 'clean': len(pos),
        'verdict_accuracy': round(sum(r['label_ok'] == r['judge_ok'] for r in ok_rows) / len(ok_rows), 3) if ok_rows else None,
        'defect_recall': round(sum(not r['judge_ok'] for r in neg) / len(neg), 3) if neg else None,
        'false_reject_rate': round(sum(not r['judge_ok'] for r in pos) / len(pos), 3) if pos else None,
        'attribute_accuracy': None,
        'sec_per_frame': round(sum(r['sec'] for r in rows) / len(rows), 1),
        'list_usd_per_frame': round(sum(usd) / len(usd), 4) if usd else None,
    }
    with open(os.path.join(out_dir, 'eval.json'), 'w') as f:
        json.dump({'metrics': m, 'rows': rows}, f, ensure_ascii=False, indent=2)
    with open(os.path.join(runs_dir, 'judge_evals.jsonl'), 'a') as f:
        f.write(json.dumps(m, ensure_ascii=False) + '\n')
    return m, rows, out_dir
