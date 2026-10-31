"""Приёмка M1 (specs/m1-mock-pipeline.md). Запуск: python3 -m unittest -v"""
import os
import shutil
import tempfile
import unittest

import yaml
from PIL import Image

from factory import spec
from factory.agents.judge import JudgeAgent
from factory.agents.montage import chunks
from factory.backends.local import have_ffmpeg
from factory.budget import Budget, BudgetExceeded
from factory.memory import Memory
from factory.orchestrator import Orchestrator
from factory.runlog import RunLog

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIEF = os.path.join(ROOT, 'briefs', 'demo_earrings.yaml')


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def brief(self, **outputs):
        b = spec.load(BRIEF)
        if outputs:
            b.outputs = outputs
        return b

    def run_mock(self, seed=0, mem='mem.yaml', brief=None):
        o = Orchestrator(brief or self.brief(cards=False, videos=False, montage=False), 'mock', seed,
                         os.path.join(self.tmp, mem), os.path.join(self.tmp, 'runs'))
        return o, o.run()


class TestSpec(Base):
    def test_demo_brief_valid(self):
        b = self.brief()
        self.assertEqual((b.width, b.height), (720, 960))
        self.assertEqual(len(b.shots), 4)

    def test_unknown_product_rejected(self):
        with open(BRIEF) as f:
            raw = yaml.safe_load(f)
        raw['shots'][0]['product'] = 'nope'
        for p in raw['products']:
            p['photos'] = [os.path.join(ROOT, 'examples', 'assets', os.path.basename(x)) for x in p['photos']]
        for r in raw['references']:
            r['path'] = os.path.join(ROOT, 'examples', 'assets', os.path.basename(r['path']))
        path = os.path.join(self.tmp, 'bad.yaml')
        with open(path, 'w') as f:
            yaml.safe_dump(raw, f, allow_unicode=True)
        with self.assertRaisesRegex(spec.SpecError, 'неизвестный product'):
            spec.load(path)


class TestMemory(Base):
    def test_learn_then_hit_and_scope(self):
        b = self.brief()
        m = Memory(os.path.join(self.tmp, 'm.yaml'))
        r, new = m.learn(b.products['stud-pearl'], 'stone', 'pearl', 'crystal', 'run1')
        self.assertTrue(new)
        r, new = m.learn(b.products['stud-pearl'], 'stone', 'pearl', 'crystal', 'run2')
        self.assertFalse(new)
        self.assertEqual(Memory(m.path).rules[0]['hits'], 2)  # сохранено на диск
        self.assertEqual(len(m.for_product(b.products['stud-pearl'])), 1)
        # тот же тег earring-stud, но камень другой — правило про жемчуг не применяется
        self.assertEqual(m.for_product(b.products['stud-crystal']), [])


class TestJudge(Base):
    def test_technical_checks(self):
        b = self.brief()
        j = JudgeAgent(RunLog(self.tmp, 'r', 'mock'), None, b)
        p = os.path.join(self.tmp, 'x.png')
        Image.new('RGB', (b.width, b.height), (128, 128, 128)).save(p)
        self.assertIn('пустой/однотонный кадр', j.technical(p, [b.width, b.height]))
        Image.new('RGB', (100, 100)).save(p)
        self.assertTrue(any('размер' in i for i in j.technical(p, None)))


class TestBudget(Base):
    def test_reserve_over_limit(self):
        bud = Budget(0.05)
        bud.reserve('frame', 'mock')
        with self.assertRaises(BudgetExceeded):
            bud.reserve('frame', 'mock')

    def test_run_stops_on_budget(self):
        b = self.brief(cards=False, videos=False, montage=False)
        b.limits['budget_usd'] = 0.08
        _, m = self.run_mock(brief=b)
        self.assertTrue(m['stopped_by_budget'])
        self.assertLessEqual(m['cost_usd'], 0.08)

    def test_live_requires_allow_spend(self):
        with self.assertRaises(SystemExit):
            Orchestrator(self.brief(), 'live', runs_dir=os.path.join(self.tmp, 'runs'))


class TestPipeline(Base):
    @unittest.skipUnless(have_ffmpeg(), 'нужен ffmpeg')
    def test_end_to_end_mock(self):
        o, m = self.run_mock(brief=self.brief(cards=True, videos=True, montage=True))
        self.assertEqual(m['mode'], 'mock')
        self.assertTrue(m['cost_is_simulated'])
        self.assertEqual(m['frames_generated'], m['frames_approved'] + m['frames_rejected'])
        self.assertTrue(os.path.exists(os.path.join(o.log.dir, m['montage'])))
        for f in ('events.jsonl', 'metrics.json', 'report.md'):
            self.assertTrue(os.path.exists(os.path.join(o.log.dir, f)))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, 'runs', 'index.jsonl')))

    def test_rejects_go_back_with_feedback(self):
        import json
        for seed in range(10):
            o, m = self.run_mock(seed=seed, mem=f'm{seed}.yaml')
            if m['frames_rejected']:
                with open(os.path.join(o.log.dir, 'events.jsonl')) as f:
                    ev = [json.loads(l) for l in f]
                rej = next(e for e in ev if e['event'] == 'rejected')
                retry = next(e for e in ev if e['event'] == 'generated' and e['task'] == rej['task'] and e['attempt'] == rej['attempt'] + 1)
                self.assertIn(rej['mismatches'][0]['attribute'], retry['constraints'])
                return
        self.fail('ни одной отбраковки за 10 сидов')

    def test_memory_lowers_reject_rate(self):
        cold = warm = 0
        for seed in range(12):
            cold += self.run_mock(seed, mem=f'cold{seed}.yaml')[1]['frames_rejected']
            warm += self.run_mock(seed, mem='warm.yaml')[1]['frames_rejected']
        self.assertLess(warm, cold)


class TestSubtitles(unittest.TestCase):
    def test_no_dangling_function_words(self):
        self.assertEqual(chunks('Серебряные кольца для смелого образа'), ['Серебряные кольца', 'для смелого образа'])
        for c in chunks('Вы реально думаете, что на маркетплейсе можно купить технику в два раза дешевле'):
            self.assertNotIn(c.split()[-1].lower(), {'в', 'на', 'что', 'для'})


if __name__ == '__main__':
    unittest.main()
