"""Озвучка и пословный тайминг: Piper TTS (локально) → faster-whisper (локально) → выравнивание по сценарию.

Текст на экране всегда из сценария: Whisper даёт только время слов. Ослышки («которые» вместо «которое»)
так исправляются автоматически — это ручная «правка ослышек» из монтажа Shorts, переведённая в код.
"""
import difflib
import os
import re
import subprocess
import wave

FUNC = {'в', 'на', 'с', 'и', 'для', 'не', 'о', 'что', 'как', 'за', 'при', 'от', 'по', 'а', 'но', 'мы', 'я', 'вы',
        'без', 'или', 'так', 'же', 'то', 'это', 'к', 'у', 'из', 'до', '—'}


def norm(w):
    return re.sub(r'[^\wё-]', '', w.lower()).replace('ё', 'е')


def wav_duration(path):
    with wave.open(path) as w:
        return w.getnframes() / w.getframerate()


def tts(text, out_wav, piper_bin, voice, length_scale=1.0):
    subprocess.run([piper_bin, '--model', voice, '--output_file', out_wav, '--length_scale', str(length_scale)],
                   input=text, text=True, capture_output=True, check=True)
    return wav_duration(out_wav)


_MODELS = {}


def transcribe(path, model='small', language='ru'):
    from faster_whisper import WhisperModel
    if model not in _MODELS:
        _MODELS[model] = WhisperModel(model, device='cpu', compute_type='int8')
    segs, _ = _MODELS[model].transcribe(path, language=language, word_timestamps=True)
    return [{'w': w.word.strip(), 's': round(w.start, 3), 'e': round(w.end, 3)} for s in segs for w in s.words]


def align(script, heard):
    """Слова сценария получают время распознанных слов. Возвращает (слова, ослышки)."""
    sw = script.split()
    a, b = [norm(w) for w in sw], [norm(h['w']) for h in heard]
    out = [None] * len(sw)
    mishear = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == 'equal':
            for k in range(i2 - i1):
                out[i1 + k] = {'t': sw[i1 + k], 's': heard[j1 + k]['s'], 'e': heard[j1 + k]['e']}
        elif tag == 'replace' and i2 - i1 == j2 - j1:
            for k in range(i2 - i1):
                out[i1 + k] = {'t': sw[i1 + k], 's': heard[j1 + k]['s'], 'e': heard[j1 + k]['e']}
                mishear.append({'script': sw[i1 + k], 'heard': heard[j1 + k]['w']})
        elif tag in ('replace', 'insert', 'delete'):
            for k in range(i1, i2):
                mishear.append({'script': sw[k], 'heard': None})
    # слова без пары — время делим между соседями с известным временем
    known = [i for i, w in enumerate(out) if w]
    for i, w in enumerate(out):
        if w:
            continue
        prev = max([k for k in known if k < i], default=None)
        nxt = min([k for k in known if k > i], default=None)
        s = out[prev]['e'] if prev is not None else 0.0
        e = out[nxt]['s'] if nxt is not None else s + 0.4
        run = [k for k in range(i, len(out)) if not out[k] and (nxt is None or k < nxt)]
        step = max(0.05, (e - s) / max(1, len(run)))
        for n, k in enumerate(run):
            out[k] = {'t': sw[k], 's': round(s + n * step, 3), 'e': round(s + (n + 1) * step, 3)}
        known = [i for i, w in enumerate(out) if w]
    return out, mishear


def phrases(words, n=3):
    """Фразы ≤ n слов (максимум n+1, если иначе на конце остаётся служебное слово); не висящее последнее слово."""
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if norm(w['t']) in FUNC and nxt and len(cur) <= n:
            continue
        if not nxt or len(cur) >= n or w['t'][-1] in ',.?!:' or (len(cur) >= 2 and norm(nxt['t']) in FUNC):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    if len(out) > 1 and len(out[-1]) == 1 and len(out[-2]) <= n and out[-2][-1]['t'][-1] not in ',.?!:':
        tail = out.pop()  # не out[-2] += out.pop(): индекс -2 пересчитался бы уже после pop
        out[-1] += tail
    return [{'words': p, 's': p[0]['s'], 'e': p[-1]['e']} for p in out]
