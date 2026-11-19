"""Regressions for observed command formats and evidence scope."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from topolog import features, profiles, verify


def sample(command, text):
    return {'command': command, 'status': 'collected', 'text': text, 'file': '01.txt'}


class EvidenceRegressions(unittest.TestCase):
    def test_ike_prefers_established_row_and_rejects_deleted_inactive_and_other_peer(self):
        rule = {'kind': 'isakmp_sa', 'command': 'show crypto isakmp sa', 'peer': '192.168.12.1'}
        incomplete = '''IPv4 Crypto ISAKMP SA
192.168.12.1 192.168.45.2 MM_NO_STATE 0 ACTIVE (deleted)
192.168.45.2 192.168.12.1 MM_SA_SETUP 0 ACTIVE
192.168.45.2 192.168.12.1 QM_IDLE 0 INACTIVE
192.168.45.2 192.168.12.10 QM_IDLE 1 ACTIVE
'''
        established = '192.168.45.2 192.168.12.1 QM_IDLE 1001 ACTIVE'
        self.assertEqual(verify.evaluate(rule, [sample(rule['command'], incomplete)])['status'], 'failed')
        result = verify.evaluate(rule, [sample(rule['command'], incomplete + established)])
        self.assertEqual((result['status'], result['observed']), ('passed', established))

    def test_ipv6_address_matches_only_the_requested_interface(self):
        rule = {'kind': 'ipv6_address', 'command': 'show ipv6 interface brief',
                'interface': 'Se1/0', 'address': '2001:db8::1'}
        text = '   Serial1/0 [UP/up]\n     unassigned\nTunnel0 [up/up]\n   2001:0DB8:0:0:0:0:0:1/64\nR1#'
        item = sample(rule['command'], text)
        self.assertEqual(verify.evaluate(rule, [item])['status'], 'failed')
        self.assertEqual(verify.evaluate({**rule, 'interface': 'Tu0'}, [item])['status'], 'passed')
        self.assertEqual(verify.interface_states([item])[(6, 'se1/0')], ('up', 'up'))
        self.assertEqual(verify.evaluate(rule, [sample(rule['command'], 'Output unavailable')])['status'], 'inconclusive')

    def test_ipsec_requires_exact_peer_and_nonzero_spi_in_a_matching_block(self):
        rule = {'kind': 'ipsec_sa', 'command': 'show crypto ipsec sa', 'peer': '192.0.2.1'}
        incomplete = '''current_peer 192.0.2.10 port 500
 inbound esp sas:
  spi: 0x1234
current_peer 192.0.2.1 port 500
 outbound ah sas:
  spi: 0x0
'''
        self.assertEqual(verify.evaluate(rule, [sample(rule['command'], incomplete)])['status'], 'failed')
        later = 'current_peer 192.0.2.1 port 500\n #pkts encaps: 2\n inbound ah sas:\n  spi: 0x5678\n'
        result = verify.evaluate(rule, [sample(rule['command'], incomplete + later)])
        self.assertEqual((result['status'], result['observed']), ('passed', 'spi 0x5678 · encaps 2'))

    def test_etherswitch_stp_state_from_saved_console_format(self):
        fixture = Path(__file__).parent / 'fixtures' / 'command_compatibility' / 'stp_real.txt'
        rule = {'kind': 'stp_instance', 'command': 'show spanning-tree', 'vlan': 1}
        item = sample(rule['command'], fixture.read_text(encoding='utf-8'))
        self.assertEqual(features.evaluate(rule, [item], 'ios')['status'], 'passed')
        self.assertEqual(features.evaluate({**rule, 'vlan': 11}, [item], 'ios')['status'], 'failed')
        unknown = sample(rule['command'], 'unsupported format')
        self.assertEqual(features.evaluate(rule, [unknown], 'ios')['status'], 'inconclusive')

    def test_manual_ping_uses_the_same_command_for_collection_and_evaluation(self):
        for platform, target, command in (
            ('asa', '192.0.2.1', 'ping 192.0.2.1'),
            ('ios', '2001:0DB8:0:0:0:0:0:1', 'ping ipv6 2001:db8::1 repeat 5 timeout 1'),
        ):
            with self.subTest(platform=platform):
                rule = {'kind': 'ping', 'target': target, 'min_percent': 100}
                self.assertEqual(profiles.command_for(rule, platform), command)
                node = {'platform': platform, 'commands': [sample(command, 'Success rate is 100 percent (5/5)')]}
                self.assertEqual(features.evaluate_user(rule, node)['status'], 'passed')
        with self.assertRaises(ValueError):
            profiles.command_for({'kind': 'ping', 'target': 'fe80::1%Ethernet'}, 'ios')
