"""Синтетические фото товаров и референсы для демо-брифа (рисуются кодом, без чужих фото и брендов).

python3 examples/make_assets.py
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from factory.backends.render import earrings  # noqa: E402

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets')
os.makedirs(D, exist_ok=True)


def product(name, attrs):
    earrings(attrs).save(os.path.join(D, name))


def ref(name, top, bottom):
    w, h = 600, 800
    im = Image.new('RGB', (w, h))
    px = im.load()
    for y in range(h):
        t = y / h
        c = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        for x in range(w):
            px[x, y] = c
    d = ImageDraw.Draw(im)
    d.ellipse((150, 120, 450, 520), fill=tuple(min(255, v + 25) for v in top))
    im.filter(ImageFilter.GaussianBlur(30)).save(os.path.join(D, name))


product('stud_pearl.png', {'earring_type': 'stud', 'metal': 'gold', 'stone': 'pearl'})
product('hoop_silver.png', {'earring_type': 'hoop', 'metal': 'silver'})
product('stud_crystal.png', {'earring_type': 'stud', 'metal': 'gold', 'stone': 'crystal'})
ref('ref_studio_warm.jpg', (214, 190, 170), (120, 95, 80))
ref('ref_studio_cool.jpg', (180, 195, 210), (60, 70, 90))
print('assets ->', D)
