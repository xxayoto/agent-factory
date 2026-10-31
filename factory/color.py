"""Цвет камня без модели: средний цвет самых насыщенных пикселей в рамке (камень, металл) в CIE Lab
и разница ΔE76 с фото товара. Ловит «шампань стал оранжевым», чего судья-модель не замечает."""
import numpy as np
from PIL import Image


def _lab(rgb):
    c = rgb / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    xyz = c @ np.array([[0.4124, 0.2126, 0.0193], [0.3576, 0.7152, 0.1192], [0.1805, 0.0722, 0.9505]])
    xyz /= np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])], axis=1)


def stone_lab(path, box=None, top=0.5, min_sat=0.3):
    """box — доли (x0, y0, x1, y1). Пиксели с насыщенностью ≥ min_sat (не белый фон, не блики), из них —
    верхняя половина по насыщенности (камень и металл, а не кожа)."""
    im = Image.open(path).convert('RGB')
    if box:
        W, H = im.size
        x0, y0, x1, y1 = (max(0.0, min(1.0, v)) for v in box)
        if x1 - x0 > 0.01 and y1 - y0 > 0.01:
            im = im.crop((int(x0 * W), int(y0 * H), int(x1 * W), int(y1 * H)))
    im.thumbnail((256, 256))
    px = np.asarray(im, dtype=np.float64).reshape(-1, 3)
    mx, mn = px.max(axis=1), px.min(axis=1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0)
    cand = (mx > 60) & (sat >= min_sat)
    if cand.sum() < 20:
        cand = mx > 60
    keep = cand & (sat >= np.quantile(sat[cand], 1 - top))
    sel = px[keep] if keep.sum() > 20 else px[cand]
    return _lab(sel).mean(axis=0)


def delta_e(a, b):
    return float(np.sqrt(((np.asarray(a) - np.asarray(b)) ** 2).sum()))
