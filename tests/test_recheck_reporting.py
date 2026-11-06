from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from topolog import findings,reporting,portfolio
from topolog.engine import write_collection_record
import test_portfolio as fixtures
import test_final_observation as observation


class RecheckReportingTests(unittest.TestCase):
    def test_legacy_failure_visible_in_all_report_formats_without_mutating_data(self):
        report=fixtures.report();node=report['projects'][0]['nodes'][0]
        node['final_routing']={'status':'completed','rechecks':[{'status':'failed','reason':'timeout'}]}
        note='마지막 재확인 실패'
        self.assertIn(note,reporting.render(report,[]))
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);write_collection_record(folder,report);portfolio.write(report,folder)
            for name in ['findings.md','report(2026-11-04).md','report-document(2026-11-04).md']:
                self.assertIn(note,(folder/name).read_text(encoding='utf-8'))
        self.assertEqual(node['final_routing']['status'],'completed')
        self.assertEqual(node['checks'][1]['observed'],'경로 없음')

    def test_later_success_does_not_show_stale_failure_warning(self):
        node={'final_routing':{'status':'completed','rechecks':[{'status':'failed'},{'status':'completed'}]}}
        self.assertEqual(findings.final_recheck_note(node),'')

    def test_rejected_recheck_is_partial_and_preserves_original(self):
        node=observation.node('r1',[('show ip interface brief',observation.BRIEF_DOWN)])
        observation.run([node],{'show ip interface brief':[observation.BRIEF_DOWN,'% Invalid input detected at ^ marker.']},stabilize_delay=0,stabilize_rounds=1)
        self.assertEqual(node['collection'],'partial')
        self.assertEqual(node['final_routing']['status'],'recheck_failed')
        self.assertTrue(node['final_routing']['rechecks'][-1]['status']=='failed')
        self.assertEqual(node['commands'][0]['text'],observation.BRIEF_DOWN)


if __name__=='__main__':unittest.main()
