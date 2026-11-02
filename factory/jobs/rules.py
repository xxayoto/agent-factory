"""Фильтр-агент: правила из config/jobs.yaml. Дешёвый детерминированный отсев до судьи —
судья (лимит подписки) видит только то, что прошло. Каждый отсев подписан id правила."""
import datetime as dt
import re


def _rx(patterns):
    return [re.compile(p, re.I | re.M) for p in patterns]


class Filter:
    def __init__(self, cfg, today=None):
        f = cfg['filter']
        self.remote = _rx(f['remote'])
        self.remote_sources = tuple(f.get('remote_sources', []))
        self.skills = _rx(f['skills'])
        self.title_only = tuple(f.get('skills_title_only', []))
        self.drop = []
        for r in f['drop']:
            groups = [_rx(g) for g in r['all']] if 'all' in r else [_rx(r['any'])]
            self.drop.append((r['id'], groups, _rx(r.get('unless', []))))
        self.max_age = cfg.get('max_age_days')
        self.today = today or dt.date.today()

    def check(self, lead):
        """→ (True, None) или (False, id правила)."""
        text = f"{lead['title']}\n{lead['text']}"
        if self.max_age and lead.get('posted'):
            try:
                if (self.today - dt.date.fromisoformat(lead['posted'])).days > self.max_age:
                    return False, 'old'
            except ValueError:
                pass
        # сначала «наше ли это вообще», потом стоп-правила: отсев по правилам считается только по релевантным лидам
        if not lead['source'].startswith(self.remote_sources) and not any(
                p.search(text + ' ' + lead.get('location', '')) for p in self.remote):
            return False, 'not_remote'
        where = lead['title'] if lead['source'].startswith(self.title_only) else text
        if not any(p.search(where) for p in self.skills):
            return False, 'no_skill_match'
        hay = text + '\n' + lead.get('location', '')  # location — отдельной строкой: «^US$» ловит поле целиком
        for rid, groups, unless in self.drop:
            if all(any(p.search(hay) for p in g) for g in groups) and not any(p.search(hay) for p in unless):
                return False, rid
        return True, None
