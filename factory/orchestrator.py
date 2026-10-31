"""Оркестратор: бриф → граф задач → агенты. Цикл «кадр → судья → перегенерация с замечаниями»,
обучение памяти на отбраковках, бюджет, журнал и метрики прогона.

    кадры (параллельно по шотам) ──► судья ──► reject? ──► правило в память + замечание ──► кадр ещё раз
                      │ approved
                      ├──► карточка (параллельно)
                      └──► видео (параллельно) ──► монтаж + субтитры
"""
import datetime as dt
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import yaml

from . import backends
from .agents.card import CardAgent
from .agents.frame import FrameAgent
from .agents.judge import JudgeAgent
from .agents.montage import MontageAgent
from .agents.video import VideoAgent
from .backends.local import have_ffmpeg
from .budget import Budget, BudgetExceeded
from .memory import Memory
from .runlog import RunLog

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def live_config():
    with open(os.path.join(ROOT, 'config', 'backends.yaml')) as f:
        cfg = yaml.safe_load(f)
    local = os.path.join(ROOT, 'config', 'local.yaml')  # локальные пути, в .gitignore
    if os.path.exists(local):
        with open(local) as f:
            for k, v in (yaml.safe_load(f) or {}).items():
                cfg[k] = {**cfg.get(k, {}), **v} if isinstance(v, dict) else v
    return cfg


def plan(brief):
    """Список задач и вилка стоимости — без единого вызова API."""
    b = Budget(10 ** 9)
    tasks = []
    for s in brief.shots:
        tasks.append(('frame+judge', s.id))
        if brief.outputs.get('cards') and s.card:
            tasks.append(('card', s.id))
        if brief.outputs.get('videos') and s.video:
            tasks.append(('video', s.id))
    if brief.outputs.get('montage'):
        tasks.append(('montage', '*'))
    n_frames, n_vid = len(brief.shots), sum(1 for t in tasks if t[0] == 'video')
    retries = brief.limits['max_retries']
    est = {}
    for mode, fb, vb in (('mock', 'mock', 'mock'), ('live', live_config()['live']['frame'], live_config()['live'].get('video', 'local_kenburns'))):
        lo = n_frames * b.price('frame', fb) + n_vid * b.price('video', vb)
        hi = n_frames * (1 + retries) * b.price('frame', fb) + n_vid * b.price('video', vb)
        est[mode] = (round(lo, 3), round(hi, 3))
    return tasks, est


class Orchestrator:
    def __init__(self, brief, mode='mock', seed=0, memory_path=None, runs_dir=None, allow_spend=None, defect_rate=0.3, judge=None, resume=None):
        self.brief, self.mode = brief, mode
        self.memory = Memory(memory_path) if memory_path else Memory()
        self.runs_dir = os.path.abspath(runs_dir or os.path.join(ROOT, 'runs'))
        limit = float(brief.limits['budget_usd'])
        if mode == 'live':
            if allow_spend is None:
                raise SystemExit('live-режим тратит деньги: нужен явный --allow-spend <USD>')
            limit = min(limit, float(allow_spend))
            cfg = live_config()
            ws = cfg.get('wavespeed', {})
            frame_be = backends.frame_backend(cfg['live']['frame'], model=ws.get('frame_model'), resolution=ws.get('resolution', '1k'))
            inspector = self._inspector(judge or cfg['live']['judge'])
            vname = cfg['live'].get('video', 'local_kenburns')
            self.video_be = backends.video_backend(vname, model=ws.get('video_model'), durations=ws.get('video_durations'),
                                                   api_json=cfg.get('runninghub', {}).get('h3_api_json'))
            self.video_price = vname
        else:
            frame_be = backends.frame_backend('mock', seed=seed, defect_rate=defect_rate)
            inspector = self._inspector(judge or 'mock')
            self.video_be, self.video_price = backends.video_backend('mock'), 'mock'
        self.budget = Budget(limit)
        if resume:
            resume = os.path.abspath(resume)
            self.runs_dir, run_id = os.path.dirname(resume), os.path.basename(resume)
        else:
            run_id = f"{dt.datetime.now():%Y%m%d-%H%M%S}-{brief.project}-{uuid.uuid4().hex[:4]}"
        self.log = RunLog(self.runs_dir, run_id, mode, resume=bool(resume))
        self.out = os.path.join(self.log.dir, 'out')
        os.makedirs(self.out, exist_ok=True)
        self.frame = FrameAgent(self.log, frame_be, self.budget, brief)
        prev_bal = [e['usd'] for e in self.log.previous if e['event'] == 'balance']
        self.balance_before = prev_bal[0] if prev_bal else self._balance()
        if self.balance_before is not None and not prev_bal:
            self.log.event('orchestrator', '*', 'balance', usd=self.balance_before)
        self.judge = JudgeAgent(self.log, inspector, brief)
        self.card = CardAgent(self.log, brief)
        self.video = VideoAgent(self.log, self.video_be, self.budget, brief, self.judge)
        self.montage = MontageAgent(self.log, brief)
        self.stats = {'generated': 0, 'rejected': 0, 'first_pass': 0, 'learned': 0, 'rules_used': set(),
                      'judged': 0, 'judge_verdict_ok': 0, 'judge_attrs_exact': 0, 'judge_list_usd': 0.0}
        self.stopped_by_budget = False
        self.done = {}
        self._restore()

    def _restore(self):
        """--resume: статистика и принятые кадры из журнала прерванного прогона; потраченное идёт в бюджет."""
        for e in self.log.previous:
            ev = e['event']
            if ev == 'generated':
                self.stats['generated'] += 1
                self.log.add_cost(e.get('cost_usd') or 0.0)
                self.budget.spent += e.get('cost_usd') or 0.0
            elif ev == 'done' and e.get('agent') == 'video':  # оплаченный ролик тоже в стоимости прогона
                self.log.add_cost(e.get('cost_usd') or 0.0)
                self.budget.spent += e.get('cost_usd') or 0.0
            elif ev == 'approved':
                self.done[e['task']] = e['path']
                self.stats['first_pass'] += e['attempt'] == 1
            elif ev == 'rejected':
                self.stats['rejected'] += 1
            elif ev == 'rule_learned':
                self.stats['learned'] += 1
            if e.get('agent') == 'judge':
                self.stats['judge_list_usd'] += e.get('list_usd') or 0.0
        if self.log.previous:
            self.log.event('orchestrator', '*', 'resumed', approved=sorted(self.done), spent_usd=round(self.budget.spent, 4))

    def shot_loop(self, shot):
        """Генерация → судья → перегенерация с замечаниями, пока не примут или не кончатся попытки."""
        if shot.id in self.done:
            return self.done[shot.id]
        product = self.brief.products[shot.product]
        feedback = []
        judged = {(e['task'], e['attempt']) for e in self.log.previous if e['event'] in ('approved', 'rejected')}
        for attempt in range(1, self.brief.limits['max_retries'] + 2):
            if (shot.id, attempt) in judged:  # эта попытка уже отбракована в прерванном прогоне
                feedback = next(e['mismatches'] for e in self.log.previous
                                if e['event'] == 'rejected' and e['task'] == shot.id and e['attempt'] == attempt)
                continue
            rules = self.memory.for_product(product)
            self.stats['rules_used'].update(r['id'] for r in rules)
            try:
                path, info = self.frame.run(shot, product, rules, feedback, attempt, self.out)
            except BudgetExceeded as e:
                self.stopped_by_budget = True
                self.log.event('orchestrator', shot.id, 'budget_stop', reason=str(e))
                return None
            except Exception as e:  # сеть/провайдер: шот помечается сбойным, остальные шоты идут дальше
                self.log.event('orchestrator', shot.id, 'error', attempt=attempt, error=f'{type(e).__name__}: {str(e)[:300]}')
                return None
            if not info.get('reused'):
                self.stats['generated'] += 1
            try:
                ok, tech, mism, extra = self.judge.review(shot, product, path, info.get('raw_size'), rules, attempt)
            except Exception as e:  # судья недоступен (лимит подписки, сеть): кадр не принимаем непроверенным
                self.log.event('judge', shot.id, 'judge_error', attempt=attempt, path=path, error=f'{type(e).__name__}: {str(e)[:300]}')
                return None
            if 'truth_verdict_ok' in extra:
                self.stats['judged'] += 1
                self.stats['judge_verdict_ok'] += extra['truth_verdict_ok']
                self.stats['judge_attrs_exact'] += extra['truth_attrs_exact']
            self.stats['judge_list_usd'] += extra.get('list_usd') or 0.0
            if ok:
                if attempt == 1:
                    self.stats['first_pass'] += 1
                return path
            self.stats['rejected'] += 1
            for m in mism:
                if m['attribute'] == 'scene':  # сцена — свойство шота, а не товара: в память товара не пишем
                    continue
                rule, new = self.memory.learn(product, m['attribute'], m['expected'], m['got'], self.log.run_id)
                self.stats['learned'] += new
                self.log.event('memory', shot.id, 'rule_learned' if new else 'rule_hit', rule=rule['id'], hits=rule['hits'])
            feedback = mism
        self.log.event('orchestrator', shot.id, 'gave_up', attempts=attempt)
        return None

    def run(self):
        b = self.brief
        tasks, est = plan(b)
        self.log.event('orchestrator', '*', 'plan', tasks=tasks, estimate_usd=est[self.mode], shots=len(b.shots),
                       memory_rules=len(self.memory.rules))
        workers = int(b.limits['workers'])
        with ThreadPoolExecutor(workers) as pool:
            frames = dict(zip([s.id for s in b.shots], pool.map(self.shot_loop, b.shots)))
        approved = [s for s in b.shots if frames[s.id]]

        cards, videos = {}, {}
        with ThreadPoolExecutor(workers) as pool:
            fc = {s.id: pool.submit(self.card.run, s, frames[s.id], self.out) for s in approved if b.outputs.get('cards') and s.card}
            fv = {}
            if b.outputs.get('videos') and have_ffmpeg():
                fv = {s.id: pool.submit(self._video, s, frames[s.id]) for s in approved if s.video}
            cards = {k: f.result() for k, f in fc.items()}
            videos = {k: v for k, v in ((k, f.result()) for k, f in fv.items()) if v}

        montage = None
        if b.outputs.get('montage') and approved and have_ffmpeg():
            # в монтаж — ролик или чистый кадр, не карточка: плашка карточки и субтитры дали бы два слоя текста
            # (найдено на live #1: «В ПОДАРОК» под субтитром «ПОДАРОК БЕЗ ПОВОДА»)
            items = [(s, videos.get(s.id) or frames[s.id]) for s in approved]
            try:
                montage = self.montage.run(items, self.out)
            except Exception as e:  # монтаж — последний шаг; его сбой не должен стирать метрики прогона
                self.log.event('montage', 'montage', 'failed', error=str(e)[:300])

        g, r = self.stats['generated'], self.stats['rejected']
        after = self._balance()
        if after is not None:
            self.log.event('orchestrator', '*', 'balance', usd=after)
        real = round(self.balance_before - after, 4) if self.balance_before is not None and after is not None else None
        des = [e['color_delta_e'] for e in self.log.read() if e.get('color_delta_e') is not None]
        scenes = [e['scene_ok'] for e in self.log.read() if e.get('scene_ok') is not None]
        return self.log.finish({
            'color_delta_e_mean': round(sum(des) / len(des), 1) if des else None,
            'scene_ok_rate': round(sum(scenes) / len(scenes), 3) if scenes else None,
            'project': b.project, 'brief': os.path.relpath(b.path, ROOT),
            'shots': len(b.shots), 'frames_generated': g, 'frames_approved': len(approved), 'frames_rejected': r,
            'reject_rate': round(r / g, 3) if g else 0.0,
            'first_pass_yield': round(self.stats['first_pass'] / len(b.shots), 3),
            'failed_shots': [s.id for s in b.shots if not frames[s.id]],
            'resumed': bool(self.log.previous),
            'rules_applied': len(self.stats['rules_used']), 'rules_learned': self.stats['learned'],
            'cards': len(cards), 'videos': len(videos),
            'videos_approved': sum(e['event'] == 'video_approved' for e in self.log.read()), 'montage': montage and os.path.relpath(montage, self.log.dir),
            'stopped_by_budget': self.stopped_by_budget, 'budget_usd': self.budget.limit,
            'judge': self.judge.inspector.name,
            'judge_verdict_accuracy': round(self.stats['judge_verdict_ok'] / self.stats['judged'], 3) if self.stats['judged'] else None,
            'judge_attrs_exact': round(self.stats['judge_attrs_exact'] / self.stats['judged'], 3) if self.stats['judged'] else None,
            'judge_list_usd': round(self.stats['judge_list_usd'], 4),
            'real_spend_usd': real,
        })

    def _inspector(self, name):
        kw = {}
        if name == 'claude':
            kw = {'vocab': self.brief.judge.get('vocab'), 'describe': self.brief.judge.get('describe')}
            if self.brief.judge.get('model'):
                kw['model'] = self.brief.judge['model']
        return backends.judge_inspector(name, **kw)

    def _balance(self):
        """Реальный баланс провайдера (live): разница до/после — фактические траты, а не оценка по прайсу."""
        fn = getattr(self.frame.backend, 'balance', None)
        if self.mode != 'live' or not fn:
            return None
        try:
            return fn()
        except Exception as e:
            self.log.event('orchestrator', '*', 'balance_failed', error=str(e)[:200])
            return None

    def _video(self, shot, frame):
        try:
            return self.video.run(shot, frame, self.out, self.video_price)
        except BudgetExceeded as e:
            self.stopped_by_budget = True
            self.log.event('orchestrator', shot.id, 'budget_stop', reason=str(e))
            return None
