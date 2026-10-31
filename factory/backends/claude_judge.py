"""Судья на Claude Code в headless-режиме (`claude -p`): смотрит кадр инструментом Read и возвращает
атрибуты товара строго по JSON-схеме. Работает по подписке Claude, без API-ключа в проекте.

Контекст урезан (свой system prompt, только Read, без MCP и сохранения сессии) — ~10–15 c и мало токенов на кадр.
"""
import json
import os
import shutil
import subprocess

VOCAB = {
    'earring_type': ['stud', 'hoop', 'drop'],
    'metal': ['gold', 'silver'],
    'stone': ['pearl', 'crystal', 'none'],
    'count': ['pair', 'single'],
}
DESCRIBE = {
    'earring_type': 'stud = sits on the earlobe, nothing hangs below; hoop = open ring; drop = something hangs below the stud',
    'stone': 'pearl = round white/cream bead with sheen; crystal = faceted or colored gem of any shape; none = only metal',
}
SYSTEM = ('You are a strict visual QA inspector for jewelry product shots. Describe only what is visible in the image. '
          'Never assume the product matches a description: generators often turn studs into drops, swap metals or stones.')


def schema(attributes, vocab, describe=DESCRIBE, scene=False, bbox=False):
    props = {}
    for a in attributes:
        v = vocab.get(a)
        props[a] = {'type': 'string', 'enum': v} if v else {'type': 'string'}
        if a in describe:
            props[a]['description'] = describe[a]
    props['notes'] = {'type': 'string', 'description': 'one short sentence: anything wrong with the product'}
    req = list(attributes) + ['notes']
    if scene:
        props['scene_ok'] = {'type': 'boolean', 'description': 'true only if the shot shows the scene described in SHOT BRIEF: '
                             'composition, pose, how the product is shown (worn / held / lying), setting'}
        props['scene_issue'] = {'type': 'string', 'description': 'if scene_ok is false: what differs from the brief, one sentence; else empty'}
        req += ['scene_ok', 'scene_issue']
    if bbox:
        props['bbox'] = {'type': 'object', 'description': 'tight box around the center stone of the most visible earring, '
                         'fractions of image width/height (0..1)',
                         'properties': {k: {'type': 'number'} for k in ('x0', 'y0', 'x1', 'y1')}, 'required': ['x0', 'y0', 'x1', 'y1']}
        req += ['bbox']
    return {'type': 'object', 'properties': props, 'required': req}


class ClaudeInspector:
    name = 'claude'

    def __init__(self, model='sonnet', vocab=None, describe=None, timeout=180, with_reference=True, zoom_below=0.12):
        """zoom_below — если серьга уже этой доли ширины кадра, второй проход судит по увеличенному фрагменту
        (замер 01.11: на кадре в полный рост судья выдумал деталь на серьге в ~3 % ширины). None — без зума."""
        if not shutil.which('claude'):
            raise RuntimeError('нет Claude Code CLI (`claude`) в PATH')
        self.model, self.timeout, self.with_reference, self.zoom_below = model, timeout, with_reference, zoom_below
        self.vocab = {**VOCAB, **(vocab or {})}
        self.describe = {**DESCRIBE, **(describe or {})}
        self.last = {}

    def _call(self, frame_path, product, scene=None, stone_box=False, earring_box=False, zoomed=False):
        d, f = os.path.split(os.path.abspath(frame_path))
        attrs = list(product.attributes)
        what = ('It is a ZOOMED CROP of a generated advertising shot, showing the earring up close. ' if zoomed
                else 'It is a generated advertising shot of jewelry. ')
        prompt = f'Read the image file {f} with the Read tool. {what}Report these attributes of the jewelry you SEE in it: {", ".join(attrs)}.'
        extra = []
        if self.with_reference:
            ref = os.path.abspath(product.photos[0])
            prompt = (f'First Read the product photo {ref} — this is the real product, the ground truth for its shape, metal and stone. '
                      f'Then ' + prompt[0].lower() + prompt[1:] +
                      ' Compare it with the product photo detail by detail and report what is actually in the generated shot, '
                      'even if it differs from the product.')
            extra = ['--add-dir', os.path.dirname(ref)]
        if scene:
            prompt += f' Also check the scene against the SHOT BRIEF: "{scene.strip()}"'
        sc = schema(attrs, self.vocab, self.describe, bool(scene), stone_box)
        if earring_box:
            sc['properties']['earring_box'] = {
                'type': 'object', 'description': 'tight box around the whole most visible earring, fractions of image width/height (0..1)',
                'properties': {k: {'type': 'number'} for k in ('x0', 'y0', 'x1', 'y1')}, 'required': ['x0', 'y0', 'x1', 'y1']}
            sc['required'].append('earring_box')
        cmd = ['claude', '-p', prompt, *extra, '--model', self.model, '--output-format', 'json', '--tools', 'Read',
               '--allowedTools', 'Read', '--strict-mcp-config', '--no-session-persistence', '--system-prompt', SYSTEM,
               '--json-schema', json.dumps(sc)]
        p = subprocess.run(cmd, cwd=d, capture_output=True, text=True, timeout=self.timeout, stdin=subprocess.DEVNULL)
        try:
            j = json.loads(p.stdout)
        except json.JSONDecodeError:
            raise RuntimeError(f'claude judge: не JSON: {(p.stdout or p.stderr)[:300]}')
        out = j.get('structured_output')
        if j.get('is_error') or not isinstance(out, dict):
            raise RuntimeError(f"claude judge: {str(j.get('result'))[:300]}")
        # стоимость по прайсу API — для подписки это расход лимита, а не деньги
        meta = {'list_usd': j.get('total_cost_usd') or 0.0, 'sec': (j.get('duration_ms') or 0) / 1000, 'notes': out.pop('notes', '')}
        for k in ('scene_ok', 'scene_issue', 'bbox', 'earring_box'):
            if k in out:
                meta[k] = out.pop(k)
        return out, meta

    def inspect(self, frame_path, product, scene=None):
        out, meta = self._call(frame_path, product, scene, bool(product.color_box), self.zoom_below is not None)
        box = meta.get('earring_box')
        if box and self.zoom_below is not None and 0 < box['x1'] - box['x0'] < self.zoom_below:
            crop = zoom_crop(frame_path, box)
            try:
                out2, meta2 = self._call(crop, product, zoomed=True)
            finally:
                shutil.rmtree(os.path.dirname(crop), ignore_errors=True)
            # спорный признак: проходы расходятся (зум теряет контекст света, общий план — детали) —
            # такой признак не повод для брака, а вопрос человеку (замер 01.11: r29 и r33)
            disputed = {a: [out.get(a), out2.get(a)] for a in product.attributes if out.get(a) != out2.get(a)}
            meta.update({'zoomed': True, 'first_pass': out, 'disputed': disputed, 'notes': meta2['notes'],
                         'list_usd': meta['list_usd'] + meta2['list_usd'], 'sec': meta['sec'] + meta2['sec']})
            out = out2
        self.last = meta
        return out


def zoom_crop(frame_path, box, pad=1.5, size=1024):
    """Фрагмент вокруг серьги (с запасом pad от её размера), увеличенный до size по длинной стороне, во временной папке."""
    import tempfile
    from PIL import Image
    im = Image.open(frame_path).convert('RGB')
    W, H = im.size
    cx, cy = (box['x0'] + box['x1']) / 2 * W, (box['y0'] + box['y1']) / 2 * H
    half = max((box['x1'] - box['x0']) * W, (box['y1'] - box['y0']) * H) * (0.5 + pad)
    c = im.crop((int(max(0, cx - half)), int(max(0, cy - half)), int(min(W, cx + half)), int(min(H, cy + half))))
    k = size / max(c.size)
    c = c.resize((max(1, int(c.width * k)), max(1, int(c.height * k))), Image.LANCZOS)
    d = tempfile.mkdtemp(prefix='judge-zoom-')
    path = os.path.join(d, 'zoom.png')
    c.save(path)
    return path
