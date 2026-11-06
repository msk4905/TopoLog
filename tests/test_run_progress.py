from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from topolog.run_progress import RunProgress
from topolog.engine import Runner
import test_batch as fixtures


class ProgressTests(unittest.TestCase):
    def test_monotonic_and_no_100_until_export_finished(self):
        now=[0.];tracker=RunProgress(lambda:now[0])
        self.assertIn('측정 중',tracker.snapshot()['text'])
        now[0]=20;tracker.update({'fraction':.25,'stage':'장비 수집'})
        self.assertEqual(tracker.snapshot()['percent'],25)
        self.assertIn('약 1분',tracker.snapshot()['text'])
        tracker.update({'fraction':.1});self.assertEqual(tracker.snapshot()['percent'],25)
        tracker.update({'fraction':1});self.assertEqual(tracker.snapshot()['percent'],99)
        tracker.finish(True);self.assertEqual(tracker.snapshot()['percent'],100)
        finished=tracker.snapshot()['text'];now[0]+=100
        self.assertEqual(tracker.snapshot()['text'],finished)

    def test_password_wait_excluded_from_estimate(self):
        now=[0.];tracker=RunProgress(lambda:now[0])
        now[0]=20;tracker.update({'fraction':.25,'paused':True})
        now[0]=80;self.assertIn('사용자 입력 대기',tracker.snapshot()['text'])
        tracker.update({'paused':False})
        self.assertEqual(tracker.busy_seconds,20)
        self.assertEqual(tracker.eta,60)

    def test_cancel_not_complete_and_restart_resets(self):
        tracker=RunProgress();tracker.update({'fraction':.3});tracker.finish(False)
        self.assertEqual(tracker.snapshot()['percent'],30)
        self.assertIn('중지됨',tracker.snapshot()['text'])
        tracker.reset();self.assertEqual(tracker.snapshot()['percent'],0)

    def test_runner_emits_real_milestones_and_project_timing(self):
        events=[]
        def collect(*args,**kwargs):
            return {'collection':'collected','platform':'unknown','commands':[],
                    'checks':[{'status':'unverified'}]}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'input';root.mkdir();fixtures.project(root,'p',fixtures.P)
            result=Runner(fixtures.FakeAPI(),Path(temp)/'out',collector=collect,settle_seconds=0,
                          reachability=False,progress_state=events.append).run(root)
        fractions=[event['fraction'] for event in events]
        self.assertEqual(fractions,sorted(fractions))
        self.assertEqual(fractions[-1],.95)
        self.assertTrue(result['projects'][0]['started_at'])
        self.assertTrue(result['projects'][0]['finished_at'])
        self.assertTrue(any('장비' in e['stage'] for e in events))


if __name__=='__main__':unittest.main()
