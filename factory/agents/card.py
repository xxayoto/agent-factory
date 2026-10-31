"""Агент карточек: логотип бренда и инфо-плашка поверх принятого кадра (перенос job_search/workflows/card.py)."""
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .base import Agent

AVENIR = '/System/Library/Fonts/Avenir Next.ttc'


def font(size, idx=7):
    try:
        return ImageFont.truetype(AVENIR, int(size), index=idx)
    except OSError:
        return ImageFont.load_default(size=int(size))


def draw_logo(im, name, tagline):
    W, H = im.size
    layer = Image.new('RGBA', im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    f1, f2 = font(W * 0.075, 5), font(W * 0.032)
    t1, t2 = d.textlength(name, font=f1), d.textlength(tagline, font=f2)
    y = W * 0.06
    d.text(((W - t1) / 2, y), name, font=f1, fill=(255, 255, 255, 255))
    if tagline:
        d.text(((W - t2) / 2, y + f1.size * 1.1), tagline, font=f2, fill=(255, 255, 255, 240))
    sh = Image.new('RGBA', im.size, (0, 0, 0, 0))
    sh.putalpha(layer.split()[3].filter(ImageFilter.GaussianBlur(W * 0.006)).point(lambda v: int(v * 0.45)))
    im.alpha_composite(sh)
    im.alpha_composite(layer)


def draw_plate(im, title, sub, rows, xp=0.06, yp=0.68):
    W, H = im.size
    ft, fs = font(W * 0.052), font(W * 0.027)
    d = ImageDraw.Draw(im)
    pad = W * 0.03
    tw = max([d.textlength(title, font=ft), d.textlength(sub, font=fs), W * 0.36 if rows else 0])
    h = ft.size * 1.12 + (fs.size * 1.35 if sub else 0) + len(rows) * fs.size * 1.6 + (fs.size * 0.4 if rows else 0)
    x0, y0 = W * xp, H * yp
    box = Image.new('RGBA', im.size, (0, 0, 0, 0))
    ImageDraw.Draw(box).rounded_rectangle((x0, y0, x0 + tw + pad * 2, y0 + h + pad * 1.6), radius=W * 0.018, fill=(0, 0, 0, 95))
    im.alpha_composite(box)
    d = ImageDraw.Draw(im)
    y = y0 + pad * 0.8
    d.text((x0 + pad, y), title, font=ft, fill='white')
    y += ft.size * 1.12
    if sub:
        d.text((x0 + pad, y), sub, font=fs, fill=(255, 255, 255, 235))
        y += fs.size * 1.35
    y += fs.size * 0.3 if rows else 0
    for r in rows:
        k, v = (s.strip() for s in r.split(':', 1))
        d.text((x0 + pad, y), k, font=fs, fill='white')
        d.text((x0 + pad + tw - d.textlength(v, font=fs), y), v, font=fs, fill='white')
        y += fs.size * 1.32
        d.line((x0 + pad, y, x0 + pad + tw, y), fill=(255, 255, 255, 170), width=max(1, int(W * 0.0012)))
        y += fs.size * 0.28


class CardAgent(Agent):
    name = 'card'

    def __init__(self, log, brief):
        super().__init__(log)
        self.brief = brief

    def run(self, shot, frame, out_dir):
        def _run():
            im = Image.open(frame).convert('RGBA')
            b = self.brief.brand
            if b.get('name'):
                draw_logo(im, b['name'], b.get('tagline', ''))
            c = shot.card or {}
            if c.get('title'):
                draw_plate(im, c['title'], c.get('subtitle', ''), c.get('rows', []), c.get('x', 0.06), c.get('y', 0.68))
            out = os.path.join(out_dir, f'{shot.id}_card.jpg')
            im.convert('RGB').save(out, quality=93)
            return out
        out = self.timed(shot.id, _run)
        self.log.event(self.name, shot.id, 'done', path=out)
        return out
