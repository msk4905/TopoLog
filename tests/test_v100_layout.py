"""v1.0.0 result layout: only what people open stays at the top, records go to 수집_기록."""
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
import zipfile
from urllib.parse import unquote
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import topolog.engine as _engine; _engine.SERVER_CHECK_INTERVAL = 0   # no real server to wait for
from topolog.engine import Runner  # noqa: E402
from topolog.resultdir import RECORDS, records, top  # noqa: E402
from test_batch import FakeAPI, collected, project, P  # noqa: E402
from test_v015_outputs import fake_capture  # noqa: E402

TOP = {'index.html', 'ai_prompt.md', RECORDS, '토폴로지_사진'}


def links(html):
    return [unquote(m) for m in re.findall(r'(?:href|src)="\./([^"#]+)"', html)]


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        labs = self.root / 'labs'
        labs.mkdir()
        project(labs, 'routing', P)
        self.output = self.root / 'out'
        with patch('topolog.topology.capture_native', side_effect=fake_capture):
            self.report = Runner(FakeAPI(), self.output, collector=collected, settle_seconds=0, capture=True).run(labs)

    def tearDown(self):
        self.temp.cleanup()

    def test_top_holds_only_what_people_open(self):
        names = {p.name for p in self.output.iterdir()}
        word = {n for n in names if n.endswith('.docx')}
        # The collection-time Word report, plus the annotated one where Windows drew annotated copies.
        self.assertEqual(len([n for n in word if not n.endswith('_주석본.docx')]), 1)
        self.assertLessEqual(len(word), 2)
        self.assertEqual(names - word, TOP)
        data = records(self.output)
        self.assertEqual(data, self.output / RECORDS)
        for name in ('summary.json', 'manifest.json', 'criteria-used.json', 'findings.md', 'routing'):
            self.assertTrue((data / name).exists(), name)
        self.assertTrue(next(data.glob('report(*).md')).is_file())
        self.assertTrue(next(data.glob('report-document(*).md')).is_file())
        self.assertEqual(top(data), self.output)                         # picking the records folder still works

    def test_every_report_link_and_picture_resolves(self):
        html = (self.output / 'index.html').read_text(encoding='utf-8')
        found = links(html)
        self.assertTrue(any(f.startswith(RECORDS + '/routing/') for f in found))
        self.assertTrue(any(f.startswith('토폴로지_사진/') for f in found))
        self.assertIn('토폴로지_사진/', found)                               # folder links open the folders
        self.assertIn(RECORDS + '/', found)
        self.assertIn(RECORDS + '/routing/', found)
        for relative in found:
            target = self.output / relative
            self.assertTrue(target.is_dir() if relative.endswith('/') else target.is_file(), relative)
        record = next(records(self.output).glob('report(*).md'))
        for target in re.findall(r'!\[[^\]]*\]\(([^)]+)\)', record.read_text(encoding='utf-8')):
            self.assertTrue((record.parent / unquote(target)).resolve().is_file(), target)
        document = next(records(self.output).glob('report-document(*).md'))
        for target in re.findall(r'!\[[^\]]*\]\(([^)]+)\)', document.read_text(encoding='utf-8')):
            self.assertTrue((document.parent / unquote(target)).resolve().is_file(), target)

    def test_manifest_and_zip_cover_the_whole_result(self):
        manifest = {f['path'] for f in json.loads((records(self.output) / 'manifest.json').read_text(encoding='utf-8'))['files']}
        self.assertIn('index.html', manifest)
        self.assertIn(RECORDS + '/summary.json', manifest)
        with zipfile.ZipFile(str(self.output) + '.zip') as archive:
            self.assertIn(RECORDS + '/manifest.json', archive.namelist())
            self.assertIn('index.html', archive.namelist())

    def test_refresh_and_reassess_keep_the_layout_and_old_results_still_open(self):
        from topolog.presentation import refresh_report
        from topolog.criteria import reassess
        refresh_report(self.output)
        self.assertFalse((self.output / 'summary.json').exists())
        again = self.root / 'again'
        reassess(self.output, again)
        self.assertEqual({p.name for p in again.iterdir()} - {n for n in os.listdir(again) if n.endswith('.docx')}, TOP)
        self.assertTrue((again / RECORDS / 'routing' / 'topology.json').is_file())
        self.assertFalse((again / RECORDS / '토폴로지_사진').exists())
        # A result written before v1.0.0 (everything at the top) still refreshes in place.
        old = self.root / 'old'
        shutil.copytree(self.output / RECORDS, old)
        summary = json.loads((old / 'summary.json').read_text(encoding='utf-8'))
        summary.pop('records_dir')
        (old / 'summary.json').write_text(json.dumps(summary), encoding='utf-8')
        refresh_report(old)
        html = (old / 'index.html').read_text(encoding='utf-8')
        for relative in links(html):
            target = old / relative
            self.assertTrue(target.is_dir() if relative.endswith('/') else target.is_file(), relative)
        self.assertNotIn(RECORDS + '/', links(html))
        self.assertFalse((old / RECORDS).exists())


if __name__ == '__main__':
    unittest.main()
