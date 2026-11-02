"""`factory jobs scan`: источники → дедуп → фильтр → судья → письма → журнал + дайджест.
Ничего не отправляет: результат — файлы в out_dir."""
import collections
import datetime as dt
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import yaml

from . import agents, sources, store
from .rules import Filter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG = os.path.join(ROOT, 'config', 'jobs.yaml')


def load_config(path=CONFIG):
    with open(path) as f:
        cfg = yaml.safe_load(f)
    cfg['out_dir'] = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(path)), '..', cfg.get('out_dir', 'job_search'))) \
        if not os.path.isabs(cfg.get('out_dir', '')) else cfg['out_dir']
    return cfg


def scan(cfg, plan=None, ask=agents.claude_ask, today=None, judge_on=True, draft_on=True, workers=6):
    """plan — [(имя, fn)] источников (тесты подставляют свои); ask — вызов Claude. → (путь дайджеста, метрики)."""
    t0 = time.time()
    today = today or dt.date.today()
    plan = plan if plan is not None else sources.plan(cfg['sources'])
    out_dir = cfg['out_dir']
    log = store.LeadLog(os.path.join(out_dir, 'leads.jsonl'))

    errors, fetched = {}, []

    def safe(item):
        name, fn = item
        try:
            return name, fn(), None
        except Exception as e:  # сбой источника — строка в дайджесте, не падение скана
            return name, [], f'{type(e).__name__}: {str(e)[:120]}'
    with ThreadPoolExecutor(workers) as ex:
        for name, got, err in ex.map(safe, plan):
            if err:
                errors[name] = err
            fetched += got

    new, keys = [], set()
    for x in fetched:
        k = store.norm_url(x['url'])
        if x['url'] and log.is_new(x) and k not in keys:
            keys.add(k)
            new.append(x)

    flt = Filter(cfg, today)
    dropped = collections.Counter()
    passed = []
    for x in new:
        ok, rid = flt.check(x)
        x['passed'], x['drop'] = ok, rid
        if ok:
            passed.append(x)
        else:
            dropped[rid] += 1

    jc, dc = cfg.get('judge', {}), cfg.get('draft', {})
    passed.sort(key=lambda x: x['posted'], reverse=True)  # свежие — первыми под лимит судьи
    to_judge = passed[:jc.get('max_leads', 40)]
    metas = agents.judge(to_judge, cfg['profile'], ask, jc.get('model', 'sonnet'), jc.get('batch', 8)) if judge_on and to_judge else []

    min_score = dc.get('min_score', 4)
    good = sorted([x for x in to_judge if x.get('score', 0) >= min_score], key=lambda x: -x['score'])
    if draft_on:
        for x in good[:dc.get('max_drafts', 8)]:
            x['lang'] = agents.lang_of(x)
            try:
                x['letter'], x['tone_issues'], dm = agents.draft(x, cfg['profile'], cfg['tone']['banned'], ask, dc.get('model', 'sonnet'))
                metas += dm
            except Exception as e:
                x['draft_error'] = str(e)[:200]

    stamp = dt.datetime.now().isoformat(timespec='seconds')
    log.append([{**x, 'scanned_at': stamp} for x in new])  # полный текст — чтобы пересобрать письмо без повторного сбора

    m = {'date': today.isoformat(), 'sources': len(plan), 'sources_ok': len(plan) - len(errors), 'fetched': len(fetched),
         'duplicates': len(fetched) - len(new), 'new': len(new), 'dropped': dict(dropped), 'passed': len(passed),
         'judged': sum(1 for x in to_judge if x.get('score')), 'min_score': min_score, 'good': len(good),
         'scores': dict(collections.Counter(x['score'] for x in to_judge if x.get('score'))),
         'drafts': sum(1 for x in new if x.get('letter')), 'tone_flagged': sum(1 for x in new if x.get('tone_issues')),
         'claude_calls': len([mm for mm in metas if 'error' not in mm]), 'claude_errors': len([mm for mm in metas if 'error' in mm]),
         'claude_list_usd': round(sum(mm.get('list_usd', 0) for mm in metas), 3), 'wall_sec': round(time.time() - t0, 1)}
    path = os.path.join(out_dir, f'leads_digest_{today.isoformat()}.md')
    with open(path, 'w') as f:
        f.write(store.digest(today.isoformat(), m, new, errors))
    with open(os.path.join(out_dir, 'scans.jsonl'), 'a') as f:
        f.write(json.dumps({**m, 'errors': errors}, ensure_ascii=False) + '\n')
    return path, m
