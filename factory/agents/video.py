"""Агент видео: принятый кадр → короткий ролик. Mock/локально — наезд камеры ffmpeg; live (M2) — i2v на RunningHub."""
import os

from .base import Agent


class VideoAgent(Agent):
    name = 'video'

    def __init__(self, log, backend, budget, brief):
        super().__init__(log)
        self.backend, self.budget, self.brief = backend, budget, brief

    def run(self, shot, frame, out_dir, price_key):
        v = shot.video or {}
        usd = self.budget.reserve('video', price_key)
        out = os.path.join(out_dir, f'{shot.id}.mp4')
        self.timed(shot.id, self.backend.animate, frame, out, v.get('motion', ''), float(v.get('seconds', 3)),
                   self.brief.width, self.brief.height)
        self.log.add_cost(usd)
        self.log.event(self.name, shot.id, 'done', path=out, cost_usd=usd, motion=v.get('motion', ''))
        return out
