"""Приёмка M5 (specs/m5-job-hunt.md): ленты подменены, Claude подменён — ни сети, ни лимита."""
import datetime as dt
import glob
import json
import os
import re
import shutil
import tempfile
import unittest

from factory.jobs import agents, scan, sources, store
from factory.jobs.rules import Filter

TODAY = dt.date(2026, 11, 2)

TG = '''<div class="tgme_widget_message_wrap"><div class="tgme_widget_message" data-post="motionhunter/101">
<div class="tgme_widget_message_text js-message_text" dir="auto">AI-видеомонтажёр Reels<br/>Удалённо, 80 000 ₽. Монтаж в CapCut, Seedance, Kling.</div>
<a class="tgme_widget_message_date"><time datetime="2026-11-01T10:00:00+00:00">10:00</time></a></div></div>
<div class="tgme_widget_message_wrap"><div class="tgme_widget_message" data-post="motionhunter/102">
<div class="tgme_widget_message_photo_wrap"></div></div></div>'''
REMOTEOK = json.dumps([{'legal': 'notice'}, {'position': 'Motion Designer', 'company': 'Acme', 'tags': ['video'], 'description': '<p>After Effects</p>',
                                              'location': 'Worldwide', 'salary_min': 50000, 'salary_max': 70000,
                                              'url': 'https://remoteOK.com/remote-jobs/1?ref=rss', 'date': '2026-11-01T08:00:00+00:00'}])
WWR = '''<rss><channel><item><title>Studio: Video Editor</title><region>Anywhere in the World</region>
<link>https://weworkremotely.com/remote-jobs/studio-video-editor</link><pubDate>Sat, 31 Oct 2026 10:00:00 +0000</pubDate>
<description>&lt;p&gt;Edit short-form video.&lt;/p&gt;</description></item></channel></rss>'''
GREENHOUSE = json.dumps({'jobs': [{'absolute_url': 'https://job-boards.greenhouse.io/heygen/jobs/1', 'title': 'AI Video Creator', 'company_name': 'HeyGen',
                                   'content': '&lt;p&gt;Remote. Create AI video.&lt;/p&gt;', 'location': {'name': 'Remote'}, 'first_published': '2026-10-30T00:00:00Z'}]})
LEVER = json.dumps([{'hostedUrl': 'https://jobs.lever.co/x/1', 'text': 'Motion Designer', 'categories': {'location': 'Anywhere'},
                     'workplaceType': 'remote', 'createdAt': 1793000000000, 'descriptionPlain': 'Remote motion design', 'additionalPlain': ''}])
ASHBY = json.dumps({'jobs': [{'title': 'Video Editor', 'location': 'Remote', 'isRemote': True, 'isListed': True, 'jobUrl': 'https://jobs.ashbyhq.com/pika/1',
                              'descriptionPlain': 'Edit launch videos', 'publishedAt': '2026-10-31T00:00:00Z', 'compensation': {}}]})


def cfg(tmp):
    c = scan.load_config()
    c['out_dir'] = tmp
    return c


def mk(title, text='', source='telegram:test', location='', url=None, posted='2026-11-01'):
    return sources.lead(url or f'https://t.me/test/{abs(hash(title)) % 10**6}', source, title, '', text, location, '', posted)


def fake_ask(log=None, letters=None):
    """Судья: «монтаж/video/motion» в заголовке → 5, иначе 2. Автор: письма из очереди letters или стандартное."""
    letters = list(letters or [])

    def ask(prompt, schema, system, model):
        if log is not None:
            log.append(schema)
        if 'leads' in schema['properties']:
            posts = re.findall(r'### (\d+)\nTitle: (.*)', prompt)
            return {'leads': [{'n': int(n), 'score': 5 if re.search(r'монтаж|video|motion', t, re.I) else 2,
                               'reason': 'тест'} for n, t in posts]}, {'list_usd': 0.01, 'sec': 1}
        return {'letter': letters.pop(0) if letters else 'Сделал 86-секундный Shorts из 19 минут интервью. Портфолио по ссылке.'}, {'list_usd': 0.01, 'sec': 1}
    return ask


class TestJobSources(unittest.TestCase):
    def test_parsers_unified_lead(self):
        keys = {'url', 'source', 'title', 'company', 'text', 'location', 'salary', 'posted'}
        got = (sources.parse_telegram(TG, 'motionhunter') + sources.parse_remoteok(REMOTEOK) + sources.parse_wwr(WWR)
               + sources.parse_greenhouse(GREENHOUSE, 'heygen') + sources.parse_lever(LEVER, 'x') + sources.parse_ashby(ASHBY, 'pika'))
        self.assertEqual(len(got), 6)  # пост без текста и уведомление RemoteOK пропущены
        for x in got:
            self.assertEqual(set(x), keys)
            self.assertTrue(x['url'].startswith('https://') and x['title'] and re.match(r'\d{4}-\d\d-\d\d$', x['posted']), x)
        tg = got[0]
        self.assertEqual((tg['url'], tg['title']), ('https://t.me/motionhunter/101', 'AI-видеомонтажёр Reels'))
        self.assertEqual(got[2]['company'], 'Studio')
        self.assertIn('(remote)', got[4]['location'])

    def test_plan_uses_given_get(self):
        urls = []

        def get(u):
            urls.append(u)
            return {'t.me': TG, 'remoteok': REMOTEOK, 'weworkremotely': WWR, 'greenhouse': GREENHOUSE, 'lever': LEVER, 'ashby': ASHBY}[
                next(k for k in ('t.me', 'remoteok', 'weworkremotely', 'greenhouse', 'lever', 'ashby') if k in u)]
        p = sources.plan({'telegram': {'channels': ['a'], 'pages': 1}, 'remoteok': True, 'weworkremotely': ['https://weworkremotely.com/x.rss'],
                          'greenhouse': ['g'], 'lever': ['l'], 'ashby': ['o']}, get)
        self.assertEqual(sum(len(fn()) for _, fn in p), 6)
        self.assertEqual(len(urls), 6)


class TestJobSafety(unittest.TestCase):
    def test_read_only_network(self):
        """Модуль только читает: один urlopen, метод GET, никаких POST, отправки сообщений, форм, браузера."""
        code = {f: open(f).read() for f in glob.glob(os.path.join(scan.ROOT, 'factory', 'jobs', '*.py'))}
        allcode = '\n'.join(code.values())
        self.assertEqual(allcode.count('urlopen('), 1)
        self.assertIn("method='GET'", code[os.path.join(scan.ROOT, 'factory', 'jobs', 'sources.py')])
        for bad in ("method='POST'", 'requests.', 'smtplib', 'webbrowser', 'sendMessage', 'data=', 'selenium', 'playwright', 'tg://'):
            self.assertNotIn(bad, allcode, bad)


class TestJobFilter(unittest.TestCase):
    def setUp(self):
        self.f = Filter(scan.load_config(), TODAY)

    def check(self, *a, **kw):
        return self.f.check(mk(*a, **kw))

    def test_pass(self):
        self.assertEqual(self.check('Монтажёр Reels', 'Удалённо, оплата 60 000 ₽, монтаж в CapCut'), (True, None))
        self.assertEqual(self.check('Motion Designer', 'After Effects', source='remoteok', location='Worldwide'), (True, None))

    def test_remote_paid_skills(self):
        self.assertEqual(self.check('Монтажёр', 'Офис в Москве, монтаж роликов')[1], 'not_remote')
        self.assertEqual(self.check('Бухгалтер', 'Удалённо, 1С')[1], 'no_skill_match')
        self.assertEqual(self.check('Account Executive', 'Our video platform...', source='ashby:x', location='Remote (remote)')[1], 'no_skill_match')
        self.assertEqual(self.check('Монтажёр', 'Удалённо, без оплаты, для портфолио монтаж')[1], 'unpaid')
        self.assertEqual(self.check('Монтажёр', 'Удалённо', posted='2026-09-01')[1], 'old')

    def test_drop_rules(self):
        cases = {
            'on_camera': ('UGC-креатор', 'Удалённо. Снимать себя на телефон, короткие видео'),
            'location': ('Video Editor', 'Remote, US only. Edit videos'),
            'prepayment': ('AI-видео', 'Удалённо, видео. Нужна предоплата за доступ к курсу'),
            'adult': ('Монтаж для OnlyFans', 'Удалённо, монтаж видео'),
            'free_test_content': ('Монтажёр нарезок', 'Удалённо, монтаж видео. Тестовое: готовый ролик под наше гео, наш логотип в конце, сдать на Google Диск'),
            'resume_post': ('Всем привет! Я Евгений — 2D/3D Motion Designer', 'Ищу проекты, удалённо, моушн'),
        }
        for rid, (t, x) in cases.items():
            self.assertEqual(self.check(t, x)[1], rid, rid)
        self.assertEqual(self.check('Video Editor', 'Edit videos', source='remoteok', location='USA')[1], 'location')

    def test_paid_test_is_ok(self):
        ok = self.check('Монтажёр нарезок', 'Удалённо, монтаж видео. Оплачиваемое тестовое: ролик с нашим логотипом в конце')
        self.assertEqual(ok, (True, None))


class TestJobDraft(unittest.TestCase):
    def setUp(self):
        self.c = scan.load_config()

    def test_lang(self):
        self.assertEqual(agents.lang_of(mk('Монтажёр', 'Удалённо, монтаж видео')), 'ru')
        self.assertEqual(agents.lang_of(mk('Video Editor', 'Remote, edit videos')), 'en')

    def test_tone_retry(self):
        ask = fake_ask(letters=['Скажу честно, опыта мало, но попробую.', 'Собрал Shorts из 19 минут интервью — 86 секунд, субтитры по словам.'])
        letter, issues, metas = agents.draft(mk('Монтажёр', 'Удалённо'), self.c['profile'], self.c['tone']['banned'], ask)
        self.assertEqual((issues, len(metas)), ([], 2))
        self.assertIn('86 секунд', letter)

    def test_tone_flagged_after_retry(self):
        bad = 'I am just a beginner, sorry to bother you.'
        letter, issues, _ = agents.draft(mk('Video Editor', 'Remote'), self.c['profile'], self.c['tone']['banned'], fake_ask(letters=[bad, bad]))
        self.assertTrue(issues)


class TestJobScan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.c = cfg(self.tmp)
        self.feed = [mk('Монтажёр Reels', 'Удалённо, 70 000 ₽, монтаж', url='https://t.me/a/1'),
                     mk('Моушн-дизайнер', 'Удалённо, After Effects, моушн', url='https://t.me/a/2'),
                     mk('SMM', 'Удалённо, видео для соцсетей', url='https://t.me/a/3'),
                     mk('Монтажёр', 'Офис, монтаж', url='https://t.me/a/4'),
                     mk('Монтажёр Reels', 'Удалённо, 70 000 ₽, монтаж', url='https://t.me/a/1/?utm_source=x')]  # дубль

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_scan(self, plan=None, calls=None):
        return scan.scan(self.c, plan or [('telegram:a', lambda: list(map(dict, self.feed)))], fake_ask(calls), TODAY)

    def test_digest_written(self):
        calls = []
        path, m = self.run_scan(calls=calls)
        self.assertEqual(path, os.path.join(self.tmp, 'leads_digest_2026-11-02.md'))
        md = open(path).read()
        self.assertEqual((m['fetched'], m['duplicates'], m['new'], m['passed'], m['judged']), (5, 1, 4, 3, 3))
        self.assertEqual(m['dropped'], {'not_remote': 1})
        self.assertEqual(sum(1 for s in calls if 'leads' in s['properties']), 1)  # 3 лида — одна пачка судьи
        self.assertEqual(m['drafts'], 1)  # оценка 5 только у «Монтажёр Reels» (у моушна заголовок по-русски)
        self.assertIn('ничего не отправляла', md)
        self.assertIn('## Письма', md)
        self.assertIn('не удалёнка', md)

    def test_dedup_second_scan(self):
        self.run_scan()
        _, m = self.run_scan()
        self.assertEqual((m['new'], m['judged'], m['drafts']), (0, 0, 0))

    def test_log_append_only(self):
        self.run_scan()
        log = os.path.join(self.tmp, 'leads.jsonl')
        first = open(log).read()
        self.feed.append(mk('Video editor', 'Remote, video', url='https://t.me/a/9'))
        self.run_scan()
        now = open(log).read()
        self.assertTrue(now.startswith(first))
        self.assertEqual(len(now.splitlines()), len(first.splitlines()) + 1)

    def test_source_error_isolated(self):
        def boom():
            raise TimeoutError('timed out')
        path, m = self.run_scan(plan=[('telegram:a', lambda: list(map(dict, self.feed))), ('remoteok', boom)])
        self.assertEqual((m['sources'], m['sources_ok'], m['new']), (2, 1, 4))
        self.assertIn('remoteok — TimeoutError', open(path).read())

    def test_norm_url(self):
        self.assertEqual(store.norm_url('http://www.RemoteOK.com/remote-jobs/1/?ref=rss#x'), 'https://remoteok.com/remote-jobs/1')


if __name__ == '__main__':
    unittest.main()
