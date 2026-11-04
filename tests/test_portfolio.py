"""The portfolio document is generated from judged evidence only and is a valid .docx."""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from topolog import portfolio


def png(width=40, height=20):
    raw = b''.join(b'\x00' + b'\xff\x00\x00' * width for _ in range(height))
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) + \
        chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')


CONFIG = ("Current configuration : 1 bytes\nhostname R1\nenable secret 5 $1$abc\nusername admin password 0 lab-secret\n"
          "interface Serial1/0\n ip address 10.0.12.1 255.255.255.0\n ip ospf cost 10\nrouter ospf 1\n network 10.0.12.0 0.0.0.255 area 0\n"
          "crypto isakmp key lab-secret address 10.0.12.2\n")


def report():
    node = {'node_id': 'n1', 'name': 'R1', 'node_type': 'dynamips', 'platform': 'ios', 'collection': 'collected',
            'analysis': {'features': ['ospf', 'other']},
            'commands': [{'command': 'show running-config', 'status': 'collected', 'text': CONFIG, 'file': '02.txt'},
                         {'command': 'show ip ospf neighbor', 'status': 'collected', 'file': '03.txt',
                          'text': 'Neighbor ID Pri State Dead Time Address Interface\n2.2.2.2 0 FULL/ - 00:00:35 10.0.12.2 Serial1/0\nR1#'}],
            'checks': [{'status': 'passed', 'criterion': {'kind': 'ospf_neighbors', 'neighbors': ['2.2.2.2']}, 'command': 'show ip ospf neighbor',
                        'observed': {'2.2.2.2': 'FULL/  -'}},
                       {'status': 'failed', 'criterion': {'kind': 'route_covered', 'address': '3.3.3.3'}, 'command': 'show ip route', 'observed': '경로 없음'}]}
    switch = {'node_id': 's', 'name': 'SW', 'node_type': 'ethernet_switch', 'collection': 'collected', 'checks': [{'status': 'not_applicable'}]}
    return {'started_at': '2026-11-04T00:00:00', 'projects': [{'name': '20260827-1', 'project_id': 'p1', 'status': 'completed', 'nodes': [node, switch],
            'topology_images': {'native': {'status': 'captured', 'file': 'capture/gns3-topology.png'}}}]}


class PortfolioTests(unittest.TestCase):
    def test_docx_structure_images_and_masking(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            (out / 'p1' / 'capture').mkdir(parents=True)
            (out / 'p1' / 'capture' / 'gns3-topology.png').write_bytes(png())
            (out / 'p1' / 'topology.json').write_text(json.dumps({'drawings': [{'svg': '<svg><text font-size="20.0">OSPF 실습</text></svg>'}],
                                                                 'links': [{'link_type': 'serial'}]}), encoding='utf-8')
            names = portfolio.write(report(), out)
            self.assertEqual(names, ['report.docx', 'report-document.md'])
            with zipfile.ZipFile(out / 'report.docx') as archive:
                self.assertIsNone(archive.testzip())
                for part in ('[Content_Types].xml', 'word/document.xml', 'word/styles.xml', 'word/header1.xml', 'word/media/image1.png'):
                    self.assertIn(part, archive.namelist())
                document = archive.read('word/document.xml').decode('utf-8')
                ET.fromstring(document)                       # well-formed XML
                for part in archive.namelist():
                    if part.endswith(('.xml', '.rels')):
                        ET.fromstring(archive.read(part))
            text = (out / 'report-document.md').read_text(encoding='utf-8')
            for heading in ('1. 프로젝트 개요', '2. 핵심 역량', '3. 대표 프로젝트 한눈에 보기', '구성과 목적', '주요 설정', '동작 원리',
                            '검증 결과', '4. 전체 실습 목록', '5. 증빙 자료'):
                self.assertIn(heading, text)
                self.assertIn(heading, document)
            self.assertIn('OSPF 실습', text)                  # topic from the GNS3 drawing title
            self.assertIn('GNS3 자체 Screenshot', text)
            self.assertIn('미충족', text)                     # failed checks are reported, not hidden
            self.assertIn(' ip ospf cost 10', text)
            for secret in ('lab-secret', '$1$abc'):
                self.assertNotIn(secret, text)
                self.assertNotIn(secret, document)
            self.assertIn('[직접 입력]', text)

    def test_missing_native_image_is_stated(self):
        with tempfile.TemporaryDirectory() as temp:
            data = report()
            data['projects'][0]['topology_images'] = {'native': {'status': 'unavailable'}}
            portfolio.write(data, Path(temp))
            text = (Path(temp) / 'report-document.md').read_text(encoding='utf-8')
            self.assertIn('GNS3 자체 Screenshot이 없습니다', text)
            self.assertNotIn('![](', text)


if __name__ == '__main__':
    unittest.main()
