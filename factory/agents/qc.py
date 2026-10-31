"""QC-агент монтажа: готовый mp4 против чек-листа монтажа (тот же, по которому ролики проверяются руками перед показом).

Проверки: длительность против таймлайна, звук есть, нет чёрных кадров, нет стоп-кадров, «субтитры = звук» (WER),
рассинхрон субтитров и речи, фразы без служебного слова на конце, один слой текста.
"""
import json
import os
import re
import statistics
import subprocess

from ..voice import FUNC, align, norm, transcribe
from .base import Agent


def probe(path):
    j = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration:stream=codec_type', '-of', 'json', path],
                                  capture_output=True, text=True).stdout)
    return float(j['format']['duration']), {s['codec_type'] for s in j.get('streams', [])}


def _filter_log(path, vf):
    return subprocess.run(['ffmpeg', '-v', 'info', '-i', path, '-vf', vf, '-an', '-f', 'null', '-'],
                          capture_output=True, text=True).stderr


def black_segments(path, min_dur=0.2):
    log = _filter_log(path, f'blackdetect=d={min_dur}:pix_th=0.10')
    return [(float(a), float(b)) for a, b in re.findall(r'black_start:([\d.]+) black_end:([\d.]+)', log)]


def freeze_segments(path, min_dur=1.0):
    log = _filter_log(path, f'freezedetect=n=0.001:d={min_dur}')
    starts = [float(x) for x in re.findall(r'freeze_start: ([\d.]+)', log)]
    ends = [float(x) for x in re.findall(r'freeze_end: ([\d.]+)', log)]
    return list(zip(starts, ends + [None] * (len(starts) - len(ends))))


def transcribe_by_segments(video, segments, model):
    """Звук готового ролика, распознанный по сегментам таймлайна: на склеенной речи Whisper додумывает по контексту
    («каждая грань» → «каждый гран»), хотя те же фразы по отдельности распознаёт без ошибок (замер 01.11)."""
    import tempfile
    out = []
    d = tempfile.mkdtemp(prefix='qc-')
    for i, (start, dur) in enumerate(segments):
        p = os.path.join(d, f'{i}.wav')
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{start:.3f}', '-t', f'{dur:.3f}', '-i', video, '-vn', '-ac', '1',
                        '-ar', '16000', p], check=True)
        out += [{**w, 's': round(w['s'] + start, 3), 'e': round(w['e'] + start, 3)} for w in transcribe(p, model)]
    return out


def wer(ref_words, hyp_words):
    """Доля ошибок по словам (Левенштейн по нормализованным словам)."""
    r, h = [norm(w) for w in ref_words if norm(w)], [norm(w) for w in hyp_words if norm(w)]
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(h)] / max(1, len(r))


class QCAgent(Agent):
    name = 'qc'

    def __init__(self, log, limits=None, whisper='medium'):
        super().__init__(log)
        self.lim = {'dur_tol': 0.2, 'black': 0.2, 'freeze': 1.0, 'wer': 0.15, 'sync': 0.3, **(limits or {})}
        self.whisper = whisper  # small путает синтетическую речь чаще, чем её путает человек

    def run(self, video, timeline):
        """timeline: {'duration', 'words': [{t,s,e}], 'phrases': [...], 'sources': [...]} — что монтаж собирался сделать."""
        def _run():
            checks = []
            add = lambda name, ok, value, limit: checks.append({'name': name, 'ok': bool(ok), 'value': value, 'limit': limit})
            dur, streams = probe(video)
            add('длительность = таймлайн', abs(dur - timeline['duration']) <= self.lim['dur_tol'],
                round(dur, 2), f"{timeline['duration']:.2f} ± {self.lim['dur_tol']}")
            add('звук есть', 'audio' in streams or not timeline['words'], sorted(streams), 'audio')
            blacks = black_segments(video, self.lim['black'])
            add('нет чёрных кадров', not blacks, blacks, f"< {self.lim['black']} с")
            freezes = freeze_segments(video, self.lim['freeze'])
            add('нет стоп-кадров', not freezes, freezes, f"< {self.lim['freeze']} с")
            add('один слой текста (в монтаже нет карточек)', not any('_card' in s for s in timeline['sources']),
                timeline['sources'], 'ролики и чистые кадры')
            bad_ends = [' '.join(w['t'] for w in p['words']) for p in timeline['phrases']
                        if norm(p['words'][-1]['t']) in FUNC or not norm(p['words'][-1]['t']) or len(p['words']) > 4]
            add('фразы без обрыва на служебном слове', not bad_ends, bad_ends, '≤ 4 слов, не служебное на конце')
            cuts = [a for a, _ in (timeline.get('segments') or [])][1:]
            crossing = [' '.join(w['t'] for w in p['words']) for p in timeline['phrases']
                        if any(p['s'] < c - 0.02 and p['e'] > c + 0.02 for c in cuts)]
            add('фраза не переходит через склейку', not crossing, crossing, 'фраза внутри одного шота')
            if timeline['words']:
                heard = (transcribe_by_segments(video, timeline['segments'], self.whisper) if timeline.get('segments')
                         else transcribe(video, self.whisper))
                script = [w['t'] for w in timeline['words'] if norm(w['t'])]
                e = wer(timeline.get('spoken') or script, [h['w'] for h in heard])
                add('субтитры = звук (WER)', e <= self.lim['wer'], round(e, 3), f"≤ {self.lim['wer']}")
                # тайминги сверяются по словам сценария без одиночных знаков: иначе «—» сдвигает пары на одно слово
                planned = [w for w in timeline['words'] if norm(w['t'])]
                got, mis = align(' '.join(w['t'] for w in planned), heard)
                drift = [abs(g['s'] - w['s']) for g, w in zip(got, planned)]
                if mis:
                    add('слова, которые звук и сценарий расходятся', True, mis[:10], 'для справки')
                med = statistics.median(drift) if drift else 0.0
                add('синхрон субтитров и речи (медиана)', med <= self.lim['sync'], round(med, 3), f"≤ {self.lim['sync']} с")
            return checks
        checks = self.timed('qc', _run)
        ok = all(c['ok'] for c in checks)
        out = os.path.splitext(video)[0] + '.qc.json'
        with open(out, 'w') as f:
            json.dump({'ok': ok, 'checks': checks}, f, ensure_ascii=False, indent=2)
        self.log.event(self.name, 'montage', 'qc_passed' if ok else 'qc_failed', path=video,
                       failed=[c['name'] for c in checks if not c['ok']], checks=checks)
        return ok, checks
