"""Агент-судья: технические проверки кадра (бесплатно, локально) + сверка атрибутов товара с эталоном.
Каждое расхождение — причина отбраковки с подсказкой для повторной генерации."""
from PIL import Image, ImageStat

from .base import Agent


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
        def _run():
            tech = self.technical(path, raw_size)
            mismatches = []
            if not tech:
                observed = self.inspector.inspect(path, product)
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
                       rules_checked=[r['id'] for r in rules])
        return ok, tech, mismatches
