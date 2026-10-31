"""Процедурная отрисовка серёг по атрибутам. Нужна mock-генератору, чтобы дефект был виден на картинке
(судья-модель должна найти его глазами), и make_assets.py — чтобы «фото товара» и кадр рисовались одинаково."""
from PIL import Image, ImageDraw

METAL = {'gold': (205, 165, 70), 'silver': (190, 195, 205)}
TYPES = {'stud', 'hoop', 'drop'}


def renderable(attrs):
    return attrs.get('earring_type') in TYPES and attrs.get('metal', 'gold') in METAL


def _stone(d, cx, cy, r, stone, metal):
    if stone == 'pearl':
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(240, 236, 228))
        d.ellipse((cx - r * 0.55, cy - r * 0.7, cx - r * 0.1, cy - r * 0.25), fill=(255, 255, 255))
    elif stone == 'crystal':
        d.regular_polygon((cx, cy, r * 0.95), 8, fill=(200, 225, 245), outline=(255, 255, 255))
    else:
        d.ellipse((cx - r * 0.6, cy - r * 0.6, cx + r * 0.6, cy + r * 0.6), fill=tuple(min(255, c + 30) for c in metal))


def earrings(attrs, size=(400, 400)):
    w, h = size
    im = Image.new('RGBA', size, (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    metal = METAL[attrs.get('metal', 'gold')]
    xs = (w * 0.325, w * 0.675) if attrs.get('count', 'pair') == 'pair' else (w * 0.5,)
    cy, R = h * 0.35, w * 0.105
    for cx in xs:
        t = attrs['earring_type']
        if t == 'hoop':
            d.ellipse((cx - R * 1.3, cy - R * 1.5, cx + R * 1.3, cy + R * 1.5), outline=metal, width=max(3, w // 33))
            continue
        d.ellipse((cx - R, cy - R, cx + R, cy + R), fill=metal)
        _stone(d, cx, cy, R * 0.8, attrs.get('stone'), metal)
        if t == 'drop':
            d.line((cx, cy + R, cx, cy + R * 3.2), fill=metal, width=max(2, w // 100))
            _stone(d, cx, cy + R * 3.7, R * 0.55, attrs.get('stone') or 'pearl', metal)
    return im
