"""Агент-судья: технические проверки кадра (бесплатно, локально) + сверка атрибутов товара с эталоном.
Каждое расхождение — причина отбраковки с подсказкой для повторной генерации."""
import json
import os

from PIL import Image, ImageStat

from .base import Agent


def ground_truth(path):
    p = path + '.mock.json'
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return json.load(f)['observed']


class JudgeAgent(Agent):
    name = 'judge'

    def __init__(self, log, inspector, brief):
        super().__init__(log)
        self.inspector, self.brief = inspector, brief

    def technical(self, path, raw_size):
        issues = []
        try:
            im = Image.open(path).convert('RGB')
        except OSError as e:
            return [f'не открывается: {e}']
        if im.size != (self.brief.width, self.brief.height):
            issues.append(f'размер {im.size} ≠ {(self.brief.width, self.brief.height)}')
        if max(ImageStat.Stat(im).stddev) < 4:
            issues.append('пустой/однотонный кадр')
        if raw_size and raw_size[0] * raw_size[1] < 0.5 * self.brief.width * self.brief.height:
            issues.append(f'исходник {raw_size} меньше половины целевого разрешения')
        return issues

    def review(self, shot, product, path, raw_size, rules, attempt):
        extra = {}

        def _run():
            tech = self.technical(path, raw_size)
            mismatches = []
            if not tech:
                observed = self.inspector.inspect(path, product)
                extra.update(getattr(self.inspector, 'last', {}) or {})
                extra['observed'] = observed
                truth = ground_truth(path)
                if truth is not None:  # mock: известно, что на кадре на самом деле — меряем точность судьи
                    exp = product.attributes
                    extra['truth_attrs_exact'] = all(observed.get(a) == truth.get(a) for a in exp)
                    extra['truth_verdict_ok'] = (truth == exp) == all(observed.get(a) == exp[a] for a in exp)
                for attr, exp in product.attributes.items():
                    got = observed.get(attr)
                    if got != exp:
                        mismatches.append({'attribute': attr, 'expected': exp, 'got': got,
                                           'hint': f'{attr} must be {exp}, previous attempt showed {got}'})
            return tech, mismatches
        tech, mismatches = self.timed(shot.id, _run)
        ok = not tech and not mismatches
        self.log.event(self.name, shot.id, 'approved' if ok else 'rejected', attempt=attempt, path=path,
                       technical=tech, mismatches=mismatches, checklist=sorted(product.attributes),
                       rules_checked=[r['id'] for r in rules], judge=self.inspector.name, **extra)
        return ok, tech, mismatches, extra
