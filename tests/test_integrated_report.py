import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from topolog.resultdir import records as R  # noqa: E402
from topolog import services
from topolog.topology import reconstruct,capture_native
from topolog.reporting import render
from topolog.progress import message
from topolog.engine import Runner
from test_batch import FakeAPI,collected,project,P,M

class IntegratedTests(unittest.TestCase):
    def test_runner_integrates_native_capture_and_skips_switch_artifacts(self):
        class API(FakeAPI):
            def node(self,nid):
                n=super().node(nid)
                if nid==M:n.update(node_type='ethernet_switch',name='BasicSwitch',console_type='none',
                                   properties={'ports_mapping':[{'name':'Ethernet0','port_number':0,'type':'access','vlan':1}]})
                return n
        def native(name,file,destination,cancel):
            destination.mkdir();(destination/'gns3-topology.png').write_bytes(b'fixture native exporter result')
            return {'status':'captured','kind':'gns3_native_topology_screenshot','file':'capture/gns3-topology.png'}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);labs=root/'labs';labs.mkdir();project(labs,'lab',P)
            with patch('topolog.topology.capture_native',side_effect=native):
                result=Runner(API(),root/'out',collector=collected,settle_seconds=0,capture=True).run(labs)
            self.assertEqual(result['projects'][0]['status'],'completed')
            self.assertTrue((R(root/'out')/P/'topology-reconstructed.svg').is_file())
            self.assertTrue((R(root/'out')/P/M/'switch-ports.json').is_file())
            switch=next(n for n in result['projects'][0]['nodes'] if n['node_id']==M)
            self.assertEqual(switch['collection'],'collected')
            self.assertEqual(result['projects'][0]['verdict'],'passed')
            html=(root/'out'/'index.html').read_text(encoding='utf-8')
            self.assertIn('BasicSwitch',html)
            self.assertIn('기본 동작 스위치',html)
            self.assertEqual(len(list((root/'out').glob('report(*).docx'))),1)
            self.assertFalse((root/'out'/'report-document-error.txt').exists())
            from urllib.parse import quote
            self.assertIn(quote(next((root/'out').glob('report(*).docx')).name),html)
            self.assertIn(P+'/capture/gns3-topology.png',html)

    def test_native_success_requires_exporter_record_and_png(self):
        import os
        if os.name!='nt':self.skipTest('Windows native export bridge')
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp:
            dest=Path(temp)/'capture'
            def export(*args,**kwargs):
                dest.mkdir();(dest/'capture.json').write_text(json.dumps({'capture_status':'captured'}),encoding='utf-8')
                (dest/'gns3-topology.png').write_bytes(b'\x89PNG\r\n\x1a\nfixture')
                return SimpleNamespace(returncode=0)
            with patch('topolog.topology.subprocess.Popen',side_effect=AssertionError('Must not launch extra GUI')),patch('topolog.topology.subprocess.run',side_effect=export) as invoked:
                result=capture_native('lab',Path(temp)/'lab.gns3',dest)
            self.assertEqual(result['status'],'captured')
            self.assertIn('Native',invoked.call_args.args[0])
            self.assertEqual(len(result['sha256']),64)

    def test_services_derive_and_evaluate_positive_and_negative(self):
        config=('ip dhcp pool USERS\n network 192.0.2.0 255.255.255.0\n'
                'ip nat inside source static 10.0.0.1 198.51.100.1\n'
                'interface FastEthernet0/0\n ip access-group CLIENTS in\n')
        rules=services.derive(config)
        self.assertEqual([f for f,r in rules],['dhcp','nat','acl'])
        texts={'dhcp_pool':'Pool USERS :\n 192.0.2.1 - 192.0.2.254\n',
               'static_nat':'--- 198.51.100.1 10.0.0.1 --- ---',
               'acl_binding':'FastEthernet0/0 is up, line protocol is up\n Inbound access list is CLIENTS\n'}
        for feature,rule in rules:
            self.assertTrue(services.evaluate(rule,texts[rule['kind']])[0])
            self.assertFalse(services.evaluate(rule,'unrecognized output')[0])
        self.assertFalse(services.evaluate(rules[0][1],'Pool USERS :\n 192.0.2.1 - 192.0.2.126\n')[0])

    def test_stp_requires_root_and_bridge_evidence(self):
        rule={'kind':'stp_instance','vlan':10}
        self.assertTrue(services.evaluate(rule,'VLAN0010\n Root ID Priority 10\n Bridge ID Priority 20\n')[0])
        self.assertFalse(services.evaluate(rule,'VLAN0020\n Root ID x\n Bridge ID x')[0])

    def test_reconstruction_is_svg_not_a_native_screenshot(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'map.svg'
            reconstruct({'nodes':[{'node_id':'a','name':'<R1>','x':0,'y':0},{'node_id':'b','name':'SW','x':180,'y':0,'node_type':'ethernet_switch'}],
                         'links':[{'nodes':[{'node_id':'a'},{'node_id':'b'}]}]},p)
            text=p.read_text(encoding='utf-8')
            self.assertIn('<path ',text)
            self.assertIn('&lt;R1&gt;',text)
            self.assertNotIn('<R1>',text)

    def test_native_failure_does_not_become_a_reconstructed_screenshot(self):
        from types import SimpleNamespace
        with patch('topolog.topology.subprocess.run',return_value=SimpleNamespace(returncode=1)):
            result=capture_native('lab','lab.gns3',Path('not-created'))
        self.assertEqual(result['status'],'unavailable')
        self.assertNotIn('file',result)

    def test_report_has_separate_tabs_and_downloads_without_switch_cards(self):
        switch={'node_id':'s','node_type':'ethernet_switch','name':'SHOULD_NOT_BE_A_CARD','collection':'not_applicable','checks':[]}
        report={'verdict':'inconclusive','projects':[{'name':'lab','project_id':'p','file':'lab.gns3','status':'completed',
                'topology_images':{'native':{'status':'captured','file':'capture/gns3-topology.png'},
                                   'reconstructed':{'status':'generated','file':'topology-reconstructed.svg'}},'nodes':[switch]}]}
        text=render(report,['raw.txt'])
        self.assertNotIn('SHOULD_NOT_BE_A_CARD',text)
        self.assertIn('실제 GNS3 스크린샷',text)
        self.assertIn('재구성 토폴로지',text)
        self.assertIn('download href="./raw.txt"',text)
        self.assertIn('capture/gns3-topology.png',text)
        self.assertIn('topology-reconstructed.svg',text)

    def test_progress_is_concise_and_korean(self):
        self.assertEqual(message('Node result: R1 / collected / '),'R1: 완료')
        self.assertEqual(message('Project: lab'),'프로젝트: lab')
        self.assertIsNone(message('CLI pagination: page 10'))
