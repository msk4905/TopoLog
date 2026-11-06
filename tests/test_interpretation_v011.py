"""Output interpretation fixes found by comparing real collected output (dev.6 audit)."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from topolog import features, verify


def item(command, text, file='01.txt'):
    return {'command': command, 'text': text, 'status': 'collected', 'file': file}


ESW_STP = '''show spanning-tree

 VLAN1 is executing the ieee compatible Spanning Tree protocol
  Bridge Identifier has priority 0, address cc05.2a18.0000
  We are the root of the spanning tree

 Port 42 (FastEthernet1/1) of VLAN1 is forwarding
   Designated root has priority 0, address cc05.2a18.0000

 VLAN2 is executing the ieee compatible Spanning Tree protocol
  Bridge Identifier has priority 32768, address cc05.2a18.0001
  Current root has priority 0, address cc06.0000.0000
ESW5#'''

OSPF_PROTOCOLS = '''show ip protocols
Routing Protocol is "rip"
  Redistributing: connected, isis, eigrp 100, rip, ospf 100

Routing Protocol is "ospf 100"
  Router ID 172.16.5.1
  It is an autonomous system boundary router
  Redistributing External Routes from,
    connected, includes subnets in redistribution
    isis with metric mapped to 10, includes subnets in redistribution
    eigrp 100 with metric mapped to 10, includes subnets in redistribution
    rip, includes subnets in redistribution
  Number of areas in this router is 1. 1 normal 0 stub 0 nssa
R5#'''

IPV6_PROTOCOLS = '''show ipv6 protocols
IPv6 Routing Protocol is "connected"
IPv6 Routing Protocol is "eigrp 100"
  Interfaces:
    Serial1/3
  Redistribution:
    Redistributing protocol connected with metric 0
    Redistributing protocol rip 100 with metric 0 include-connected
    Redistributing protocol isis level 1-2 with metric 0 include-connected
  Maximum path: 16

IPv6 Routing Protocol is "isis"
  Interfaces:
    Serial1/2
  Redistribution:
    Redistributing protocol ospf 100 at level 2 include-connected
IPv6 Routing Protocol is "ospf 100"
  Interfaces (Area 0):
    Serial1/1
  Redistribution:
    None
R3#'''

EIGRP6 = '''show ipv6 eigrp neighbors
IPv6-EIGRP neighbors for process 100
H   Address                 Interface       Hold Uptime   SRTT   RTO  Q  Seq
                                            (sec)         (ms)       Cnt Num
0   Link-local address:     Se1/3             13 00:01:45   35   210  0  3
    FE80::5
R3#'''


class InterpretationTests(unittest.TestCase):
    def test_indented_etherswitch_stp_block_and_root_line(self):
        evidence = [item('show spanning-tree', ESW_STP)]
        root = verify.evaluate({'kind': 'stp_root', 'command': 'show spanning-tree', 'vlan': 1, 'root': True}, evidence)
        self.assertEqual(root['status'], 'passed')
        other = verify.evaluate({'kind': 'stp_root', 'command': 'show spanning-tree', 'vlan': 2, 'root': True}, evidence)
        self.assertEqual(other['status'], 'failed')

    def test_hsrp_abbreviated_interface_and_speak_still_reported(self):
        text = 'Interface   Grp  Pri P State   Active          Standby         Virtual IP\nVl1         1    255 P Active  local           unknown         172.16.1.254\nS1#'
        rule = {'kind': 'hsrp', 'command': 'show standby brief', 'interface': 'Vlan1', 'group': '1', 'virtual_ip': '172.16.1.254'}
        self.assertEqual(features.evaluate(rule, [item('show standby brief', text)], 'ios')['status'], 'passed')
        speak = text.replace('255 P Active  local ', '0   P Speak   172.16.1.1')
        self.assertEqual(features.evaluate(rule, [item('show standby brief', speak)], 'ios')['status'], 'failed')

    def test_ospf_multiline_and_single_line_redistribution(self):
        evidence = [item('show ip protocols', OSPF_PROTOCOLS)]
        for source in ('isis', 'eigrp', 'rip'):
            result = verify.evaluate({'kind': 'redistribution', 'command': 'show ip protocols', 'into': 'ospf', 'source': source}, evidence)
            self.assertEqual(result['status'], 'passed', source)
        missing = verify.evaluate({'kind': 'redistribution', 'command': 'show ip protocols', 'into': 'ospf', 'source': 'bgp'}, evidence)
        self.assertEqual(missing['status'], 'failed')
        rip = verify.evaluate({'kind': 'redistribution', 'command': 'show ip protocols', 'into': 'rip', 'source': 'eigrp'}, evidence)
        self.assertEqual(rip['status'], 'passed')

    def test_ipv6_protocol_header_and_redistribution_list(self):
        evidence = [item('show ipv6 protocols', IPV6_PROTOCOLS)]
        ok = lambda into, source: verify.evaluate({'kind': 'redistribution', 'command': 'show ipv6 protocols',
                                                   'into': into, 'source': source}, evidence)['status']
        self.assertEqual(ok('eigrp', 'rip'), 'passed')
        self.assertEqual(ok('eigrp', 'isis'), 'passed')
        self.assertEqual(ok('isis', 'ospf'), 'passed')
        self.assertEqual(ok('ospf', 'rip'), 'failed')      # "None" is not a source
        self.assertEqual(ok('isis', 'rip'), 'failed')

    def test_ipv6_eigrp_neighbor_is_matched_on_the_linked_interface(self):
        evidence = [item('show ipv6 eigrp neighbors', EIGRP6)]
        rule = {'kind': 'ipv6_neighbor', 'command': 'show ipv6 eigrp neighbors', 'peer': 'R5', 'router_id': None, 'interface': 'Serial1/3'}
        self.assertEqual(verify.evaluate(rule, evidence)['status'], 'passed')
        self.assertEqual(verify.evaluate({**rule, 'interface': 'Serial1/2'}, evidence)['status'], 'failed')
        self.assertEqual(verify.evaluate({**rule, 'peer_address': 'fe80::5'}, evidence)['status'], 'passed')
        self.assertEqual(verify.evaluate({**rule, 'peer_address': 'FE80::9'}, evidence)['status'], 'failed')

    def test_ipv6_eigrp_rule_does_not_use_router_id_and_names_interface(self):
        r3 = 'hostname R3\nipv6 unicast-routing\ninterface Serial1/3\n ipv6 address 2001:1:1:35::3/64\n ipv6 eigrp 100\nipv6 router eigrp 100\n eigrp router-id 3.3.3.3\n'
        r5 = 'hostname R5\nipv6 unicast-routing\ninterface Serial1/0\n ipv6 address 2001:1:1:35::5/64\n ipv6 address FE80::5 link-local\n ipv6 eigrp 100\nipv6 router eigrp 100\n eigrp router-id 5.5.5.5\n'
        parsed = {'a': verify.parse(r3), 'b': verify.parse(r5)}
        project = {'nodes': [{'node_id': 'a', 'name': 'R3', 'platform': 'ios'}, {'node_id': 'b', 'name': 'R5', 'platform': 'ios'}]}
        rules, _ = verify.derive(project, parsed, lambda x, y: True)
        rule = next(r for f, r, b in rules['a'] if r['kind'] == 'ipv6_neighbor')
        self.assertIsNone(rule['router_id'])
        self.assertEqual(rule['interface'], 'Serial1/3')
        self.assertEqual(rule['peer_address'], 'fe80::5')


def router(name, lines):
    return verify.parse('hostname {}\n{}\n'.format(name, lines))


class ExpectationScopeTests(unittest.TestCase):
    """A rip <- isis level-2 boundary: level-1 origins are not required beyond it."""

    def setUp(self):
        self.parsed = {
            'r5': router('R5', 'interface Loopback0\n ip address 172.16.5.1 255.255.255.0\n ip router isis 100\n'
                               'interface Serial1/0\n ip address 192.168.57.5 255.255.255.0\n ip router isis 100\nrouter isis 100\n is-type level-1'),
            'r7': router('R7', 'interface Serial1/0\n ip address 192.168.57.7 255.255.255.0\n ip router isis 100\n'
                               'interface Serial1/1\n ip address 192.168.78.7 255.255.255.0\n'
                               'router isis 100\n redistribute rip metric 10 level-1-2\n'
                               'router rip\n version 2\n network 192.168.78.0\n redistribute isis 100 level-2 metric 10'),
            'r8': router('R8', 'interface Serial1/0\n ip address 192.168.78.8 255.255.255.0\n'
                               'interface Loopback0\n ip address 172.16.8.1 255.255.255.0\nrouter rip\n version 2\n network 192.168.78.0\n network 172.16.0.0'),
        }
        names = {'r5': 'R5', 'r7': 'R7', 'r8': 'R8'}
        self.project = {'nodes': [{'node_id': k, 'name': v, 'platform': 'ios'} for k, v in names.items()]}
        links = {('r5', 'r7'), ('r7', 'r8')}
        self.linked = lambda a, b: (a, b) in links or (b, a) in links

    def test_selective_redistribution_marks_dependent_routes_and_pings(self):
        self.assertIn(('rip', 4, 'isis'), self.parsed['r7']['redistribute_filters'])
        self.assertNotIn(('isis', 4, 'rip'), self.parsed['r7']['redistribute_filters'])
        rules, pings = verify.derive(self.project, self.parsed, self.linked)
        r8 = {r['address']: r for f, r, b in rules['r8'] if r['kind'] == 'route_covered'}
        self.assertIn('IS-IS level-2', r8['172.16.5.1']['conditional'])
        r5 = {r['address']: r for f, r, b in rules['r5'] if r['kind'] == 'route_covered'}
        self.assertNotIn('conditional', r5['172.16.8.1'])            # rip -> isis is level-1-2
        ping = next(r for r in pings['r5'] if r['target'] == '172.16.8.1')
        self.assertIn('반환 경로', ping['conditional'])               # the reply must cross level-2 only

    def test_conditional_failure_is_inconclusive_but_success_stays_passed(self):
        rule = {'kind': 'route_covered', 'command': 'show ip route', 'address': '172.16.5.1', 'conditional': 'R7 rip: IS-IS level-2 경로만 재분배'}
        missing = [item('show ip route', 'Codes: C - connected\nGateway of last resort is not set\n\nC    192.168.78.0/24 is directly connected, Serial1/0\nR8#')]
        failed = verify.evaluate(rule, missing)
        self.assertEqual(failed['status'], 'failed')
        result = verify.qualify(failed, rule, {})
        self.assertEqual(result['status'], 'inconclusive')
        self.assertEqual(result['observed'], '경로 없음')
        present = [item('show ip route', 'Codes: C - connected\nGateway of last resort is not set\n\nR    172.16.5.0/24 [120/1] via 192.168.78.7, Serial1/0\nR8#')]
        self.assertEqual(verify.qualify(verify.evaluate(rule, present), rule, {})['status'], 'passed')

    def test_inactive_origin_interface_is_not_required(self):
        brief = 'Interface IP-Address OK? Method Status Protocol\nSerial1/1 192.168.34.3 YES NVRAM up down\nLoopback0 172.16.3.1 YES NVRAM up up\nR3#'
        states = {'r3': verify.interface_states([item('show ip interface brief', brief)])}
        failed = {'status': 'failed', 'observed': '경로 없음'}
        down = verify.qualify(failed, {'kind': 'route_covered', 'origin_node': 'r3', 'origin_interface': 'Serial1/1'}, states)
        self.assertEqual(down['classification'], 'origin_inactive')
        up = verify.qualify(failed, {'kind': 'route_covered', 'origin_node': 'r3', 'origin_interface': 'Loopback0'}, states)
        self.assertEqual(up['status'], 'failed')
        self.assertEqual(verify.qualify(failed, {'kind': 'route_covered'}, states)['status'], 'failed')


class ReportTextTests(unittest.TestCase):
    def test_internal_linkage_keys_stay_out_of_readable_expectation(self):
        from topolog import reporting
        text = reporting.expectation({'kind': 'route_covered', 'address': '172.16.5.1', 'origin_node': 'uuid-x',
                                      'origin_interface': 'Loopback0', 'conditional': 'R7 rip: level-2'})
        self.assertIn('172.16.5.1', text)
        for hidden in ('uuid-x', 'conditional', 'origin_interface'):
            self.assertNotIn(hidden, text)


if __name__ == '__main__':
    unittest.main()
