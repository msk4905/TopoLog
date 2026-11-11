"""Security follow-ups: review-copy credentials, Word XML, PNG metadata, script arguments, stage timing."""
from pathlib import Path
import json
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib
from unittest.mock import patch
import xml.etree.ElementTree as ET
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from topolog.resultdir import records as R  # noqa: E402
from topolog.resultdir import evidence_dir as E  # noqa: E402
from topolog import sharing, security, portfolio, topology
import test_portfolio as fixtures


def chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)


def png(extra=b''):
    header = chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
    pixels = chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00'))
    return security.PNG_SIGNATURE + header + extra + pixels + chunk(b'IEND', b'')


class SecurityFollowupTests(unittest.TestCase):
    def test_more_cisco_credential_lines_are_redacted_and_context_kept(self):
        text = '\n'.join([
            'interface Serial1/0', ' ip ospf message-digest-key 1 md5 fx-ospf', ' standby 1 authentication fx-hsrp',
            ' vrrp 2 authentication text fx-vrrp', ' glbp 3 authentication text fx-glbp', ' ip nhrp authentication fx-nhrp',
            'snmp-server user u g v3 auth sha fx-snmpauth priv aes 128 fx-snmppriv', 'aaa-server R (inside) host 10.0.0.1',
            ' key fx-asakey', 'radius server R1', ' key fx-radius', 'failover key fx-failover',
            '  Authentication text, string "fx-show"', ' standby 1 ip 10.0.0.254', ' standby 1 priority 110',
            'router ospf 1', ' network 10.0.0.0 0.0.0.255 area 0'])
        result = sharing.redact(text)
        for secret in ['fx-ospf', 'fx-hsrp', 'fx-vrrp', 'fx-glbp', 'fx-nhrp', 'fx-snmpauth', 'fx-snmppriv', 'fx-asakey',
                       'fx-radius', 'fx-failover', 'fx-show']:
            self.assertNotIn(secret, result)
        for kept in ['interface Serial1/0', 'standby 1 ip 10.0.0.254', 'standby 1 priority 110', 'network 10.0.0.0 0.0.0.255 area 0',
                     'aaa-server R (inside) host 10.0.0.1']:
            self.assertIn(kept, result)

    def test_console_control_characters_do_not_corrupt_word_document(self):
        report = fixtures.report()
        report['projects'][0]['name'] = 'Lab\x07\x1b[0m\x0b\x00'
        with tempfile.TemporaryDirectory() as temp:
            portfolio.write(report, Path(temp))
            documents = list(Path(temp).glob('*.docx'))
            self.assertTrue(documents)
            with zipfile.ZipFile(documents[0]) as archive:
                for name in ('word/document.xml', 'docProps/core.xml'):
                    ET.fromstring(archive.read(name))

    def test_review_copy_png_drops_text_time_and_exif_chunks(self):
        original = png(chunk(b'tEXt', b'Author\x00fx-user') + chunk(b'tIME', b'\x07\xea\x0b\x05\x00\x00\x00') +
                       chunk(b'eXIf', b'fx-exif') + chunk(b'pHYs', b'\x00\x00\x0b\x13\x00\x00\x0b\x13\x01'))
        cleaned = security.png_without_metadata(original)
        self.assertTrue(cleaned.startswith(security.PNG_SIGNATURE))
        for value in (b'fx-user', b'tIME', b'fx-exif'):
            self.assertNotIn(value, cleaned)
        self.assertIn(b'IDAT', cleaned);self.assertIn(b'pHYs', cleaned)
        for broken in (b'not a png', original[:-6], original.replace(b'fx-user', b'fx-usex')):
            with self.assertRaises(ValueError):security.png_without_metadata(broken)

    def test_capture_script_never_receives_option_like_project_name(self):
        for name in ['-OutputDirectory', '/x', 'a"b', 'tab\tname', '`whoami`', '']:
            with self.assertRaises(ValueError):security.safe_argument(name)
        self.assertEqual(security.safe_argument('20260831-1 GRE 실습'), '20260831-1 GRE 실습')
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp, patch.object(topology, 'os', SimpleNamespace(name='nt')), \
                patch.object(topology.subprocess, 'run') as run:
            result = topology.capture_native('-OutputDirectory C:\\x', Path(temp) / 'p.gns3', Path(temp) / 'capture')
            run.assert_not_called()
            self.assertEqual(result['status'], 'unavailable')

    def test_runner_records_stage_seconds(self):
        import test_batch as batch
        from topolog.engine import Runner
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'input';root.mkdir();batch.project(root, 'p', batch.P)
            result = Runner(batch.FakeAPI(), Path(temp) / 'out', collector=batch.collected, settle_seconds=0, reachability=False).run(root)
            timing = result['projects'][0]['timing']['seconds']
            for stage in ('prepare_project', 'start_nodes', 'settle_wait', 'initial_collection', 'reachability', 'final_observation', 'cleanup_and_save'):
                self.assertIn(stage, timing)
            saved = json.loads((E(Path(temp) / 'out', batch.P) / 'result.json').read_text(encoding='utf-8'))
            self.assertIn('timing', saved)


if __name__ == '__main__':
    unittest.main()
