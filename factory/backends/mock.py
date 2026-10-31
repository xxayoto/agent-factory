"""Mock-генерация: собирает «кадр» из фото товара и референса, с заданной вероятностью
искажает атрибуты товара — так, как это делают реальные модели (гвоздик → подвеска, золото → серебро).

Модель дрейфа — допущение для симуляции, не замер: базовая вероятность дефекта на атрибут,
и в IMPROVE раз меньше, если агент кадров передал этот атрибут как жёсткое ограничение
(из правила памяти или замечания судьи).
Настоящие цифры даёт только live-режим.
"""
import json
import random

from PIL import Image, ImageDraw, ImageFont

DRIFT = {
    'earring_type': {'stud': 'drop', 'hoop': 'stud', 'drop': 'stud'},
    'metal': {'gold': 'silver', 'silver': 'gold'},
    'stone': {'pearl': 'crystal', 'crystal': 'pearl'},
    'count': {'pair': 'single', 'single': 'pair'},
}
IMPROVE = 6


def cover(im, w, h):
    s = max(w / im.width, h / im.height)
    im = im.resize((max(w, round(im.width * s)), max(h, round(im.height * s))), Image.LANCZOS)
    x, y = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((x, y, x + w, y + h))


def wrong_value(attr, val):
    return DRIFT.get(attr, {}).get(val, f'not_{val}')


class MockFrames:
    name = 'mock'

    def __init__(self, seed=0, defect_rate=0.3):
        self.seed, self.defect_rate = seed, defect_rate

    def generate(self, req, out_path):
        rng = random.Random(f"{self.seed}:{req['shot']}:{req['attempt']}")
        observed = {}
        for attr, val in req['attributes'].items():
            p = self.defect_rate / (IMPROVE if attr in req['constraints'] else 1)
            observed[attr] = wrong_value(attr, val) if rng.random() < p else val
        w, h = req['width'], req['height']
        bg = cover(Image.open(req['reference']).convert('RGB'), w, h) if req.get('reference') else Image.new('RGB', (w, h), (200, 190, 180))
        prod = Image.open(req['photos'][0]).convert('RGBA')
        pw = int(w * 0.5)
        prod = prod.resize((pw, int(prod.height * pw / prod.width)), Image.LANCZOS)
        px, py = (w - pw) // 2, int(h * 0.3)
        bg.paste(prod, (px, py), prod)
        d = ImageDraw.Draw(bg)
        if observed.get('earring_type') == 'drop':  # видимый дефект: у гвоздика выросла подвеска
            cx, cy = w // 2, py + prod.height
            d.line((cx, cy - prod.height * 0.15, cx, cy + h * 0.08), fill=(180, 150, 60), width=max(2, w // 200))
            r = w // 30
            d.ellipse((cx - r, cy + h * 0.08, cx + r, cy + h * 0.08 + 2 * r), fill=(235, 235, 240))
        bad = [f'{a}: {v}' for a, v in observed.items() if v != req['attributes'][a]]
        f = ImageFont.load_default(size=max(12, w // 40))
        d.text((w * 0.03, h * 0.02), f"MOCK {req['shot']} #{req['attempt']}", font=f, fill=(255, 255, 255))
        for i, t in enumerate(bad):
            d.text((w * 0.03, h * 0.02 + (i + 1) * f.size * 1.3), t, font=f, fill=(220, 40, 40))
        bg.save(out_path, quality=92)
        with open(out_path + '.mock.json', 'w') as fh:
            json.dump({'observed': observed}, fh, ensure_ascii=False)
        return {'raw_size': [w, h]}


class MockInspector:
    """Mock-судья «видит» атрибуты, записанные генератором. В live его место займёт vision-модель (M2)."""
    name = 'mock'

    def inspect(self, frame_path, product):
        with open(frame_path + '.mock.json') as f:
            return json.load(f)['observed']
