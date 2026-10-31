"""Агент кадров: бриф шота + правила памяти + замечания судьи → промпт → генерация → кадр в формате брифа."""
import os

from PIL import Image

from ..backends.mock import cover
from .base import Agent


class FrameAgent(Agent):
    name = 'frame'

    def __init__(self, log, backend, budget, brief):
        super().__init__(log)
        self.backend, self.budget, self.brief = backend, budget, brief

    def build_prompt(self, shot, product, ref, rules, feedback):
        parts = [shot.prompt.strip(), 'Product (must match the photo exactly): ' + ', '.join(str(v) for v in product.attributes.values())]
        if ref and ref.get('note'):
            parts.append(f"Style reference: {ref['note']}")
        hints = [r['prompt_hint'] for r in rules] + [f['hint'] for f in feedback]
        if hints:
            parts.append('Hard constraints: ' + '; '.join(dict.fromkeys(hints)))
        constraints = sorted({r['attribute'] for r in rules} | {f['attribute'] for f in feedback})
        return '\n'.join(parts), constraints

    def run(self, shot, product, rules, feedback, attempt, out_dir):
        ref = self.brief.references.get(shot.reference) if shot.reference else None
        prompt, constraints = self.build_prompt(shot, product, ref, rules, feedback)
        raw = os.path.join(out_dir, f'{shot.id}_a{attempt}.raw.png')
        out = os.path.join(out_dir, f'{shot.id}_a{attempt}.png')
        if os.path.exists(out):  # --resume: кадр уже сгенерирован и оплачен в прошлый раз
            self.log.event(self.name, shot.id, 'reused', attempt=attempt, path=out)
            return out, {'raw_size': list(Image.open(out).size), 'reused': True}
        # если есть <raw>.task — задача оплачена до сбоя и бэкенд её только скачает; в журнал она не попала,
        # поэтому её стоимость учитывается здесь (один раз)
        usd = self.budget.reserve('frame', self.backend.name)
        req = {'shot': shot.id, 'attempt': attempt, 'prompt': prompt, 'constraints': constraints,
               'photos': product.photos, 'reference': ref and ref['path'], 'attributes': product.attributes,
               'width': self.brief.width, 'height': self.brief.height}
        try:
            info = self.timed(shot.id, self.backend.generate, req, raw)
        except Exception:
            if os.path.exists(raw + '.task'):  # задача создана — деньги ушли, даже если скачать не удалось
                self.log.add_cost(usd)
            raise
        self.log.add_cost(usd)
        im = Image.open(raw).convert('RGB')
        if im.size != (self.brief.width, self.brief.height):
            im = cover(im, self.brief.width, self.brief.height)
        im.save(out)
        if os.path.exists(raw + '.mock.json'):
            os.replace(raw + '.mock.json', out + '.mock.json')
        os.remove(raw)
        if os.path.exists(raw + '.task'):
            os.replace(raw + '.task', out + '.task')
        self.log.event(self.name, shot.id, 'generated', attempt=attempt, path=out, cost_usd=usd, task_id=info.get('task_id'),
                       rules=[r['id'] for r in rules], constraints=constraints, prompt=prompt, raw_size=info.get('raw_size'))
        return out, info
