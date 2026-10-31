"""Агент монтажа: ролики/карточки шотов → один ролик с субтитрами кусками по 2–4 слова
(логика разбивки из test_bs_media/work/edit.py; пословная подсветка через Remotion — M3)."""
import os
import subprocess

from PIL import Image, ImageDraw

from .base import Agent
from .card import font


FUNC = {'в', 'на', 'с', 'и', 'для', 'не', 'о', 'что', 'как', 'за', 'при', 'от', 'по', 'а', 'но', 'мы', 'я', 'вы',
        'без', 'или', 'так', 'же', 'то', 'это', 'к', 'у', 'из', 'до', '—'}


def chunks(text, n=3):
    """Фразы до n слов. Служебное слово не заканчивает кусок, а начинает следующий (правило из subs_bs.py)."""
    ws, out, cur = text.split(), [], []
    for i, w in enumerate(ws):
        cur.append(w)
        nxt = ws[i + 1] if i + 1 < len(ws) else None
        if w.lower() in FUNC or not nxt:
            continue
        if len(cur) >= n or w[-1] in ',.?!:' or (len(cur) >= 2 and nxt.lower() in FUNC):
            out.append(' '.join(cur))
            cur = []
    if cur:
        if out and len(cur) == 1 and len(out[-1].split()) <= n and out[-1][-1] not in ',.?!:':
            out[-1] += ' ' + cur[0]  # одно слово в конце не оставляем висеть
        else:
            out.append(' '.join(cur))
    return out


def caption_png(text, path, width):
    f = font(width * 0.062, 5)
    probe = ImageDraw.Draw(Image.new('RGB', (10, 10)))
    lines, cur = [], ''
    for w in text.upper().split():
        t = (cur + ' ' + w).strip()
        if probe.textlength(t, font=f) > width * 0.85 and cur:
            lines.append(cur)
            cur = w
        else:
            cur = t
    lines.append(cur)
    lh = int(f.size * 1.2)
    img = Image.new('RGBA', (width, lh * len(lines) + 20), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for k, ln in enumerate(lines):
        d.text(((width - d.textlength(ln, font=f)) / 2, 10 + k * lh), ln, font=f, fill='white',
               stroke_width=max(2, width // 160), stroke_fill='black')
    img.save(path)


class MontageAgent(Agent):
    name = 'montage'

    def __init__(self, log, brief):
        super().__init__(log)
        self.brief = brief

    def segment(self, src, caption, dur, out, tmp):
        W, H = self.brief.width, self.brief.height
        is_video = src.endswith('.mp4')
        inp = ['-i', src] if is_video else ['-loop', '1', '-t', f'{dur:.2f}', '-i', src]
        fc, prev, extra = [f'[0:v]scale={W}:{H},fps=25,format=yuv420p[b]'], 'b', []
        cs = chunks(caption) if caption else []
        step = dur / max(1, len(cs))
        for k, c in enumerate(cs):
            p = os.path.join(tmp, f'{os.path.basename(out)}_{k}.png')
            caption_png(c, p, W)
            extra += ['-i', p]
            fc.append(f"[{prev}][{k + 1}:v]overlay=x=0:y=H*0.72:enable='between(t,{k * step:.2f},{(k + 1) * step:.2f})'[v{k}]")
            prev = f'v{k}'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', *inp, *extra, '-filter_complex', ';'.join(fc), '-map', f'[{prev}]',
                        '-t', f'{dur:.2f}', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-an', out], check=True)

    def run(self, items, out_dir, seconds_per_still=2.5):
        """items: [(shot, источник: mp4 или картинка)] в порядке брифа."""
        def _run():
            tmp = os.path.join(out_dir, 'montage_parts')
            os.makedirs(tmp, exist_ok=True)
            segs = []
            for shot, src in items:
                dur = float((shot.video or {}).get('seconds', 3)) if src.endswith('.mp4') else seconds_per_still
                seg = os.path.join(tmp, f'{shot.id}.mp4')
                self.segment(src, shot.caption, dur, seg, tmp)
                segs.append(seg)
            lst = os.path.join(tmp, 'list.txt')
            with open(lst, 'w') as f:
                f.write('\n'.join(f"file '{s}'" for s in segs))
            out = os.path.join(out_dir, 'montage.mp4')
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', lst, '-c', 'copy',
                            '-movflags', '+faststart', out], check=True)
            return out
        out = self.timed('montage', _run)
        self.log.event(self.name, 'montage', 'done', path=out, shots=[s.id for s, _ in items])
        return out
