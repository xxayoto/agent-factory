"""Журнал прогона: события в events.jsonl, итоговые метрики в metrics.json и report.md, сводка в runs/index.jsonl."""
import json
import os
import threading
import time


class RunLog:
    def __init__(self, runs_dir, run_id, mode, resume=False):
        self.dir = os.path.join(runs_dir, run_id)
        self.runs_dir = runs_dir
        os.makedirs(self.dir, exist_ok=True)
        self.run_id, self.mode = run_id, mode
        self.previous = self.read() if resume else []
        # время продолжает счёт с последнего события прерванного прогона
        self.t0 = time.time() - (self.previous[-1]['t'] if self.previous else 0)
        self.lock = threading.Lock()
        self.cost = 0.0
        self.agent_time = {}
        self._f = open(os.path.join(self.dir, 'events.jsonl'), 'a')

    def read(self):
        p = os.path.join(self.dir, 'events.jsonl')
        if not os.path.exists(p):
            return []
        with open(p) as f:
            return [json.loads(l) for l in f if l.strip()]

    def event(self, agent, task, event, **data):
        rec = {'t': round(time.time() - self.t0, 3), 'agent': agent, 'task': task, 'event': event, **data}
        with self.lock:
            self._f.write(json.dumps(rec, ensure_ascii=False) + '\n')
            self._f.flush()
        return rec

    def add_cost(self, usd):
        with self.lock:
            self.cost += usd

    def add_time(self, agent, sec):
        with self.lock:
            self.agent_time[agent] = self.agent_time.get(agent, 0.0) + sec

    def finish(self, metrics):
        metrics = {'run_id': self.run_id, 'mode': self.mode, 'cost_usd': round(self.cost, 4),
                   'cost_is_simulated': self.mode == 'mock', 'wall_sec': round(time.time() - self.t0, 2),
                   'agent_sec': {k: round(v, 2) for k, v in self.agent_time.items()}, **metrics}
        self._f.close()
        with open(os.path.join(self.dir, 'metrics.json'), 'w') as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)
        with open(os.path.join(self.runs_dir, 'index.jsonl'), 'a') as f:
            f.write(json.dumps(metrics, ensure_ascii=False) + '\n')
        with open(os.path.join(self.dir, 'report.md'), 'w') as f:
            f.write(render_report(metrics))
        return metrics


def render_report(m):
    sim = ' (симуляция, деньги не тратились)' if m['cost_is_simulated'] else ''
    lines = [
        f"# Прогон {m['run_id']} — {m['project']}",
        '',
        f"- режим: **{m['mode']}**",
        f"- кадров сгенерировано: {m['frames_generated']}, принято: {m['frames_approved']}, отбраковано: {m['frames_rejected']}",
        f"- доля брака: {m['reject_rate']:.0%}, с первой попытки: {m['first_pass_yield']:.0%}",
        f"- шотов не удалось: {len(m['failed_shots'])} {m['failed_shots'] or ''}",
        f"- правил применено: {m['rules_applied']}, выучено новых: {m['rules_learned']}",
        f"- судья: {m.get('judge')}" + (f", верный вердикт {m['judge_verdict_accuracy']:.0%}, атрибуты точно {m['judge_attrs_exact']:.0%}"
                                        if m.get('judge_verdict_accuracy') is not None else ''),
        f"- карточек: {m['cards']}, видео: {m['videos']} (принято судьёй {m.get('videos_approved', 0)}), монтаж: {m['montage'] or 'нет'}",
        f"- QC монтажа: " + ('—' if m.get('qc_ok') is None else ('пройден' if m['qc_ok'] else f"не пройден: {', '.join(m.get('qc_failed') or [])}")),
        f"- стоимость: ${m['cost_usd']:.3f}{sim}" + (f"; по балансу провайдера: ${m['real_spend_usd']:.3f}" if m.get('real_spend_usd') is not None else ''),
        f"- время: {m['wall_sec']} с; по агентам: {m['agent_sec']}",
        '',
    ]
    return '\n'.join(lines)


def load_index(runs_dir):
    p = os.path.join(runs_dir, 'index.jsonl')
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return [json.loads(l) for l in f if l.strip()]
