"""A project is loaded only on a steady GNS3 server, and a load cut off midway is not closed."""
from http.client import RemoteDisconnected
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import topolog.engine as _engine; _engine.SERVER_CHECK_INTERVAL = 0   # no real server to wait for
from topolog.engine import Runner
import test_batch as fixtures


class NotOpenAPI(fixtures.FakeAPI):
    def __init__(self, version_failures=0, load_error=None):
        super().__init__()
        self.version_failures, self.load_error = version_failures, load_error

    def call(self, path, method='GET', body=None):
        if path == '/v2/version':
            self.calls.append((path, method, body))
            if self.version_failures:
                self.version_failures -= 1
                raise ConnectionRefusedError('server restarting')
            return {'version': '2.2'}
        if path == '/v2/projects':
            self.calls.append((path, method, body))
            return []
        if path == '/v2/projects/load':
            self.calls.append((path, method, body))
            if self.load_error:
                raise self.load_error
            return {'project_id': fixtures.P}
        return super().call(path, method, body)


def collect(*args, **kwargs):
    return {'collection': 'collected', 'platform': 'unknown', 'commands': [], 'checks': [{'status': 'unverified'}]}


class LoadGuardTests(unittest.TestCase):
    def run_once(self, api):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'input'
            root.mkdir()
            fixtures.project(root, 'p', fixtures.P)
            return Runner(api, Path(temp) / 'out', collector=collect, settle_seconds=0, reachability=False).run(root)

    def test_project_is_loaded_only_after_three_answers_in_a_row(self):
        api = NotOpenAPI(version_failures=2)
        self.run_once(api)
        paths = [c[0] for c in api.calls]
        load = paths.index('/v2/projects/load')
        self.assertEqual(paths[:load].count('/v2/version'), 5)            # two refused, then three answers
        self.assertEqual(paths[load - 3:load], ['/v2/version'] * 3)

    def test_load_cut_off_midway_is_not_closed(self):
        api = NotOpenAPI(load_error=RemoteDisconnected('Remote end closed connection without response'))
        result = self.run_once(api)
        entry = result['projects'][0]
        self.assertEqual(entry['status'], 'error')
        self.assertIn('닫기 요청을 보내지 않았습니다', entry['load_interrupted_note'])
        self.assertFalse([c for c in api.calls if c[0].endswith('/close')])


if __name__ == '__main__':
    unittest.main()
