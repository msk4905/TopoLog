"""Synthetic configurations and device output for the extended automatic checks."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from topolog import verify, features, reach
from topolog.engine import parse_vpcs, describe_builtin, verdict


def cmd(command, text, file='01.txt', **extra):
    return {'command': command, 'status': 'collected', 'cli_error': False, 'file': file, 'text': text, **extra}


def node(nid, name, config, outputs=(), platform='ios'):
    commands = [cmd('show running-config', 'Current configuration : 1 bytes\n' + config, '02.txt')]
    commands += [cmd(c, t, '{:02d}.txt'.format(i + 3)) for i, (c, t) in enumerate(outputs)]
    return {'node_id': nid, 'name': name, 'node_type': 'qemu' if platform == 'asa' else 'dynamips',
            'platform': platform, 'collection': 'collected', 'commands': commands}


def link(a, b):
    return {'nodes': [{'node_id': a, 'adapter_number': 1, 'port_number': 0}, {'node_id': b, 'adapter_number': 1, 'port_number': 0}]}


R1 = """hostname R1
interface Loopback0
 ip address 1.1.1.1 255.255.255.255
interface Serial1/0
 ip address 10.0.12.1 255.255.255.0
router ospf 1
 network 10.0.12.0 0.0.0.255 area 0
 network 1.1.1.1 0.0.0.0 area 0
"""
R2 = """hostname R2
interface Serial1/0
 ip address 10.0.12.2 255.255.255.0
interface Serial1/1
 ip address 10.0.23.1 255.255.255.0
router ospf 1
 redistribute rip metric 10 subnets
 network 10.0.12.0 0.0.0.255 area 0
router rip
 version 2
 redistribute ospf 1 metric 5
 network 10.0.0.0
 no auto-summary
"""
R3 = """hostname R3
interface Serial1/1
 ip address 10.0.23.2 255.255.255.0
interface FastEthernet0/0
 ip address 192.168.3.1 255.255.255.0
router rip
 version 2
 network 10.0.0.0
 network 192.168.3.0
 no auto-summary
ip route 0.0.0.0 0.0.0.0 10.0.23.1
"""
ROUTES_R1 = """Codes: C - connected, S - static, R - RIP, O - OSPF
Gateway of last resort is not set
C    10.0.12.0/24 is directly connected, Serial1/0
O E2 192.168.3.0/24 [110/10] via 10.0.12.2, 00:00:05, Serial1/0
O E2 10.0.23.0/24 [110/10] via 10.0.12.2, 00:00:05, Serial1/0
"""
ROUTES_R3 = """Codes: C - connected, S - static, R - RIP, O - OSPF
Gateway of last resort is 10.0.23.1 to network 0.0.0.0
C    10.0.23.0/24 is directly connected, Serial1/1
C    192.168.3.0/24 is directly connected, FastEthernet0/0
R    10.0.12.0/24 [120/5] via 10.0.23.1, 00:00:05, Serial1/1
S*   0.0.0.0/0 [1/0] via 10.0.23.1
"""


def project(nodes, links):
    return {'nodes': nodes, 'topology_links': links}


class ParseTests(unittest.TestCase):
    def test_asa_network_uses_netmask_and_pager_residue_is_removed(self):
        cfg = verify.parse("hostname F2\ninterface GigabitEthernet0/1\n nameif inside\n ip address 192.168.12.2 255.255.255.0 \n"
                           "<--- More --->               ospf cost 10\nrouter ospf 1\n network 192.168.12.0 255.255.255.0 area 0\n", 'asa')
        interface = cfg['interfaces'][0]
        self.assertEqual(interface['address'], '192.168.12.2/24')
        self.assertIn('ospf', interface['protocols'])
        analysis = features.analyze("interface GigabitEthernet0/1\n<--- More --->               ospf cost 10\n", 'asa')
        self.assertNotIn('ospf', analysis['unclassified'])

    def test_default_asa_crypto_lines_are_not_vpn_and_management_is_not_other(self):
        analysis = features.analyze("crypto ipsec security-association pmtu-aging infinite\ncrypto ca trustpoint X\n"
                                    "ip ssh version 2\ndefault-value exec-character-bits 8\nip route 0.0.0.0 0.0.0.0 1.1.1.1\n", 'ios')
        self.assertNotIn('vpn', analysis['features'])
        self.assertNotIn('other', analysis['features'])
        self.assertIn('ssh', analysis['features'])
        self.assertIn('static', analysis['features'])

    def test_vpcs_startup_parsing(self):
        self.assertEqual(parse_vpcs('set pcname PC1\nip 192.168.1.2 192.168.1.1 24\n'), {'address': '192.168.1.2/24', 'gateway': '192.168.1.1'})
        self.assertEqual(parse_vpcs('ip 10.1.1.5/24 10.1.1.1\n'), {'address': '10.1.1.5/24', 'gateway': '10.1.1.1'})
        self.assertEqual(parse_vpcs('ip dhcp\n'), {'dhcp': True})


class DeriveTests(unittest.TestCase):
    def build(self, outputs1=(), outputs3=()):
        nodes = [node('a', 'R1', R1, [('show ip route', ROUTES_R1)] + list(outputs1)),
                 node('b', 'R2', R2), node('c', 'R3', R3, [('show ip route', ROUTES_R3)] + list(outputs3))]
        return project(nodes, [link('a', 'b'), link('b', 'c')])

    def test_redistribution_and_static_routes_become_route_checks(self):
        p = self.build()
        features.apply(p)
        r1 = {c['criterion'].get('address') or c['criterion'].get('prefix'): c for c in p['nodes'][0]['checks'] if 'criterion' in c}
        self.assertEqual(r1['192.168.3.1']['status'], 'passed')          # RIP prefix via OSPF redistribution
        r3 = [c for c in p['nodes'][2]['checks'] if c.get('criterion', {}).get('kind') in ('static_route', 'route_covered')]
        self.assertTrue(any(c['criterion']['kind'] == 'static_route' and c['status'] == 'passed' for c in r3))
        self.assertTrue(any(c['criterion'].get('address') == '1.1.1.1' and c['status'] == 'passed' for c in r3))  # via default
        r2 = p['nodes'][1]
        self.assertTrue(any(c.get('feature') == 'redistribution' for c in r2['checks']))
        self.assertFalse(any(c['status'] == 'unverified' and c.get('feature') in ('rip', 'ospf', 'static') for n in p['nodes'] for c in n['checks']))

    def test_missing_route_fails(self):
        p = self.build(outputs1=())
        p['nodes'][0]['commands'][1]['text'] = ROUTES_R1.replace('O E2 192.168.3.0/24 [110/10] via 10.0.12.2, 00:00:05, Serial1/0\n', '')
        features.apply(p)
        check = next(c for c in p['nodes'][0]['checks'] if c.get('criterion', {}).get('address') == '192.168.3.1')
        self.assertEqual(check['status'], 'failed')

    def test_ping_plan_targets_remote_routers_and_prefers_loopback(self):
        pings = features.plan_pings(self.build())
        targets = {r['target'] for r in pings['c']}
        self.assertIn('1.1.1.1', targets)
        self.assertNotIn('10.0.12.1', targets)
        self.assertFalse(any(r['policy'] for r in pings['c']))

    def test_eigrp_isis_bgp_neighbors_are_derived(self):
        a = "hostname A\ninterface Serial1/0\n ip address 10.1.1.1 255.255.255.0\n ip router isis\nrouter eigrp 10\n network 10.0.0.0\nrouter isis\n net 49.0001.0000.0000.0001.00\nrouter bgp 65001\n neighbor 10.1.1.2 remote-as 65002\n"
        b = "hostname B\ninterface Serial1/0\n ip address 10.1.1.2 255.255.255.0\n ip router isis\nrouter eigrp 10\n network 10.0.0.0\nrouter isis\n net 49.0001.0000.0000.0002.00\n"
        outputs = [('show ip eigrp neighbors', 'H   Address  Interface\n0   10.1.1.2  Se1/0  12 00:01:00  20  200  0  5\n'),
                   ('show isis neighbors', 'System Id  Type Interface IP Address  State Holdtime Circuit Id\nB          L1L2 Se1/0     10.1.1.2    UP    25       00\n'),
                   ('show ip bgp summary', 'Neighbor  V  AS MsgRcvd MsgSent TblVer InQ OutQ Up/Down State/PfxRcd\n10.1.1.2  4 65002 10 10 1 0 0 00:05:00 3\n')]
        p = project([node('a', 'A', a, outputs), node('b', 'B', b)], [link('a', 'b')])
        features.apply(p)
        kinds = {c['criterion']['kind']: c['status'] for c in p['nodes'][0]['checks'] if 'criterion' in c}
        self.assertEqual(kinds.get('eigrp_neighbor'), 'passed')
        self.assertEqual(kinds.get('isis_neighbor'), 'passed')
        self.assertEqual(kinds.get('bgp_peer'), 'passed')

    def test_gre_ipsec_ssh_nat_checks(self):
        cfg = ("hostname R1\ncrypto map VPN 10 ipsec-isakmp\n set peer 192.168.45.2\ninterface Tunnel15\n ip address 172.16.15.1 255.255.255.0\n"
               " tunnel source Serial1/1\n tunnel destination 192.168.45.2\ninterface Serial1/1\n ip address 192.168.12.1 255.255.255.0\n"
               " crypto map VPN\n ip nat outside\ninterface FastEthernet0/0\n ip address 172.16.1.254 255.255.255.0\n ip nat inside\n"
               "ip nat inside source list PAT interface Serial1/1 overload\nip ssh version 2\n")
        outputs = [('show ip interface brief', 'Interface IP-Address OK? Method Status Protocol\nTunnel15 172.16.15.1 YES NVRAM up up\nSerial1/1 192.168.12.1 YES NVRAM up up\nFastEthernet0/0 172.16.1.254 YES NVRAM up up\n'),
                   ('show crypto isakmp sa', 'dst             src             state          conn-id slot status\n192.168.45.2    192.168.12.1    QM_IDLE              1    0 ACTIVE\n'),
                   ('show crypto ipsec sa', 'interface: Serial1/1\n    current_peer 192.168.45.2 port 500\n     #pkts encaps: 9\n     inbound ah sas:\n      spi: 0x1A2B3C(1715004)\n'),
                   ('show ip ssh', 'SSH Disabled - version 1.99\n%Please create RSA keys to enable SSH.\n'),
                   ('show ip nat statistics', 'Outside interfaces:\n  Serial1/1\nInside interfaces:\n  FastEthernet0/0\nHits: 0\n'),
                   ('show ip route', 'Gateway of last resort is 0.0.0.0 to network 0.0.0.0\nC    192.168.12.0/24 is directly connected, Serial1/1\nS*   0.0.0.0/0 is directly connected, Serial1/1\n')]
        p = project([node('a', 'R1', cfg, outputs)], [])
        features.apply(p)
        result = {(c['criterion']['kind'], c['criterion'].get('interface', '')): c for c in p['nodes'][0]['checks'] if 'criterion' in c}
        self.assertEqual(result[('interface_up', 'Tunnel15')]['status'], 'passed')
        self.assertEqual(result[('isakmp_sa', '')]['status'], 'passed')
        self.assertEqual(result[('ipsec_sa', '')]['status'], 'passed')
        self.assertEqual(result[('nat_interfaces', '')]['status'], 'passed')
        self.assertEqual(result[('ssh_enabled', '')]['status'], 'failed')     # no RSA key: honest failure
        self.assertEqual(result[('route_covered', '')]['status'], 'passed')   # tunnel destination via default

    def test_isakmp_uses_latest_output_after_traffic(self):
        first = cmd('show crypto isakmp sa', 'dst src state\n', '05.txt')
        later = cmd('show crypto isakmp sa', 'dst src state\n192.168.45.2 192.168.12.1 QM_IDLE 1 0 ACTIVE\n', '09.txt', phase='reachability')
        self.assertEqual(verify.evaluate({'kind': 'isakmp_sa', 'command': 'show crypto isakmp sa', 'peer': '192.168.45.2'}, [first, later])['status'], 'passed')

    def test_stp_root_expectations(self):
        root = "hostname ESW5\ninterface Vlan1\n no ip address\nspanning-tree vlan 1 priority 0\n"
        other = "hostname ESW1\ninterface Vlan1\n no ip address\n"
        root_out = [('show spanning-tree', 'VLAN1 is executing the ieee compatible Spanning Tree protocol\n  We are the root of the spanning tree\n')]
        other_out = [('show spanning-tree', 'VLAN0001\n  Spanning tree enabled protocol ieee\n  Root ID    Priority    0\n  Bridge ID  Priority    32768\n')]
        p = project([node('a', 'ESW5', root, root_out), node('b', 'ESW1', other, other_out)], [link('a', 'b')])
        features.apply(p)
        for n in p['nodes']:
            check = next(c for c in n['checks'] if c.get('criterion', {}).get('kind') == 'stp_root')
            self.assertEqual(check['status'], 'passed', n['name'])


class PingTests(unittest.TestCase):
    def test_ping_result_and_policy_handling(self):
        rule = {'kind': 'ping', 'target': '1.1.1.1', 'min_percent': 40, 'policy': False}
        ok = cmd('ping 1.1.1.1 repeat 5 timeout 1', 'Success rate is 80 percent (4/5)')
        bad = cmd('ping 1.1.1.1 repeat 5 timeout 1', 'Success rate is 0 percent (0/5)')
        self.assertEqual(verify.evaluate(rule, [ok], {'platform': 'ios'})['status'], 'passed')
        self.assertEqual(verify.evaluate(rule, [bad], {'platform': 'ios'})['status'], 'failed')
        self.assertEqual(verify.evaluate({**rule, 'policy': True}, [bad], {'platform': 'ios'})['status'], 'inconclusive')
        self.assertEqual(verify.evaluate(rule, [], {'platform': 'ios'})['status'], 'inconclusive')
        vpcs = cmd('ping 1.1.1.1 -c 5', '84 bytes from 1.1.1.1 icmp_seq=1 ttl=255 time=9 ms\n84 bytes from 1.1.1.1 icmp_seq=2 ttl=255 time=9 ms\n')
        self.assertEqual(verify.evaluate(rule, [vpcs], {'platform': 'vpcs'})['status'], 'passed')

    def test_reachability_pass_runs_planned_pings_only(self):
        sent = []

        class Console:
            prompt = 'R3>'
            wire = bytearray()

            def __init__(self, *args, **kwargs):
                pass

            def connect(self):
                pass

            def command(self, command):
                sent.append(command)
                if command == 'enable' or 'configure' in command:
                    raise AssertionError('Only read-only commands may be sent')
                return 'Success rate is 100 percent (5/5)\nR3>'

            def close(self):
                pass

        class API:
            host = '127.0.0.1'

            def call(self, path, method='GET', body=None):
                assert method == 'GET'
                return {'console': 5000, 'console_host': '127.0.0.1'}
        p = DeriveTests().build()
        p['project_id'] = '00000000-0000-0000-0000-000000000001'
        with tempfile.TemporaryDirectory() as temp:
            for n in p['nodes']:
                (Path(temp) / n['node_id']).mkdir()
            summary = reach.run(API(), p, Path(temp), console_factory=Console)
        self.assertGreater(summary['nodes'], 0)
        self.assertTrue(all(c.startswith('ping ') for c in sent))
        features.apply(p)
        pings = [c for c in p['nodes'][2]['checks'] if c.get('feature') == 'reachability']
        self.assertTrue(pings and all(c['status'] == 'passed' for c in pings))


class BuiltinTests(unittest.TestCase):
    def test_cloud_is_collected_and_excluded_from_verdict(self):
        cloud = {'node_type': 'cloud', 'properties': {'ports_mapping': [{'name': 'eth0', 'interface': 'VMware Network Adapter VMnet8'}]}}
        result = describe_builtin(cloud)
        self.assertEqual(result['collection'], 'collected')
        self.assertIn('VMnet8', result['reason'])
        router = {'node_type': 'dynamips', 'collection': 'collected', 'checks': [{'status': 'passed'}]}
        self.assertEqual(verdict([router, {'node_type': 'cloud', **result}]), 'passed')


if __name__ == '__main__':
    unittest.main()


class IPv6Tests(unittest.TestCase):
    def test_ipv6_redistribution_route_and_ping_plan(self):
        r1 = ("hostname R1\nipv6 unicast-routing\ninterface Serial1/0\n ipv6 address 2001:1:1:13::1/64\n ipv6 rip 100 enable\n"
              "interface Loopback0\n ipv6 address 2001:1:1:1::1/64\n ipv6 rip 100 enable\nipv6 router rip 100\n")
        r3 = ("hostname R3\nipv6 unicast-routing\ninterface Serial1/0\n ipv6 address 2001:1:1:13::2/64\n ipv6 rip 100 enable\n"
              "interface Serial1/1\n ipv6 address 2001:1:1:35::2/64\n ipv6 eigrp 100\nipv6 router eigrp 100\n redistribute rip 100 metric 1 1 1 1 1 include-connected\n"
              "ipv6 router rip 100\n redistribute eigrp 100 metric 10 include-connected\n")
        r5 = ("hostname R5\nipv6 unicast-routing\ninterface Serial1/1\n ipv6 address 2001:1:1:35::1/64\n ipv6 eigrp 100\n"
              "interface Loopback0\n ipv6 address 2001:1:1:5::1/64\n ipv6 eigrp 100\nipv6 router eigrp 100\n")
        route = ('show ipv6 route', 'IPv6 Routing Table - default - 6 entries\nEX  2001:1:1:1::/64 [170/2]\n     via FE80::3, Serial1/1\n'
                 'C   2001:1:1:35::/64 [0/0]\n')
        p = project([node('a', 'R1', r1), node('c', 'R3', r3), node('e', 'R5', r5, [route])], [link('a', 'c'), link('c', 'e')])
        features.apply(p)
        checks = {c['criterion'].get('network'): c['status'] for c in p['nodes'][2]['checks']
                  if c.get('criterion', {}).get('kind') == 'route6_covered'}
        self.assertEqual(checks.get('2001:1:1:1::/64'), 'passed')
        self.assertEqual(checks.get('2001:1:1:13::/64'), 'failed')       # not in the sample table
        targets = {r['target'] for r in features.plan_pings(p)['e']}
        self.assertIn('2001:1:1:1::1', targets)
        self.assertEqual(verify.ping_command({'target': '2001:1:1:1::1'}, 'ios'), 'ping ipv6 2001:1:1:1::1 repeat 5 timeout 1')
