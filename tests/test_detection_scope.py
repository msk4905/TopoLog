"""Detection scope from the user's labs: banner/macro text, GRE vs VPN, 802.1Q sub-interfaces, PPP, OSPF area types."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from topolog import features, verify, reporting

NM16 = """hostname R1
!
interface FastEthernet0/0
 no ip address
!
interface FastEthernet0/0.12
 encapsulation dot1Q 12
 ip address 192.168.12.3 255.255.255.0
!
ip default-gateway 192.168.12.1
!
macro name add_vlan
end
vlan database
vlan $v
exit
@
!
banner exec ^C

***************************************************************
This is a normal Router with a Switch module inside (NM-16ESW)
To create vlans use the command "vlan database" in exec mode
After creating all desired vlans use "exit" to apply the config
***************************************************************
^C
!
line con 0
end
"""

GRE = """hostname R1
interface Tunnel15
 ip address 172.16.15.1 255.255.255.0
 tunnel source Serial1/1
 tunnel destination 192.168.45.2
!
interface Serial1/1
 ip address 192.168.12.1 255.255.255.0
 encapsulation ppp
!
router ospf 1
 area 10 stub no-summary
 area 20 nssa
 network 172.16.15.0 0.0.0.255 area 0
!
end
"""

SHOW_VLANS = """
Virtual LAN ID:  12 (IEEE 802.1Q Encapsulation)

   vLAN Trunk Interface:   FastEthernet0/0.12

   Protocols Configured:   Address:              Received:        Transmitted:
           IP              192.168.12.3                 0                  3
"""

SHOW_OSPF = """ Routing Process "ospf 1" with ID 1.1.1.1
    Area BACKBONE(0)
        Number of interfaces in this area is 1
    Area 10
        Number of interfaces in this area is 1
        It is a stub area, no summary LSA in this area
    Area 20
        Number of interfaces in this area is 1
        It is a NSSA area
"""

SHOW_SERIAL = """Serial1/1 is up, line protocol is up
  Internet address is 192.168.12.1/24
  Encapsulation PPP, LCP Open
  Open: IPCP, CDPCP, crc 16, loopback not set
"""


def collected(command, text):
    return {'command': command, 'text': text, 'status': 'collected', 'cli_error': False, 'file': '01.txt'}


class DetectionScopeTests(unittest.TestCase):
    def test_banner_and_macro_text_is_not_reported_as_other_configuration(self):
        analysis = features.analyze(NM16)
        self.assertEqual(analysis['unclassified'], [])
        self.assertNotIn('other', analysis['features'])
        self.assertIn('This is a normal Router', analysis['raw'])                    # raw text is kept as it was
        self.assertIn('dot1q', analysis['features'])

    def test_plain_gre_tunnel_is_gre_not_vpn(self):
        analysis = features.analyze(GRE)
        self.assertIn('gre', analysis['features'])
        self.assertNotIn('vpn', analysis['features'])
        with_ipsec = features.analyze(GRE.replace(' tunnel destination 192.168.45.2', ' tunnel destination 192.168.45.2\n tunnel protection ipsec profile P'))
        self.assertIn('vpn', with_ipsec['features'])

    def test_subinterface_vlan_ppp_link_and_ospf_area_types_are_checked(self):
        node = {'node_id': 'a', 'name': 'R1', 'platform': 'ios', 'commands': [
            collected('show running-config', NM16 + GRE),
            collected('show vlans', SHOW_VLANS), collected('show ip ospf', SHOW_OSPF),
            collected('show interfaces Serial1/1', SHOW_SERIAL)]}
        features.apply({'nodes': [node]})
        checks = {c['criterion']['kind'] + ':' + str(c['criterion'].get('interface') or c['criterion'].get('area')): c
                  for c in node['automatic_checks'] if c.get('criterion')}
        self.assertEqual(checks['dot1q_vlan:FastEthernet0/0.12']['status'], 'passed')
        self.assertEqual(checks['ppp_link:Serial1/1']['status'], 'passed')
        self.assertEqual(checks['ospf_area_type:10']['status'], 'passed')
        self.assertEqual(checks['ospf_area_type:10']['observed'], 'stub · summary LSA 없음')
        self.assertEqual(checks['ospf_area_type:20']['status'], 'passed')
        self.assertIn('show interfaces Serial1/1', features.commands(features.analyze(GRE)))
        self.assertIn('show vlans', features.commands(features.analyze(NM16)))
        self.assertEqual(reporting.expectation(checks['ospf_area_type:10']['criterion']),
                         'OSPF area 10 stub (no-summary) 확인 (show ip ospf)')

    def test_wrong_values_fail(self):
        node = {'node_id': 'a', 'name': 'R1', 'platform': 'ios', 'commands': [
            collected('show running-config', NM16 + GRE),
            collected('show vlans', SHOW_VLANS.replace('ID:  12', 'ID:  13')),
            collected('show ip ospf', SHOW_OSPF.replace('It is a stub area, no summary LSA in this area', '')),
            collected('show interfaces Serial1/1', SHOW_SERIAL.replace('LCP Open', 'LCP Closed'))]}
        features.apply({'nodes': [node]})
        failed = {c['criterion']['kind'] for c in node['automatic_checks'] if c.get('criterion') and c['status'] == 'failed'}
        self.assertTrue({'dot1q_vlan', 'ppp_link', 'ospf_area_type'} <= failed)

    def test_subinterface_status_is_checked_when_its_parent_port_is_linked(self):
        project = {'nodes': [{'node_id': 'a', 'platform': 'ios'}, {'node_id': 'b', 'platform': 'ios'}],
                   'topology_ports': {'a': [{'adapter_number': 0, 'port_number': 0, 'name': 'FastEthernet0/0'}],
                                      'b': [{'adapter_number': 0, 'port_number': 0, 'name': 'FastEthernet0/0'}]},
                   'topology_links': [{'nodes': [{'node_id': 'a', 'adapter_number': 0, 'port_number': 0},
                                                 {'node_id': 'b', 'adapter_number': 0, 'port_number': 0}]}]}
        parsed = {'a': verify.parse(NM16, 'ios')}
        rules, _ = verify.derive(project, parsed, lambda x, y: True)
        ups = [r['interface'] for _, r, _ in rules['a'] if r['kind'] == 'interface_up']
        self.assertIn('FastEthernet0/0.12', ups)

    def test_totally_nssa_passes_on_area_type_because_ios_does_not_print_no_summary_for_it(self):
        config = 'router ospf 1\n area 10 nssa no-summary\n'
        node = {'node_id': 'a', 'name': 'R4', 'platform': 'ios', 'commands': [
            collected('show running-config', config),
            collected('show ip ospf', '    Area 10\n\tIt is a NSSA area\n        Perform type-7/type-5 LSA translation\n')]}
        features.apply({'nodes': [node]})
        check = next(c for c in node['automatic_checks'] if c.get('criterion', {}).get('kind') == 'ospf_area_type')
        self.assertEqual(check['status'], 'passed')
        self.assertIn('area 종류만 확인', check['observed'])

    def test_marker_glued_to_a_console_log_message_still_cuts_the_echo(self):
        text = ('show running-config\nBuilding configuration...\nm\n*Nov 14 15:12:04.215: %LINK-5-CHANGED: Interface Serial1/0, '
                'changed state to administratively downCurrent configuration : 1598 bytes\n!\nversion 12.4\nhostname R2\nend\n')
        self.assertEqual(features.analyze(text)['unclassified'], [])
        self.assertEqual(verify.parse(text)['hostname'], 'R2')

    def test_network_of_an_unlinked_port_is_not_expected_on_other_routers(self):
        r1 = 'hostname R1\ninterface FastEthernet0/0\n ip address 10.0.12.1 255.255.255.0\nrouter ospf 1\n network 10.0.0.0 0.255.255.255 area 0\n'
        r3 = ('hostname R3\ninterface FastEthernet0/0\n ip address 10.0.12.3 255.255.255.0\n'
              'interface Serial1/1\n ip address 10.0.34.1 255.255.255.0\nrouter ospf 1\n network 10.0.0.0 0.255.255.255 area 0\n')
        port = lambda name: [{'adapter_number': 0, 'port_number': 0, 'name': 'FastEthernet0/0'},
                             {'adapter_number': 1, 'port_number': 1, 'name': name}]
        project = {'nodes': [{'node_id': 'a', 'platform': 'ios', 'name': 'R1'}, {'node_id': 'b', 'platform': 'ios', 'name': 'R3'}],
                   'topology_ports': {'a': port('Serial1/1'), 'b': port('Serial1/1')},
                   'topology_links': [{'nodes': [{'node_id': 'a', 'adapter_number': 0, 'port_number': 0},
                                                 {'node_id': 'b', 'adapter_number': 0, 'port_number': 0}]}]}
        parsed = {'a': verify.parse(r1, 'ios'), 'b': verify.parse(r3, 'ios')}
        rules, _ = verify.derive(project, parsed, lambda x, y: True)
        networks = [r.get('network') for _, r, _ in rules['a'] if r['kind'] == 'route_covered']
        self.assertNotIn('10.0.34.0/24', networks)        # R3 Serial1/1 has no GNS3 link, so it goes down


if __name__ == '__main__':
    unittest.main()
