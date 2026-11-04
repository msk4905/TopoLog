"""Built-in GNS3 Ethernet switches: recorded from the API port map, never judged."""
import json
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from topolog.engine import collect_node, verdict, describe_switch
from topolog.reporting import render
from topolog import progress

DEFAULT_PORTS=[{'name':'Ethernet{}'.format(i),'port_number':i,'type':'access','vlan':1,'ethertype':''} for i in range(8)]


def switch(ports=DEFAULT_PORTS):
    return {'node_id':'switch','name':'Switch1','node_type':'ethernet_switch','console_type':'none',
            'properties':{'ports_mapping':ports}}


class SwitchTests(unittest.TestCase):
    def test_default_switch_is_collected_and_labelled_without_console(self):
        with tempfile.TemporaryDirectory() as temp:
            driver=Mock()
            result=collect_node(Mock(),switch(),Path(temp)/'switch',{},[],console_factory=driver)
            driver.assert_not_called()
            self.assertEqual(result['collection'],'collected')
            self.assertEqual(result['switch_profile'],'default')
            self.assertTrue(result['reason'].startswith('기본 동작 스위치'))
            ports=json.loads((Path(temp)/'switch/switch-ports.json').read_text(encoding='utf-8'))
            self.assertEqual(len(ports['ports']),8)
            router={'node_type':'dynamips','collection':'collected','checks':[{'status':'passed'}]}
            self.assertEqual(verdict([router,result]),'passed')
            self.assertEqual(verdict([result]),'inconclusive')

    def test_configured_ports_are_recorded_not_called_default(self):
        ports=[dict(DEFAULT_PORTS[0],vlan=10),dict(DEFAULT_PORTS[1],type='dot1q',vlan=1)]
        result=describe_switch(switch(ports))
        self.assertEqual((result['collection'],result['switch_profile']),('collected','configured'))
        self.assertNotIn('기본 동작 스위치',result['reason'])
        self.assertIn('access VLAN 10',result['reason'])
        self.assertIn('dot1q VLAN 1',result['reason'])

    def test_missing_port_map_is_partial(self):
        result=describe_switch({'node_type':'ethernet_switch','properties':{}})
        self.assertEqual(result['collection'],'partial')

    def test_report_shows_collected_default_switch_not_excluded(self):
        node={'node_id':'s','name':'SW1','node_type':'ethernet_switch',**describe_switch(switch())}
        report={'verdict':'inconclusive','projects':[{'name':'lab','project_id':'p','file':'lab.gns3','status':'completed','nodes':[node]}]}
        text=render(report,['p/s/switch-ports.json'])
        self.assertIn('SW1',text)
        self.assertIn('기본 동작 스위치',text)
        self.assertIn('수집 완료',text)
        self.assertNotIn('수집 제외',text)
        self.assertIn('p/s/switch-ports.json',text)

    def test_progress_line_names_default_switch(self):
        self.assertEqual(progress.message('Node result: SW1 / collected / 기본 동작 스위치 · 특별한 설정 없음'),'SW1: 완료 (기본 동작 스위치)')

    def test_ios_switch_is_still_a_console_collection_target(self):
        with tempfile.TemporaryDirectory() as temp:
            driver=Mock(side_effect=ConnectionError('fixture console'))
            result=collect_node(Mock(),{'node_id':'esw','name':'ESW1','node_type':'dynamips','console_type':'telnet','console':5002},Path(temp)/'esw',{},[],console_factory=driver)
            driver.assert_called_once()
            self.assertNotIn('switch_profile',result)


if __name__ == '__main__':
    unittest.main()
