import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import topolog.engine as _engine; _engine.SERVER_CHECK_INTERVAL = 0   # no real server to wait for
from topolog import runtime
from topolog.engine import Runner
from test_batch import FakeAPI,project,collected,P

class RuntimeTests(unittest.TestCase):
    def test_outdated_window_cannot_start_devices(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);labs=root/'labs';labs.mkdir();project(labs,'lab',P)
            api=FakeAPI()
            with patch('topolog.runtime.LOADED',{'outdated':'signature'}):
                with self.assertRaises(ValueError):Runner(api,root/'out').run(labs)
            self.assertEqual(api.calls,[])
            self.assertFalse((root/'out').exists())

    def test_new_run_identifies_executed_version_and_html_format(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);labs=root/'labs';labs.mkdir();project(labs,'lab',P)
            result=Runner(FakeAPI(),root/'out',collector=collected,settle_seconds=0).run(labs)
            self.assertEqual(result['runtime']['version'],runtime.VERSION)
            self.assertIn('engine.py',result['runtime']['source_hashes'])
            html=(root/'out/index.html').read_text(encoding='utf-8')
            self.assertIn('structured-html-v1',html)
            self.assertNotIn('<h1>TopoLog</h1><pre>',html)
