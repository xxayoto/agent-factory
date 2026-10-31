"""Агент монтажа: ролики/кадры шотов → один ролик с субтитрами.

engine=ffmpeg — субтитры кусками по 2–4 слова без звука (логика test_bs_media/work/edit.py);
engine=remotion — озвучка подписей (Piper), тайминг слов (faster-whisper, текст из сценария),
пословная подсветка и наезд камеры на кадрах в Remotion (remotion/, по образцу remotion_bs).
"""
import json
import os
import subprocess
import wave

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


REMOTION = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'remotion')


def _video_len(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path],
                         capture_output=True, text=True).stdout.strip()
    return float(out or 0)


class MontageAgent(Agent):
    name = 'montage'

    def __init__(self, log, brief, voice_cfg=None):
        super().__init__(log)
        self.brief, self.voice_cfg = brief, voice_cfg or {}
        self.timeline = None

    def say(self, text):
        """Текст для синтеза: латиница брендов → как произносить (Piper читает «AURA» как «о-о-о», найдено QC 01.11).
        Субтитры остаются как в сценарии."""
        rep = dict(self.voice_cfg.get('say') or {})
        b = self.brief.brand
        if b.get('name') and b.get('say'):
            rep.setdefault(b['name'], b['say'])
        for k, v in rep.items():
            text = text.replace(k, v)
        return text.replace(' — ', ', ')

    def voice_take(self, text, wav, tries=4):
        """Озвучка с проверкой: Piper звучит каждый раз немного иначе, дубль со смазанными словами пересинтезируется
        (замер 01.11: тот же текст давал WER 0 % и 20 % в разных дублях). Берётся первый чистый или лучший дубль."""
        from ..voice import transcribe, tts, wav_duration
        from .qc import wer
        spoken = self.say(text).split()
        best, takes = None, []
        for i in range(tries):
            p = wav if i == 0 else f'{wav[:-4]}_take{i}.wav'
            tts(self.say(text), p, self.voice_cfg['bin'], self.voice_cfg['voice'], self.voice_cfg.get('length_scale', 1.0))
            heard = transcribe(p, self.voice_cfg.get('whisper', 'medium'))
            e = wer(spoken, [h['w'] for h in heard])
            takes.append(round(e, 3))
            if best is None or e < best[0]:
                best = (e, p, heard)
            if e == 0:
                break
        if best[1] != wav:
            os.replace(best[1], wav)
        return wav_duration(wav), best[2], takes

    def run_remotion(self, items, out_dir, min_still=2.2, pad=0.35):
        """Озвучка → тайминг слов → props.json → remotion render. Возвращает путь; таймлайн — в self.timeline (для QC)."""
        from ..voice import align, phrases
        def _run():
            work = os.path.join(out_dir, 'montage_voice')
            os.makedirs(work, exist_ok=True)
            segs, words, ph, mishear, pcm, rate, t = [], [], [], [], b'', None, 0.0
            for shot, src in items:
                text = (shot.caption or '').strip()
                vdur, wav = 0.0, None
                heard = None
                if text:
                    wav = os.path.join(work, f'{shot.id}.wav')
                    vdur, heard, takes = self.voice_take(text, wav)
                    self.log.event(self.name, shot.id, 'voice', takes=takes)
                is_video = src.endswith('.mp4')
                dur = max(vdur + pad, min_still)
                if is_video:
                    dur = min(_video_len(src), max(dur, min_still))
                dur = round(dur * 25) / 25
                if wav:
                    ws, mis = align(text, heard)
                    shot_words = [{'t': w['t'], 's': round(w['s'] + t, 3), 'e': round(min(w['e'], dur) + t, 3)} for w in ws]
                    words += shot_words
                    ph += phrases(shot_words)  # фраза не переходит через склейку (найдено глазами на live #1)
                    mishear += [{**m, 'shot': shot.id} for m in mis]
                    with wave.open(wav) as w:
                        rate, frames = w.getframerate(), w.readframes(w.getnframes())
                        width = w.getsampwidth()
                else:
                    frames, width = b'', 2
                rate = rate or 22050
                need = int(round(dur * rate)) * width
                pcm += frames[:need] + b'\x00' * max(0, need - len(frames))
                name = os.path.basename(src)
                segs.append({'src': name, 'kind': 'video' if is_video else 'image', 'dur': dur})
                t += dur
            voice = os.path.join(out_dir, 'voice.wav')
            with wave.open(voice, 'wb') as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(rate or 22050)
                w.writeframes(pcm)
            props = {'fps': 25, 'width': self.brief.width, 'height': self.brief.height, 'segments': segs,
                     'phrases': ph, 'audio': 'voice.wav', 'accent': self.voice_cfg.get('accent', '#FFD54A')}
            pp = os.path.join(out_dir, 'montage_props.json')
            with open(pp, 'w') as f:
                json.dump(props, f, ensure_ascii=False)
            out = os.path.join(out_dir, 'montage.mp4')
            r = subprocess.run(['npx', 'remotion', 'render', 'src/index.ts', 'Montage', out, f'--props={pp}',
                                f'--public-dir={out_dir}', '--log=error'], cwd=REMOTION, capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f'remotion render: {(r.stderr or r.stdout)[-500:]}')
            spoken = ' '.join(self.say((sh.caption or '').strip()) for sh, _ in items).split()
            starts = [sum(x['dur'] for x in segs[:i]) for i in range(len(segs))]
            self.timeline = {'duration': round(t, 3), 'words': words, 'phrases': ph, 'spoken': spoken,
                             'segments': [[round(a, 3), x['dur']] for a, x in zip(starts, segs)],
                             'sources': [s for _, s in items], 'mishear': mishear}
            return out
        out = self.timed('montage', _run)
        self.log.event(self.name, 'montage', 'done', path=out, engine='remotion', shots=[s.id for s, _ in items],
                       duration=self.timeline['duration'], mishear=self.timeline['mishear'])
        return out

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
            self.timeline = {'duration': sum(float((s.video or {}).get('seconds', 3)) if p.endswith('.mp4') else seconds_per_still
                                             for s, p in items), 'words': [], 'phrases': [], 'sources': [p for _, p in items]}
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', lst, '-c', 'copy',
                            '-movflags', '+faststart', out], check=True)
            return out
        out = self.timed('montage', _run)
        self.log.event(self.name, 'montage', 'done', path=out, shots=[s.id for s, _ in items])
        return out
