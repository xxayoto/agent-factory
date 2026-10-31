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
