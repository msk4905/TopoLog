"""Synthetic fixtures only; no user projects or GNS3 state are modified."""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import socket
import unittest
from unittest.mock import patch
from unittest.mock import Mock
from urllib.error import HTTPError, URLError
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import topolog.engine as _engine; _engine.SERVER_CHECK_INTERVAL = 0   # no real server to wait for
from topolog.resultdir import records as R  # noqa: E402
from topolog import profiles, settings
from topolog.engine import Runner, collect_node, discover, draft, error_detail, load_rules, verdict, wait_computes
from topolog.transport import Console, TelnetCodec

P = '00000000-0000-0000-0000-000000000001'
Q = '00000000-0000-0000-0000-000000000002'
N = '00000000-0000-0000-0000-000000000003'
M = '00000000-0000-0000-0000-000000000004'


def project(folder, name, pid):
    (folder / (name + '.gns3')).write_text(json.dumps({'project_id': pid, 'name': name,
        'topology': {'nodes': [{'node_id': N, 'name': 'R1'}]}}), encoding='utf-8')


class FakeAPI:
    host = '127.0.0.1'
    def __init__(self):
        self.calls = []
        self.states = {N: 'started', M: 'stopped'}
        self.fail_stop = False
        self.fail_start = False

    def raw(self, path):
        if not path.endswith('/files/configs/i1_startup-config.cfg'):
            raise ValueError('unexpected file')
        return b'hostname R1\n'

    def node(self, nid):
        return {'node_id': nid, 'project_id': P, 'name': '<router>', 'node_type': 'dynamips',
                'status': self.states[nid], 'console_type': 'telnet', 'console': 5002,
                'properties': {'dynamips_id': 1}}

    def call(self, path, method='GET', body=None):
        self.calls.append((path, method, body))
        if path == '/v2/computes':
            return [{'compute_id': 'local', 'connected': True}, {'compute_id': 'vm', 'connected': True}]
        if path == '/v2/projects':
            return [{'project_id': P, 'status': 'opened'}]
        if path == '/v2/projects/load':
            return {'project_id': Q}
        if path.endswith('/close'):
            return None
        if path.startswith('/v2/projects/' + Q):
            raise ValueError('Synthetic project failure')
        if path.endswith('/start'):
            if self.fail_start:
                raise ValueError('Synthetic start failure')
            self.states[path.split('/')[-2]] = 'started'
            return None
        if path.endswith('/stop'):
            if self.fail_stop:
                raise ValueError('Synthetic stop failure')
            self.states[path.split('/')[-2]] = 'stopped'
            return None
        if path.endswith('/links') or path.endswith('/drawings'):
            return []
        if path.endswith('/nodes'):
            return [self.node(N), self.node(M)]
        if '/nodes/' in path:
            return self.node(path.split('/')[-1])
        raise AssertionError(path)


def collected(api, node, folder, spec, selected, **kwargs):
    if api.states[M] != 'started':
        raise AssertionError('All nodes must start before collection')
    folder.mkdir(parents=True)
    return {'collection': 'collected', 'checks': [{'status': 'passed'}], 'commands': []}


class FakeConsole:
    responses = {
        'show version': 'Cisco IOS Software, test fixture\nR1#',
        'show running-config': 'router ospf 1\n redistribute rip subnets\nrouter rip\nR1#',
        'show ip ospf neighbor': '2.2.2.2 1 FULL/DR 00:00:30 10.1.0.2\nR1#',
    }
    def __init__(self, *args, **kwargs):
        self.wire = bytearray(b'Synthetic console bytes')
    def connect(self):
        pass
    def command(self, command):
        return self.responses.get(command, 'Synthetic show output\nR1#')
    def close(self):
        pass


class SettingsTests(unittest.TestCase):
    def test_defaults_use_gns_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            config = base / 'GNS3/2.2'
            config.mkdir(parents=True)
            (config / 'gns3_gui.ini').write_text(json.dumps({'General': {'projects_path': str(base / 'labs')}}))
            self.assertEqual(settings.project_default(base, base), base / 'labs')
            (config / 'gns3_gui.ini').unlink()
            self.assertEqual(settings.project_default(base, base), base / 'GNS3/projects')

    def test_remember_unicode_paths_and_reject_nested_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            projects = root / '프로젝트 폴더'
            projects.mkdir()
            file = root / 'settings.json'
            settings.save(projects, root / '결과', file)
            self.assertEqual(Path(settings.load(file)['project_directory']), projects.resolve())
            for bad in (projects, projects / 'output'):
                with self.assertRaises(ValueError):
                    settings.validate(projects, bad)

    def test_draft_generates_ids_without_success_criteria(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project(root, 'lab', P)
            data = draft(root)
            self.assertEqual(data['projects'][P]['nodes'][N]['name'], 'R1')
            self.assertEqual(data['projects'][P]['nodes'][N]['checks'], [])


class BackendTests(unittest.TestCase):
    def test_bundled_helpers_are_visible_only_to_the_child(self):
        from topolog.backend import launch_environment
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            executable = root / 'gns3server.exe'
            executable.touch()
            for directory, name in (('dynamips', 'dynamips.exe'), ('ubridge', 'ubridge.exe')):
                (root / directory).mkdir()
                (root / directory / name).touch()
            with patch.dict(os.environ, {'PATH': 'existing-path'}):
                environment = launch_environment(executable)
                self.assertEqual(os.environ['PATH'], 'existing-path')
                parts = environment['PATH'].split(os.pathsep)
                self.assertEqual(parts, [str(root / 'dynamips'), str(root / 'ubridge'), 'existing-path'])
                self.assertNotIn(str(root / 'qemu'), parts)

    def test_vpcs_helper_folder_matches_gui_server_path(self):
        from topolog.backend import launch_environment
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            executable = root / 'gns3server.exe'
            executable.touch()
            (root / 'vpcs').mkdir()
            (root / 'vpcs' / 'vpcs.exe').touch()
            with patch.dict(os.environ, {'PATH': 'existing-path'}):
                parts = launch_environment(executable)['PATH'].split(os.pathsep)
            self.assertEqual(parts, [str(root / 'vpcs'), 'existing-path'])

    def test_missing_helpers_preserve_the_environment(self):
        from topolog.backend import launch_environment
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict(os.environ, {'PATH': 'existing-path'}):
                self.assertEqual(launch_environment(Path(temp) / 'gns3server.exe'), dict(os.environ))

    def test_running_backend_is_reused(self):
        from topolog.cli import connect
        api = Mock(base='http://127.0.0.1:3080', host='127.0.0.1')
        api.call.return_value = {'version': '2.2.61'}
        with patch('topolog.cli.API', return_value=api), patch('topolog.cli.backend.discover') as discovery:
            self.assertIs(connect(), api)
            discovery.assert_not_called()

    def test_host_only_local_address_can_autostart(self):
        from topolog.cli import connect
        api = Mock(base='http://172.16.1.1:3080', host='172.16.1.1')
        api.call.side_effect = [URLError('connection refused'), {'version': '2.2.61'}]
        executable = Path('gns3server.exe')
        with patch('topolog.cli.API', return_value=api), patch('topolog.cli.backend.discover', return_value=({'172.16.1.1'}, [executable])), patch('topolog.cli.backend.launch_environment', return_value={'PATH': 'bundled-helpers'}), patch('topolog.cli.subprocess.Popen') as spawn:
            self.assertIs(connect(), api)
            self.assertEqual(spawn.call_args.args[0], [str(executable), '--local'])
            self.assertEqual(spawn.call_args.kwargs['env'], {'PATH': 'bundled-helpers'})

    def test_remote_server_is_not_started_locally(self):
        from topolog.cli import connect
        api = Mock(base='http://192.0.2.1:3080', host='192.0.2.1')
        api.call.side_effect = URLError('connection refused')
        with patch('topolog.cli.API', return_value=api), patch('topolog.cli.backend.discover', return_value=({'127.0.0.1'}, [Path('gns3server.exe')])), patch('topolog.cli.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(ValueError, 'remote VM'):
                connect()
            spawn.assert_not_called()

    def test_authentication_error_is_not_reported_as_backend_offline(self):
        from topolog.cli import connect
        api = Mock(base='http://127.0.0.1:3080', host='127.0.0.1')
        api.call.side_effect = HTTPError(api.base, 401, 'Unauthorized', {}, None)
        with patch('topolog.cli.API', return_value=api), patch('topolog.cli.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(ValueError, 'authentication'):
                connect()
            spawn.assert_not_called()

    def test_direct_api_does_not_use_system_proxy(self):
        from topolog.transport import API
        with patch('topolog.transport.urllib.request.build_opener') as build:
            API('http://127.0.0.1:3080')
            self.assertEqual(build.call_args.args[0].proxies, {})


class ProfileTests(unittest.TestCase):
    def evidence(self, command, text, error=False):
        return [{'command': command, 'status': 'collected', 'text': text, 'file': '01.txt', 'cli_error': error}]

    def test_qemu_does_not_identify_guest(self):
        self.assertEqual(profiles.platform({'node_type': 'qemu'}), 'unknown')
        self.assertEqual(profiles.platform({'node_type': 'qemu'}, 'Cisco Adaptive Security Appliance Software Version 9'), 'asa')
        self.assertEqual(profiles.platform({'node_type': 'dynamips'}, 'Cisco IOS Software'), 'ios')
        with self.assertRaises(ValueError):
            profiles.platform({'node_type': 'qemu'}, 'Cisco Adaptive Security Appliance', 'ios')

    def test_multiple_profiles(self):
        detected = profiles.detect_profiles('router ospf 1\nrouter rip\n redistribute rip subnets')
        self.assertEqual(set(detected), {'common', 'ospf', 'rip', 'redistribution'})
        self.assertIn('show ip protocols', profiles.commands('ios', detected))
        self.assertIn('show route-map', profiles.commands('ios', detected))

    def test_ospf_full_must_match_expected_router(self):
        rule = {'kind': 'ospf_neighbors', 'neighbors': ['2.2.2.2']}
        command = profiles.command_for(rule, 'ios')
        good = self.evidence(command, '2.2.2.2 1 FULL/DR 00:30\n3.3.3.3 1 INIT/DROTHER 00:30')
        bad = self.evidence(command, '2.2.2.2 1 INIT/DROTHER 00:30\n3.3.3.3 1 FULL/DR 00:30')
        self.assertEqual(profiles.evaluate(rule, good, 'ios')['status'], 'passed')
        point_to_point = self.evidence(command, '2.2.2.2 0 FULL/  - 00:00:30 10.1.0.2 Serial1/1')
        self.assertEqual(profiles.evaluate(rule, point_to_point, 'ios')['status'], 'passed')
        self.assertEqual(profiles.evaluate(rule, bad, 'ios')['status'], 'failed')
        self.assertEqual(profiles.evaluate(rule, [], 'ios')['status'], 'inconclusive')

    def test_asa_final_result_overrides_phase_allow(self):
        rule = {'kind': 'asa_packet', 'interface': 'inside', 'protocol': 'tcp', 'source': '192.0.2.1', 'source_port': 12345,
                'destination': '198.51.100.1', 'destination_port': 443, 'expected_action': 'drop'}
        command = profiles.command_for(rule, 'asa')
        text = 'Phase: 1\nAction: ALLOW\nResult:\ninput-interface: inside\nAction: drop\n'
        self.assertEqual(profiles.evaluate(rule, self.evidence(command, text), 'asa')['status'], 'passed')
        self.assertEqual(profiles.evaluate(rule, self.evidence(command, 'Action: ALLOW'), 'asa')['status'], 'inconclusive')
        with self.assertRaises(ValueError):
            profiles.command_for({**rule, 'interface': 'inside\nreload'}, 'asa')

    def test_ping_failure_not_firewall_policy_success(self):
        rule = {'kind': 'ping', 'target': '192.0.2.1'}
        command = profiles.command_for(rule, 'ios')
        self.assertEqual(profiles.evaluate(rule, self.evidence(command, 'Success rate is 0 percent (0/5)'), 'ios')['status'], 'failed')
        with self.assertRaises(ValueError):
            profiles.command_for({**rule, 'target': '192.0.2.1\nreload'}, 'ios')

    def test_route_protocol_and_next_hop_belong_to_same_prefix(self):
        rule = {'kind': 'route', 'prefix': '192.0.2.0/24', 'protocol': 'R', 'next_hop': '10.0.0.2'}
        command = profiles.command_for(rule, 'ios')
        good = 'R 192.0.2.0/24 [120/1] via 10.0.0.2, 00:00:03, FastEthernet0/0\n'
        bad = 'O 192.0.2.0/24 [110/2] via 10.0.0.2\nR 198.51.100.0/24 [120/1] via 10.0.0.2\n'
        self.assertEqual(profiles.evaluate(rule, self.evidence(command, good), 'ios')['status'], 'passed')
        self.assertEqual(profiles.evaluate(rule, self.evidence(command, bad), 'ios')['status'], 'failed')
        self.assertEqual(profiles.evaluate(rule, self.evidence(command, 'unrecognized text'), 'ios')['status'], 'inconclusive')

    def test_redistributed_external_route(self):
        rule = {'kind': 'route', 'prefix': '198.51.100.0/24', 'protocol': 'O E2'}
        text = 'O E2 198.51.100.0/24 [110/20] via 10.1.0.2, 00:00:03, FastEthernet0/0\n'
        command = profiles.command_for(rule, 'ios')
        self.assertEqual(profiles.evaluate(rule, self.evidence(command, text), 'ios')['status'], 'passed')

    def test_echo_and_cli_errors_do_not_pass(self):
        rule = {'kind': 'contains', 'command': 'show ip route', 'expected': ['show ip route']}
        self.assertEqual(profiles.evaluate(rule, self.evidence(rule['command'], 'R1#show ip route\nR1#'), 'ios')['status'], 'failed')
        self.assertEqual(profiles.evaluate(rule, self.evidence(rule['command'], 'show ip route', True), 'ios')['status'], 'inconclusive')
        with self.assertRaises(ValueError):
            profiles.command_for({**rule, 'command': 'reload'}, 'ios')


class EngineTests(unittest.TestCase):
    def test_id_collision_preserves_evidence_and_reports_recovery(self):
        class CollisionAPI(FakeAPI):
            def call(self, path, method='GET', body=None):
                if path == '/v2/projects/' + Q + '/nodes':
                    raise HTTPError('http://127.0.0.1:3080' + path, 409, 'Conflict', {},
                                    io.BytesIO(b'{"message":"Dynamips identifier 1 is already used by another router"}'))
                return super().call(path, method, body)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            labs = root / 'labs'
            labs.mkdir()
            project(labs, 'a', P)
            project(labs, 'b', Q)
            original = (labs / 'b.gns3').read_bytes()
            result = Runner(CollisionAPI(), root / 'out', collector=collected, settle_seconds=0).run(labs)
            failed = result['projects'][1]
            self.assertEqual(result['verdict'], 'inconclusive')
            self.assertIn('HTTP 409', failed['reason'])
            self.assertIn('restart', failed['recovery_hint'])
            self.assertIn(failed['recovery_hint'], (root / 'out/index.html').read_text(encoding='utf-8'))
            self.assertEqual((labs / 'b.gns3').read_bytes(), original)

    def test_unprivileged_asa_does_not_send_privileged_show_commands(self):
        class UnprivilegedASA(FakeConsole):
            prompt = 'F2>'
            def command(self, command):
                if command != 'show version':
                    raise AssertionError('Privileged command must not be sent')
                return 'Cisco Adaptive Security Appliance Software Version 9.9\nF2>'
        with tempfile.TemporaryDirectory() as temp:
            api = FakeAPI()
            result = collect_node(api, {**api.node(N), 'node_type': 'qemu'}, Path(temp) / 'asa', {}, [], console_factory=UnprivilegedASA)
            self.assertEqual(result['collection'], 'requires_privilege')
            self.assertEqual([item['command'] for item in result['commands']], ['show version'])
            self.assertIn('No enable/password was sent', result['reason'])

    def test_compute_vm_boot_waits_until_connected(self):
        api = Mock()
        api.call.side_effect = [[{'compute_id': 'vm', 'connected': False}], [{'compute_id': 'vm', 'connected': True}]]
        log = []
        with patch('topolog.engine.time.sleep'):
            wait_computes(api, {'vm'}, timeout=1, progress=log.append)
        self.assertEqual(api.call.call_count, 2)
        self.assertTrue(any('Waiting for GNS3 compute/VM' in text for text in log))

    def test_compute_timeout_and_cancel(self):
        api = Mock()
        api.call.return_value = [{'compute_id': 'local', 'connected': True}]
        with self.assertRaises(TimeoutError):
            wait_computes(api, {'vm'}, timeout=0)
        event = threading.Event()
        event.set()
        with self.assertRaises(InterruptedError):
            wait_computes(api, {'vm'}, cancel=event)

    def test_http_error_includes_server_message(self):
        error = HTTPError('http://127.0.0.1:3080', 409, 'Conflict', {}, io.BytesIO(json.dumps({'message': 'Dynamips identifier 1 is already used by another router'}).encode()))
        detail = error_detail(error)
        self.assertIn('HTTP 409', detail)
        self.assertIn('Dynamips identifier', detail)

    def test_cli_prepare_does_not_contact_api(self):
        from topolog.cli import main
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            labs = root / 'labs'
            labs.mkdir()
            project(labs, 'a', P)
            with patch('topolog.cli.connect', side_effect=AssertionError('Must not contact GNS3')):
                self.assertEqual(main([str(labs), '--output-dir', str(root / 'out'), '--prepare']), 0)
            files = list((root / 'out').rglob('criteria.json'))
            self.assertEqual(len(files), 1)
            self.assertIn(N, load_rules(files[0])[P]['nodes'])

    def test_asa_driver_collects_final_policy_result(self):
        class ASA(FakeConsole):
            def command(self, command):
                if command == 'show version':
                    return 'Cisco Adaptive Security Appliance Software Version 9.1\nasa#'
                if command.startswith('packet-tracer '):
                    return 'Phase: 1\nAction: ALLOW\nResult:\nAction: drop\nasa#'
                return 'Synthetic ASA show output\nasa#'
        with tempfile.TemporaryDirectory() as temp:
            api = FakeAPI()
            spec = {'checks': [{'kind': 'asa_packet', 'interface': 'inside', 'protocol': 'tcp',
                    'source': '192.0.2.1', 'source_port': 12345, 'destination': '198.51.100.1',
                    'destination_port': 443, 'expected_action': 'drop'}]}
            result = collect_node(api, {**api.node(N), 'node_type': 'qemu'}, Path(temp) / 'asa', spec, [], console_factory=ASA)
            self.assertEqual(result['platform'], 'asa')
            self.assertIn('show access-list', [c['command'] for c in result['commands']])
            self.assertEqual(result['checks'][0]['status'], 'passed')

    def test_cli_errors_block_complete_collection(self):
        class Invalid(FakeConsole):
            def command(self, command):
                return '% Invalid input detected\nR1#' if command == 'show ip route' else super().command(command)
        with tempfile.TemporaryDirectory() as temp:
            api = FakeAPI()
            result = collect_node(api, api.node(N), Path(temp) / 'node', {}, [], console_factory=Invalid)
            self.assertEqual(result['collection'], 'partial')
            self.assertEqual(verdict([result]), 'inconclusive')

    def test_discovery_duplicate_and_malformed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project(root, 'a', P)
            project(root, 'b', P)
            (root / 'bad.gns3').write_text('{')
            _, entries = discover(root)
            self.assertEqual(len(entries), 3)
            # A copied project keeps the GNS3 ID: it stays collectable under its own TopoLog key.
            self.assertEqual(sum(x['status'] == 'excluded' for x in entries), 1)
            second = next(x for x in entries if x['file'] == 'b.gns3')
            self.assertEqual(second['source_project_id'], P)
            self.assertNotEqual(second['project_id'], P)
            self.assertEqual(discover(root)[1][1]['project_id'], second['project_id'])   # same key on every scan

    def test_start_all_then_collect_preserve_and_continue(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            labs = root / 'labs'
            labs.mkdir()
            project(labs, 'a', P)
            project(labs, 'b', Q)
            api = FakeAPI()
            report = Runner(api, root / 'out', collector=collected, settle_seconds=0).run(labs)
            self.assertEqual(report['projects'][0]['verdict'], 'passed')
            self.assertEqual(report['projects'][1]['status'], 'error')
            self.assertEqual(api.states, {N: 'started', M: 'stopped'})
            self.assertNotIn(('/v2/projects/' + P + '/close', 'POST', None), api.calls)
            self.assertEqual(report['verdict'], 'inconclusive')
            self.assertNotIn('<router>', (root / 'out/index.html').read_text(encoding='utf-8'))
            manifest = json.loads((R(root / 'out') / 'manifest.json').read_text(encoding='utf-8'))
            with zipfile.ZipFile(str(root / 'out') + '.zip') as archive:
                self.assertIsNone(archive.testzip())
                for item in manifest['files']:
                    self.assertEqual(hashlib.sha256(archive.read(item['path'])).hexdigest(), item['sha256'])

    def test_cleanup_error_blocks_overall_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            labs = root / 'labs'
            labs.mkdir()
            project(labs, 'a', P)
            api = FakeAPI()
            api.fail_stop = True
            result = Runner(api, root / 'out', collector=collected, settle_seconds=0).run(labs)
            self.assertEqual(result['projects'][0]['status'], 'cleanup_failed')
            self.assertNotEqual(result['verdict'], 'passed')

    def test_missing_rule_target_never_passes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            labs = root / 'labs'
            labs.mkdir()
            project(labs, 'a', P)
            result = Runner(FakeAPI(), root / 'out', {P: {'nodes': {Q: {}}}}, collector=collected, settle_seconds=0).run(labs)
            self.assertEqual(result['projects'][0]['missing_rule_nodes'], [Q])
            self.assertEqual(result['verdict'], 'inconclusive')

    def test_cancel_restores_started_nodes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            labs = root / 'labs'
            labs.mkdir()
            project(labs, 'a', P)
            api = FakeAPI()
            event = threading.Event()
            def collector(*args, **kwargs):
                result = collected(*args, **kwargs)
                event.set()
                return result
            result = Runner(api, root / 'out', collector=collector, settle_seconds=0, cancel=event).run(labs)
            self.assertEqual(api.states, {N: 'started', M: 'stopped'})
            self.assertNotEqual(result['verdict'], 'passed')

    def test_collector_detects_profiles_and_keeps_config(self):
        with tempfile.TemporaryDirectory() as temp:
            api = FakeAPI()
            spec = {'checks': [{'kind': 'ospf_neighbors', 'neighbors': ['2.2.2.2']}]}
            result = collect_node(api, api.node(N), Path(temp) / 'node', spec, [], console_factory=FakeConsole)
            self.assertEqual(result['collection'], 'collected')
            self.assertEqual(result['platform'], 'ios')
            self.assertIn('redistribution', result['profiles'])
            self.assertEqual(result['checks'][0]['status'], 'passed')
            self.assertEqual((Path(temp) / 'node/startup.cfg').read_bytes(), b'hostname R1\n')

    def test_ios_paging_disabled_for_session_then_restored(self):
        sent = []
        class Terminal(FakeConsole):
            responses = {**FakeConsole.responses,
                         'show terminal | include Length': 'R1#show terminal | include Length\nLength: 24 lines, Width: 80 columns\nR1#',
                         'terminal length 0': 'R1#terminal length 0\nR1#',
                         'terminal length 24': 'R1#terminal length 24\nR1#'}
            def command(self, command):
                sent.append(command)
                return super().command(command)
        with tempfile.TemporaryDirectory() as temp:
            api = FakeAPI()
            result = collect_node(api, api.node(N), Path(temp) / 'node', {}, [], console_factory=Terminal)
        self.assertEqual(result['collection'], 'collected')
        self.assertEqual(result['terminal'], {'paging': 'restored', 'original_length': 24})
        self.assertLess(sent.index('terminal length 0'), sent.index('show running-config'))
        self.assertEqual(sent[-1], 'terminal length 24')
        self.assertNotIn('terminal length 0', [c['command'] for c in result['commands']])
        self.assertNotIn('configure terminal', sent)

    def test_unknown_terminal_length_keeps_pager_fallback(self):
        sent = []
        class NoLength(FakeConsole):
            def command(self, command):
                sent.append(command)
                return super().command(command)
        with tempfile.TemporaryDirectory() as temp:
            api = FakeAPI()
            result = collect_node(api, api.node(N), Path(temp) / 'node', {}, [], console_factory=NoLength)
        self.assertEqual(result['terminal']['paging'], 'unchanged')
        self.assertFalse(any(c.startswith('terminal length') for c in sent))

    def test_no_rules_is_unverified_and_unknown_guest_unsupported(self):
        with tempfile.TemporaryDirectory() as temp:
            api = FakeAPI()
            result = collect_node(api, api.node(N), Path(temp) / 'a', {}, [], console_factory=FakeConsole)
            self.assertEqual(result['checks'][0]['status'], 'unverified')
            class Unknown(FakeConsole):
                responses = {'show version': 'Linux guest\nR1#'}
            result = collect_node(api, {**api.node(N), 'node_type': 'qemu'}, Path(temp) / 'b', {}, [], console_factory=Unknown)
            self.assertEqual(result['collection'], 'unsupported')
            self.assertEqual(verdict([result]), 'inconclusive')

    def test_invalid_rules_rejected_before_running(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'criteria.json'
            data = {'schema': 'topolog-rules-v1', 'projects': {P: {'nodes': {N: {'checks': [{'kind': 'contains', 'command': 'reload', 'expected': ['yes']}]}}}}}
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                load_rules(path)

    def test_fragmented_telnet_negotiation(self):
        codec = TelnetCodec()
        self.assertEqual(codec.feed(b'abc\xff')[0], b'abc')
        self.assertEqual(codec.feed(bytes([251, 1]))[1], bytes([255, 253, 1]))
        self.assertEqual(codec.feed(bytes([255, 250, 1, 255]))[0], b'')
        self.assertEqual(codec.feed(bytes([240]) + b'R1#')[0], b'R1#')


class ScriptedSocket:
    def __init__(self, responses):
        self.responses = responses
        self.pending = []
        self.sent = []
        self.closed = False
    def settimeout(self, value):
        pass
    def sendall(self, data):
        self.sent.append(data)
        if data.endswith(b'\r\n'):
            self.pending.extend(self.responses.get(data.strip().decode(), []))
    def recv(self, count):
        if self.pending:
            return self.pending.pop(0)
        raise socket.timeout()
    def close(self):
        self.closed = True


class IOSPagerSocket:
    """Emulates IOS automore: output stops at --More-- until a key is read.

    `drop` keys are discarded as if they arrived before the emulated console
    started reading, which leaves the device waiting at --More--.
    """
    ERASE = b'\x08' * 9 + b' ' * 9 + b'\x08' * 9
    def __init__(self, pages, drop=0, prompt=b'S1#'):
        self.pages, self.drop, self.prompt = pages, drop, prompt
        self.pending, self.sent, self.waiting, self.index = [], [], False, 0
    def settimeout(self, value):
        pass
    def _emit_page(self):
        page = self.pages[self.index]
        self.index += 1
        if self.index < len(self.pages):
            self.pending.extend([page, b' --More-- '])
            self.waiting = True
        else:
            self.pending.append(page + b'\r\n' + self.prompt)
    def sendall(self, data):
        self.sent.append(data)
        if data == b'\r\n':
            self.pending.append(b'\r\n' + self.prompt)
        elif data.endswith(b'\r\n'):
            self.index = 0
            self.pending.append(data.strip() + b'\r\n')
            self._emit_page()
        elif data == b' ' and self.waiting:
            if self.drop:
                self.drop -= 1
                return
            self.waiting = False
            self.pending.append(self.ERASE)
            self._emit_page()
    def recv(self, count):
        if self.pending:
            return self.pending.pop(0)
        raise socket.timeout()
    def close(self):
        pass


class TransportTests(unittest.TestCase):
    def ios_pages(self):
        header = b'Interface                  IP-Address      OK? Method Status                Protocol\r\n'
        first = header + b''.join(b'FastEthernet1/%d            unassigned      YES unset  up                    down\r\n' % i for i in range(9))
        second = b''.join(b'FastEthernet1/%d            unassigned      YES unset  up                    down\r\n' % i for i in range(9, 16))
        return [first, second + b'Vlan1                      unassigned      YES unset  up                    up']

    def test_dropped_pager_key_is_resent_until_prompt(self):
        sock = IOSPagerSocket(self.ios_pages(), drop=1)
        log = []
        console = Console('127.0.0.1', 5002, command_timeout=2, pager_retry=.05, progress=log.append)
        with patch('topolog.transport.socket.create_connection', return_value=sock):
            console.connect()
            text = console.command('show ip interface brief')
        self.assertIn('Vlan1', text)
        self.assertNotIn('--More--', text)
        self.assertEqual(console.pager_resends, 1)
        self.assertTrue(any('re-sent' in message for message in log))
        self.assertEqual(console.prompt, 'S1#')

    def test_answered_pager_is_not_resent(self):
        sock = IOSPagerSocket(self.ios_pages() + [b'Loopback0                  1.1.1.1         YES manual up                    up'])
        console = Console('127.0.0.1', 5002, command_timeout=1, pager_retry=.01)
        with patch('topolog.transport.socket.create_connection', return_value=sock):
            console.connect()
            text = console.command('show ip interface brief')
        self.assertIn('Loopback0', text)
        self.assertEqual(console.pager_resends, 0)
        self.assertEqual(sock.sent.count(b' '), 2)

    def test_unanswered_pager_retries_are_bounded(self):
        sock = IOSPagerSocket(self.ios_pages(), drop=10)
        console = Console('127.0.0.1', 5002, command_timeout=.6, pager_retry=.05, pager_retries=3)
        with patch('topolog.transport.socket.create_connection', return_value=sock):
            console.connect()
            with self.assertRaises(TimeoutError):
                console.command('show ip interface brief')
        self.assertEqual(console.pager_resends, 3)
        self.assertEqual(sock.sent.count(b' '), 4)

    def test_cancel_while_waiting_at_pager(self):
        sock = IOSPagerSocket(self.ios_pages(), drop=10)
        cancel = threading.Event()
        console = Console('127.0.0.1', 5002, command_timeout=5, pager_retry=.05, cancel=cancel,
                          progress=lambda message: cancel.set() if 're-sent' in message else None)
        with patch('topolog.transport.socket.create_connection', return_value=sock):
            console.connect()
            with self.assertRaises(InterruptedError):
                console.command('show ip interface brief')
        self.assertIn(b'--More--', console.wire)

    def test_asa_pagination_is_advanced_even_when_fragmented(self):
        sock = ScriptedSocket({'': [b'asa#'], 'show version': [b'ASA test\r\n<--- Mo', b're --->', b'\r\ninterface data\r\n<--- More --->', b'\r\nasa#']})
        log = []
        console = Console('127.0.0.1', 5002, command_timeout=.05, progress=log.append)
        with patch('topolog.transport.socket.create_connection', return_value=sock):
            console.connect()
            self.assertIn('interface data', console.command('show version'))
        self.assertEqual(sock.sent.count(b' '), 2)
        self.assertTrue(any('pagination' in message for message in log))
        console.close()

    def test_fragmented_prompt_and_pagination(self):
        sock = ScriptedSocket({'': [b'R', b'1#'], 'show ip route': [b'show ip route\r\nline1 --More--', b'\r\nline2\r\nR1#']})
        console = Console('127.0.0.1', 5002, command_timeout=.05)
        with patch('topolog.transport.socket.create_connection', return_value=sock):
            console.connect()
            text = console.command('show ip route')
        self.assertIn('line2', text)
        self.assertIn(b' ', sock.sent)
        self.assertTrue(console.wire)
        console.close()
        self.assertTrue(sock.closed)

    def test_incomplete_response_times_out_and_retains_raw(self):
        sock = ScriptedSocket({'': [b'R1#'], 'show ip route': [b'partial output without prompt']})
        console = Console('127.0.0.1', 5002, command_timeout=.01)
        with patch('topolog.transport.socket.create_connection', return_value=sock):
            console.connect()
            with self.assertRaises(TimeoutError):
                console.command('show ip route')
        self.assertIn(b'partial output', console.wire)
        console.close()

    def test_login_and_configuration_modes_are_rejected(self):
        for output in (b'Password:', b'R1(config)#'):
            sock = ScriptedSocket({'': [output]})
            console = Console('127.0.0.1', 5002, command_timeout=.02)
            with patch('topolog.transport.socket.create_connection', return_value=sock):
                with self.assertRaises(ValueError):
                    console.connect()
            console.close()


if __name__ == '__main__':
    unittest.main()
