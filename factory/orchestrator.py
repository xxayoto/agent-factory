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
        return yaml.safe_load(f)


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
    for mode, fb, vb in (('mock', 'mock', 'mock'), ('live', 'runninghub_seedream', 'runninghub_h3')):
        lo = n_frames * b.price('frame', fb) + n_vid * b.price('video', vb)
        hi = n_frames * (1 + retries) * b.price('frame', fb) + n_vid * b.price('video', vb)
        est[mode] = (round(lo, 3), round(hi, 3))
    return tasks, est


class Orchestrator:
    def __init__(self, brief, mode='mock', seed=0, memory_path=None, runs_dir=None, allow_spend=None, defect_rate=0.3):
        self.brief, self.mode = brief, mode
        self.memory = Memory(memory_path) if memory_path else Memory()
        self.runs_dir = runs_dir or os.path.join(ROOT, 'runs')
        limit = float(brief.limits['budget_usd'])
        if mode == 'live':
            if allow_spend is None:
                raise SystemExit('live-режим тратит деньги: нужен явный --allow-spend <USD>')
            limit = min(limit, float(allow_spend))
            cfg = live_config()
            frame_be = backends.frame_backend(cfg['live']['frame'], model=cfg.get('wavespeed', {}).get('frame_model'))
            inspector = backends.judge_inspector(cfg['live']['judge'])
            self.video_be, self.video_price = backends.video_backend('local_kenburns'), 'local_kenburns'
        else:
            frame_be = backends.frame_backend('mock', seed=seed, defect_rate=defect_rate)
            inspector = backends.judge_inspector('mock')
            self.video_be, self.video_price = backends.video_backend('mock'), 'mock'
        self.budget = Budget(limit)
        run_id = f"{dt.datetime.now():%Y%m%d-%H%M%S}-{brief.project}-{uuid.uuid4().hex[:4]}"
        self.log = RunLog(self.runs_dir, run_id, mode)
        self.out = os.path.join(self.log.dir, 'out')
        os.makedirs(self.out, exist_ok=True)
        self.frame = FrameAgent(self.log, frame_be, self.budget, brief)
        self.judge = JudgeAgent(self.log, inspector, brief)
        self.card = CardAgent(self.log, brief)
        self.video = VideoAgent(self.log, self.video_be, self.budget, brief)
        self.montage = MontageAgent(self.log, brief)
        self.stats = {'generated': 0, 'rejected': 0, 'first_pass': 0, 'learned': 0, 'rules_used': set()}
        self.stopped_by_budget = False

    def shot_loop(self, shot):
        """Генерация → судья → перегенерация с замечаниями, пока не примут или не кончатся попытки."""
        product = self.brief.products[shot.product]
        feedback = []
        for attempt in range(1, self.brief.limits['max_retries'] + 2):
            rules = self.memory.for_product(product)
            self.stats['rules_used'].update(r['id'] for r in rules)
            try:
                path, info = self.frame.run(shot, product, rules, feedback, attempt, self.out)
            except BudgetExceeded as e:
                self.stopped_by_budget = True
                self.log.event('orchestrator', shot.id, 'budget_stop', reason=str(e))
                return None
            self.stats['generated'] += 1
            ok, tech, mism = self.judge.review(shot, product, path, info.get('raw_size'), rules, attempt)
            if ok:
                if attempt == 1:
                    self.stats['first_pass'] += 1
                return path
            self.stats['rejected'] += 1
            for m in mism:
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
            items = [(s, videos.get(s.id) or cards.get(s.id) or frames[s.id]) for s in approved]
            montage = self.montage.run(items, self.out)

        g, r = self.stats['generated'], self.stats['rejected']
        return self.log.finish({
            'project': b.project, 'brief': os.path.relpath(b.path, ROOT),
            'shots': len(b.shots), 'frames_generated': g, 'frames_approved': len(approved), 'frames_rejected': r,
            'reject_rate': round(r / g, 3) if g else 0.0,
            'first_pass_yield': round(self.stats['first_pass'] / len(b.shots), 3),
            'failed_shots': [s.id for s in b.shots if not frames[s.id]],
            'rules_applied': len(self.stats['rules_used']), 'rules_learned': self.stats['learned'],
            'cards': len(cards), 'videos': len(videos), 'montage': montage and os.path.relpath(montage, self.log.dir),
            'stopped_by_budget': self.stopped_by_budget, 'budget_usd': self.budget.limit,
        })

    def _video(self, shot, frame):
        try:
            return self.video.run(shot, frame, self.out, self.video_price)
        except BudgetExceeded as e:
            self.stopped_by_budget = True
            self.log.event('orchestrator', shot.id, 'budget_stop', reason=str(e))
            return None
