"""Downloaded-file mark: only TopoLog's own scripts in its own tools folder are unblocked."""
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from topolog import motw  # noqa: E402


class MarkTests(unittest.TestCase):
    def test_list_matches_the_bundled_scripts(self):
        spec = (Path(__file__).resolve().parents[1] / 'packaging' / 'TopoLog.spec').read_text(encoding='utf-8')
        bundled = re.search(r'capture_tools = \[(.*?)\]', spec, re.S).group(1)
        self.assertEqual(sorted(re.findall(r"'([^']+)'", bundled)), sorted(motw.SCRIPTS))
        for name in motw.SCRIPTS:
            self.assertTrue((Path(__file__).resolve().parents[1] / 'tools' / name).is_file(), name)

    def test_only_known_scripts_lose_the_mark_and_nothing_else_changes(self):
        removed = []
        def remove(path):
            if not path.endswith(motw.STREAM):
                raise AssertionError('only the mark stream may be removed: ' + path)
            if 'dialog-filename' in path:
                raise FileNotFoundError(path)                  # not marked
            if 'annotate' in path:
                raise PermissionError(path)
            removed.append(Path(path[:-len(motw.STREAM)]).name)
        with tempfile.TemporaryDirectory() as temp:
            for name in motw.SCRIPTS + ('other.ps1', 'TopoLog.exe'):
                (Path(temp) / name).write_text('x', encoding='utf-8')
            with patch('topolog.motw._windows', return_value=True), patch('topolog.motw.os.remove', side_effect=remove):
                result = motw.unblock(temp)
        self.assertNotIn('other.ps1', removed)
        self.assertNotIn('TopoLog.exe', removed)
        self.assertNotIn('dialog-filename.ps1', result['unblocked'])
        self.assertEqual(result['failed'], [('annotate-topology.ps1', 'PermissionError')])
        self.assertEqual(len(result['unblocked']), len(motw.SCRIPTS) - 2)
        self.assertIn('차단 해제 실패', motw.describe(result))

    def test_nothing_happens_off_windows(self):
        with patch('topolog.motw._windows', return_value=False), patch('topolog.motw.os.remove') as remove:
            self.assertEqual(motw.unblock('/nowhere'), {'unblocked': [], 'failed': []})
        remove.assert_not_called()
        self.assertEqual(motw.describe({'unblocked': [], 'failed': []}), '')


class PolicyTests(unittest.TestCase):
    def test_powershell_started_by_topolog_uses_process_remote_signed_never_bypass(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(motw.PROCESS_POLICY, ('-ExecutionPolicy', 'RemoteSigned'))
        files = list((root / 'tools').glob('*.ps1')) + list((root / 'tools' / 'topolog').glob('*.py')) + list(root.glob('*.cmd'))
        for path in files:
            text = path.read_text(encoding='utf-8-sig', errors='replace')
            self.assertNotRegex(text, r'(?i)-ExecutionPolicy[\s\',]+Bypass', path.name)
            self.assertNotRegex(text, r'(?i)Set-ExecutionPolicy', path.name)
        for name in ('topology.py', 'annotate.py'):
            self.assertIn('*PROCESS_POLICY', (root / 'tools' / 'topolog' / name).read_text(encoding='utf-8'))
        for name in ('export-gns3-screenshot.ps1', 'open-gns3-project.ps1'):
            text = (root / 'tools' / name).read_text(encoding='utf-8-sig')
            launches = re.findall(r"-NoProfile[^\n]*-File", text)
            self.assertTrue(launches)
            for launch in launches:
                self.assertIn('RemoteSigned', launch)
        for name in ('TopoLog-Batch.cmd', 'TopoLog.cmd'):
            self.assertIn('-ExecutionPolicy RemoteSigned -File', (root / name).read_text(encoding='utf-8', errors='replace'))


if __name__ == '__main__':
    unittest.main()
