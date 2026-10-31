"""Агент-судья: технические проверки кадра (бесплатно, локально) + сверка атрибутов товара с эталоном.
Каждое расхождение — причина отбраковки с подсказкой для повторной генерации."""
import json
import os

from PIL import Image, ImageStat

from ..color import delta_e, stone_lab

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

    def color(self, path, product, bbox):
        """ΔE76 между камнем на кадре (рамка от судьи) и на фото товара (color_box из брифа)."""
        if not product.color_box or not bbox:
            return None
        box = [bbox[k] for k in ('x0', 'y0', 'x1', 'y1')]
        return round(delta_e(stone_lab(product.photos[0], product.color_box), stone_lab(path, box)), 1)

    def review(self, shot, product, path, raw_size, rules, attempt):
        extra = {}
        cfg = self.brief.judge
        scene = shot.prompt if cfg.get('check_scene', True) else None

        def _run():
            tech = self.technical(path, raw_size)
            mismatches = []
            if not tech:
                observed = self.inspector.inspect(path, product, scene=scene)
                extra.update(getattr(self.inspector, 'last', {}) or {})
                de = self.color(path, product, extra.get('bbox'))
                if de is not None:
                    extra['color_delta_e'] = de
                    lim = cfg.get('max_delta_e')
                    if lim is not None and de > lim:
                        mismatches.append({'attribute': 'stone_color', 'expected': f'ΔE ≤ {lim}', 'got': f'ΔE {de}',
                                           'hint': 'keep the exact stone color and saturation of the product photo'})
                if extra.get('scene_ok') is False:
                    mismatches.append({'attribute': 'scene', 'expected': 'shot brief', 'got': extra.get('scene_issue', ''),
                                       'hint': f"follow the shot brief exactly: {extra.get('scene_issue', '')}"})
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
