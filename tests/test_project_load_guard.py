"""A project is loaded only on a steady GNS3 server, and a load cut off midway is not closed."""
from http.client import RemoteDisconnected
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.error import HTTPError
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import topolog.engine as _engine; _engine.SERVER_CHECK_INTERVAL = 0   # no real server to wait for
from topolog.engine import Runner
from topolog.transport import API, HTTP_TIMEOUT, PROJECT_OPEN_TIMEOUT
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

    def test_http_rejection_is_not_described_as_a_disconnected_server(self):
        api = NotOpenAPI(load_error=HTTPError('http://localhost/v2/projects/load',409,'missing helper',{},None))
        entry = self.run_once(api)['projects'][0]
        self.assertEqual(entry['status'],'error')
        self.assertNotIn('load_interrupted',entry)
        self.assertNotIn('load_interrupted_note',entry)
        self.assertIn('409',entry['reason'])

    def test_project_load_and_open_have_a_separate_bounded_response_budget(self):
        api=API('http://127.0.0.1:3085')
        response=MagicMock()
        response.read.return_value=b'{}'
        api.opener=MagicMock()
        api.opener.open.return_value.__enter__.return_value=response
        for path,method,expected in [('/v2/projects/load','POST',PROJECT_OPEN_TIMEOUT),
                                     ('/v2/projects/abc/open','POST',PROJECT_OPEN_TIMEOUT),
                                     ('/v2/projects/abc/close','POST',PROJECT_OPEN_TIMEOUT),
                                     ('/v2/projects/abc','DELETE',PROJECT_OPEN_TIMEOUT),
                                     ('/v2/projects','GET',HTTP_TIMEOUT),
                                     ('/v2/projects/abc/nodes/x/start','POST',HTTP_TIMEOUT)]:
            api.raw(path,method)
            self.assertEqual(api.opener.open.call_args.kwargs['timeout'],expected)

    def test_request_budget_and_target_follow_a_different_server_origin(self):
        api=API('http://192.0.2.40:8080')
        api.opener=MagicMock()
        api.opener.open.return_value.__enter__.return_value.read.return_value=b'{}'
        api.raw('/v2/projects/load','POST',{'path':'/srv/gns3/labs/project.gns3'})
        request=api.opener.open.call_args.args[0]
        self.assertEqual(request.full_url,'http://192.0.2.40:8080/v2/projects/load')
        self.assertEqual(api.opener.open.call_args.kwargs['timeout'],PROJECT_OPEN_TIMEOUT)

    def test_rejected_load_without_a_registered_project_keeps_its_original_error(self):
        class Rejected(NotOpenAPI):
            def call(self,path,method='GET',body=None):
                if path.endswith('/close'):
                    raise HTTPError('http://localhost'+path,404,'project not registered',{},None)
                return super().call(path,method,body)
        api=Rejected(load_error=HTTPError('http://localhost/v2/projects/load',409,'duplicate name',{},None))
        entry=self.run_once(api)['projects'][0]
        self.assertEqual(entry['status'],'error')
        self.assertIn('409',entry['reason'])
        self.assertEqual(entry['cleanup_errors'],[])


if __name__ == '__main__':
    unittest.main()
