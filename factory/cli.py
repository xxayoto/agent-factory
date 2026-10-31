"""factory — CLI фабрики.

  factory validate BRIEF               проверить бриф
  factory plan BRIEF                   граф задач и вилка стоимости, без вызовов API
  factory run BRIEF [--mode mock]      прогон; live только с --allow-spend USD
  factory bench BRIEF --seeds N        mock: серия прогонов без памяти vs с памятью
  factory eval-judge BRIEF --judge claude   точность судьи на кадрах с известной истиной
  factory dashboard                    HTML-дашборд runs/dashboard.html
  factory rejudge BRIEF RUN_DIR        перепроверить кадры прогона текущим судьёй (без генерации)
  factory new PHOTOS_DIR --task "ТЗ"   черновик брифа из фото товара (Claude по подписке)
  factory weekly [--since YYYY-MM-DD]  отчёт по live-прогонам за период (цифры для кейса)
  factory stats                        сводка по всем прогонам (runs/index.jsonl)
  factory memory [list|reset]          выученные правила
"""
import argparse
import json
import os
import sys

from . import spec
from .memory import DEFAULT_PATH, Memory
from .orchestrator import ROOT, Orchestrator, plan
from .runlog import load_index


def cmd_stats(runs_dir, as_json=False):
    rows = load_index(runs_dir)
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if not rows:
        print('прогонов ещё нет')
        return
    print(f"{'run':<34} {'mode':<5} {'shots':>5} {'frames':>6} {'reject':>7} {'1st':>5} {'rules+':>6} {'$':>7} {'sec':>6}")
    for m in rows:
        print(f"{m['run_id'][:34]:<34} {m['mode']:<5} {m['shots']:>5} {m['frames_generated']:>6} {m['reject_rate']:>7.0%} "
              f"{m['first_pass_yield']:>5.0%} {m['rules_learned']:>6} {m['cost_usd']:>7.3f} {m['wall_sec']:>6.1f}")
    g = sum(m['frames_generated'] for m in rows)
    rej = sum(m['frames_rejected'] for m in rows)
    live = [m for m in rows if m['mode'] == 'live']
    print(f"\nвсего: прогонов {len(rows)} (live {len(live)}), кадров {g}, брак {rej / g if g else 0:.0%}, "
          f"потрачено реально ${sum(m['cost_usd'] for m in live):.3f}")


def cmd_bench(brief, seeds, out_root, defect_rate):
    """Симуляция эффекта памяти: одинаковые сиды, холодная память на каждом прогоне vs накапливаемая."""
    import tempfile
    brief.outputs = {'cards': False, 'videos': False, 'montage': False}
    brief.limits['workers'] = 1  # шоты по очереди: правило, выученное внутри прогона, попадает в соседний шот детерминированно
    root = tempfile.mkdtemp(prefix='bench-', dir=out_root) if out_root else tempfile.mkdtemp(prefix='factory-bench-')
    res = {}
    for label in ('без памяти', 'с памятью'):
        rows, mem = [], os.path.join(root, f'{label}.yaml')
        for seed in range(seeds):
            if label == 'без памяти' and os.path.exists(mem):
                os.remove(mem)
            o = Orchestrator(brief, 'mock', seed, mem, os.path.join(root, 'runs'), None, defect_rate)
            rows.append(o.run())
        res[label] = rows
    print(f'mock-бенч, {seeds} прогонов × {len(brief.shots)} шотов, дрейф атрибута {defect_rate:.0%} (симуляция)')
    print(f"{'':<11} {'брак':>6} {'с 1-й':>6} {'кадров/шот':>11} {'провалов':>9} {'$/принятый':>11}")
    for label, rows in res.items():
        g = sum(m['frames_generated'] for m in rows)
        rej = sum(m['frames_rejected'] for m in rows)
        ok = sum(m['frames_approved'] for m in rows)
        fp = sum(m['first_pass_yield'] for m in rows) / len(rows)
        fail = sum(len(m['failed_shots']) for m in rows)
        usd = sum(m['cost_usd'] for m in rows)
        print(f"{label:<11} {rej / g:>6.0%} {fp:>6.0%} {g / (seeds * len(brief.shots)):>11.2f} {fail:>9} {usd / max(ok, 1):>11.3f}")
    second_half = res['с памятью'][seeds // 2:]
    print(f"с памятью, вторая половина серии: брак {sum(m['frames_rejected'] for m in second_half) / max(1, sum(m['frames_generated'] for m in second_half)):.0%}")
    print('журналы:', root)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='factory', description='Агентная фабрика визуального контента')
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('validate', 'plan'):
        sub.add_parser(name).add_argument('brief')
    r = sub.add_parser('run')
    r.add_argument('brief')
    r.add_argument('--mode', choices=['mock', 'live'], default='mock')
    r.add_argument('--seed', type=int, default=0)
    r.add_argument('--allow-spend', type=float, help='потолок реальных трат, USD (обязателен для live)')
    r.add_argument('--memory', default=DEFAULT_PATH, help='файл правил памяти')
    r.add_argument('--runs', default=os.path.join(ROOT, 'runs'))
    r.add_argument('--defect-rate', type=float, default=0.3, help='mock: вероятность дрейфа атрибута')
    r.add_argument('--judge', choices=['mock', 'claude', 'manual'], help='кто судит кадры (по умолчанию mock / из config)')
    r.add_argument('--resume', metavar='RUN_DIR', help='продолжить прерванный прогон: оплаченные кадры не генерируются заново')
    bn = sub.add_parser('bench')
    bn.add_argument('brief')
    bn.add_argument('--seeds', type=int, default=10)
    bn.add_argument('--out', help='куда писать журналы бенча (по умолчанию временная папка)')
    bn.add_argument('--defect-rate', type=float, default=0.3)
    ev = sub.add_parser('eval-judge')
    ev.add_argument('brief')
    ev.add_argument('--judge', choices=['mock', 'claude', 'manual'], default='mock')
    ev.add_argument('--model', default='sonnet', help='модель для --judge claude')
    ev.add_argument('--n', type=int, default=12)
    ev.add_argument('--seed', type=int, default=0)
    ev.add_argument('--workers', type=int, default=3)
    ev.add_argument('--labels', help='YAML с ручной разметкой реальных кадров (вместо синтетики)')
    ev.add_argument('--ids', help='только эти id из разметки, через запятую (точечная перепроверка)')
    ev.add_argument('--no-zoom', action='store_true', help='без второго прохода по увеличенному фрагменту')
    ev.add_argument('--runs', default=os.path.join(ROOT, 'runs'))
    rj = sub.add_parser('rejudge')
    rj.add_argument('brief')
    rj.add_argument('run_dir')
    rj.add_argument('--judge', choices=['claude', 'manual', 'mock'], default='claude')
    nw = sub.add_parser('new')
    nw.add_argument('photos_dir')
    nw.add_argument('--task', default='', help='текст ТЗ клиента')
    nw.add_argument('--task-file', help='файл с ТЗ')
    nw.add_argument('--project', required=True)
    nw.add_argument('--brand')
    nw.add_argument('--out', help='куда писать бриф (по умолчанию briefs/local/<project>.yaml — фото клиента в git не идут)')
    wk = sub.add_parser('weekly')
    wk.add_argument('--since')
    wk.add_argument('--until')
    wk.add_argument('--runs', default=os.path.join(ROOT, 'runs'))
    wk.add_argument('--save', action='store_true', help='записать в results/weekly/<неделя>.md')
    db = sub.add_parser('dashboard')
    db.add_argument('--runs', default=os.path.join(ROOT, 'runs'))
    s = sub.add_parser('stats')
    s.add_argument('--runs', default=os.path.join(ROOT, 'runs'))
    s.add_argument('--json', action='store_true')
    m = sub.add_parser('memory')
    m.add_argument('action', choices=['list', 'reset'], nargs='?', default='list')
    m.add_argument('--memory', default=DEFAULT_PATH)
    a = ap.parse_args(argv)
    from .net import use_macos_keychain
    use_macos_keychain()

    if a.cmd in ('validate', 'plan', 'run', 'bench', 'eval-judge', 'rejudge'):
        try:
            brief = spec.load(a.brief)
        except spec.SpecError as e:
            sys.exit(f'бриф не прошёл проверку: {e}')
    if a.cmd == 'validate':
        print(f'ok: {brief.project}, шотов {len(brief.shots)}, товаров {len(brief.products)}, формат {brief.width}×{brief.height}')
    elif a.cmd == 'plan':
        tasks, est = plan(brief)
        for t in tasks:
            print(f'  {t[0]:<12} {t[1]}')
        print(f"стоимость mock (симуляция): ${est['mock'][0]}–{est['mock'][1]}; live (оценка по config/prices.yaml): ${est['live'][0]}–{est['live'][1]}")
    elif a.cmd == 'run':
        o = Orchestrator(brief, a.mode, a.seed, a.memory, a.runs, a.allow_spend, a.defect_rate, a.judge, a.resume)
        res = o.run()
        print(open(os.path.join(o.log.dir, 'report.md')).read())
        print('журнал:', o.log.dir)
    elif a.cmd == 'bench':
        cmd_bench(brief, a.seeds, a.out, a.defect_rate)
    elif a.cmd == 'eval-judge':
        from . import evals
        kw = {'model': a.model} if a.judge == 'claude' else {}
        if a.labels:
            kw.update({k: v for k, v in (('vocab', brief.judge.get('vocab')), ('describe', brief.judge.get('describe'))) if v})
            if a.no_zoom:
                kw['zoom_below'] = None
            ids = set(a.ids.split(',')) if a.ids else None
            m, rows, out = evals.run_labeled(brief, a.labels, a.runs, a.workers, ids, **kw)
            for r in rows:
                mark = 'OK ' if r['label_ok'] == r['judge_ok'] else 'ERR'
                print(f"  {mark} {r['id']:<6} разметка {'чист' if r['label_ok'] else 'брак'} судья {'чист' if r['judge_ok'] else 'брак'} "
                      f"ΔE {r['delta_e']} {'зум ' if r.get('zoomed') else ''}{'спор ' + str(r['disputed']) + ' ' if r.get('disputed') else ''}{r['wrong'] or ''} {r['error'] or ''}")
            print(json.dumps(m, ensure_ascii=False, indent=2))
            print('файлы:', out)
            return
        m, rows, out = evals.run(brief, a.judge, a.n, a.runs, a.seed, a.workers, **kw)
        for r in rows:
            mark = 'ERR' if r['error'] else ('брак' if r['defect'] else 'чист')
            wrong = {k: f"{v}→{r['got'].get(k)}" for k, v in r['truth'].items() if r['got'].get(k) != v}
            print(f"  {r['path']:<12} {mark:<5} {'ok' if not wrong and not r['error'] else wrong or r['error']}")
        print(json.dumps(m, ensure_ascii=False, indent=2))
        print('файлы:', out)
    elif a.cmd == 'rejudge':
        from . import evals
        kw = {k: v for k, v in (('vocab', brief.judge.get('vocab')), ('describe', brief.judge.get('describe')),
                                ('model', brief.judge.get('model'))) if v} if a.judge == 'claude' else {}
        rows, out = evals.rejudge(brief, a.run_dir, a.judge, **kw)
        for r in rows:
            why = '; '.join(f"{m['attribute']}: {m['got']}" for m in r['mismatches'])
            print(f"  {r['shot']:<18} было {r['was']:<9} стало {r['now']:<9} ΔE {r['color_delta_e']}  {why}")
        print('журнал:', out)
    elif a.cmd == 'new':
        from . import new
        photos = new.list_photos(a.photos_dir)
        if not photos:
            sys.exit(f'в {a.photos_dir} нет фото ({", ".join(new.IMG)})')
        task = open(a.task_file).read() if a.task_file else a.task
        draft = new.ask_claude(photos[:8], task)
        out = a.out or os.path.join(ROOT, 'briefs', 'local', f'{new.slug(a.project)}.yaml')
        new.write(new.build_brief(draft, photos[:8], a.project, a.brand), out)
        b = spec.load(out)
        tasks, est = plan(b)
        print(f'черновик: {out}\nтоваров {len(b.products)}, шотов {len(b.shots)}; live: ${est["live"][0]}–{est["live"][1]}')
        for pid, p in b.products.items():
            print(f'  {pid}: {p.attributes}')
    elif a.cmd == 'weekly':
        import datetime as dt
        from . import weekly
        d = lambda x: dt.date.fromisoformat(x) if x else None
        since, until = d(a.since), d(a.until)
        md = weekly.render(weekly.summary(weekly.live_runs(a.runs, since, until)), since, until)
        print(md)
        if a.save:
            wk_id = (until or dt.date.today()).strftime('%G-W%V')
            out = os.path.join(ROOT, 'results', 'weekly', f'{wk_id}.md')
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, 'w') as f:
                f.write(md)
            print('сохранено:', out)
    elif a.cmd == 'dashboard':
        from .dashboard import render
        print(render(a.runs))
    elif a.cmd == 'stats':
        cmd_stats(a.runs, a.json)
    elif a.cmd == 'memory':
        mem = Memory(a.memory)
        if a.action == 'reset':
            mem.rules = []
            mem.save()
            print('память очищена:', a.memory)
        for r in mem.rules:
            print(f"- [{r['tag']}] {r['text']}  (hits {r.get('hits', 0)}, с {r.get('learned_in', '?')})")
        if not mem.rules and a.action == 'list':
            print('правил нет')
