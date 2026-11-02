"""Источники вакансий: только публичные ленты и только GET. Каждый парсер — чистая функция текст → лиды,
сеть отделена (`http_get`), чтобы тесты подставляли свои ленты.

Лид: url, source, title, company, text, location, salary, posted (ISO-дата или '').
"""
import datetime as dt
import html
import json
import re
import urllib.request
import xml.etree.ElementTree as ET

UA = 'Mozilla/5.0 (agent-factory jobs scan; read-only)'


def http_get(url, timeout=25):
    """Единственная сетевая функция модуля: GET без тела, без cookies и авторизации."""
    req = urllib.request.Request(url, headers={'User-Agent': UA}, method='GET')
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', 'replace')


def strip_html(s):
    s = re.sub(r'<br\s*/?>|</p>|</li>|</div>', '\n', s or '', flags=re.I)
    s = html.unescape(re.sub(r'<[^>]+>', ' ', s))
    return re.sub(r'[ \t ]+', ' ', re.sub(r'\n\s*\n+', '\n', s)).strip()


def lead(url, source, title, company='', text='', location='', salary='', posted=''):
    return {'url': url, 'source': source, 'title': (title or '').strip()[:200], 'company': (company or '').strip(),
            'text': (text or '').strip(), 'location': (location or '').strip(), 'salary': (salary or '').strip(),
            'posted': (posted or '')[:10]}


# ---- Telegram: веб-превью t.me/s/<канал>
TG_MSG = re.compile(r'data-post="([^"]+)"(.*?)(?=data-post="|\Z)', re.S)


def parse_telegram(page, channel):
    out = []
    for post, body in TG_MSG.findall(page):
        m = re.search(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', body, re.S)
        if not m:
            continue  # фото без подписи, опрос
        text = strip_html(m.group(1))
        t = re.search(r'<time datetime="([^"]+)"', body)
        title = next((ln for ln in text.split('\n') if len(re.sub(r'\W', '', ln)) > 3), text[:80])
        out.append(lead(f'https://t.me/{post}', f'telegram:{channel}', title[:120], channel, text, posted=t.group(1) if t else ''))
    return out


def fetch_telegram(channel, pages=1, get=http_get):
    out, before = [], ''
    for _ in range(pages):
        page = get(f'https://t.me/s/{channel}' + (f'?before={before}' if before else ''))
        got = parse_telegram(page, channel)
        if not got:
            break
        out += got
        before = min(int(x['url'].rsplit('/', 1)[1]) for x in got)
    return out


# ---- RemoteOK: первый элемент — уведомление о лицензии
def parse_remoteok(raw):
    out = []
    for j in json.loads(raw):
        if not isinstance(j, dict) or not j.get('position'):
            continue
        sal = f"${j['salary_min']}–{j['salary_max']}" if j.get('salary_max') else ''
        text = strip_html(j.get('description', '')) + '\nTags: ' + ', '.join(j.get('tags') or [])
        out.append(lead(j.get('url') or j.get('apply_url'), 'remoteok', j['position'], j.get('company'), text,
                        j.get('location'), sal, j.get('date', '')))
    return out


# ---- WeWorkRemotely RSS
def parse_wwr(raw):
    out = []
    for it in ET.fromstring(raw).iter('item'):
        g = lambda k: (it.findtext(k) or '').strip()
        company, _, title = g('title').partition(': ')
        posted = ''
        try:
            posted = dt.datetime.strptime(g('pubDate'), '%a, %d %b %Y %H:%M:%S %z').date().isoformat()
        except ValueError:
            pass
        out.append(lead(g('link'), 'weworkremotely', title or company, company if title else '',
                        strip_html(g('description')), g('region'), '', posted))
    return out


# ---- ATS-доски компаний
def parse_greenhouse(raw, board):
    return [lead(j['absolute_url'], f'greenhouse:{board}', j['title'], j.get('company_name') or board,
                 strip_html(html.unescape(j.get('content', ''))), (j.get('location') or {}).get('name', ''), '',
                 j.get('first_published') or j.get('updated_at', '')) for j in json.loads(raw).get('jobs', [])]


def parse_lever(raw, company):
    out = []
    for j in json.loads(raw):
        c = j.get('categories') or {}
        loc = c.get('location', '') + (' (remote)' if j.get('workplaceType') == 'remote' else '')
        posted = dt.datetime.fromtimestamp(j['createdAt'] / 1000, dt.timezone.utc).date().isoformat() if j.get('createdAt') else ''
        out.append(lead(j['hostedUrl'], f'lever:{company}', j['text'], company,
                        j.get('descriptionPlain', '') + '\n' + j.get('additionalPlain', ''), loc, '', posted))
    return out


def parse_ashby(raw, org):
    out = []
    for j in json.loads(raw).get('jobs', []):
        if j.get('isListed') is False:
            continue
        loc = j.get('location', '') + (' (remote)' if j.get('isRemote') else '')
        sal = ((j.get('compensation') or {}).get('compensationTierSummary')) or ''
        out.append(lead(j['jobUrl'], f'ashby:{org}', j['title'], org, j.get('descriptionPlain', ''), loc, sal, j.get('publishedAt', '')))
    return out


def plan(sources, get=http_get):
    """Список (имя источника, функция без аргументов → лиды) по разделу sources config/jobs.yaml."""
    return list(_plan(sources, get))


def _plan(s, get):
    tg = s.get('telegram') or {}
    for ch in tg.get('channels', []):
        yield f'telegram:{ch}', lambda ch=ch: fetch_telegram(ch, tg.get('pages', 1), get)
    if s.get('remoteok'):
        yield 'remoteok', lambda: parse_remoteok(get('https://remoteok.com/api'))
    for u in s.get('weworkremotely') or []:
        yield f'weworkremotely:{u.rsplit("/", 1)[-1]}', lambda u=u: parse_wwr(get(u))
    for b in s.get('greenhouse') or []:
        yield f'greenhouse:{b}', lambda b=b: parse_greenhouse(get(f'https://boards-api.greenhouse.io/v1/boards/{b}/jobs?content=true'), b)
    for c in s.get('lever') or []:
        yield f'lever:{c}', lambda c=c: parse_lever(get(f'https://api.lever.co/v0/postings/{c}?mode=json'), c)
    for o in s.get('ashby') or []:
        yield f'ashby:{o}', lambda o=o: parse_ashby(get(f'https://api.ashbyhq.com/posting-api/job-board/{o}?includeCompensation=true'), o)
