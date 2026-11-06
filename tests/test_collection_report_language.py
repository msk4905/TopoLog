from pathlib import Path
import copy
import sys
import tempfile
import unittest
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from topolog import portfolio,reporting
from topolog.engine import write_collection_record
import test_portfolio as fixtures


class CollectionLanguageTests(unittest.TestCase):
    def test_all_report_formats_keep_observations_without_grades(self):
        report=fixtures.report();report['verdict']='failed';before=copy.deepcopy(report)
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            write_collection_record(folder,report)
            portfolio.write(report,folder)
            with zipfile.ZipFile(folder/'report(2026-11-04).docx') as z:word=z.read('word/document.xml').decode()
            texts=[reporting.render(report,[]),word]+[p.read_text(encoding='utf-8') for p in folder.glob('report*.md')]
            for text in texts:
                for phrase in ['검증 항목','미충족','충족한 검증','전체 판정','기준 불일치','판정 보류','미검증','실패·보류 검토']:
                    self.assertNotIn(phrase,text)
                self.assertIn('경로 없음',text)
                self.assertIn('2.2.2.2',text)
        self.assertEqual(report,before)

    def test_collection_failure_is_not_hidden_with_behaviour_grades(self):
        report=fixtures.report();node=report['projects'][0]['nodes'][0]
        node.update(collection='failed',reason='Connection timed out')
        html=reporting.render(report,[])
        self.assertIn('수집 실패',html)
        self.assertIn('Connection timed out',html)
        self.assertIn('수집 메모',html)
        self.assertNotIn('기준 불일치',html)

    def test_no_success_ratios_in_overview_or_grouped_rows(self):
        report=fixtures.report();project=report['projects'][0]
        self.assertNotRegex(portfolio._headline(project),r'\d+/\d+')
        for row in portfolio._verification_rows(project):
            self.assertNotRegex(row[2],r'충족|미충족|\d+개 중 \d+개|\d+/\d+ 확인')


if __name__=='__main__':unittest.main()
