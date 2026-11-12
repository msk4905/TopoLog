"""Run-time reductions: settle overlap, picture beside console checks, bulk node stop."""
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import topolog.engine as _engine; _engine.SERVER_CHECK_INTERVAL = 0   # no real server to wait for
from topolog.engine import Runner
from test_batch import FakeAPI, collected, project, P, N, M
from test_v015_outputs import png_bytes


class AllStoppedAPI(FakeAPI):
    def __init__(self):
        super().__init__()
        self.states = {N: 'stopped', M: 'stopped'}


class SpeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.labs = self.root / 'labs'
        self.labs.mkdir()
        project(self.labs, 'routing', P)

    def tearDown(self):
        self.temp.cleanup()

    def test_settle_time_overlaps_collection_and_still_precedes_traffic_checks(self):
        began, collected_at, pinged_at = time.monotonic(), [], []
        def collector(*args, **kwargs):
            collected_at.append(time.monotonic() - began)
            return collected(*args, **kwargs)
        def reach(*args, **kwargs):
            pinged_at.append(time.monotonic() - began)
            return {}
        with patch('topolog.reach.run', side_effect=reach):
            report = Runner(FakeAPI(), self.root / 'out', collector=collector, settle_seconds=1).run(self.labs)
        self.assertLess(min(collected_at), .8)             # consoles are not held back by the settle time
        self.assertGreaterEqual(pinged_at[0], 1.0)         # but pings still wait for it after node start
        self.assertIn('settle_wait', report['projects'][0]['timing']['seconds'])

    def test_no_settle_wait_when_every_node_was_already_running(self):
        api = FakeAPI()
        api.states = {N: 'started', M: 'started'}
        began = time.monotonic()
        with patch('topolog.reach.run', return_value={}):
            Runner(api, self.root / 'out', collector=collected, settle_seconds=5).run(self.labs)
        self.assertLess(time.monotonic() - began, 4)

    def test_gns3_picture_is_saved_while_console_checks_run(self):
        events = []
        def capture(name, project_file, destination, cancel=None):
            events.append(('capture-start', time.monotonic()))
            time.sleep(.4)
            Path(destination).mkdir(parents=True)
            (Path(destination) / 'gns3-topology.png').write_bytes(png_bytes())
            events.append(('capture-end', time.monotonic()))
            return {'status': 'captured', 'kind': 'gns3_native_topology_screenshot', 'file': 'capture/gns3-topology.png'}
        def reach(*args, **kwargs):
            events.append(('reach-start', time.monotonic()))
            time.sleep(.4)
            events.append(('reach-end', time.monotonic()))
            return {}
        with patch('topolog.topology.capture_native', side_effect=capture), patch('topolog.reach.run', side_effect=reach):
            report = Runner(FakeAPI(), self.root / 'out', collector=collected, settle_seconds=0, capture=True).run(self.labs)
        times = dict(events)
        self.assertLess(times['capture-start'], times['reach-end'])
        self.assertLess(times['reach-start'], times['capture-end'])
        self.assertEqual(report['projects'][0]['topology_images']['native']['status'], 'captured')

    def test_nodes_are_stopped_only_after_the_picture_finished(self):
        order = []
        class API(FakeAPI):
            def call(self, path, method='GET', body=None):
                if path.endswith('/stop'):
                    order.append('stop')
                return super().call(path, method, body)
        def capture(name, project_file, destination, cancel=None):
            time.sleep(.3)
            order.append('captured')
            return {'status': 'unavailable', 'reason': 'fixture'}
        with patch('topolog.topology.capture_native', side_effect=capture), patch('topolog.reach.run', return_value={}):
            Runner(API(), self.root / 'out', collector=collected, settle_seconds=0, capture=True).run(self.labs)
        self.assertEqual(order[0], 'captured')

    def test_capture_error_is_recorded_not_raised(self):
        def capture(*args, **kwargs):
            raise OSError('fixture powershell missing')
        with patch('topolog.topology.capture_native', side_effect=capture), patch('topolog.reach.run', return_value={}):
            report = Runner(FakeAPI(), self.root / 'out', collector=collected, settle_seconds=0, capture=True).run(self.labs)
        native = report['projects'][0]['topology_images']['native']
        self.assertEqual(native['status'], 'unavailable')
        self.assertIn('fixture powershell missing', native['reason'])
        self.assertEqual(report['projects'][0]['status'], 'completed')

    def test_password_prompt_waits_for_a_running_picture(self):
        order = []
        release = threading.Event()
        def capture(*args, **kwargs):
            release.wait(2)
            order.append('captured')
            return {'status': 'unavailable', 'reason': 'fixture'}
        def provider(name):
            order.append('asked')
            return None
        def final(api, entry, folder, factory, progress, cancel, enable, password, ask, known, workers=2):
            threading.Timer(.3, release.set).start()
            ask('ASA')
        with patch('topolog.topology.capture_native', side_effect=capture), patch('topolog.reach.run', return_value={}), \
             patch('topolog.final_routes.refresh', side_effect=final):
            Runner(FakeAPI(), self.root / 'out', collector=collected, settle_seconds=0, capture=True,
                   password_provider=provider).run(self.labs)
        self.assertEqual(order, ['captured', 'asked'])

    def test_bulk_stop_when_this_run_started_every_node(self):
        api = AllStoppedAPI()
        with patch('topolog.reach.run', return_value={}):
            Runner(api, self.root / 'out', collector=lambda a, n, f, *x, **k: (f.mkdir(parents=True), {'collection': 'collected', 'checks': [], 'commands': []})[1],
                   settle_seconds=0).run(self.labs)
        stops = [c[0] for c in api.calls if c[0].endswith('/stop')]
        self.assertEqual(stops, ['/v2/projects/' + P + '/nodes/stop'])

    def test_running_nodes_are_left_running_and_only_started_ones_stopped(self):
        api = FakeAPI()                                   # N already running, M started by this run
        with patch('topolog.reach.run', return_value={}):
            Runner(api, self.root / 'out', collector=collected, settle_seconds=0).run(self.labs)
        stops = [c[0] for c in api.calls if c[0].endswith('/stop')]
        self.assertEqual(stops, ['/v2/projects/' + P + '/nodes/' + M + '/stop'])
        self.assertEqual(api.states[N], 'started')

    def test_default_console_workers_is_three_and_capped(self):
        self.assertEqual(Runner(FakeAPI(), self.root / 'a').console_workers, 3)
        self.assertEqual(Runner(FakeAPI(), self.root / 'b', console_workers=9).console_workers, 4)

    def registered_api(self, path, name='20260929'):
        test = self
        class API(FakeAPI):
            def call(self, url, method='GET', body=None):
                if url == '/v2/projects' and method == 'GET':
                    return [{'project_id': P, 'name': name, 'path': path, 'status': 'opened'}]
                if url == '/v2/projects/' + P:
                    return {'project_id': P, 'name': name, 'path': path}
                return super().call(url, method, body)
        return API()

    def test_project_registered_from_another_folder_is_not_collected(self):
        api, seen = self.registered_api('C:/Users/x/GNS3/projects/20260929'), []
        with patch('topolog.topology.capture_native', side_effect=lambda *a, **k: seen.append(a)), \
             patch('topolog.reach.run', return_value={}):
            report = Runner(api, self.root / 'out', collector=collected, settle_seconds=0, capture=True).run(self.labs)
        project = report['projects'][0]
        self.assertNotEqual(project['status'], 'completed')
        self.assertIn('다른 폴더', project['reason'])
        self.assertEqual(project['gns3_registered_project']['path'], 'C:/Users/x/GNS3/projects/20260929')
        self.assertEqual(seen, [])
        self.assertFalse([c for c in api.calls if c[1] == 'POST'])          # nothing opened, started or stopped

    def test_same_folder_with_a_different_gns3_name_uses_that_name_for_the_window(self):
        api, seen = self.registered_api(str(self.labs)), []
        def capture(name, project_file, destination, cancel=None):
            seen.append(name)
            return {'status': 'unavailable', 'reason': 'fixture'}
        with patch('topolog.topology.capture_native', side_effect=capture), patch('topolog.reach.run', return_value={}):
            report = Runner(api, self.root / 'out', collector=collected, settle_seconds=0, capture=True).run(self.labs)
        self.assertEqual(seen, ['20260929'])
        self.assertEqual(report['projects'][0]['gns3_project_name'], '20260929')
        self.assertIn('GNS3에 등록된 프로젝트 이름', (self.root / 'out' / 'index.html').read_text(encoding='utf-8'))


AUTOMATIC = {'_', 'PSHOME', 'PSScriptRoot', 'PSVersionTable', 'true', 'false', 'null', 'args', 'Error', 'env', 'this',
             'LASTEXITCODE', 'ErrorActionPreference', 'input', 'matches', 'Matches', 'host'}


def _blocks(text):
    """(top-level text, [(function header, body)]) by brace matching."""
    import re
    top, functions, index = [], [], 0
    for match in re.finditer(r'(?m)^\s*function\s+[\w-]+[^{]*\{', text):
        if match.start() < index:
            continue
        top.append(text[index:match.start()])
        depth, end = 0, match.end() - 1
        while end < len(text):
            depth += {'{': 1, '}': -1}.get(text[end], 0)
            if depth == 0:
                break
            end += 1
        functions.append((match.group(0), text[match.end():end]))
        index = end + 1
    top.append(text[index:])
    return ''.join(top), functions


def unassigned_variables(text):
    """Variables used as $x.Member that nothing assigns in reach (catches a dropped '$x = ...')."""
    import re
    def assigned(body):
        return (set(re.findall(r'\$(?:script:)?([A-Za-z_]\w*)\s*(?:=|\+=)(?!=)', body)) |
                set(re.findall(r'foreach\s*\(\s*\$(\w+)\s+in', body)) | set(re.findall(r'\[ref\]\$(\w+)', body)) |
                set(re.findall(r'\]\s*\$(\w+)', body)))
    used = lambda body: set(re.findall(r'(?<![:\w])\$([A-Za-z_]\w*)\.(?=[A-Za-z])', body))
    top, functions = _blocks(text)
    script_level = assigned(top)
    missing = used(top) - script_level - AUTOMATIC
    for header, body in functions:
        missing |= used(body) - assigned(body) - set(re.findall(r'\$(\w+)', header)) - script_level - AUTOMATIC
    return sorted(missing)


class CaptureScriptTests(unittest.TestCase):
    def test_open_action_retries_skip_hidden_buttons_and_use_file_menu(self):
        from topolog.resources import tool_directory
        opener = (tool_directory() / 'open-gns3-project.ps1').read_text(encoding='utf-8-sig')
        export = (tool_directory() / 'export-gns3-screenshot.ps1').read_text(encoding='utf-8-sig')
        for text in ('Invoke-ThroughFileMenu', 'IsOffscreen', "$attempt -le 4", 'window_at_start', "'attempts'", 'Assert-IdleWindow'):
            self.assertIn(text, opener)
        self.assertIn('offscreen_screenshot_actions', export)
        for forbidden in ('BlockInput', 'SendInput', 'keybd_event', 'SetWindowsHookEx', 'mouse_event'):
            self.assertNotIn(forbidden, opener + export)
        from topolog.topology import explain
        self.assertIn('File 메뉴', explain('The GNS3 project file dialog did not open.'))

    def test_capture_scripts_never_use_a_variable_nothing_assigns(self):
        from topolog.resources import tool_directory
        for name in ('open-gns3-project.ps1', 'capture-gns3.ps1', 'export-gns3-screenshot.ps1', 'annotate-topology.ps1',
                     'dialog-filename.ps1', 'invoke-gns3-action.ps1'):
            text = (tool_directory() / name).read_text(encoding='utf-8-sig')
            self.assertEqual(unassigned_variables(text), [], name)
        broken = (tool_directory() / 'open-gns3-project.ps1').read_text(encoding='utf-8-sig').replace(
            '$clock=[Diagnostics.Stopwatch]::StartNew()\n    # Large', '$clock.Restart()\n    # Large')
        self.assertEqual(unassigned_variables(broken), ['clock'])          # the 10-07-16 failure is caught

    def test_failed_screenshot_invoke_is_retried_only_while_no_dialog_is_open(self):
        # 14:06 gate: Invoke right after widening the window failed with a generic COM error.
        from topolog.resources import tool_directory
        export = (tool_directory() / 'export-gns3-screenshot.ps1').read_text(encoding='utf-8-sig')
        loop = export[export.index("$record['invoke_attempts']=@()"):export.index("if(-not $ownedDialog) {throw $lastInvokeError}")]
        self.assertIn('$invokeAttempt -le 3 -and -not $ownedDialog', loop)
        self.assertIn('Find-ScreenshotDialogs', loop.split('$fresh=')[0])     # a dialog from the last try ends the loop first
        self.assertIn('인식할 수 없는 오류', loop)
        self.assertIn('Assert-TargetWindow', loop)
        self.assertIn('Start-Sleep -Milliseconds 400', export[export.index('Widen('):])


class DuplicateFolderTests(unittest.TestCase):
    def test_projects_also_in_the_gns3_default_folder_are_found_read_only(self):
        import json
        from topolog import settings
        with tempfile.TemporaryDirectory() as temp:
            root, default = Path(temp) / 'chosen', Path(temp) / 'GNS3' / 'projects'
            (root / 'a').mkdir(parents=True); (root / 'b').mkdir(parents=True); (default / 'a').mkdir(parents=True)
            (root / 'a' / 'a.gns3').write_text(json.dumps({'project_id': 'id-a'}), encoding='utf-8')
            (root / 'b' / 'b.gns3').write_text(json.dumps({'project_id': 'id-b'}), encoding='utf-8')
            copy = default / 'a' / 'a.gns3'
            copy.write_text(json.dumps({'project_id': 'id-a'}), encoding='utf-8')
            before = copy.stat().st_mtime_ns
            entries = [{'file': 'a/a.gns3', 'project_id': 'id-a', 'status': 'pending'},
                       {'file': 'b/b.gns3', 'project_id': 'id-b', 'status': 'pending'}]
            found = settings.duplicate_projects(root, entries, default)
            self.assertEqual([f['project_id'] for f in found], ['id-a'])
            self.assertEqual(found[0]['gns3_copy'], str(copy))
            self.assertEqual(settings.duplicate_projects(default, entries, default), [])   # default folder itself
            self.assertEqual(copy.stat().st_mtime_ns, before)


if __name__ == '__main__':
    unittest.main()
