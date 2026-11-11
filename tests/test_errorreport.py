"""Error report: nothing leaves the PC until the person reads it and agrees."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from topolog import errorreport  # noqa: E402


class Response:
    status = 200
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False


class ReportTests(unittest.TestCase):
    def test_endpoint_comes_from_the_dsn(self):
        url, key = errorreport.endpoint()
        self.assertEqual(url, 'https://o4512410646806528.ingest.us.sentry.io/api/4512410670792704/envelope/')
        self.assertEqual(key, 'aa693189fef0f78db462019747e91fa6')
        with self.assertRaises(ValueError):
            errorreport.endpoint('http://key@example.com/1')

    def test_report_hides_secrets_and_home_and_matches_the_preview(self):
        home = str(Path.home())
        log = ['enable secret 5 $1$abc', ' password cisco123', 'folder ' + home + '/labs']
        report = errorreport.build('Boom at ' + home, log, {'step': '확인·실행'})
        for text in (report['preview'], report['log'], json.dumps(report['event'], ensure_ascii=False)):
            self.assertNotIn('cisco123', text)
            self.assertNotIn('$1$abc', text)
            self.assertNotIn(home + '/', text)
        self.assertIn('%USERPROFILE%', report['log'])
        self.assertIn(report['log'], report['preview'])                    # what is shown is what is sent

    def test_send_posts_one_envelope_with_the_shown_log(self):
        seen = []
        def opener(request, timeout):
            seen.append(request)
            return Response()
        report = errorreport.build('Boom', ['line 1'])
        self.assertEqual(errorreport.send(report, opener=opener), report['event']['event_id'])
        request = seen[0]
        self.assertIn('sentry_key=aa693189fef0f78db462019747e91fa6', request.get_header('X-sentry-auth'))
        lines = request.data.split(b'\n')
        self.assertEqual(json.loads(lines[1])['type'], 'event')
        self.assertEqual(json.loads(lines[3])['filename'], 'topolog-run.log')
        self.assertEqual(lines[4].decode('utf-8'), report['log'])

    def test_report_copy_is_kept_beside_the_logs(self):
        with tempfile.TemporaryDirectory() as temp:
            path = errorreport.keep(errorreport.build('Boom', ['x']), temp)
            self.assertIn('Boom', path.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
