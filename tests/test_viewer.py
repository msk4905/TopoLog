import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from topolog.viewer import open_report, latest_report, report_view
import json

class ViewerTests(unittest.TestCase):
    def test_legacy_preview_is_upgraded_without_overwriting_original(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);report=root/'index.html'
            report.write_text('<h1>TopoLog</h1><pre># raw markdown</pre>')
            original=report.read_bytes()
            (root/'summary.json').write_text(json.dumps({'schema':'topolog-batch-v2','verdict':'inconclusive','projects':[]}))
            cache=root/'cache';cache.mkdir()
            with patch('topolog.viewer.tempfile.mkdtemp',return_value=str(cache)):
                view=report_view(report)
            self.assertEqual(report.read_bytes(),original)
            content=view.read_text(encoding='utf-8')
            self.assertIn('<base href="'+root.as_uri()+'/',content)
            self.assertIn('프로젝트 요약',content)
            self.assertNotIn('# raw markdown',content)

    def test_windows_launch_uses_separate_arguments_for_a_local_report(self):
        if os.name!='nt':self.skipTest('Windows only')
        with tempfile.TemporaryDirectory() as temp:
            report=Path(temp)/'a space.html';report.touch()
            browser=Path(temp)/'chrome.exe'
            with patch('topolog.viewer.windows_browsers',return_value=[browser]), patch('topolog.viewer.subprocess.Popen') as launch:
                self.assertEqual(open_report(report),'chrome.exe')
                self.assertEqual(launch.call_args.args[0],[str(browser),report.resolve().as_uri()])

    def test_failure_falls_back_and_missing_report_does_not_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            report=Path(temp)/'index.html';report.touch()
            with patch('topolog.viewer.windows_browsers',return_value=[]),patch('topolog.viewer.webbrowser.open',return_value=False):
                with self.assertRaises(OSError):open_report(report)
            with patch('topolog.viewer.subprocess.Popen') as launch:
                with self.assertRaises(OSError):open_report(report.parent/'missing.html')
                launch.assert_not_called()

    def test_restart_finds_existing_report_and_ignores_zip(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);run=root/'batch_a';run.mkdir();(run/'index.html').touch()
            (root/'batch_z.zip').touch()
            self.assertEqual(latest_report(root),run)
            self.assertIsNone(latest_report(root/'missing'))
