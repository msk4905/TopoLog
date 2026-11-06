import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from topolog.resultdir import records as R  # noqa: E402
from topolog.selection import select,retry_ids
from topolog.sharing import redact,create
from topolog.engine import Runner
from test_batch import FakeAPI,project,P,Q,collected

class SelectionSharingTests(unittest.TestCase):
    def test_select_rejects_empty_and_missing(self):
        entries=[{'project_id':P},{'project_id':Q}]
        self.assertEqual(select(entries,{P}),entries[:1])
        for ids in [set(),{'absent'}]:
            with self.assertRaises(ValueError):select(entries,ids)

    def test_retry_preserves_passed_projects_and_skipped_photo(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)
            (p/'summary.json').write_text(json.dumps({'schema':'topolog-batch-v2','projects':[
                {'project_id':P,'status':'completed','verdict':'passed','topology_images':{'native':{'status':'unavailable','reason':'실제 GNS3 캡처를 사용하지 않은 실행입니다.'}}},
                {'project_id':Q,'status':'completed','verdict':'failed'}]}),encoding='utf-8')
            self.assertEqual(retry_ids(p),{Q})

    def test_runner_only_runs_selected_project(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);labs=p/'labs';labs.mkdir();project(labs,'first',P);project(labs,'other',Q)
            api=FakeAPI()
            result=Runner(api,p/'out',collector=collected,settle_seconds=0,project_ids={P},reachability=False).run(labs)
            self.assertEqual([x['project_id'] for x in result['projects']],[P])
            self.assertFalse(any(Q in call[0] for call in api.calls))

    def test_redaction_covers_credentials_and_private_keys(self):
        text='hostname R1\nusername user secret 5 fixture-secret\nsnmp-server community fixture-snmp\n-----BEGIN RSA PRIVATE KEY-----\nfixture-key\n-----END RSA PRIVATE KEY-----'
        result=redact({'password':'fixture-pw','text':text})
        for value in ['fixture-secret','fixture-snmp','fixture-key','fixture-pw']:self.assertNotIn(value,json.dumps(result))
        self.assertIn('hostname R1',result['text'])

    def test_share_copy_preserves_original_and_excludes_binary_sessions(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'old';source.mkdir()
            data={'schema':'topolog-batch-v2','verdict':'inconclusive','projects':[], 'unmatched_rule_projects':[], 'runtime':{'version':'fixture','source_directory':'private-path'}}
            (source/'summary.json').write_text(json.dumps(data),encoding='utf-8');(source/'session.rx.bin').write_bytes(b'fixture-secret')
            old=(source/'summary.json').read_bytes()
            report=create(source,root/'new')
            self.assertEqual(old,(source/'summary.json').read_bytes())
            self.assertFalse((root/'new'/'session.rx.bin').exists())
            self.assertEqual(report['sharing']['source_summary_sha256'],hashlib.sha256(old).hexdigest())
            self.assertIn('공개 검토용 사본',(root/'new'/'PUBLIC_REVIEW.md').read_text(encoding='utf-8'))
            self.assertNotIn('공개 전에',(R(root/'new')/'report-document.md').read_text(encoding='utf-8'))
            self.assertTrue((root/'new'/'report.docx').exists())

    def test_share_copy_blocks_path_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'old';source.mkdir()
            (source/'summary.json').write_text(json.dumps({'schema':'topolog-batch-v2','projects':[{'project_id':'../escape'}]}),encoding='utf-8')
            with self.assertRaises(ValueError):create(source,root/'new')
