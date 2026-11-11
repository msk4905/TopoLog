"""Captions on a copy of the GNS3 picture, title choices and regeneration without GNS3."""
import json
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from topolog.resultdir import records as R  # noqa: E402
from topolog.resultdir import evidence_dir as E  # noqa: E402
from topolog import annotate, gallery

PID = '00000000-0000-0000-0000-0000000000a1'
R1, R2 = '00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000b2'


def png(width, height, boxes=(), color=2, filters=(0,)):
    """Synthetic picture: white canvas with dark filled boxes (x, y, w, h)."""
    channels = 3 if color == 2 else 4
    pixel = lambda x, y: (40, 120, 130) if any(bx <= x < bx + bw and by <= y < by + bh for bx, by, bw, bh in boxes) else (255, 255, 255)
    raw, previous = bytearray(), bytes(width * channels)
    for y in range(height):
        line = bytearray()
        for x in range(width):
            line += bytes(pixel(x, y)) + (b'\xff' if channels == 4 else b'')
        kind = filters[y % len(filters)]
        out = bytearray(line)
        if kind == 1:
            for i in range(len(line) - 1, channels - 1, -1):
                out[i] = (line[i] - line[i - channels]) & 255
        elif kind == 2:
            for i in range(len(line)):
                out[i] = (line[i] - previous[i]) & 255
        raw += bytes([kind]) + out
        previous = bytes(line)

    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, color, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(bytes(raw))) + chunk(b'IEND', b''))


CONFIG_1 = ('Current configuration : 100 bytes\n!\nhostname R1\n!\ninterface Serial1/1\n ip address 192.168.12.1 255.255.255.0\n'
            ' ip ospf cost 10\n!\nrouter ospf 1\n network 192.168.12.0 0.0.0.255 area 0\n!\nend\nR1#')
CONFIG_2 = ('Current configuration : 100 bytes\n!\nhostname R2\n!\ninterface Serial1/2\n ip address 192.168.12.2 255.255.255.0\n'
            ' ip ospf cost 10\n!\nend\nR2#')
NODES = [{'node_id': R1, 'name': 'R1', 'x': -200, 'y': -50, 'width': 66, 'height': 45,
          'label': {'text': 'R1', 'x': 20, 'y': -25, 'style': 'font-family: TypeWriter;font-size: 10.0;font-weight: bold;fill: #000000;'}},
         {'node_id': R2, 'name': 'R2', 'x': 100, 'y': -50, 'width': 66, 'height': 45,
          'label': {'text': 'R2', 'x': 20, 'y': -25, 'style': ''}}]
LINKS = [{'link_id': 'l1', 'nodes': [{'node_id': R1, 'adapter_number': 1, 'port_number': 1, 'label': {'text': 's1/1', 'x': 50, 'y': -6}},
                                     {'node_id': R2, 'adapter_number': 1, 'port_number': 2, 'label': {'text': 's1/2', 'x': -20, 'y': -6}}]}]
PORTS = {R1: [{'adapter_number': 1, 'port_number': 1, 'name': 'Serial1/1'}],
         R2: [{'adapter_number': 1, 'port_number': 2, 'name': 'Serial1/2'}]}
OFFSET = (230, 90)


def node_record(nid, name, config):
    return {'node_id': nid, 'name': name, 'node_type': 'dynamips', 'platform': 'ios', 'collection': 'collected',
            'analysis': {'features': ['ospf']}, 'checks': [],
            'commands': [{'command': 'show running-config', 'status': 'collected', 'text': config, 'file': '01.txt'}]}


def fake_renderer(seen):
    def render(jobs):
        results = {}
        for job in jobs:
            seen.append(job)
            shutil.copyfile(job['source'], job['output'])
            results[job['output']] = {'status': 'drawn', 'mode': job['spec']['mode'], 'overlaps': 0}
        return results
    return render


class Fixture:
    def __init__(self, root, drawings=(), boxes=True):
        self.folder = root / 'result'
        base = self.folder / PID
        (base / 'capture').mkdir(parents=True)
        shapes = [(n['x'] + OFFSET[0], n['y'] + OFFSET[1], n['width'], n['height']) for n in NODES] if boxes else []
        (base / 'capture' / 'gns3-topology.png').write_bytes(png(560, 200, shapes))
        (base / 'topology.json').write_text(json.dumps({'nodes': NODES, 'links': LINKS, 'drawings': list(drawings)}), encoding='utf-8')
        self.project = {'project_id': PID, 'name': '20260827-1', 'status': 'completed', 'file': 'x.gns3',
                        'nodes': [node_record(R1, 'R1', CONFIG_1), node_record(R2, 'R2', CONFIG_2)],
                        'topology_links': LINKS, 'topology_ports': PORTS,
                        'topology_images': {'native': {'status': 'captured', 'file': 'capture/gns3-topology.png'},
                                            'reconstructed': {'status': 'unavailable'}}}
        self.report = {'schema': 'topolog-batch-v2', 'started_at': '2026-11-06T00:00:00+00:00',
                       'finished_at': '2026-11-06T00:01:00+00:00', 'projects': [self.project]}
        (self.folder / 'summary.json').write_text(json.dumps(self.report), encoding='utf-8')


class PictureTests(unittest.TestCase):
    def test_png_reader_handles_rgb_rgba_and_filters(self):
        for color in (2, 6):
            width, height, rows = annotate.read_png(png(9, 6, [(2, 1, 3, 3)], color=color, filters=(0, 1, 2)))
            self.assertEqual((width, height), (9, 6))
            self.assertEqual(rows[2][3 * 3:3 * 3 + 3], bytes((40, 120, 130)))
            self.assertEqual(rows[0][:3], b'\xff\xff\xff')
        width, height, _ = annotate.read_png((Path(__file__).resolve().parents[1] / 'tools/topolog/assets/example-gns3-native.png').read_bytes())
        self.assertGreater(width * height, 0)

    def test_calibration_finds_the_icon_offset_and_refuses_an_empty_picture(self):
        shapes = [(n['x'] + OFFSET[0], n['y'] + OFFSET[1], n['width'], n['height']) for n in NODES]
        ink = annotate.Ink(*annotate.read_png(png(560, 200, shapes)))
        result = annotate.calibrate(NODES, ink)
        self.assertEqual(result['offset'], list(OFFSET))
        self.assertTrue(result['certain'])
        self.assertFalse(annotate.calibrate(NODES, annotate.Ink(*annotate.read_png(png(560, 200))))['certain'])


class ValueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = Fixture(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_subnet_and_cost_come_from_the_collected_configs(self):
        topology = json.loads((self.fixture.folder / PID / 'topology.json').read_text(encoding='utf-8'))
        links = annotate.link_values(self.fixture.project, topology)
        self.assertEqual(links[0]['lines'], ['192.168.12.0/24', 'cost : 10'])
        self.assertEqual(annotate.interfaces('interface G0/0\n ip address 10.0.0.1 255.255.255.252\n ip ospf cost 5\n'),
                         {'G0/0': {'ip': __import__('ipaddress').IPv4Interface('10.0.0.1/30'), 'cost': 5}})

    def test_title_choices_candidate_project_name_custom_and_empty(self):
        topology = json.loads((self.fixture.folder / PID / 'topology.json').read_text(encoding='utf-8'))
        links = annotate.link_values(self.fixture.project, topology)
        candidate = annotate.candidate(self.fixture.project, topology, links)
        self.assertEqual(candidate, 'OSPF · 경로 비용(cost 10)')
        for banned in ('완성', '미구현', '충족', '우수', '성공'):
            self.assertNotIn(banned, candidate)
        self.assertEqual(annotate.resolve_title(self.fixture.project, topology, {}, links), ('candidate', candidate))
        self.assertEqual(annotate.resolve_title(self.fixture.project, topology, {PID: {'choice': 'project'}}, links),
                         ('project', '20260827-1'))
        saved = annotate.save_titles(self.fixture.folder, {PID: {'choice': 'project', 'text': ' OSPF 비용 조정 실습 '}})
        self.assertEqual(saved[PID]['text'], 'OSPF 비용 조정 실습')
        self.assertEqual(annotate.resolve_title(self.fixture.project, topology, annotate.load_titles(self.fixture.folder), links),
                         ('project', 'OSPF 비용 조정 실습'))
        self.assertEqual(annotate.resolve_title(self.fixture.project, topology, {PID: {'choice': 'candidate', 'text': ''}}, links),
                         ('candidate', ''))


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_copy_is_made_beside_the_untouched_gns3_picture(self):
        fixture, seen = Fixture(self.root), []
        original = (fixture.folder / PID / 'capture' / 'gns3-topology.png').read_bytes()
        record = annotate.build(fixture.folder, fixture.project, {}, fake_renderer(seen))
        self.assertEqual(record['status'], 'generated')
        self.assertEqual(record['mode'], 'on_image')
        self.assertEqual((fixture.folder / PID / 'capture' / 'gns3-topology.png').read_bytes(), original)
        self.assertTrue((fixture.folder / PID / annotate.FILE).is_file())
        spec = seen[0]['spec']
        self.assertEqual(spec['title'], 'OSPF · 경로 비용(cost 10)')
        self.assertEqual(spec['items'][0]['text'], '192.168.12.0/24\ncost : 10')
        self.assertEqual(spec['label_font'], {'family': 'TypeWriter', 'size': 10.0, 'bold': True, 'color': '#000000'})
        self.assertEqual(spec['title_font']['family'], '맑은 고딕')
        self.assertNotIn('mark', spec)
        mid_y = NODES[0]['y'] + OFFSET[1] + 22.5
        self.assertAlmostEqual(spec['items'][0]['anchor'][1], mid_y)
        self.assertNotIn('.1', spec['items'][0]['text'].split('\n')[-1])       # no host-part captions
        saved = json.loads((fixture.folder / PID / annotate.RECORD).read_text(encoding='utf-8'))
        self.assertEqual(saved['status'], 'generated')
        self.assertEqual(fixture.project['topology_images']['annotated']['file'], annotate.FILE)

    def test_only_the_chosen_caption_kinds_are_drawn(self):
        fixture, seen = Fixture(self.root), []
        titles = annotate.save_titles(fixture.folder, {PID: {'choice': 'candidate', 'text': '', 'show': {'subnet': False, 'cost': True}}})
        self.assertEqual(titles[PID]['show'], {'subnet': False, 'cost': True})
        record = annotate.build(fixture.folder, fixture.project, annotate.load_titles(fixture.folder), fake_renderer(seen))
        self.assertEqual(record['status'], 'generated')
        self.assertEqual(seen[0]['spec']['items'][0]['text'], 'cost : 10')
        self.assertEqual(record['show'], {'subnet': False, 'cost': True})
        seen.clear()
        off = {PID: {'choice': 'candidate', 'text': '', 'show': {'subnet': False, 'cost': False}}}
        record = annotate.build(fixture.folder, fixture.project, off, fake_renderer(seen))
        self.assertEqual(record['status'], 'skipped')                       # nothing chosen and no title: no copy
        self.assertEqual(seen, [])
        self.assertEqual(annotate.overview(fixture.folder)[0]['captions'], [['192.168.12.0/24', 'cost : 10']])

    def test_projects_with_user_text_are_skipped(self):
        drawing = {'svg': '<svg><text font-size="20">OSPF 실습</text></svg>', 'x': 0, 'y': 0}
        fixture, seen = Fixture(self.root, drawings=[drawing]), []
        record = annotate.build(fixture.folder, fixture.project, {}, fake_renderer(seen))
        self.assertEqual(record['status'], 'skipped')
        self.assertEqual(seen, [])
        self.assertFalse((fixture.folder / PID / annotate.FILE).exists())

    def test_uncertain_match_puts_captions_under_the_picture(self):
        fixture, seen = Fixture(self.root, boxes=False), []
        record = annotate.build(fixture.folder, fixture.project, {}, fake_renderer(seen))
        self.assertEqual(record['mode'], 'legend')
        self.assertEqual(seen[0]['spec']['items'], [])
        self.assertIn('R1 Serial1/1 – R2 Serial1/2  192.168.12.0/24  cost : 10', seen[0]['spec']['legend'])

    def test_failed_drawing_is_reported_and_no_copy_claimed(self):
        fixture = Fixture(self.root)
        record = annotate.build(fixture.folder, fixture.project, {}, lambda jobs: {j['output']: 'fixture font error' for j in jobs})
        self.assertEqual(record['status'], 'unavailable')
        self.assertIn('fixture font error', record['reason'])
        self.assertNotIn('file', record)

    def test_rebuild_keeps_titles_refreshes_reports_gallery_and_zip(self):
        fixture, seen = Fixture(self.root), []
        from topolog.engine import export
        export(fixture.folder, fixture.report)
        annotate.rebuild(fixture.folder, {PID: {'choice': 'project', 'text': '경로 비용 조정'}}, fake_renderer(seen))
        self.assertEqual(seen[-1]['spec']['title'], '경로 비용 조정')
        report = json.loads((fixture.folder / 'summary.json').read_text(encoding='utf-8'))
        self.assertEqual(report['projects'][0]['topology_images']['annotated']['title'], '경로 비용 조정')
        copy = fixture.folder / gallery.FOLDER / 'GNS3_주석_추가본' / '20260827-1.png'
        self.assertTrue(copy.is_file())
        html = (fixture.folder / 'index.html').read_text(encoding='utf-8')
        self.assertIn('자동 주석 사진', html)
        manifest = {f['path'] for f in json.loads((fixture.folder / 'manifest.json').read_text(encoding='utf-8'))['files']}
        self.assertIn(annotate.TITLES, manifest)
        with zipfile.ZipFile(str(fixture.folder) + '.zip') as archive:
            self.assertIn(gallery.FOLDER + '/GNS3_주석_추가본/20260827-1.png', archive.namelist())
        # The collection-time documents stay as GNS3 saved the pictures; the annotated Word report is separate.
        document = next(fixture.folder.glob('report-document(*).md')).read_text(encoding='utf-8')
        self.assertNotIn('경로 비용 조정', document)
        self.assertIn('GNS3 토폴로지 (GNS3 자체 Screenshot)', document)
        annotated_word = next(fixture.folder.glob('report(*)_주석본.docx'))
        with zipfile.ZipFile(annotated_word) as word:
            body = word.read('word/document.xml').decode('utf-8')
        self.assertIn('경로 비용 조정', body)
        self.assertIn('TopoLog가 서브넷·cost 주석 추가', body)
        self.assertIn('Word 보고서(주석본)', html)
        annotate.rebuild(fixture.folder, None, fake_renderer(seen))                # no new choice: saved one stays
        self.assertEqual(seen[-1]['spec']['title'], '경로 비용 조정')

    def test_batch_run_annotates_after_collection(self):
        from test_batch import FakeAPI, collected, project, P
        labs = self.root / 'labs'
        labs.mkdir()
        project(labs, 'routing', P)

        def capture(name, project_file, destination, cancel=None):
            Path(destination).mkdir(parents=True)
            (Path(destination) / 'gns3-topology.png').write_bytes(png(40, 30, [(5, 5, 10, 10)]))
            return {'status': 'captured', 'kind': 'gns3_native_topology_screenshot', 'file': 'capture/gns3-topology.png'}
        seen = []
        from topolog.engine import Runner
        with patch('topolog.topology.capture_native', side_effect=capture), patch('topolog.reach.run', return_value={}), \
             patch('topolog.annotate.render_windows', side_effect=fake_renderer(seen)):
            report = Runner(FakeAPI(), self.root / 'out', collector=collected, settle_seconds=0, capture=True).run(labs)
        annotated = report['projects'][0]['topology_images']['annotated']
        self.assertIn(annotated['status'], ('generated', 'skipped'))
        self.assertTrue((E(self.root / 'out', P) / annotate.RECORD).is_file())
        with patch('topolog.topology.capture_native', side_effect=capture), patch('topolog.reach.run', return_value={}):
            report = Runner(FakeAPI(), self.root / 'out2', collector=collected, settle_seconds=0, capture=True,
                            annotate=False).run(labs)
        self.assertNotIn('annotated', report['projects'][0]['topology_images'])

    def test_renderer_script_only_reads_the_source_and_uses_no_input_apis(self):
        from topolog.resources import tool_directory
        script = (tool_directory() / 'annotate-topology.ps1').read_bytes()
        self.assertTrue(script.startswith(b'\xef\xbb\xbf'))
        text = script.decode('utf-8-sig')
        self.assertIn('param([Parameter(Mandatory = $true)][string]$Jobs)', text)
        self.assertIn("Get-Family ([string]$style.family)", text)
        self.assertIn("'Tahoma'", text)
        for forbidden in ('BlockInput', 'SendInput', 'keybd_event', 'Remove-Item', 'ExecutionPolicy'):
            self.assertNotIn(forbidden, text)
        self.assertEqual(text.count('.Save('), 1)                                 # only the output copy is written
        # 15-03-12 run: every picture failed with "path format is not supported" in Image.FromFile.
        self.assertNotIn('Image]::FromFile', text)
        self.assertNotIn('$markText', text)                                    # no unrequested TopoLog mark
        # 15-29-01 run: with several pictures the job list became one item (ConvertFrom-Json array quirk).
        self.assertNotIn('@(Get-Content -LiteralPath $Jobs', text)
        self.assertIn('$list = @($parsed | ForEach-Object { $_ })', text)
        self.assertIn('Get-PlainPath', text)

    def test_renderer_gets_plain_windows_paths(self):
        self.assertEqual(annotate.plain_path('\\\\?\\C:\\결과\\수집_기록\\a.png'), 'C:\\결과\\수집_기록\\a.png')
        self.assertEqual(annotate.plain_path('\\\\?\\UNC\\server\\share\\a.png'), '\\\\server\\share\\a.png')
        fixture = Fixture(self.root)
        _, job = annotate.prepare(fixture.folder, fixture.project, {})
        for key in ('source', 'output'):
            self.assertFalse(job[key].startswith('\\\\?\\'))
            self.assertTrue(Path(job[key]).is_absolute())


class EditorTests(unittest.TestCase):
    def setUp(self):
        import tkinter
        try:
            self.window = tkinter.Tk()
        except tkinter.TclError as exc:
            self.skipTest('display unavailable: ' + str(exc))
        self.window.withdraw()
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = Fixture(Path(self.temp.name))

    def tearDown(self):
        self.window.destroy()
        self.temp.cleanup()

    def test_two_starting_titles_are_editable_and_saved_without_gns3(self):
        from topolog.title_editor import open_editor
        calls = []
        def rebuild(folder, titles, progress=None):
            calls.append((Path(folder), titles))
            return Path(folder) / 'index.html'
        editor = open_editor(self.window, self.fixture.folder, rebuild=rebuild)
        ui = editor._topolog
        self.assertEqual(ui['text'].get(), 'OSPF · 경로 비용(cost 10)')       # candidate first
        ui['choice'].set('project'); ui['switch']()
        self.assertEqual(ui['text'].get(), '20260827-1')
        ui['text'].set('20260827-1 OSPF cost 실습')
        ui['choice'].set('candidate'); ui['switch']()
        self.assertEqual(ui['text'].get(), 'OSPF · 경로 비용(cost 10)')       # each start keeps its own edit
        ui['choice'].set('project'); ui['switch']()
        self.assertEqual(ui['text'].get(), '20260827-1 OSPF cost 실습')
        ui['save'].invoke()
        for _ in range(50):
            self.window.update()
            if calls and not ui['state']['busy']:
                break
            __import__('time').sleep(.02)
        self.assertEqual(calls[0][1], {PID: {'choice': 'project', 'text': '20260827-1 OSPF cost 실습',
                                             'show': {'subnet': True, 'cost': True}}})
        self.assertIn('저장했습니다', ui['status'].get())

    def test_buttons_stay_visible_after_a_long_message_and_save_comes_first(self):
        from topolog.title_editor import open_editor
        editor = open_editor(self.window, self.fixture.folder, rebuild=lambda *a, **k: None)
        ui = editor._topolog
        editor.geometry('1020x660')
        ui['status'].set('저장했습니다. 주석 사진: ' + 'C:\\Users\\user\\Documents\\TopoLog\\exports\\' * 6)
        editor.update()
        save, folder, close = ui['buttons']
        for button in (save, folder, close):
            self.assertTrue(button.winfo_ismapped())
            self.assertGreater(button.winfo_width(), 20)
            self.assertLessEqual(button.winfo_rootx() + button.winfo_width(), editor.winfo_rootx() + editor.winfo_width())
            self.assertLessEqual(button.winfo_rooty() + button.winfo_height(), editor.winfo_rooty() + editor.winfo_height())
        self.assertLess(save.winfo_rootx(), folder.winfo_rootx())               # report button first
        self.assertLess(folder.winfo_rootx(), close.winfo_rootx())

    def test_caption_kinds_are_chosen_per_project_and_can_be_applied_to_all(self):
        from topolog.title_editor import open_editor
        calls = []
        editor = open_editor(self.window, self.fixture.folder,
                             rebuild=lambda folder, titles, progress=None: calls.append(titles) or Path(folder) / 'index.html')
        ui = editor._topolog
        self.assertEqual({k: v.get() for k, v in ui['kinds'].items()}, {'subnet': True, 'cost': True})
        ui['kinds']['subnet'].set(False)
        ui['same_for_all']()
        self.assertEqual(ui['titles']()[PID]['show'], {'subnet': False, 'cost': True})
        ui['save'].invoke()
        for _ in range(50):
            self.window.update()
            if calls and not ui['state']['busy']:
                break
            __import__('time').sleep(.02)
        self.assertEqual(calls[0][PID]['show'], {'subnet': False, 'cost': True})

    def test_project_with_user_text_cannot_be_edited(self):
        from topolog.title_editor import open_editor
        shutil.rmtree(self.fixture.folder)
        self.fixture = Fixture(Path(self.temp.name), drawings=[{'svg': '<svg><text font-size="20">직접 제목</text></svg>'}])
        editor = open_editor(self.window, self.fixture.folder, rebuild=lambda *a, **k: None)
        ui = editor._topolog
        self.assertEqual(ui['editable'], [])
        self.assertIn('직접 입력한 글자', ui['note'].get())
        self.assertEqual(ui['titles'](), {})


if __name__ == '__main__':
    unittest.main()
