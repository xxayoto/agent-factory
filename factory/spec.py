"""Бриф (spec) в YAML → проверенная структура. Всё, что делает фабрика, выводится из брифа."""
import os
from dataclasses import dataclass, field

import yaml


class SpecError(ValueError):
    pass


@dataclass
class Product:
    id: str
    photos: list
    tags: list
    attributes: dict


@dataclass
class Shot:
    id: str
    product: str
    reference: str | None
    prompt: str
    caption: str = ''
    card: dict | None = None
    video: dict | None = None


@dataclass
class Brief:
    path: str
    project: str
    brand: dict
    width: int
    height: int
    products: dict
    references: dict
    shots: list
    outputs: dict
    limits: dict = field(default_factory=dict)

    @property
    def aspect(self):
        return self.width / self.height


def _req(d, key, where):
    if key not in d or d[key] in (None, '', []):
        raise SpecError(f'{where}: нет обязательного поля «{key}»')
    return d[key]


def load(path):
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    base = os.path.dirname(os.path.abspath(path))
    resolve = lambda p: p if os.path.isabs(p) else os.path.normpath(os.path.join(base, p))

    fmt = _req(raw, 'format', 'бриф')
    width, height = int(_req(fmt, 'width', 'format')), int(_req(fmt, 'height', 'format'))

    products = {}
    for p in _req(raw, 'products', 'бриф'):
        pid = _req(p, 'id', 'products[]')
        photos = [resolve(x) for x in _req(p, 'photos', f'product {pid}')]
        for ph in photos:
            if not os.path.exists(ph):
                raise SpecError(f'product {pid}: нет файла {ph}')
        products[pid] = Product(pid, photos, p.get('tags', []), _req(p, 'attributes', f'product {pid}'))

    references = {}
    for r in raw.get('references', []) or []:
        rid = _req(r, 'id', 'references[]')
        rp = resolve(_req(r, 'path', f'reference {rid}'))
        if not os.path.exists(rp):
            raise SpecError(f'reference {rid}: нет файла {rp}')
        references[rid] = {'path': rp, 'note': r.get('note', '')}

    shots, seen = [], set()
    for s in _req(raw, 'shots', 'бриф'):
        sid = _req(s, 'id', 'shots[]')
        if sid in seen:
            raise SpecError(f'shot {sid}: id повторяется')
        seen.add(sid)
        prod = _req(s, 'product', f'shot {sid}')
        if prod not in products:
            raise SpecError(f'shot {sid}: неизвестный product «{prod}»')
        ref = s.get('reference')
        if ref and ref not in references:
            raise SpecError(f'shot {sid}: неизвестный reference «{ref}»')
        shots.append(Shot(sid, prod, ref, _req(s, 'prompt', f'shot {sid}'), s.get('caption', ''), s.get('card'), s.get('video')))

    outputs = {'cards': True, 'videos': True, 'montage': True, **(raw.get('outputs') or {})}
    limits = {'max_retries': 2, 'workers': 3, 'budget_usd': 2.0, **(raw.get('limits') or {})}
    return Brief(os.path.abspath(path), _req(raw, 'project', 'бриф'), raw.get('brand') or {}, width, height,
                 products, references, shots, outputs, limits)
