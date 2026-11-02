"""Память поиска: журнал leads.jsonl (только дописывается) — он же база дедупа по URL; дайджест дня в markdown."""
import json
import os
import urllib.parse

DROP_QUERY = ('utm_', 'ref', 'source', 'gh_src', 'lever-source')


def norm_url(u):
    """Один URL — один лид: без схемы-регистра, фрагмента, хвостового слэша и трекинговых параметров."""
    p = urllib.parse.urlsplit((u or '').strip())
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query) if not k.lower().startswith(DROP_QUERY)]
    return urllib.parse.urlunsplit(('https', p.netloc.lower().removeprefix('www.'), p.path.rstrip('/'), urllib.parse.urlencode(q), ''))


class LeadLog:
    def __init__(self, path):
        self.path = path
        self.seen = set()
        if os.path.exists(path):
            with open(path) as f:
                for ln in f:
                    if ln.strip():
                        self.seen.add(json.loads(ln).get('key'))

    def is_new(self, lead):
        return norm_url(lead['url']) not in self.seen

    def append(self, rows):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, 'a') as f:
            for r in rows:
                r['key'] = norm_url(r['url'])
                self.seen.add(r['key'])
                f.write(json.dumps(r, ensure_ascii=False) + '\n')


RULE_RU = {'old': 'старше срока', 'not_remote': 'не удалёнка', 'no_skill_match': 'нет совпадения навыков', 'on_camera': 'съёмка себя / UGC в кадре',
           'location': 'только US / EU', 'prepayment': 'предоплата', 'adult': 'adult', 'unpaid': 'без оплаты',
           'free_test_content': 'бесплатное тестовое = контент их канала', 'resume_post': 'чужое резюме', 'office': 'офис / гибрид'}


def digest(day, m, leads, errors):
    """Дайджест: сводка, отсев по правилам, оценённые лиды по убыванию оценки, письма."""
    L = [f'# Вакансии {day}: дайджест `factory jobs scan`', '',
         '> Только исследование и черновики: система ничего не отправляла. Отклик — вручную по ссылке.', '',
         f"Собрано **{m['fetched']}** из {m['sources_ok']} источников, дублей {m['duplicates']}, новых {m['new']}; "
         f"прошли фильтр **{m['passed']}**, оценено {m['judged']}, оценка ≥ {m['min_score']}: **{m['good']}**, писем {m['drafts']}. "
         f"Время {m['wall_sec']} с.", '']
    if errors:
        L += ['**Источники с ошибкой:** ' + '; '.join(f'{k} — {v}' for k, v in errors.items()), '']
    if m['dropped']:
        L += ['| Отсев | Лидов |', '|---|---|'] + [f'| {RULE_RU.get(k, k)} (`{k}`) | {v} |' for k, v in
                                                  sorted(m['dropped'].items(), key=lambda kv: -kv[1])] + ['']
    scored = sorted([x for x in leads if x.get('score')], key=lambda x: (-x['score'], x['posted']), reverse=False)
    if scored:
        L += ['## Лиды по оценке', '', '| Оценка | Вакансия | Источник | Дата | Почему |', '|---|---|---|---|---|']
        for x in scored:
            title = x['title'].replace('|', '/')[:80] + (f" — {x['company']}" if x['company'] and x['company'] not in x['source'] else '')
            L.append(f"| {x['score']} | [{title}]({x['url']}) | {x['source']} | {x['posted']} | {x.get('reason', '').replace('|', '/')} |")
        L.append('')
    letters = [x for x in scored if x.get('letter')]
    if letters:
        L += ['## Письма', '']
        for x in letters:
            L += [f"### {x['score']} · {x['title'][:90]}", f"{x['url']} · {x['source']} · {x.get('lang', '').upper()}", '']
            if x.get('tone_issues'):
                L += [f"⚠️ Проверка тона не пройдена ({', '.join(x['tone_issues'])}) — править руками.", '']
            L += ['```', x['letter'], '```', '']
    unjudged = [x for x in leads if x.get('passed') and not x.get('score')]
    if unjudged:
        L += [f'## Прошли фильтр, без оценки ({len(unjudged)})', '', 'Сверх лимита судьи или ошибка вызова — смотреть вручную.', '']
        L += [f"- [{x['title'][:90]}]({x['url']}) · {x['source']}" for x in unjudged] + ['']
    return '\n'.join(L)
