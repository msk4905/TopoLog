"""Picture retry after the run, staged ping retries, readable evidence folders and report wording."""
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import topolog.engine as _engine; _engine.SERVER_CHECK_INTERVAL = 0   # no real server to wait for
from topolog import reach, reporting, verify  # noqa: E402
from topolog.engine import Runner  # noqa: E402
from topolog.resultdir import RECORDS, records, folder_name  # noqa: E402
from test_batch import FakeAPI, collected, project, P, N, M  # noqa: E402
from test_v015_outputs import fake_capture  # noqa: E402


def links(html):
    return [unquote(m) for m in re.findall(r'(?:href|src)="\./([^"#]+)"', html)]


class PictureRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.labs = self.root / 'labs'
        self.labs.mkdir()
        project(self.labs, 'routing', P)

    def tearDown(self):
        self.temp.cleanup()

    def run_with(self, outcomes, retake=True, **options):
        attempts = []
        def capture(name, project_file, destination, cancel=None):
            attempts.append(Path(destination))
            outcome = outcomes[len(attempts) - 1]
            if outcome == 'captured':
                return fake_capture(name, project_file, destination, cancel)
            Path(destination).mkdir(parents=True)
            (Path(destination) / 'capture.json').write_text('{"error":"fixture"}', encoding='utf-8')
            return {'status': 'unavailable', 'reason': 'fixture first failure', **outcome}
        api = FakeAPI()
        with patch('topolog.topology.capture_native', side_effect=capture), patch('topolog.reach.run', return_value={}):
            report = Runner(api, self.root / 'out', collector=collected, settle_seconds=0, capture=True, **options).run(self.labs)
            if retake:
                from topolog.engine import retake_pictures
                report = retake_pictures(api, self.root / 'out', self.labs)
        return report, attempts, api

    def test_failed_picture_is_saved_again_on_request_after_the_run(self):
        report, attempts, api = self.run_with([{}, 'captured'])
        self.assertEqual(len(attempts), 2)
        native = report['projects'][0]['topology_images']['native']
        self.assertEqual(native['status'], 'captured')
        self.assertEqual(native['retry']['when'], 'on_request_after_run')
        self.assertEqual(native['retry']['first_reason'], 'fixture first failure')
        folder = records(self.root / 'out') / 'routing'
        self.assertTrue((folder / 'capture-1' / 'capture.json').is_file())          # first attempt's record kept
        self.assertTrue((folder / 'capture' / 'gns3-topology.png').is_file())
        self.assertEqual(report['photo_retry'], {'when': 'on_request_after_run', 'projects': 1, 'captured': 1})
        with zipfile.ZipFile(str(self.root / 'out') + '.zip') as archive:              # ZIP rebuilt with the new picture
            self.assertIn(RECORDS + '/routing/capture/gns3-topology.png', archive.namelist())
        # The node this run had started is started for the picture and stopped again.
        starts = [c[0] for c in api.calls if c[0].endswith(M + '/start')]
        self.assertEqual(len(starts), 2)
        self.assertEqual(api.states[M], 'stopped')
        html = (self.root / 'out' / 'index.html').read_text(encoding='utf-8')
        self.assertIn('수집이 끝난 뒤 다시 저장했습니다', html)
        self.assertTrue(any(f.startswith('토폴로지_사진/GNS3_실제_사진/') for f in links(html)))

    def test_second_failure_stays_a_failure(self):
        report, attempts, _ = self.run_with([{}, {}])
        native = report['projects'][0]['topology_images']['native']
        self.assertEqual(native['status'], 'unavailable')
        self.assertIn('retry', native)
        self.assertIn('다시 시도했지만 저장하지 못했습니다', (self.root / 'out' / 'index.html').read_text(encoding='utf-8'))

    def test_no_retry_when_turned_off_or_pointless(self):
        report, attempts, _ = self.run_with([{}], retake=False)
        self.assertEqual(len(attempts), 1)                                       # nothing is retaken unless asked
        self.assertNotIn('photo_retry', report)
        shutil.rmtree(self.root / 'out')
        (self.root / 'out.zip').unlink()
        report, attempts, _ = self.run_with([{'retryable': False}])
        self.assertEqual(len(attempts), 1)


class StagedPingTests(unittest.TestCase):
    def ping(self, plans, nodes, answers, route=True):
        """answers: {node_id: [success percent per ping sent by that node, in order]}"""
        sent = {nid: iter(values) for nid, values in answers.items()}
        class API:
            host = '127.0.0.1'
            def call(self, path):
                return {'console': 5000, 'node_id': path.split('/')[-1]}
        class Console:
            prompt = 'R>'
            wire = b''
            def __init__(self, host, port, **kwargs):
                self.nid = None
            def connect(self):
                pass
            def close(self):
                pass
            def command(self, command):
                if command.startswith('show ip route'):
                    if not route:
                        return '% Network not in table'
                    return 'Routing entry for 10.0.0.0/24\n  Known via "ospf 1", distance 110'
                nid = next(n for n, rules in plans.items() if any(verify.ping_command(r, 'ios') == command for r in rules))
                return 'Success rate is {} percent'.format(next(sent[nid]))
        entry = {'project_id': 'p', 'nodes': nodes}
        waits = []
        with tempfile.TemporaryDirectory() as temp:
            for n in nodes:
                (Path(temp) / n['folder']).mkdir()
            with patch('topolog.features.plan_pings', return_value=plans), patch('topolog.reach.time.sleep', side_effect=waits.append):
                reach.run(API(), entry, Path(temp), Console, workers=1)
            files = sorted(p.relative_to(temp).as_posix() for p in Path(temp).rglob('*.txt'))
        return entry, waits, files

    def node(self, nid, name):
        return {'node_id': nid, 'name': name, 'folder': name, 'platform': 'ios', 'collection': 'collected', 'commands': []}

    def test_late_answer_is_retried_with_growing_waits_and_reported_as_late(self):
        a = self.node('a', 'R1')
        rule = {'kind': 'ping', 'target': '10.0.0.2'}
        entry, waits, files = self.ping({'a': [rule]}, [a], {'a': [0, 0, 100]})
        self.assertEqual(waits, [5, 10])
        self.assertEqual([c['attempt'] for c in a['commands']], [1, 2, 3])
        result = verify.evaluate(rule, a['commands'], a)
        self.assertEqual(result['status'], 'passed')
        self.assertIn('3번째 시도에서 응답', result['reason'])
        self.assertIn('앞선 2번은 응답 없음', reporting.ping_observation(result))
        self.assertEqual(files, ['R1/01.txt', 'R1/02.txt', 'R1/03.txt'])          # every attempt kept

    def test_unanswered_ping_gets_route_lookup_reverse_result_and_a_last_try(self):
        a, b = self.node('a', 'R1'), self.node('b', 'R2')
        forward = {'kind': 'ping', 'target': '10.0.0.2', 'origin_node': 'b'}
        back = {'kind': 'ping', 'target': '10.0.0.1', 'origin_node': 'a'}
        entry, waits, files = self.ping({'a': [forward], 'b': [back]}, [a, b], {'a': [0] * 7, 'b': [100]},)
        result = verify.evaluate(forward, a['commands'], a)
        self.assertEqual(result['status'], 'failed')                             # still a failure: never turned into a pass
        self.assertIn('반대 방향 ping(R2→10.0.0.1): 응답 있음', result['reason'])
        self.assertIn('10.0.0.0/24 · ospf 1', result['reason'])
        labels = {e['label']: e for e in result['additional_evidence']}
        self.assertEqual(labels['반대 방향 ping 원문']['node_id'], 'b')
        self.assertEqual(a['commands'][-1]['attempt_reason'], 'after_all_devices')
        self.assertIn('show ip route 10.0.0.2', [c['command'] for c in a['commands']])
        self.assertIn('R1', entry['reachability']['followed_up'])

    def test_budget_limits_the_rounds(self):
        a = self.node('a', 'R1')
        rule = {'kind': 'ping', 'target': '10.0.0.2'}
        entry, waits, _ = self.ping({'a': [rule]}, [a], {'a': [0] * 10})
        self.assertLessEqual(sum(waits), reach.RETRY_BUDGET)
        self.assertEqual(waits, list(reach.RETRY_DELAYS[:len(waits)]))


    def test_policy_path_ping_gets_the_whole_schedule_so_an_allowed_ping_is_seen_answering(self):
        a = self.node('a', 'R1')
        rule = {'kind': 'ping', 'target': '10.0.0.2', 'policy_context': {'policy': True}}
        entry, waits, _ = self.ping({'a': [rule]}, [a], {'a': [0, 0, 0, 0, 100]})
        self.assertEqual(waits, [5, 10, 20, 30])
        self.assertEqual(verify.evaluate(rule, a['commands'], a)['status'], 'passed')

    def test_report_text_has_no_repeated_label_and_reply_trace_without_session_is_not_called_a_block(self):
        from topolog.asa_diagnostics import describe
        check = {'status': 'inconclusive', 'observed': {'success_percent': 0}, 'reason': '응답 없음. 정책 확인 필요'}
        self.assertEqual(reporting.ping_observation(check), '응답 없음. 정책 확인 필요')
        text = describe({'status': 'completed', 'request': {'action': 'drop', 'drop_reason': '(acl-drop) Flow is denied by configured rule'},
                         'reply': {'action': 'drop', 'drop_reason': '(inspect-icmp-seq-num-not-matched) ICMP Inspect seq num not matched'}})
        self.assertIn('원인: ASA 접근 규칙(ACL)이 요청을 차단함', text)
        self.assertNotIn('응답 모의 검사', text)                                # a reply without its request says nothing
        other = describe({'status': 'completed', 'request': {'action': 'allow'},
                          'reply': {'action': 'drop', 'drop_reason': '(inspect-icmp-seq-num-not-matched) x'}})
        self.assertIn('ASA 요청 모의 검사: 통과', other)
        self.assertNotIn('응답 모의 검사', other)


class ReadableFolderTests(unittest.TestCase):
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

    def test_project_and_device_names_name_the_folders(self):
        project = self.report['projects'][0]
        self.assertEqual(project['folder'], 'routing')
        self.assertEqual(sorted(n['folder'] for n in project['nodes']), ['_router_', '_router__2'])
        data = records(self.output)
        self.assertTrue((data / 'routing' / 'topology.json').is_file())
        self.assertFalse((data / P).exists())
        for node in project['nodes']:
            self.assertTrue((data / 'routing' / node['folder'] / 'collection.json').is_file())

    def test_results_with_id_folders_still_open(self):
        old = self.root / 'old'
        shutil.copytree(self.output, old)
        data = old / RECORDS
        summary = json.loads((data / 'summary.json').read_text(encoding='utf-8'))
        project = summary['projects'][0]
        (data / project.pop('folder')).rename(data / P)
        for node in project['nodes']:
            (data / P / node.pop('folder')).rename(data / P / node['node_id'])
        (data / 'summary.json').write_text(json.dumps(summary), encoding='utf-8')
        from topolog.presentation import refresh_report
        refresh_report(old)
        html = (old / 'index.html').read_text(encoding='utf-8')
        found = links(html)
        self.assertTrue(any(f.startswith(RECORDS + '/' + P + '/') for f in found))
        for relative in found:
            target = old / relative
            self.assertTrue(target.is_dir() if relative.endswith('/') else target.is_file(), relative)

    def test_folder_names_are_safe_and_unique(self):
        used = set()
        names = [folder_name(x, used, 'node') for x in ['R1', 'r1', 'NUL', 'a/b:c', '', 'summary.json', 'x' * 90]]
        self.assertEqual(names[:5], ['R1', 'r1_2', '_NUL', 'a_b_c', 'node'])
        self.assertEqual(names[5], 'summary.json_2')
        self.assertEqual(len(names[6]), 60)


class ReportWordingTests(unittest.TestCase):
    def test_checks_read_as_sentences_not_json(self):
        rule = {'kind': 'interface_up', 'command': 'show ip interface brief', 'interface': 'Loopback0'}
        self.assertEqual(reporting.expectation(rule), 'Loopback0 인터페이스 상태 up/up 확인 (show ip interface brief)')
        for rule in [{'kind': 'bgp_peer', 'command': 'show ip bgp summary', 'address': '10.0.0.2'},
                     {'kind': 'redistribution', 'command': 'show ip protocols', 'into': 'ospf', 'source': 'rip'},
                     {'kind': 'isakmp_sa', 'command': 'show crypto isakmp sa', 'peer': '1.1.1.1'},
                     {'kind': 'something_new', 'command': 'show x', 'value': 3},
                     {'kind': 'vpcs_ip', 'command': 'show ip', 'address': '10.0.0.5', 'gateway': '10.0.0.1'},
                     {'kind': 'route_covered', 'command': 'show ip route', 'address': '10.9.9.1', 'origin': 'R9 Loopback0', 'origin_node': 'x'}]:
            text = reporting.expectation(rule)
            self.assertNotIn('{', text)
            self.assertNotIn('"kind"', text)
            self.assertIn(rule['command'], text)

    def test_running_config_link_sits_beside_saved_configuration(self):
        node = {'node_id': 'n', 'folder': 'R1', 'name': 'R1', 'collection': 'collected', 'platform': 'ios', 'checks': [],
                'startup_configuration': {'status': 'collected', 'file': 'startup.cfg', 'source': 'GNS3 API'},
                'commands': [{'command': 'show running-config', 'file': '03.txt', 'status': 'collected'}]}
        report = {'records_dir': RECORDS, 'projects': [{'project_id': P, 'folder': 'lab', 'name': 'lab', 'status': 'completed', 'nodes': [node]}]}
        html = reporting.render(report, [])
        line = re.search(r'저장 설정:.*?</p>', html).group(0)
        self.assertIn('startup.cfg 보기', line)
        self.assertIn('running-config 보기', line)
        self.assertIn(RECORDS + '/lab/R1/03.txt', unquote(line))



class TemporaryCopyTests(unittest.TestCase):
    """Projects sharing a GNS3 ID are collected from a temporary copy; the user's files are only read."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.labs = self.root / 'labs'
        self.labs.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def api(self, registered):
        from test_batch import FakeAPI as Base
        class API(Base):
            def __init__(self):
                super().__init__()
                self.loaded, self.deleted = {}, []
            def call(self, path, method='GET', body=None):
                if path == '/v2/projects' and method == 'GET':
                    self.calls.append((path, method, body))
                    return registered
                if path.endswith('/open') and method == 'POST':
                    self.calls.append((path, method, body))
                    return None
                if path == '/v2/projects/load':
                    self.calls.append((path, method, body))
                    pid = json.loads(Path(body['path']).read_text(encoding='utf-8'))['project_id']
                    self.loaded[pid] = str(Path(body['path']).parent)
                    return {'project_id': pid}
                for pid, folder in self.loaded.items():
                    prefix = '/v2/projects/' + pid
                    if path == prefix and method == 'GET':
                        self.calls.append((path, method, body))
                        return {'project_id': pid, 'name': 'routing', 'path': folder}
                    if path == prefix and method == 'DELETE':
                        self.calls.append((path, method, body))
                        self.deleted.append(pid)
                        return None
                    if path.startswith(prefix + '/'):
                        self.calls.append((path, method, body))
                        return super().call('/v2/projects/' + P + path[len(prefix):], method, body)
                return super().call(path, method, body)
        return API()

    def originals(self):
        return {p: p.read_bytes() for p in self.labs.rglob('*') if p.is_file()}

    def test_project_registered_elsewhere_is_collected_from_a_copy(self):
        folder = self.labs / 'routing'
        folder.mkdir()
        project(folder, 'routing', P)
        (folder / 'project-files' / 'dynamips').mkdir(parents=True)
        (folder / 'project-files' / 'dynamips' / 'i1_startup-config.cfg').write_text('hostname R1\n')
        before = self.originals()
        api = self.api([{'project_id': P, 'name': 'routing', 'path': 'C:/Users/x/GNS3/projects/routing', 'status': 'closed'}])
        report = Runner(api, self.root / 'out', collector=collected, settle_seconds=0).run(self.labs)
        entry = report['projects'][0]
        self.assertEqual(entry['status'], 'completed')
        copy = entry['gns3_copy']
        self.assertNotEqual(copy['project_id'], P)
        self.assertEqual(copy['reason'], 'registered_elsewhere')
        self.assertTrue(copy['removed'])
        self.assertEqual(api.deleted, [copy['project_id']])                       # only the copy is deleted in GNS3
        self.assertFalse((self.root / 'out_임시사본').exists())
        self.assertEqual(self.originals(), before)                                # user's files untouched
        loads = [c for c in api.calls if c[0] == '/v2/projects/load']
        self.assertTrue(loads[0][2]['path'].startswith(str(self.root / 'out_임시사본')))
        self.assertTrue(any(c[0].startswith('/v2/projects/' + copy['project_id'] + '/nodes') for c in api.calls))
        html = (self.root / 'out' / 'index.html').read_text(encoding='utf-8')
        self.assertIn('임시 사본(새 ID)으로 열어 수집했습니다', html)

    def test_second_copy_in_the_same_folder_is_collected_too(self):
        for name in ('lab-a', 'lab-b'):
            (self.labs / name).mkdir()
            project(self.labs / name, name, P)
        api = self.api([{'project_id': P, 'name': 'lab-a', 'path': str(self.labs / 'lab-a'), 'status': 'closed'}])
        report = Runner(api, self.root / 'out', collector=collected, settle_seconds=0).run(self.labs)
        first, second = report['projects']
        self.assertEqual([first['status'], second['status']], ['completed', 'completed'])
        self.assertNotIn('gns3_copy', first)
        self.assertEqual(second['gns3_copy']['reason'], 'same_folder')
        self.assertEqual(second['source_project_id'], P)
        self.assertNotEqual(second['project_id'], P)

    def test_other_copy_open_in_gns3_is_not_collected(self):
        project(self.labs, 'routing', P)
        api = self.api([{'project_id': P, 'name': 'routing', 'path': 'C:/elsewhere/routing', 'status': 'opened'}])
        report = Runner(api, self.root / 'out', collector=collected, settle_seconds=0).run(self.labs)
        self.assertEqual(report['projects'][0]['status'], 'error')
        self.assertIn('열려 있어', report['projects'][0]['reason'])
        self.assertFalse([c for c in api.calls if c[1] == 'POST'])

    def test_not_enough_space_is_reported_and_nothing_is_copied(self):
        project(self.labs, 'routing', P)
        api = self.api([{'project_id': P, 'name': 'routing', 'path': 'C:/elsewhere/routing', 'status': 'closed'}])
        with patch('topolog.projectcopy.shutil.disk_usage', return_value=type('U', (), {'free': 10})()):
            report = Runner(api, self.root / 'out', collector=collected, settle_seconds=0).run(self.labs)
        self.assertIn('공간이 부족', report['projects'][0]['reason'])
        self.assertFalse((self.root / 'out_임시사본').exists())

    def test_picture_of_a_copied_project_is_retaken_from_a_new_copy(self):
        folder = self.labs / 'routing'
        folder.mkdir()
        project(folder, 'routing', P)
        api = self.api([{'project_id': P, 'name': 'routing', 'path': 'C:/elsewhere/routing', 'status': 'closed'}])
        outcomes = iter([{'status': 'unavailable', 'reason': 'fixture'}, 'captured'])
        def capture(name, project_file, destination, cancel=None):
            outcome = next(outcomes)
            self.assertTrue(str(project_file).startswith(str(self.root / 'out_임시사본')))   # always a copy, never the original
            if outcome == 'captured':
                return fake_capture(name, project_file, destination, cancel)
            Path(destination).mkdir(parents=True)
            return outcome
        from topolog.engine import retake_pictures
        with patch('topolog.topology.capture_native', side_effect=capture), patch('topolog.reach.run', return_value={}):
            first = Runner(api, self.root / 'out', collector=collected, settle_seconds=0, capture=True).run(self.labs)
            again = retake_pictures(api, self.root / 'out', self.labs)
        self.assertEqual(again['projects'][0]['topology_images']['native']['status'], 'captured')
        self.assertNotEqual(first['projects'][0]['gns3_copy']['project_id'], again['projects'][0]['gns3_copy']['project_id'])
        self.assertEqual(len(api.deleted), 2)                                  # both temporary copies removed from GNS3
        self.assertFalse((self.root / 'out_임시사본').exists())


if __name__ == '__main__':
    unittest.main()
