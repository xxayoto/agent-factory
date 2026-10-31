"""Долговременная память фабрики: правила, выученные на отбраковках судьи.

Правило привязано к тегу товара и атрибуту. Агент кадров добавляет правила в промпт,
судья — в чек-лист. Каждое срабатывание правила увеличивает счётчик hits.
"""
import datetime as dt
import os
import threading

import yaml

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'memory', 'rules.yaml')


class Memory:
    def __init__(self, path=DEFAULT_PATH):
        self.path = path
        self.lock = threading.Lock()
        self.rules = []
        if os.path.exists(path):
            with open(path) as f:
                self.rules = (yaml.safe_load(f) or {}).get('rules', [])

    def save(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, 'w') as f:
            yaml.safe_dump({'rules': self.rules}, f, allow_unicode=True, sort_keys=False)

    def for_product(self, product):
        """Правила по тегам товара — только те, что не противоречат его атрибутам (жемчуг ≠ кристалл)."""
        tags = set(product.tags) | {product.id}
        return [r for r in self.rules if (r.get('tag') in tags or r.get('tag') == '*')
                and product.attributes.get(r['attribute']) == r['expected']]

    def learn(self, product, attribute, expected, got, run_id):
        """Отбраковка → правило. Возвращает (правило, новое ли оно)."""
        tag = product.tags[0] if product.tags else product.id
        with self.lock:
            for r in self.rules:
                if r['tag'] == tag and r['attribute'] == attribute and r['expected'] == expected:
                    r['hits'] = r.get('hits', 0) + 1
                    if got not in r.setdefault('seen_wrong', []):
                        r['seen_wrong'].append(got)
                    self.save()
                    return r, False
            r = {
                'id': f'{tag}.{attribute}.{expected}',
                'tag': tag,
                'attribute': attribute,
                'expected': expected,
                'seen_wrong': [got],
                'text': f'{attribute} должен оставаться «{expected}» (судья видел «{got}»)',
                'prompt_hint': f'keep {attribute} exactly {expected}, do not turn it into {got}',
                'hits': 1,
                'learned_in': run_id,
                'created': dt.date.today().isoformat(),
            }
            self.rules.append(r)
            self.save()
            return r, True
