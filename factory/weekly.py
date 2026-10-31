"""factory weekly: отчёт по реальным (live) прогонам за период — цифры для кейса. Mock в отчёт не попадает."""
import datetime as dt

from .runlog import load_index


def live_runs(runs_dir, since=None, until=None):
    """Последняя запись каждого live-прогона (--resume дописывает прогон в индекс повторно)."""
    last = {}
    for m in load_index(runs_dir):
        if m.get('mode') != 'live':
            continue
        day = dt.datetime.strptime(m['run_id'][:8], '%Y%m%d').date()
        if (since and day < since) or (until and day > until):
            continue
        last[m['run_id']] = m
    return list(last.values())


def summary(runs):
    frames = sum(m['frames_generated'] for m in runs)
    ok = sum(m['frames_approved'] for m in runs)
    usd = sum(m['real_spend_usd'] if m.get('real_spend_usd') is not None else m['cost_usd'] for m in runs)
    sec = sum(m['wall_sec'] for m in runs)
    qc = [m['qc_ok'] for m in runs if m.get('qc_ok') is not None]
    return {'runs': len(runs), 'projects': len({m['project'] for m in runs}), 'frames': frames, 'approved': ok,
            'approved_rate': round(ok / frames, 3) if frames else None, 'first_pass': round(sum(m['first_pass_yield'] * m['shots'] for m in runs)
                                                                                           / max(1, sum(m['shots'] for m in runs)), 3),
            'videos': sum(m.get('videos', 0) for m in runs), 'usd': round(usd, 3),
            # реальный баланс провайдера не делится на кадры и ролики — доли берутся по прайсу
            'usd_per_approved': round(sum(m.get('cost_frames_usd', m['cost_usd']) for m in runs) / ok, 4) if ok else None,
            'usd_per_video': round(sum(m.get('cost_videos_usd', 0) for m in runs) / max(1, sum(m.get('videos', 0) for m in runs)), 3),
            'sec_per_approved': round(sec / ok, 1) if ok else None,
            'qc_passed': f'{sum(qc)}/{len(qc)}' if qc else '—'}


def render(s, since, until):
    rng = f"{since or '…'} — {until or '…'}"
    return '\n'.join([
        f'# Неделя фабрики: {rng}', '',
        '| Метрика | Значение |', '|---|---|',
        f"| live-прогонов / проектов | {s['runs']} / {s['projects']} |",
        f"| кадров сгенерировано / принято | {s['frames']} / {s['approved']} ({s['approved_rate'] if s['approved_rate'] is not None else '—'}) |",
        f"| принято с первой попытки (по шотам) | {s['first_pass']} |",
        f"| роликов | {s['videos']} |",
        f"| реально потрачено | ${s['usd']} |",
        f"| $ на принятый кадр (по прайсу, все попытки) | {s['usd_per_approved'] if s['usd_per_approved'] is not None else '—'} |",
        f"| $ на ролик (по прайсу) | {s['usd_per_video'] if s['videos'] else '—'} |",
        f"| секунд прогона на принятый кадр (с судьёй, роликами, монтажом) | {s['sec_per_approved'] if s['sec_per_approved'] is not None else '—'} |",
        f"| QC монтажа пройден | {s['qc_passed']} |", ''])
