"""Synthetic GNS3 responses only; no ports, projects or nodes are changed."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from topolog.engine import diagnose_start_failure
from topolog import progress

DETAIL = ("Node startup failed: HTTP 409: Could not start Telnet QEMU console [Errno 98] error while attempting "
          "to bind on address ('0.0.0.0', 5000): [errno 98] address already in use")
F2 = {'node_id': 'f2', 'name': 'F2', 'compute_id': 'vm', 'console': 5000, 'status': 'stopped'}


class API:
    def __init__(self, projects, nodes, fail=False):
        self.projects, self.nodes, self.fail, self.calls = projects, nodes, fail, []

    def call(self, path, method='GET', body=None):
        self.calls.append((path, method))
        if method != 'GET':
            raise AssertionError('Diagnosis must be read-only: ' + method + ' ' + path)
        if self.fail:
            raise OSError('controller unavailable')
        if path == '/v2/projects':
            return self.projects
        return self.nodes.get(path.split('/')[3], [])


class PortConflictTests(unittest.TestCase):
    def test_lists_other_opened_project_holding_port(self):
        api = API([{'project_id': 'a', 'name': 'lab-a', 'status': 'opened'},
                   {'project_id': 'b', 'name': 'lab-b', 'status': 'closed'}],
                  {'a': [dict(F2), {'node_id': 'x', 'name': 'FW', 'compute_id': 'vm', 'console': 5000, 'status': 'started'},
                         {'node_id': 'y', 'name': 'PC', 'compute_id': 'local', 'console': 5000}],
                   'b': [{'node_id': 'z', 'name': 'closed', 'compute_id': 'vm', 'console': 5000}]})
        result = diagnose_start_failure(api, F2, DETAIL)
        self.assertEqual((result['port'], result['port_role'], result['compute_id']), (5000, 'console', 'vm'))
        self.assertEqual([h['node_name'] for h in result['holders']], ['FW'])
        self.assertIn('does not stop other projects', result['hint'])
        self.assertTrue(all(method == 'GET' for _, method in api.calls))
        self.assertNotIn('/v2/projects/b/nodes', [p for p, _ in api.calls])

    def test_no_holder_points_to_leftover_process_without_editing(self):
        result = diagnose_start_failure(API([{'project_id': 'a', 'status': 'opened'}], {'a': [dict(F2)]}), F2, DETAIL)
        self.assertEqual(result['holders'], [])
        self.assertIn('leftover process', result['hint'])
        self.assertIn('left to the user', result['hint'])

    def test_lookup_failure_is_reported_not_raised(self):
        result = diagnose_start_failure(API([], {}, fail=True), F2, DETAIL)
        self.assertTrue(result['holder_lookup'].startswith('failed:'))

    def test_other_errors_are_not_classified(self):
        self.assertIsNone(diagnose_start_failure(API([], {}), F2, 'Node startup failed: HTTP 409: image missing'))

    def test_runner_records_diagnosis_and_report_shows_hint(self):
        import tempfile
        from test_batch import FakeAPI, project, P, M
        from topolog.engine import Runner

        class Conflict(FakeAPI):
            def call(self, path, method='GET', body=None):
                if path.endswith('/start'):
                    self.calls.append((path, method, body))
                    raise ValueError(DETAIL.split('HTTP 409: ')[1])
                return super().call(path, method, body)

        def collected(api, node, folder, spec, selected, **kwargs):
            folder.mkdir(parents=True)
            return {'collection': 'collected', 'checks': [{'status': 'passed'}], 'commands': []}

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            labs = root / 'labs'
            labs.mkdir()
            project(labs, 'a', P)
            api = Conflict()
            report = Runner(api, root / 'out', collector=collected, settle_seconds=0).run(labs)
            record = next(n for n in report['projects'][0]['nodes'] if n['node_id'] == M)
            self.assertEqual(record['collection'], 'failed')
            self.assertEqual(record['start_diagnosis']['port'], 5000)
            self.assertIn(record['recovery_hint'], (root / 'out/index.html').read_text(encoding='utf-8').replace('&#x27;', "'"))
            self.assertFalse([c for c in api.calls if c[1] == 'PUT'])
            self.assertNotEqual(report['verdict'], 'passed')

    def test_progress_message_does_not_suggest_editing_ports(self):
        text = progress.message('Port conflict: anything')
        self.assertIn('변경하지 않았', text)


if __name__ == '__main__':
    unittest.main()
