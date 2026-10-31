"""Приёмка M1 (specs/m1-mock-pipeline.md). Запуск: python3 -m unittest -v"""
import os
import shutil
import subprocess
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

    @unittest.skipUnless(have_ffmpeg(), 'нужен ffmpeg')
    def test_relative_runs_dir(self):
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            o = Orchestrator(self.brief(cards=False, videos=False, montage=True), 'mock', 0, 'mem.yaml', 'runs')
            m = o.run()
        finally:
            os.chdir(cwd)
        self.assertTrue(m['montage'])

    @unittest.skipUnless(have_ffmpeg(), 'нужен ffmpeg')
    def test_video_is_judged_separately(self):
        import json
        o, m = self.run_mock(brief=self.brief(cards=False, videos=True, montage=False))
        with open(os.path.join(o.log.dir, 'events.jsonl')) as f:
            ev = [json.loads(l) for l in f]
        vj = [e for e in ev if e['event'] in ('video_frame_approved', 'video_frame_rejected')]
        self.assertEqual(len(vj), 2 * m['videos'])  # середина и конец каждого ролика
        self.assertTrue(any(e['event'] in ('video_approved', 'video_rejected') for e in ev))
        judged = [e for e in ev if e['event'] in ('approved', 'rejected')]
        self.assertEqual(len(judged), m['frames_generated'])  # кадры ролика не попали в статистику кадров

    @unittest.skipUnless(have_ffmpeg(), 'нужен ффmpeg'.replace('фф', 'ff'))
    def test_montage_uses_clean_frames_not_cards(self):
        import json
        o, m = self.run_mock(brief=self.brief(cards=True, videos=False, montage=True))
        with open(os.path.join(o.log.dir, 'events.jsonl')) as f:
            mont = [json.loads(l) for l in f if '"montage"' in l and '"done"' in l][0]
        parts = os.listdir(os.path.join(o.log.dir, 'out', 'montage_parts'))
        self.assertTrue(mont['shots'])
        self.assertFalse(any('_card' in p for p in parts))

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


class TestResilience(Base):
    def test_one_shot_failure_does_not_stop_run(self):
        o = Orchestrator(self.brief(cards=False, videos=False, montage=False), 'mock', 0,
                         os.path.join(self.tmp, 'm.yaml'), os.path.join(self.tmp, 'runs'))
        real = o.frame.backend.generate

        def flaky(req, out):
            if req['shot'] == 's2-hoop':
                raise TimeoutError('The read operation timed out')
            return real(req, out)
        o.frame.backend.generate = flaky
        m = o.run()
        self.assertEqual(m['failed_shots'], ['s2-hoop'])
        self.assertEqual(m['frames_approved'] + len(m['failed_shots']), 4)

    def test_resume_does_not_pay_twice(self):
        import json
        b = self.brief(cards=False, videos=False, montage=False)
        runs, mem = os.path.join(self.tmp, 'runs'), os.path.join(self.tmp, 'm.yaml')
        o = Orchestrator(b, 'mock', 0, mem, runs)
        m1 = o.run()
        ev_path = os.path.join(o.log.dir, 'events.jsonl')
        with open(ev_path) as f:
            ev = [json.loads(l) for l in f]
        # «падение» перед вердиктом по последнему шоту: кадр на диске, вердикта и итогов нет
        last = max(i for i, e in enumerate(ev) if e['event'] == 'approved')
        with open(ev_path, 'w') as f:
            f.write(''.join(json.dumps(e, ensure_ascii=False) + '\n' for e in ev[:last]))
        o2 = Orchestrator(b, 'mock', 0, mem, runs, resume=o.log.dir)
        m2 = o2.run()
        self.assertEqual(m2['frames_generated'], m1['frames_generated'])
        self.assertAlmostEqual(m2['cost_usd'], m1['cost_usd'])
        self.assertEqual(m2['frames_approved'], m1['frames_approved'])
        self.assertTrue(m2['resumed'])


class TestJudgeEval(Base):
    def test_mock_judge_eval_has_known_truth(self):
        from factory import evals
        m, rows, out = evals.run(self.brief(), 'mock', 10, os.path.join(self.tmp, 'runs'), seed=3)
        self.assertEqual(m['defective'] + m['clean'], 10)
        self.assertGreater(m['defective'], 0)
        self.assertEqual((m['attribute_accuracy'], m['defect_recall'], m['false_reject_rate']), (1.0, 1.0, 0.0))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, 'runs', 'judge_evals.jsonl')))

    def test_defect_is_drawn_not_written(self):
        from factory.backends.render import earrings
        stud = earrings({'earring_type': 'stud', 'metal': 'gold', 'stone': 'pearl'})
        drop = earrings({'earring_type': 'drop', 'metal': 'gold', 'stone': 'pearl'})
        self.assertNotEqual(stud.getbbox(), drop.getbbox())  # у подвески выше рамка — дефект виден глазами

    def test_zoom_crop_centers_small_earring(self):
        from factory.backends.claude_judge import zoom_crop
        p = os.path.join(self.tmp, 'full.png')
        im = Image.new('RGB', (1800, 2400), (200, 190, 180))
        im.paste(Image.new('RGB', (54, 54), (255, 0, 0)), (1300, 400))  # «серьга» ~3 % ширины
        im.save(p)
        z = Image.open(zoom_crop(p, {'x0': 1300 / 1800, 'y0': 400 / 2400, 'x1': 1354 / 1800, 'y1': 454 / 2400}))
        self.assertEqual(max(z.size), 1024)
        self.assertEqual(z.getpixel((z.width // 2, z.height // 2)), (255, 0, 0))  # серьга в центре и крупно

    def test_claude_judge_schema(self):
        from factory.backends.claude_judge import VOCAB, schema
        sc = schema(['earring_type', 'metal', 'engraving'], VOCAB)
        self.assertEqual(sc['properties']['earring_type']['enum'], ['stud', 'hoop', 'drop'])
        self.assertEqual(sc['properties']['engraving'], {'type': 'string'})
        self.assertIn('notes', sc['required'])


class TestColor(Base):
    def test_delta_e_separates_stone_colors(self):
        from factory.color import delta_e, stone_lab

        def img(rgb):
            p = os.path.join(self.tmp, f'{rgb}.png')
            im = Image.new('RGB', (200, 200), (250, 250, 248))  # белый фон не должен влиять
            im.paste(Image.new('RGB', (80, 80), rgb), (60, 60))
            im.save(p)
            return p
        amber, amber2, blue = img((205, 130, 40)), img((200, 128, 45)), img((60, 120, 210))
        self.assertLess(delta_e(stone_lab(amber), stone_lab(amber2)), 5)
        self.assertGreater(delta_e(stone_lab(amber), stone_lab(blue)), 25)

    def test_scene_reject_is_not_learned_into_memory(self):
        o = Orchestrator(self.brief(cards=False, videos=False, montage=False), 'mock', 0,
                         os.path.join(self.tmp, 'm.yaml'), os.path.join(self.tmp, 'runs'))
        real = o.judge.review

        def scene_fail(shot, product, path, raw, rules, attempt):
            ok, tech, mism, extra = real(shot, product, path, raw, rules, attempt)
            if attempt == 1:
                mism = mism + [{'attribute': 'scene', 'expected': 'shot brief', 'got': 'worn instead of held', 'hint': 'x'}]
                ok = False
            return ok, tech, mism, extra
        o.judge.review = scene_fail
        o.run()
        self.assertFalse(any(r['attribute'] == 'scene' for r in o.memory.rules))


class TestVoice(unittest.TestCase):
    def test_align_fixes_mishearing(self):
        from factory.voice import align
        heard = [{'w': 'сияние,', 's': 0.0, 'e': 0.52}, {'w': 'которые', 's': 0.64, 'e': 0.9},
                 {'w': 'замечают', 's': 0.9, 'e': 1.7}]
        words, mis = align('Сияние, которое замечают', heard)
        self.assertEqual([w['t'] for w in words], ['Сияние,', 'которое', 'замечают'])
        self.assertEqual(words[1]['s'], 0.64)
        self.assertEqual(mis, [{'script': 'которое', 'heard': 'которые'}])
        # слово не распознано вовсе — время между соседями
        words, mis = align('Сияние, которое замечают', [heard[0], heard[2]])
        self.assertTrue(0.52 <= words[1]['s'] < words[1]['e'] <= 0.9)

    def test_phrases(self):
        from factory.voice import FUNC, norm, phrases
        ws = [{'t': t, 's': i * 0.3, 'e': i * 0.3 + 0.25} for i, t in
              enumerate('Серебряные кольца для смелого образа и для каждого дня'.split())]
        ph = phrases(ws)
        self.assertTrue(all(len(p['words']) <= 4 for p in ph))
        self.assertTrue(all(norm(p['words'][-1]['t']) not in FUNC for p in ph))
        self.assertEqual(sum(len(p['words']) for p in ph), len(ws))


@unittest.skipUnless(have_ffmpeg(), 'нужен ffmpeg')
class TestQC(Base):
    def lavfi(self, name, src, sec):
        p = os.path.join(self.tmp, name)
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f"{src}{':' if '=' in src else '='}d={sec}:s=320x240:r=25", '-pix_fmt', 'yuv420p', p], check=True)
        return p

    def test_black_detected(self):
        from factory.agents.qc import black_segments
        self.assertTrue(black_segments(self.lavfi('black.mp4', 'color=c=black', 1)))
        self.assertFalse(black_segments(self.lavfi('noise.mp4', 'testsrc2', 1)))

    def test_freeze_detected(self):
        from factory.agents.qc import freeze_segments
        self.assertTrue(freeze_segments(self.lavfi('still.mp4', 'color=c=gray', 2)))
        self.assertFalse(freeze_segments(self.lavfi('moving.mp4', 'testsrc2', 2)))

    def test_wer(self):
        from factory.agents.qc import wer
        self.assertEqual(wer('Сияние, которое замечают'.split(), ['сияние', 'которое', 'замечают']), 0)
        self.assertAlmostEqual(wer('Сияние, которое замечают'.split(), ['сияние', 'которые', 'замечают']), 1 / 3)

    def test_phrase_across_cut_fails(self):
        from factory.agents.qc import QCAgent
        from factory.runlog import RunLog
        v = self.lavfi('m.mp4', 'testsrc', 4)
        w = lambda t, s: {'t': t, 's': s, 'e': s + 0.3}
        tl = {'duration': 4.0, 'words': [], 'sources': ['a.mp4', 'b.png'], 'segments': [[0, 2.0], [2.0, 2.0]],
              'phrases': [{'words': [w('которое', 1.5), w('замечают', 1.8), w('подарок', 2.1)], 's': 1.5, 'e': 2.4}]}
        ok, checks = QCAgent(RunLog(self.tmp, 'qc', 'mock')).run(v, tl)
        self.assertFalse(next(c for c in checks if c['name'] == 'фраза не переходит через склейку')['ok'])

    def test_mock_run_has_qc(self):
        o, m = self.run_mock(brief=self.brief(cards=False, videos=True, montage=True))
        self.assertIsNotNone(m['qc_ok'])
        self.assertTrue(os.path.exists(os.path.join(o.log.dir, 'out', 'montage.qc.json')))


class TestCard(unittest.TestCase):
    def test_logo_contrast(self):
        from factory.agents.card import logo_color
        self.assertNotEqual(logo_color(Image.new('RGB', (100, 100), (245, 238, 225)), (0, 0, 100, 100)), (255, 255, 255))
        self.assertEqual(logo_color(Image.new('RGB', (100, 100), (60, 40, 30)), (0, 0, 100, 100)), (255, 255, 255))


class TestSubtitles(unittest.TestCase):
    def test_no_dangling_function_words(self):
        self.assertEqual(chunks('Серебряные кольца для смелого образа'), ['Серебряные кольца', 'для смелого образа'])
        for c in chunks('Вы реально думаете, что на маркетплейсе можно купить технику в два раза дешевле'):
            self.assertNotIn(c.split()[-1].lower(), {'в', 'на', 'что', 'для'})


if __name__ == '__main__':
    unittest.main()
