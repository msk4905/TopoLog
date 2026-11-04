import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import os
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from topolog.presentation import refresh_report

class PresentationTests(unittest.TestCase):
    def fixture(self,root):
        folder=root/'batch_test';folder.mkdir()
        (folder/'summary.json').write_text(json.dumps({'schema':'topolog-batch-v2','finished_at':'done','verdict':'inconclusive','projects':[]}))
        (folder/'index.html').write_text('<h1>TopoLog</h1><pre>old</pre>')
        (folder/'raw.txt').write_bytes(b'original evidence\r\n')
        (folder/'manifest.json').write_text('{"files":[]}')
        with zipfile.ZipFile(str(folder)+'.zip','w') as z:z.writestr('old','old zip')
        return folder

    def test_same_report_path_is_updated_and_archive_hashes_match(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=self.fixture(Path(temp));before=(folder/'summary.json').read_bytes()
            self.assertEqual(refresh_report(folder),folder/'index.html')
            self.assertIn('프로젝트 요약',(folder/'index.html').read_text(encoding='utf-8'))
            self.assertEqual((folder/'summary.json').read_bytes(),before)
            self.assertEqual((folder/'raw.txt').read_bytes(),b'original evidence\r\n')
            manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
            with zipfile.ZipFile(str(folder)+'.zip') as z:
                self.assertIsNone(z.testzip())
                for item in manifest['files']:
                    self.assertEqual(hashlib.sha256(z.read(item['path'])).hexdigest(),item['sha256'])

    def test_failed_manifest_commit_rolls_back_html(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=self.fixture(Path(temp));old=(folder/'index.html').read_bytes()
            original_replace=os.replace
            def replace(src,dst):
                if Path(dst)==folder/'manifest.json':raise PermissionError('fixture locked manifest')
                return original_replace(src,dst)
            with patch('topolog.presentation.os.replace',side_effect=replace):
                with self.assertRaises(PermissionError):refresh_report(folder)
            self.assertEqual((folder/'index.html').read_bytes(),old)
