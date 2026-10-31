"""Синтетические фото товаров и референсы для демо-брифа (рисуются кодом, без чужих фото и брендов).

python3 examples/make_assets.py
"""
import os

from PIL import Image, ImageDraw, ImageFilter

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets')
os.makedirs(D, exist_ok=True)


def product(name, draw):
    im = Image.new('RGBA', (400, 300), (0, 0, 0, 0))
    draw(ImageDraw.Draw(im))
    im.save(os.path.join(D, name))


def stud_pearl(d):
    for cx in (130, 270):
        d.ellipse((cx - 42, 108, cx + 42, 192), fill=(205, 165, 70))
        d.ellipse((cx - 34, 116, cx + 34, 184), fill=(240, 236, 228))
        d.ellipse((cx - 18, 126, cx - 4, 140), fill=(255, 255, 255))


def hoop_silver(d):
    for cx in (130, 270):
        d.ellipse((cx - 55, 90, cx + 55, 210), outline=(190, 195, 205), width=12)


def stud_crystal(d):
    for cx in (130, 270):
        d.ellipse((cx - 38, 112, cx + 38, 188), fill=(205, 165, 70))
        d.regular_polygon((cx, 150, 30), 8, fill=(200, 225, 245), outline=(255, 255, 255))


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


product('stud_pearl.png', stud_pearl)
product('hoop_silver.png', hoop_silver)
product('stud_crystal.png', stud_crystal)
ref('ref_studio_warm.jpg', (214, 190, 170), (120, 95, 80))
ref('ref_studio_cool.jpg', (180, 195, 210), (60, 70, 90))
print('assets ->', D)
