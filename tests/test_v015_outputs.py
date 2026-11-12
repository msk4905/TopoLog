"""Topology picture folder, report detail range, pause points and frozen resources."""
import json
from pathlib import Path
import struct
import sys
import tempfile
import threading
import unittest
import zipfile
import zlib
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import topolog.engine as _engine; _engine.SERVER_CHECK_INTERVAL = 0   # no real server to wait for
from topolog.resultdir import records as R  # noqa: E402
from topolog.resultdir import evidence_dir as E  # noqa: E402
from topolog import gallery, report_options, resources, winfocus
from topolog.engine import Runner
from topolog.pausing import PauseGate
from test_batch import FakeAPI, collected, project, P


def png_bytes():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00')) + chunk(b'IEND', b''))


def fake_capture(name, project_file, destination, cancel=None):
    destination = Path(destination)
    destination.mkdir(parents=True)
    (destination / 'gns3-topology.png').write_bytes(png_bytes())
    (destination / 'capture.json').write_text('{"capture_status":"captured"}', encoding='utf-8')
    return {'status': 'captured', 'kind': 'gns3_native_topology_screenshot', 'file': 'capture/gns3-topology.png', 'review': 'unreviewed'}


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.labs = self.root / 'labs'
        self.labs.mkdir()
        project(self.labs, 'routing', P)

    def tearDown(self):
        self.temp.cleanup()

    def run_batch(self, **kwargs):
        output = self.root / 'out'
        with patch('topolog.topology.capture_native', side_effect=fake_capture):
            report = Runner(FakeAPI(), output, collector=collected, settle_seconds=0, capture=True, **kwargs).run(self.labs)
        return output, report

    def test_picture_folder_separates_real_and_reconstructed_and_reports_link_it(self):
        output, report = self.run_batch()
        real = output / '토폴로지_사진' / 'GNS3_실제_사진' / 'routing.png'
        drawn = output / '토폴로지_사진' / '재구성_그림' / 'routing.svg'
        self.assertEqual(real.read_bytes(), (E(output, P) / 'capture' / 'gns3-topology.png').read_bytes())
        self.assertTrue(drawn.is_file())
        self.assertTrue((E(output, P) / 'capture' / 'gns3-topology.png').is_file())          # evidence kept in place
        self.assertTrue((output / '토폴로지_사진' / '안내.txt').is_file())
        html = (output / 'index.html').read_text(encoding='utf-8')
        from urllib.parse import quote
        self.assertIn(quote('토폴로지_사진/GNS3_실제_사진/routing.png', safe='/'), html)
        self.assertIn(quote('토폴로지_사진/재구성_그림/routing.svg', safe='/'), html)
        manifest = {f['path'] for f in json.loads((R(output) / 'manifest.json').read_text(encoding='utf-8'))['files']}
        self.assertIn('토폴로지_사진/GNS3_실제_사진/routing.png', manifest)
        with zipfile.ZipFile(str(output) + '.zip') as archive:
            self.assertIn('토폴로지_사진/재구성_그림/routing.svg', archive.namelist())
        record = next(R(output).glob('report(*).md')).read_text(encoding='utf-8')
        self.assertIn(quote('토폴로지_사진/GNS3_실제_사진/routing.png', safe='/'), record)
        with zipfile.ZipFile(next(output.glob('report(*).docx'))) as word:
            self.assertTrue(any(n.startswith('word/media/') for n in word.namelist()))

    def test_unavailable_picture_is_not_filled_with_an_empty_file(self):
        output = self.root / 'out'
        Runner(FakeAPI(), output, collector=collected, settle_seconds=0, capture=False).run(self.labs)
        self.assertFalse((output / '토폴로지_사진' / 'GNS3_실제_사진').exists())
        self.assertTrue((output / '토폴로지_사진' / '재구성_그림' / 'routing.svg').is_file())

    def test_duplicate_and_unsafe_names_get_distinct_safe_files(self):
        report = {'projects': [
            {'project_id': 'aaaaaaaa-1', 'name': 'CON', 'topology_images': {'reconstructed': {'status': 'generated', 'file': 'topology-reconstructed.svg'}}},
            {'project_id': 'bbbbbbbb-2', 'name': 'lab:1', 'topology_images': {'reconstructed': {'status': 'generated', 'file': 'x.svg'}}},
            {'project_id': 'cccccccc-3', 'name': 'lab:1', 'topology_images': {'native': {'status': 'unavailable'}}}]}
        plan = gallery.planned(report)
        self.assertEqual(plan[('aaaaaaaa-1', 'reconstructed')], '토폴로지_사진/재구성_그림/_CON.svg')
        self.assertEqual(plan[('bbbbbbbb-2', 'reconstructed')], '토폴로지_사진/재구성_그림/lab_1-bbbbbbbb.svg')
        self.assertNotIn(('cccccccc-3', 'native'), plan)
        from topolog.security import relative_name
        for relative in plan.values():
            relative_name(relative)

    def test_detail_range_all_details_every_project_and_regeneration_keeps_choice(self):
        output, report = self.run_batch(detail_scope='all')
        self.assertEqual(report['report_options']['detail_scope'], 'all')
        document = next(R(output).glob('report-document(*).md')).read_text(encoding='utf-8')
        self.assertIn('3. 프로젝트 한눈에 보기', document)
        from topolog.presentation import refresh_report
        refresh_report(output, 'representative')
        self.assertEqual(report_options.detail_scope(json.loads((R(output) / 'summary.json').read_text(encoding='utf-8')), R(output)), 'representative')
        document = next(R(output).glob('report-document(*).md')).read_text(encoding='utf-8')
        self.assertIn('대표 프로젝트 한눈에 보기', document)
        self.assertIn('중요도나 완성도를 평가한 선정이 아니며', document)
        self.assertTrue((output / '토폴로지_사진' / 'GNS3_실제_사진' / 'routing.png').is_file())
        manifest = {f['path'] for f in json.loads((R(output) / 'manifest.json').read_text(encoding='utf-8'))['files']}
        self.assertIn('수집_기록/report-options.json', manifest)
        refresh_report(output)                      # no choice: the saved one stays
        self.assertEqual(report_options.detail_scope({}, R(output)), 'representative')
        for banned in ('충족률', '미충족', '검증 항목 수'):
            self.assertNotIn(banned, document)

    def test_review_copy_keeps_range_and_rebuilds_pictures_from_cleaned_files(self):
        output, _ = self.run_batch(detail_scope='all')
        from topolog.sharing import create
        copy = self.root / 'copy'
        create(output, copy)
        summary = json.loads((R(copy) / 'summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['report_options']['detail_scope'], 'all')
        self.assertEqual((copy / '토폴로지_사진' / 'GNS3_실제_사진' / 'routing.png').read_bytes(),
                         (E(copy, P) / 'capture' / 'gns3-topology.png').read_bytes())

    def test_runner_pauses_only_at_safe_points_and_cancel_while_paused(self):
        gate, cancel = PauseGate(), threading.Event()
        seen = []
        gate.listeners.append(lambda paused, where: seen.append((paused, where)))
        gate.request()
        def release(paused, where):
            if paused:
                cancel.set()
        gate.listeners.append(release)
        report = Runner(FakeAPI(), self.root / 'out', collector=collected, settle_seconds=0, cancel=cancel, pause=gate).run(self.labs)
        self.assertTrue(report['cancelled'])
        self.assertEqual(seen[0], (True, '프로젝트 시작 전'))
        self.assertEqual(report['projects'][0]['status'], 'not_run')

    def test_pause_and_resume_finishes_normally(self):
        gate = PauseGate()
        gate.request()
        threading.Timer(.2, gate.resume).start()
        report = Runner(FakeAPI(), self.root / 'out', collector=collected, settle_seconds=0, pause=gate).run(self.labs)
        self.assertEqual(report['projects'][0]['status'], 'completed')


class PlatformTests(unittest.TestCase):
    def test_capture_target_survives_a_failed_return_but_respects_a_user_switch(self):
        before = {'handle': 1, 'pid': 10, 'exe': 'topolog.exe'}
        gns3 = {'handle': 2, 'pid': 20, 'exe': 'gns3.exe'}
        browser = {'handle': 3, 'pid': 30, 'exe': 'chrome.exe'}
        winfocus.remember_target(before)
        try:
            with patch.object(winfocus, 'snapshot', return_value=gns3):
                self.assertEqual(winfocus.capture_target(), before)
            with patch.object(winfocus, 'snapshot', return_value=browser):
                self.assertEqual(winfocus.capture_target(), browser)
            with patch.object(winfocus, 'snapshot', return_value=gns3):
                self.assertEqual(winfocus.capture_target(), browser)
                winfocus.remember_target(None)
                self.assertIsNone(winfocus.capture_target())
        finally:
            winfocus.remember_target(None)

    @unittest.skipUnless(sys.platform == 'win32', 'Win32 process handle check')
    def test_process_name_uses_the_full_native_handle(self):
        import os
        self.assertEqual(winfocus._process_name(os.getpid()), Path(sys.executable).name.lower())

    def test_frozen_layout_resolves_bundled_tools_and_assets(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(resources, 'FROZEN', True), \
             patch.object(resources.sys, '_MEIPASS', temp, create=True):
            self.assertEqual(resources.tool_directory(), Path(temp) / 'tools')
            self.assertEqual(resources.version_file(), Path(temp) / 'VERSION')
        self.assertTrue(resources.asset('example-gns3-native.png').is_file())
        self.assertTrue(resources.asset('example-reconstructed.png').is_file())
        self.assertTrue((resources.tool_directory() / 'capture-gns3.ps1').is_file())

    def test_focus_is_restored_only_from_windows_topolog_raised(self):
        before = {'handle': 1, 'pid': 10, 'exe': 'topolog.exe'}
        gns3 = {'handle': 2, 'pid': 20, 'exe': 'gns3.exe'}
        browser = {'handle': 3, 'pid': 30, 'exe': 'chrome.exe'}
        self.assertEqual(winfocus.decide(before, gns3), 'restore')
        self.assertEqual(winfocus.decide(before, browser), 'user_switched')
        self.assertEqual(winfocus.decide(before, before), 'unchanged')
        self.assertEqual(winfocus.decide(None, gns3), 'no_previous')
        self.assertEqual(winfocus.decide(before, gns3, previous_exists=False), 'previous_closed')
        self.assertEqual(winfocus.decide({'handle': 2, 'pid': 20, 'exe': 'gns3.exe'}, gns3), 'unchanged')
        self.assertEqual(winfocus.decide({'handle': 5, 'pid': 50, 'exe': 'vmware.exe'}, gns3), 'previous_was_gns3_or_vm')

    def test_capture_scripts_guard_target_window_and_retry_only_accessibility_errors(self):
        tools = resources.tool_directory()
        export = (tools / 'export-gns3-screenshot.ps1').read_text(encoding='utf-8-sig')
        self.assertIn('Assert-TargetWindow', export)
        self.assertIn("$attempt -le 8", export)
        self.assertIn('The screenshot filename changed before saving', export)
        opener = (tools / 'open-gns3-project.ps1').read_text(encoding='utf-8-sig')
        self.assertIn('window_title_at_timeout', opener)
        capture = (tools / 'capture-gns3.ps1').read_text(encoding='utf-8-sig')
        self.assertIn('RestoreForeground', capture)
        self.assertIn('open-project-attempt1.json', capture)
        source = (tools / 'TopoLogWindowCapture.cs').read_text(encoding='utf-8-sig')
        self.assertIn('user_switched', source)
        for forbidden in ('BlockInput', 'SetWindowsHookEx', 'keybd_event', 'SendInput'):
            self.assertNotIn(forbidden, export + opener + capture + source)


if __name__ == '__main__':
    unittest.main()
