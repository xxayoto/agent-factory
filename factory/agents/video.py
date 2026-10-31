"""Агент видео: принятый кадр → ролик → проверка судьёй.

Mock — наезд камеры ffmpeg (бесплатно); live — i2v (WaveSpeed Hailuo 2.3 или H3 на RunningHub).
Судья видео: кадры из середины и конца ролика сверяются с товаром тем же судьёй, что и кадры, —
в движении модели «плавят» изделие (камень меняет форму, гвоздик отрастает в подвеску).
Перегенерации видео нет (дорого): вердикт и причины уходят в журнал и метрики.
"""
import os
import shutil
import subprocess

from .base import Agent


def sample_frames(video, out_dir, at=(0.5, 0.95)):
    dur = float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', video],
                               capture_output=True, text=True).stdout.strip() or 0)
    paths = []
    for k in at:
        p = os.path.join(out_dir, f'{os.path.splitext(os.path.basename(video))[0]}_t{int(k * 100):02d}.png')
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{dur * k:.2f}', '-i', video, '-frames:v', '1', p], check=True)
        paths.append(p)
    return paths


class VideoAgent(Agent):
    name = 'video'

    def __init__(self, log, backend, budget, brief, judge=None):
        super().__init__(log)
        self.backend, self.budget, self.brief, self.judge = backend, budget, brief, judge

    def run(self, shot, frame, out_dir, price_key):
        v = shot.video or {}
        out = os.path.join(out_dir, f'{shot.id}.mp4')
        if os.path.exists(out):  # --resume: ролик уже оплачен и скачан
            self.log.event(self.name, shot.id, 'reused', path=out)
            return out
        usd = self.budget.reserve('video', price_key)
        try:
            info = self.timed(shot.id, self.backend.animate, frame, out, v.get('motion', ''), float(v.get('seconds', 3)),
                              self.brief.width, self.brief.height) or {}
        except Exception:
            if os.path.exists(out + '.task'):  # задача создана — деньги ушли
                self.log.add_cost(usd)
            raise
        self.log.add_cost(usd)
        self.log.event(self.name, shot.id, 'done', path=out, cost_usd=usd, motion=v.get('motion', ''), task_id=info.get('task_id'))
        if self.judge and self.brief.judge.get('check_video', True):
            self.review(shot, out, out_dir)
        return out

    def review(self, shot, video, out_dir):
        product = self.brief.products[shot.product]
        verdicts = []
        src = os.path.join(out_dir, [f for f in sorted(os.listdir(out_dir)) if f.startswith(shot.id + '_a') and f.endswith('.png.mock.json')][-1]) \
            if any(f.startswith(shot.id + '_a') and f.endswith('.png.mock.json') for f in os.listdir(out_dir)) else None
        for p in sample_frames(video, out_dir):
            if src:  # mock: истина ролика — истина исходного кадра (наезд камеры товар не меняет)
                shutil.copy(src, p + '.mock.json')
            try:
                ok, tech, mism, extra = self.judge.review(shot, product, p, None, [], attempt=os.path.basename(p), kind='video_frame')
            except Exception as e:
                self.log.event(self.name, shot.id, 'video_judge_error', path=p, error=str(e)[:300])
                return None
            verdicts.append(ok)
        ok = all(verdicts)
        self.log.event(self.name, shot.id, 'video_approved' if ok else 'video_rejected', path=video, frames_ok=verdicts)
        return ok
