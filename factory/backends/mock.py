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

from .render import earrings, renderable

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
        observed = dict(req['force_observed']) if req.get('force_observed') else {}
        for attr, val in req['attributes'].items():
            if attr in observed:
                continue
            p = self.defect_rate / (IMPROVE if attr in req['constraints'] else 1)
            observed[attr] = wrong_value(attr, val) if rng.random() < p else val
        w, h = req['width'], req['height']
        bg = cover(Image.open(req['reference']).convert('RGB'), w, h) if req.get('reference') else Image.new('RGB', (w, h), (200, 190, 180))
        # дефект рисуется, а не подписывается: судья-модель должна увидеть его сама
        prod = earrings(observed) if renderable(observed) else Image.open(req['photos'][0]).convert('RGBA')
        pw = int(w * 0.5)
        prod = prod.resize((pw, int(prod.height * pw / prod.width)), Image.LANCZOS)
        bg.paste(prod, ((w - pw) // 2, int(h * 0.25)), prod)
        d = ImageDraw.Draw(bg)
        f = ImageFont.load_default(size=max(12, w // 40))
        d.text((w * 0.03, h * 0.02), f"MOCK {req['shot']} #{req['attempt']}", font=f, fill=(255, 255, 255))
        bg.save(out_path, quality=92)
        with open(out_path + '.mock.json', 'w') as fh:
            json.dump({'observed': observed}, fh, ensure_ascii=False)
        return {'raw_size': [w, h]}


class MockInspector:
    """Mock-судья «видит» атрибуты, записанные генератором. В live его место займёт vision-модель (M2)."""
    name = 'mock'

    def inspect(self, frame_path, product, scene=None):
        with open(frame_path + '.mock.json') as f:
            return json.load(f)['observed']
