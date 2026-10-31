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


def schema(attributes, vocab, describe=DESCRIBE):
    props = {}
    for a in attributes:
        v = vocab.get(a)
        props[a] = {'type': 'string', 'enum': v} if v else {'type': 'string'}
        if a in describe:
            props[a]['description'] = describe[a]
    props['notes'] = {'type': 'string', 'description': 'one short sentence: anything wrong with the product'}
    return {'type': 'object', 'properties': props, 'required': list(attributes) + ['notes']}


class ClaudeInspector:
    name = 'claude'

    def __init__(self, model='sonnet', vocab=None, describe=None, timeout=180, with_reference=True):
        if not shutil.which('claude'):
            raise RuntimeError('нет Claude Code CLI (`claude`) в PATH')
        self.model, self.timeout, self.with_reference = model, timeout, with_reference
        self.vocab = {**VOCAB, **(vocab or {})}
        self.describe = {**DESCRIBE, **(describe or {})}
        self.last = {}

    def inspect(self, frame_path, product):
        d, f = os.path.split(os.path.abspath(frame_path))
        attrs = list(product.attributes)
        prompt = (f'Read the image file {f} with the Read tool. It is a generated advertising shot of jewelry. '
                  f'Report these attributes of the jewelry you SEE in it: {", ".join(attrs)}.')
        extra = []
        if self.with_reference:
            ref = os.path.abspath(product.photos[0])
            prompt = (f'First Read the product photo {ref} — this is the real product, the ground truth for its shape, metal and stone. '
                      f'Then ' + prompt[0].lower() + prompt[1:] +
                      ' Compare it with the product photo detail by detail and report what is actually in the generated shot, '
                      'even if it differs from the product.')
            extra = ['--add-dir', os.path.dirname(ref)]
        cmd = ['claude', '-p', prompt, *extra, '--model', self.model, '--output-format', 'json', '--tools', 'Read',
               '--allowedTools', 'Read', '--strict-mcp-config', '--no-session-persistence', '--system-prompt', SYSTEM,
               '--json-schema', json.dumps(schema(attrs, self.vocab, self.describe))]
        p = subprocess.run(cmd, cwd=d, capture_output=True, text=True, timeout=self.timeout, stdin=subprocess.DEVNULL)
        try:
            j = json.loads(p.stdout)
        except json.JSONDecodeError:
            raise RuntimeError(f'claude judge: не JSON: {(p.stdout or p.stderr)[:300]}')
        out = j.get('structured_output')
        if j.get('is_error') or not isinstance(out, dict):
            raise RuntimeError(f"claude judge: {str(j.get('result'))[:300]}")
        # стоимость по прайсу API — для подписки это расход лимита, а не деньги
        self.last = {'list_usd': j.get('total_cost_usd'), 'sec': (j.get('duration_ms') or 0) / 1000, 'notes': out.pop('notes', '')}
        return out
