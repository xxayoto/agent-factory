"""Судья и автор писем на Claude Code (`claude -p`, по подписке, без инструментов). Вызов вынесен в `ask`,
тесты подставляют свою функцию. Ответ — строго по JSON-схеме, как у судьи кадров."""
import json
import re
import shutil
import subprocess


def claude_ask(prompt, schema, system, model='sonnet', timeout=240):
    """→ (structured_output, meta). Без инструментов, MCP и сохранения сессии: только текст."""
    if not shutil.which('claude'):
        raise RuntimeError('нет Claude Code CLI (`claude`) в PATH')
    cmd = ['claude', '-p', prompt, '--model', model, '--output-format', 'json', '--tools', '', '--strict-mcp-config',
           '--no-session-persistence', '--system-prompt', system, '--json-schema', json.dumps(schema)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    try:
        j = json.loads(p.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f'claude: не JSON: {(p.stdout or p.stderr)[:300]}')
    out = j.get('structured_output')
    if j.get('is_error') or not isinstance(out, dict):
        raise RuntimeError(f"claude: {str(j.get('result'))[:300]}")
    return out, {'list_usd': j.get('total_cost_usd') or 0.0, 'sec': (j.get('duration_ms') or 0) / 1000}


def profile_text(p):
    return (f"{p['name']} — {p['role']}, {p['location']}.\nSkills: {p['skills']}\nResults:\n"
            + '\n'.join(f'- {x}' for x in p['facts']) + f"\nDoes NOT do: {p['not_doing']}\n"
            + 'Links: ' + ', '.join(f'{k} {v}' for k, v in p['links'].items()))


# ---- судья
JUDGE_SYSTEM = ('You screen job posts for one candidate. Be strict and concrete: a high score needs real overlap with the '
                "candidate's proven skills and a paid, remote role he can do without appearing on camera.")
JUDGE_SCHEMA = {'type': 'object', 'required': ['leads'], 'properties': {'leads': {'type': 'array', 'items': {
    'type': 'object', 'required': ['n', 'score', 'reason'], 'properties': {
        'n': {'type': 'integer'},
        'score': {'type': 'integer', 'minimum': 1, 'maximum': 5,
                  'description': '5 = core skills, clearly paid and remote; 4 = strong fit; 3 = partial; 2 = weak; 1 = not a fit / not a real paid job'},
        'reason': {'type': 'string', 'description': 'one line, max 20 words, in Russian: why this score'}}}}}}


def judge(leads, profile, ask, model='sonnet', batch=8):
    """Оценка 1–5 и причина пачками по batch лидов. Пишет в лиды score, reason; возвращает метаданные вызовов."""
    metas = []
    for i in range(0, len(leads), batch):
        part = leads[i:i + batch]
        posts = '\n\n'.join(f"### {n}\nTitle: {x['title']}\nCompany: {x['company']}\nLocation: {x['location']}\n"
                            f"Salary: {x['salary'] or '—'}\nSource: {x['source']}\n{x['text'][:1800]}" for n, x in enumerate(part))
        prompt = f'CANDIDATE:\n{profile_text(profile)}\n\nScore every job post below (n = its number).\n\n{posts}'
        try:
            out, meta = ask(prompt, JUDGE_SCHEMA, JUDGE_SYSTEM, model)
        except Exception as e:  # пачка без оценки не роняет скан
            for x in part:
                x['judge_error'] = str(e)[:200]
            metas.append({'error': str(e)[:200]})
            continue
        by_n = {r['n']: r for r in out['leads']}
        for n, x in enumerate(part):
            r = by_n.get(n)
            if r:
                x['score'], x['reason'] = max(1, min(5, int(r['score']))), r['reason'].strip()
        metas.append(meta)
    return metas


# ---- автор писем
DRAFT_SYSTEM = ('You write short job application messages for the candidate, in first person. Confident and results-first: '
                'open with the most relevant concrete result with a number, then 1–2 more facts that match the post, then the '
                'portfolio link and a clear next step. Never apologise, never mention lack of experience, never beg, no clichés, '
                'never say what the candidate cannot do or has not done, never offer to do a test task, never merge two facts into one, '
                'no placeholders, no invented facts — use only the candidate facts given. '
                'Sound like a lively human, not a form letter: no bureaucratese, no bullet lists. 60–120 words. Plain text, no subject line.')
DRAFT_SCHEMA = {'type': 'object', 'required': ['letter'], 'properties': {'letter': {'type': 'string'}}}


def lang_of(lead):
    t = lead['title'] + ' ' + lead['text'][:1500]
    cyr = len(re.findall(r'[а-яё]', t, re.I))
    return 'ru' if cyr > len(re.findall(r'[a-z]', t, re.I)) * 0.5 else 'en'


def tone_issues(letter, banned):
    return [p for p in banned if re.search(p, letter, re.I)]


def draft(lead, profile, banned, ask, model='sonnet'):
    """Письмо на языке вакансии; проверка тона → одна перегенерация с замечанием. → (letter, issues, metas)."""
    lang = 'Russian' if lang_of(lead) == 'ru' else 'English'
    base = (f'CANDIDATE:\n{profile_text(profile)}\n\nJOB POST ({lead["source"]}):\nTitle: {lead["title"]}\nCompany: {lead["company"]}\n'
            f'{lead["text"][:3000]}\n\nWrite the application message in {lang}. Address what this post asks for.')
    metas, prompt = [], base
    for _ in range(2):
        out, meta = ask(prompt, DRAFT_SCHEMA, DRAFT_SYSTEM, model)
        metas.append(meta)
        letter = out['letter'].strip()
        issues = tone_issues(letter, banned)
        if not issues:
            return letter, [], metas
        prompt = base + f'\n\nYour previous draft was rejected for self-deprecating tone ({", ".join(issues)}). Rewrite without it.'
    return letter, issues, metas
