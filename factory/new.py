"""factory new: черновик брифа из папки с фото товара и текста ТЗ.

Claude (через `claude -p`, по подписке) смотрит фото и предлагает товары с проверяемыми признаками, словарь
и описания для судьи, четыре шота. Человек правит черновик 5–10 минут вместо того, чтобы писать бриф с нуля.
"""
import json
import os
import re
import shutil
import subprocess

import yaml

IMG = ('.jpg', '.jpeg', '.png', '.webp')

SCHEMA = {
    'type': 'object',
    'properties': {
        'products': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'id': {'type': 'string', 'description': 'short kebab-case id'},
            'photos': {'type': 'array', 'items': {'type': 'integer'}, 'description': 'indices of photos showing this product, best packshot first'},
            'tags': {'type': 'array', 'items': {'type': 'string'}, 'description': 'kebab-case, most specific first, e.g. earring-stud, earring'},
            'attributes': {'type': 'object', 'additionalProperties': {'type': 'string'},
                           'description': '3-5 visually checkable attributes of ONE item that generators tend to break (type, metal, finish, stone, shape, color); '
                                          'never attributes that depend on composition or how many items are in frame (pair, symmetry, count, position); '
                                          'snake_case values'},
            'vocab': {'type': 'object', 'additionalProperties': {'type': 'array', 'items': {'type': 'string'}},
                      'description': 'for each attribute: the correct value plus 2-3 typical wrong values and "other"'},
            'describe': {'type': 'object', 'additionalProperties': {'type': 'string'},
                         'description': 'for each attribute: one sentence defining the correct value visually'},
        }, 'required': ['id', 'photos', 'tags', 'attributes', 'vocab', 'describe']}},
        'shots': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'id': {'type': 'string'}, 'product': {'type': 'string'},
            'prompt': {'type': 'string', 'description': 'English image prompt: scene, pose, how the product is shown, light'},
            'caption': {'type': 'string', 'description': 'Russian subtitle, 3-6 words'},
            'card_title': {'type': 'string'}, 'card_subtitle': {'type': 'string'},
        }, 'required': ['id', 'product', 'prompt', 'caption', 'card_title', 'card_subtitle']}},
    },
    'required': ['products', 'shots'],
}


def list_photos(folder):
    return sorted(os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(IMG))


def ask_claude(photos, task, model='sonnet', timeout=600):
    if not shutil.which('claude'):
        raise RuntimeError('нет Claude Code CLI (`claude`)')
    listing = '\n'.join(f'{i}: {p}' for i, p in enumerate(photos))
    prompt = (f'Read each product photo with the Read tool:\n{listing}\n\nClient task (ТЗ):\n{task}\n\n'
              'Draft a production brief for an AI product-photo pipeline. Identify the distinct products. For each product choose '
              'attributes a QA judge can verify on a generated image, the ones generators typically break. Propose exactly 4 shots '
              'that fit the task.')
    dirs = sorted({os.path.dirname(p) for p in photos})
    cmd = ['claude', '-p', prompt, '--model', model, '--output-format', 'json', '--tools', 'Read', '--allowedTools', 'Read',
           '--strict-mcp-config', '--no-session-persistence', '--json-schema', json.dumps(SCHEMA)]
    for d in dirs:
        cmd += ['--add-dir', d]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    j = json.loads(p.stdout)
    if j.get('is_error') or not isinstance(j.get('structured_output'), dict):
        raise RuntimeError(f"claude: {str(j.get('result'))[:300]}")
    return j['structured_output']


def slug(s):
    return re.sub(r'[^a-z0-9-]+', '-', s.lower()).strip('-') or 'item'


def build_brief(draft, photos, project, brand=None, width=1080, height=1440):
    """Черновик модели → бриф фабрики. Чистая функция: без сети, тестируется отдельно."""
    products, vocab, describe = [], {}, {}
    for p in draft['products']:
        idx = [i for i in p.get('photos', []) if 0 <= i < len(photos)] or [0]
        products.append({'id': slug(p['id']), 'photos': [photos[i] for i in idx], 'tags': [slug(t) for t in p.get('tags', [])],
                         'attributes': p['attributes']})
        for a, vals in (p.get('vocab') or {}).items():
            vocab[a] = sorted(set(vocab.get(a, [])) | set(vals) | {p['attributes'].get(a, 'other')})
        describe.update(p.get('describe') or {})
    ids = {x['id'] for x in products}
    shots = []
    for k, s in enumerate(draft['shots'], 1):
        prod = slug(s['product']) if slug(s['product']) in ids else products[0]['id']
        shots.append({'id': f's{k}-{slug(s["id"])}'[:40], 'product': prod, 'prompt': s['prompt'], 'caption': s['caption'],
                      'card': {'title': s.get('card_title', ''), 'subtitle': s.get('card_subtitle', '')}})
    brief = {'project': slug(project), 'format': {'width': width, 'height': height}, 'products': products,
             'judge': {'model': 'sonnet', 'check_scene': True, 'max_delta_e': None, 'vocab': vocab, 'describe': describe},
             'shots': shots, 'outputs': {'cards': True, 'videos': False, 'montage': {'engine': 'remotion'}},
             'limits': {'max_retries': 1, 'workers': 2, 'budget_usd': 0.5}}
    if brand:
        brief['brand'] = {'name': brand}
    return brief


def write(brief, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'w') as f:
        f.write('# Черновик от factory new: проверить признаки, словарь судьи и промпты шотов.\n'
                '# color_box (рамка камня на первом фото) — по желанию; без неё цвет не меряется.\n')
        yaml.safe_dump(brief, f, allow_unicode=True, sort_keys=False, width=140)
    return path
