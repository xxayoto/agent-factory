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

    def review(self, shot, product, path, raw_size, rules, attempt, kind='frame'):
        """kind='video_frame' — кадр из ролика: размер другой, сцену фото-промпта не проверяем, события отдельно."""
        extra = {}
        cfg = self.brief.judge
        scene = shot.prompt if cfg.get('check_scene', True) and kind == 'frame' else None

        def _run():
            tech = self.technical(path, raw_size)
            if kind != 'frame':
                tech = [t for t in tech if not t.startswith('размер')]
            mismatches = []
            if not tech:
                observed = self.inspector.inspect(path, product, scene=scene)
                extra.update(getattr(self.inspector, 'last', {}) or {})
                de = self.color(path, product, extra.get('bbox'))
                if de is not None:
                    extra['color_delta_e'] = de
                    lim = cfg.get('max_delta_e')
                    if lim is not None and de > lim:
                        # ΔE зависит от рамки модели (на постере 01.11 она попала на красный фон). Сам по себе он не бракует:
                        # согласна модель, что камень не тот, — брак (это ловит сверка атрибутов), иначе — вопрос человеку
                        extra.setdefault('disputed', {})['stone_color'] = [f'ΔE {de}', 'модель: камень верный']
                if extra.get('scene_ok') is False:
                    mismatches.append({'attribute': 'scene', 'expected': 'shot brief', 'got': extra.get('scene_issue', ''),
                                       'hint': f"follow the shot brief exactly: {extra.get('scene_issue', '')}"})
                extra['observed'] = observed
                truth = ground_truth(path)
                if truth is not None:  # mock: известно, что на кадре на самом деле — меряем точность судьи
                    exp = product.attributes
                    extra['truth_attrs_exact'] = all(observed.get(a) == truth.get(a) for a in exp)
                    extra['truth_verdict_ok'] = (truth == exp) == all(observed.get(a) == exp[a] for a in exp)
                disputed = extra.get('disputed') or {}
                unseen = [a for a in product.attributes if observed.get(a) == 'not_visible']
                if unseen:
                    extra['not_visible'] = unseen
                for attr, exp in product.attributes.items():
                    got = observed.get(attr)
                    if got != exp and attr not in disputed and got != 'not_visible':
                        mismatches.append({'attribute': attr, 'expected': exp, 'got': got,
                                           'hint': f'{attr} must be {exp}, previous attempt showed {got}'})
            return tech, mismatches
        tech, mismatches = self.timed(shot.id, _run)
        ok = not tech and not mismatches
        prefix = '' if kind == 'frame' else f'{kind}_'
        if ok and extra.get('disputed'):
            self.log.event(self.name, shot.id, prefix + 'needs_review', attempt=attempt, path=path, disputed=extra['disputed'])
        self.log.event(self.name, shot.id, prefix + ('approved' if ok else 'rejected'), attempt=attempt, path=path,
                       technical=tech, mismatches=mismatches, checklist=sorted(product.attributes),
                       rules_checked=[r['id'] for r in rules], judge=self.inspector.name, **extra)
        return ok, tech, mismatches, extra
