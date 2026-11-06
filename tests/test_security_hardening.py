from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from topolog import security,sharing,reporting
from topolog.presentation import refresh_report
import test_portfolio as fixtures


class SecurityTests(unittest.TestCase):
    def test_vendor_key_lines_and_structured_keys_are_redacted(self):
        text='hostname R1\ncrypto isakmp key fixture-psk address 192.0.2.1\nradius-server key fixture-radius\ntacacs-server host 192.0.2.2 key fixture-tacacs\nsnmp-server host 192.0.2.3 version 2c fixture-snmp\n key 7 fixture-key\n'
        result=sharing.redact({'text':text,'api_key':'fixture-api','community':'fixture-community','pre_shared_key':'fixture-structured'})
        for secret in ['fixture-psk','fixture-radius','fixture-tacacs','fixture-snmp','fixture-key','fixture-api','fixture-community','fixture-structured']:
            self.assertNotIn(secret,json.dumps(result))
        self.assertIn('hostname R1',result['text'])

    def test_path_traversal_windows_streams_and_reserved_names_rejected(self):
        for name in ['../escape','..\\escape','C:\\outside','\\\\server\\share','01.txt:payload','capture/../../x','CON.txt','p/NUL','p/file.','p/file ','p/\x00x','COM¹.txt','CONOUT$','file?.txt']:
            with self.subTest(name=name),self.assertRaises(ValueError):security.relative_name(name)
        self.assertEqual(security.relative_name('capture/토폴로지.png'),'capture/토폴로지.png')

    def test_malicious_report_rejected_before_generated_files_written(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);report=fixtures.report();report.update(schema='topolog-batch-v2',finished_at='2026-11-05T00:00:00')
            report['projects'][0]['project_id']='../outside'
            (folder/'summary.json').write_text(json.dumps(report),encoding='utf-8')
            (folder/'index.html').write_text('original',encoding='utf-8')
            with patch('topolog.portfolio.write') as write:
                with self.assertRaises(ValueError):refresh_report(folder)
                write.assert_not_called()
            self.assertEqual((folder/'index.html').read_text(),'original')

    def test_linked_output_rejected_before_write(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            (folder/'summary.json').write_text(json.dumps({'schema':'topolog-batch-v2','finished_at':'now','projects':[]}),encoding='utf-8')
            (folder/'report.md').write_text('sentinel',encoding='utf-8')
            with patch('topolog.security.linked',side_effect=lambda p:p.name=='report.md'),patch('topolog.portfolio.write') as write:
                with self.assertRaises(ValueError):refresh_report(folder)
                write.assert_not_called()
            self.assertEqual((folder/'report.md').read_text(),'sentinel')

    def test_stream_filename_in_shared_command_rejected_before_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/'source';source.mkdir();report=fixtures.report();report['schema']='topolog-batch-v2'
            report['projects'][0]['nodes'][0]['commands'][0]['file']='01.txt:hidden'
            (source/'summary.json').write_text(json.dumps(report),encoding='utf-8')
            target=Path(temp)/'target'
            with self.assertRaises(ValueError):sharing.create(source,target)
            self.assertFalse(target.exists())

    def test_public_svg_keeps_geometry_without_active_content(self):
        text='<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><script>alert(2)</script><foreignObject/><rect x="1" width="10"/><use href="https://example.invalid/x"/><path fill="url(https://example.invalid/x)"/><text>R1</text></svg>'
        result=security.passive_svg(text)
        for value in ['script','onload','foreignObject','https://','alert(']:self.assertNotIn(value,result)
        self.assertIn('rect',result);self.assertIn('R1',result);ET.fromstring(result)
        with self.assertRaises(ValueError):security.passive_svg('<!DOCTYPE svg [<!ENTITY x "value">]><svg/>')

    def test_csp_and_untrusted_display_text_and_image_paths(self):
        report=fixtures.report();report['projects'][0]['name']='<script>alert(1)</script>'
        report['projects'][0]['topology_images']['native']['file']='..\\outside.png'
        text=reporting.render(report,[])
        self.assertIn('Content-Security-Policy',text)
        self.assertIn('script-src &#39;none&#39;',text)
        self.assertNotIn('<script>',text)
        self.assertNotIn('<img',text)
        self.assertNotIn('href=',reporting.link('01.txt:payload','download'))


if __name__=='__main__':unittest.main()
