"""Recovery, criteria round trips and offline use of recorded command output."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from topolog.engine import collect_node, Runner, load_rules, verdict
from topolog.criteria import CriteriaDocument, make_rule, reassess
from topolog.transport import Console
from topolog import profiles
from test_batch import FakeAPI, FakeConsole, ScriptedSocket, P, Q, N, M, project


class TerminalConsole(FakeConsole):
    responses = {**FakeConsole.responses, 'show terminal | include Length': 'Length: 24 lines\nR1#',
                 'terminal length 0': 'R1#', 'terminal length 24': 'R1#'}
    sent = []
    failure = None
    fail_on = 'show ip route'

    def command(self, command):
        self.sent.append(command)
        if command == self.fail_on and self.failure:
            raise self.failure
        return super().command(command)


class RecoveryTests(unittest.TestCase):
    def test_error_and_cancellation_restore_terminal_and_preserve_reason(self):
        for failure, status in ((TimeoutError('fixture timeout'), 'failed'),
                                (InterruptedError('fixture stop'), 'cancelled'),
                                (KeyboardInterrupt(), 'cancelled')):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temp:
                class Interrupted(TerminalConsole):
                    sent = []
                Interrupted.failure = failure
                api = FakeAPI()
                folder = Path(temp) / 'node'
                result = collect_node(api, api.node(N), folder, {}, [], console_factory=Interrupted)
                self.assertEqual(result['collection'], status)
                self.assertEqual(result['terminal']['paging'], 'restored')
                self.assertEqual(Interrupted.sent[-1], 'terminal length 24')
                self.assertIn('reason', result)
                self.assertEqual(json.loads((folder / 'collection.json').read_text()), result)
                self.assertTrue(result['commands'][-1]['finished_at'])

    def test_disable_ack_timeout_still_restores_original(self):
        class LostAck(TerminalConsole):
            sent = []
            failure = TimeoutError('ack lost')
            fail_on = 'terminal length 0'
        with tempfile.TemporaryDirectory() as temp:
            api = FakeAPI()
            result = collect_node(api, api.node(N), Path(temp) / 'n', {}, [], console_factory=LostAck)
            self.assertEqual(result['terminal']['paging'], 'restored')
            self.assertEqual(LostAck.sent[-1], 'terminal length 24')

    def test_restore_rejection_blocks_success(self):
        class RejectRestore(TerminalConsole):
            responses = {**TerminalConsole.responses, 'terminal length 24': '% Invalid input\nR1#'}
        with tempfile.TemporaryDirectory() as temp:
            api = FakeAPI()
            result = collect_node(api, api.node(N), Path(temp) / 'n', {}, [], console_factory=RejectRestore)
            self.assertEqual(result['terminal']['paging'], 'restore_failed')
            self.assertEqual(result['collection'], 'partial')
            self.assertTrue(result['cleanup_errors'])
            self.assertEqual(verdict([result]), 'inconclusive')

    def test_console_recovery_ignores_stop_and_resynchronizes_before_typing(self):
        class RecoverySocket(ScriptedSocket):
            def sendall(self, data):
                if data == b'\x03':
                    self.sent.append(data)
                    self.pending.append(b'\r\nR1#')
                else:
                    super().sendall(data)
        event = threading.Event()
        sock = RecoverySocket({'': [b'R1#'], 'terminal length 24': [b'R1#']})
        console = Console('127.0.0.1', 1, command_timeout=.1, cancel=event)
        with patch('topolog.transport.socket.create_connection', return_value=sock):
            console.connect()
            event.set()
            console.awaiting_prompt = True
            console.restore_terminal_length(24)
        self.assertLess(sock.sent.index(b'\x03'), sock.sent.index(b'terminal length 24\r\n'))
        self.assertIs(console.cancel, event)
        self.assertEqual(console.command_timeout, .1)
        self.assertFalse(console.awaiting_prompt)
        console.close()

    def test_cancelled_node_and_summary_agree_and_later_projects_not_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            labs = root / 'labs'
            labs.mkdir()
            project(labs, 'a', P)
            project(labs, 'b', Q)
            api = FakeAPI()
            def cancelled(*args, **kwargs):
                raise KeyboardInterrupt()
            output = root / 'out'
            result = Runner(api, output, collector=cancelled, settle_seconds=0).run(labs)
            self.assertTrue(result['cancelled'])
            self.assertEqual(result['projects'][0]['status'], 'cancelled')
            self.assertEqual(result['projects'][1]['status'], 'not_run')
            self.assertEqual(api.states, {N: 'started', M: 'stopped'})
            nodes = result['projects'][0]['nodes']
            self.assertEqual([n['collection'] for n in nodes], ['cancelled', 'not_run'])
            for node in nodes:
                saved = json.loads((output / P / node['node_id'] / 'collection.json').read_text())
                self.assertEqual(saved, node)


class CriteriaTests(unittest.TestCase):
    def test_unknown_prefix_cannot_supply_next_hop_to_previous_route(self):
        text = ('Codes: O - OSPF\nO 192.0.2.0/24 [110/20] via 10.0.0.1\n'
                'O 198.51.100.0 [110/20] via 10.0.0.2\n   [110/20] via 10.0.0.3')
        rule = {'kind':'route','prefix':'192.0.2.0/24','protocol':'O','next_hop':'10.0.0.3'}
        evidence = [{'command':'show ip route','file':'route.txt','status':'collected','text':text}]
        self.assertEqual(profiles.evaluate(rule,evidence,'ios')['status'],'failed')

    def test_classic_ios_routes_and_redistributed_routes(self):
        text = ('Codes: O - OSPF, R - RIP\n'
                '     172.16.0.0/24 is subnetted, 4 subnets\n'
                'O       172.16.4.0 [110/121] via 192.168.12.2, 00:00:02, Serial1/1\n'
                'R       172.16.3.0 [120/2] via 192.168.12.3, 00:00:02, Serial1/1\n'
                'O E2 192.0.2.0/24 [110/20] via 192.168.12.2, 00:00:02, Serial1/1\n')
        evidence = [{'command':'show ip route','file':'route.txt','status':'collected','text':text}]
        for prefix, protocol, hop in [('172.16.4.0/24','O','192.168.12.2'),
                                      ('172.16.3.0/24','R','192.168.12.3'),('192.0.2.0/24','O E2','192.168.12.2')]:
            rule = {'kind':'route','prefix':prefix,'protocol':protocol,'next_hop':hop}
            self.assertEqual(profiles.evaluate(rule,evidence,'ios')['status'],'passed')
            self.assertEqual(profiles.evaluate({**rule,'next_hop':'203.0.113.1'},evidence,'ios')['status'],'failed')
        ambiguous = [{'command':'show ip route','file':'r.txt','status':'collected',
                      'text':'Codes: O - OSPF\nO 172.16.4.0 [110/20] via 192.168.12.2'}]
        self.assertEqual(profiles.evaluate({'kind':'route','prefix':'172.16.4.0/24','protocol':'O'},ambiguous,'ios')['status'],'inconclusive')

    def test_named_editor_roundtrip_and_bad_rule_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project(root, 'routing', P)
            model = CriteriaDocument(root)
            rule = make_rule('route', {'prefix': '10.0.0.0/24', 'protocol': 'R', 'next_hop': '192.0.2.1'})
            model.update(P, N, 'ios', ['common', 'rip'], [rule])
            path = root / 'criteria.json'
            model.write(path)
            original = path.read_bytes()
            self.assertEqual(load_rules(path)[P]['nodes'][N]['checks'], [rule])
            reopened = CriteriaDocument(root, path)
            self.assertEqual(reopened.node(P, N)['name'], 'R1')
            with self.assertRaises(ValueError):
                reopened.update(P, N, 'vpcs', [], [rule])
            self.assertEqual(path.read_bytes(), original)

    def test_editor_rejects_invalid_addresses_and_command_injection(self):
        for kind, fields in [('ospf_neighbors', {'neighbors': 'not IPv4'}),
                             ('ping', {'target': '192.0.2.1', 'min_percent': '0'}),
                             ('contains', {'command': 'reload', 'expected': 'yes'}),
                             ('route', {'prefix': 'garbage', 'protocol': 'O'})]:
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                make_rule(kind, fields)

    def test_offline_reassessment_pass_fail_missing_evidence_and_provenance(self):
        from topolog.engine import save, export
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            labs = root / 'labs'
            labs.mkdir()
            project(labs, 'routing', P)
            original = root / 'recorded'
            original.mkdir()
            node = {'node_id': N, 'name': 'R1', 'node_type': 'dynamips', 'collection': 'collected',
                    'platform': 'ios', 'commands': [{'command':'show ip ospf neighbor','status':'collected',
                    'cli_error':False,'file':'01.txt','text':'2.2.2.2 1 FULL/DR 00:30\nR1#'}],
                    'checks':[{'status':'unverified'}]}
            data = {'schema':'topolog-batch-v2', 'started_at':'original-time', 'finished_at':'original-end',
                    'verdict':'inconclusive','unmatched_rule_projects':[],
                    'projects':[{'project_id':P,'name':'routing','file':'routing.gns3','status':'completed',
                                 'verdict':'inconclusive','nodes':[node],'cleanup_errors':[]}]}
            save(original / 'summary.json', data)
            save(original / P / N / 'collection.json', node)
            (original / P / N / '01.txt').write_text(node['commands'][0]['text'])
            before = {p.relative_to(original):p.read_bytes() for p in original.rglob('*') if p.is_file()}
            model = CriteriaDocument(labs)
            path = root / 'criteria.json'
            for index, (neighbors, expected) in enumerate([('2.2.2.2','passed'),('3.3.3.3','failed')]):
                model.update(P,N,'ios',['ospf'],[make_rule('ospf_neighbors',{'neighbors':neighbors})])
                model.write(path)
                result = reassess(original,root / ('out'+str(index)),path)
                self.assertEqual(result['verdict'], expected)
                self.assertEqual(result['started_at'], 'original-time')
                self.assertIn('source_summary_sha256', result['assessment'])
                self.assertIn('observed', result['projects'][0]['nodes'][0]['checks'][0])
            model.update(P,N,'ios',[],[make_rule('ping',{'target':'192.0.2.1','min_percent':'100'})])
            model.write(path)
            self.assertEqual(reassess(original, root / 'missing', path)['verdict'], 'inconclusive')
            self.assertEqual(before, {p.relative_to(original):p.read_bytes() for p in original.rglob('*') if p.is_file()})
            with self.assertRaises(ValueError):
                reassess(original,original/'nested',path)
