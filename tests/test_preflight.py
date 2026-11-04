"""GNS3 readiness before a batch; no real process is started in these tests."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from topolog import preflight, settings, progress


class API:
    def __init__(self, vm_enabled=True, vm_connected=True, up=True):
        self.vm_enabled, self.vm_connected, self.up, self.calls = vm_enabled, vm_connected, up, []

    def call(self, path, method='GET', body=None):
        self.calls.append((path, method))
        if method != 'GET':
            raise AssertionError('Preflight must be read-only')
        if not self.up:
            raise OSError('connection refused')
        if path == '/v2/version':
            return {'version': '2.2.61', 'local': True}
        if path == '/v2/gns3vm':
            return {'enable': self.vm_enabled, 'engine': 'vmware'}
        if path == '/v2/computes':
            return [{'compute_id': 'local', 'connected': True}, {'compute_id': 'vm', 'connected': self.vm_connected}]
        raise AssertionError(path)


class PreflightTests(unittest.TestCase):
    def test_not_running_is_launched_and_waits_for_server_and_vm(self):
        api = API(vm_connected=False)
        start = Mock(side_effect=lambda exe: setattr(api, 'vm_connected', True))
        record = preflight.prepare(lambda: api, True, is_running=lambda: False,
                                   find_executable=lambda: Path('C:/GNS3/gns3.exe'), start=start, gui_timeout=2, vm_timeout=2)
        start.assert_called_once()
        self.assertTrue(record['launched'])
        self.assertEqual(record['gns3_vm'], {'enabled': True, 'connected': True})

    def test_not_running_without_launch_only_reports(self):
        start = Mock()
        record = preflight.prepare(lambda: API(), False, is_running=lambda: False, start=start)
        start.assert_not_called()
        self.assertFalse(record['launched'])
        self.assertNotIn('controller', record)

    def test_running_gui_is_never_started_again(self):
        start = Mock()
        record = preflight.prepare(lambda: API(), True, is_running=lambda: True, start=start)
        start.assert_not_called()
        self.assertEqual(record['controller'], 'ready')

    def test_disabled_vm_is_not_waited_for(self):
        api = API(vm_enabled=False, vm_connected=False)
        record = preflight.prepare(lambda: api, True, is_running=lambda: True, vm_timeout=60)
        self.assertEqual(record['gns3_vm'], {'enabled': False, 'connected': False})
        self.assertLess(sum(1 for p, _ in api.calls if p == '/v2/computes'), 3)

    def test_vm_not_started_stops_quickly_with_restart_instruction(self):
        api = API(vm_connected=False)
        with self.assertRaises(ValueError) as caught:
            preflight.prepare(lambda: api, True, is_running=lambda: True, vm_timeout=1, needs_vm=True)
        self.assertIn('VMware에서 GNS3 VM을 켠 뒤 다시 실행하세요', str(caught.exception))
        self.assertFalse([c for c in api.calls if c[1] != 'GET'])

    def test_vm_not_needed_continues_with_note(self):
        messages = []
        record = preflight.prepare(lambda: API(vm_connected=False), True, progress=messages.append,
                                   is_running=lambda: True, vm_timeout=1, needs_vm=False)
        self.assertFalse(record['gns3_vm']['connected'])
        self.assertIn('계속 진행', messages[-1])

    def test_default_vm_wait_is_short(self):
        import inspect
        self.assertIn("90 if record['launched'] else 60", inspect.getsource(preflight.prepare))

    def test_projects_need_vm_reads_compute_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'a.gns3').write_text(json.dumps({'topology': {'nodes': [{'compute_id': 'local'}]}}), encoding='utf-8')
            (root / 'b.gns3').write_text(json.dumps({'topology': {'nodes': [{'compute_id': 'vm'}]}}), encoding='utf-8')
            self.assertFalse(preflight.projects_need_vm(root, [{'file': 'a.gns3', 'status': 'pending'}]))
            self.assertTrue(preflight.projects_need_vm(root, [{'file': 'a.gns3'}, {'file': 'b.gns3'}]))
            self.assertFalse(preflight.projects_need_vm(root, [{'file': 'b.gns3', 'status': 'excluded'}]))

    def test_missing_executable_is_a_clear_error(self):
        with self.assertRaises(ValueError):
            preflight.prepare(lambda: API(), True, is_running=lambda: False, find_executable=lambda: None)

    def test_launched_but_server_never_ready_times_out(self):
        with self.assertRaises(TimeoutError):
            preflight.prepare(lambda: API(up=False), True, is_running=lambda: False,
                              find_executable=lambda: Path('gns3.exe'), start=Mock(), gui_timeout=1)

    def test_unknown_platform_keeps_previous_behaviour(self):
        start = Mock()
        record = preflight.prepare(lambda: API(), True, is_running=lambda: None, start=start)
        start.assert_not_called()
        self.assertIsNone(record['gui_running'])

    def test_process_check_reads_non_utf8_console_output(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        none = '정보: 지정된 조건에 맞는 작업이 실행되고 있지 않습니다.\r\n'.encode('cp949')
        found = b'"gns3.exe","4120","Console","1","210,512 K"\r\n'
        with patch('topolog.preflight.os.name', 'nt'):
            for output, expected in ((none, False), (found, True)):
                with patch('topolog.preflight.subprocess.run', return_value=SimpleNamespace(stdout=output, returncode=0)) as run:
                    self.assertIs(preflight.gui_running(), expected)
                    self.assertNotIn('text', run.call_args.kwargs)
            with patch('topolog.preflight.subprocess.run', return_value=SimpleNamespace(stdout=None, returncode=1)):
                self.assertIsNone(preflight.gui_running())

    def test_gui_executable_is_found_next_to_server(self):
        with tempfile.TemporaryDirectory() as temp:
            server = Path(temp) / 'gns3server.exe'; server.write_bytes(b'')
            (Path(temp) / 'gns3.exe').write_bytes(b'')
            self.assertEqual(preflight.gui_executable([server]), (Path(temp) / 'gns3.exe').resolve())

    def test_progress_prefix_is_shown_plainly(self):
        self.assertEqual(progress.message('Preflight: GNS3를 실행합니다.'), 'GNS3를 실행합니다.')

    def test_auto_start_choice_is_remembered(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); projects = root / 'p'; projects.mkdir(); file = root / 's.json'
            self.assertTrue(settings.load(file)['auto_start_gns3'])
            settings.save(projects, root / 'out', file, auto_start_gns3=False)
            self.assertFalse(settings.load(file)['auto_start_gns3'])
            settings.save(projects, root / 'out', file)
            self.assertFalse(json.loads(file.read_text(encoding='utf-8'))['auto_start_gns3'])


if __name__ == '__main__':
    unittest.main()
