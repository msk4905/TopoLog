"""Expectation editor: automatic checks listed, switched off, changed, added; candidates from configurations."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from topolog import criteria_editor as editor, features, profiles  # noqa: E402

CONFIG = '''hostname R{n}
interface Loopback0
 ip address {n}.{n}.{n}.{n} 255.255.255.255
interface FastEthernet0/0
 ip address 10.0.12.{n} 255.255.255.0
router ospf 1
 router-id {n}.{n}.{n}.{n}
 network 0.0.0.0 255.255.255.255 area 0
'''


def lab(folder):
    nodes, links = [], [{'nodes': [{'node_id': 'a'}, {'node_id': 'b'}]}]
    for n, nid in ((1, 'a'), (2, 'b')):
        nodes.append({'node_id': nid, 'name': 'R%d' % n, 'node_type': 'dynamips', 'properties': {'dynamips_id': n}})
        config = folder / 'project-files' / 'dynamips' / nid / 'configs'
        config.mkdir(parents=True)
        (config / ('i%d_startup-config.cfg' % n)).write_text(CONFIG.format(n=n))
    (folder / 'lab.gns3').write_text(json.dumps({'project_id': 'p', 'name': 'lab', 'topology': {'nodes': nodes, 'links': links}}))
    return {'file': 'lab.gns3', 'name': 'lab', 'project_id': 'p'}


class EditorDataTests(unittest.TestCase):
    def test_a_project_never_collected_gets_checks_and_candidates_from_its_startup_configs(self):
        with tempfile.TemporaryDirectory() as temp:
            entry = lab(Path(temp))
            basis = editor.basis_from_startup(temp, entry)
        checks = basis['nodes']['a']['checks']
        self.assertTrue(any(c['kind'] == 'interface_address' and c['interface'] == 'Loopback0' for c in checks))
        self.assertTrue(any(c['kind'] == 'ospf_neighbors' and c['neighbors'] == ['2.2.2.2'] for c in checks))
        options = editor.candidates(basis, 'a')
        self.assertIn('Loopback0', options['interface'])
        self.assertIn('2.2.2.2', options['neighbors'])
        self.assertNotIn('1.1.1.1', options['neighbors'])               # its own router ID is not a neighbour
        self.assertIn('10.0.12.2', options['target'])
        self.assertIn('10.0.12.0/24', options['prefix'])

    def test_rows_show_switched_off_changed_and_added_checks(self):
        auto = [{'kind': 'interface_up', 'command': 'show ip interface brief', 'interface': 'Loopback0'},
                {'kind': 'ospf_neighbors', 'neighbors': ['2.2.2.2']}]
        keys = [features.rule_key(r) for r in auto]
        spec = {'disabled': [keys[0]], 'checks': [{'kind': 'ospf_neighbors', 'neighbors': ['3.3.3.3'], 'replaces': keys[1]},
                                                  {'kind': 'ping', 'target': '10.0.12.2', 'min_percent': 100.0}]}
        rows = editor.rows(spec, auto)
        self.assertEqual([r[1] for r in rows], ['제외', '바꿈', '추가'])
        self.assertEqual(rows[1][2]['neighbors'], ['3.3.3.3'])
        self.assertNotIn('replaces', rows[1][2])

    def test_new_rules_get_the_platform_command_and_typed_values(self):
        self.assertEqual(editor.new_rule('인터페이스 상태 up/up', {'interface': 'Gi0/0'}, 'asa'),
                         {'kind': 'interface_up', 'interface': 'Gi0/0', 'command': 'show interface ip brief'})
        self.assertEqual(editor.new_rule('VLAN 활성', {'vlan': '10'}, 'ios')['vlan'], 10)
        self.assertEqual(editor.new_rule('OSPF 이웃 FULL', {'neighbors': '2.2.2.2, 3.3.3.3'}, 'ios')['neighbors'], ['2.2.2.2', '3.3.3.3'])
        self.assertEqual(editor.new_rule('Ping 응답', {'target': '10.0.0.1', 'min_percent': ''}, 'ios')['min_percent'], 100.0)
        with self.assertRaises(ValueError):
            editor.new_rule('BGP 이웃 연결', {'address': ''}, 'ios')
        self.assertEqual(editor.parse_field('root', 'False', True), False)

    def test_only_read_only_show_commands_are_accepted_in_user_rules(self):
        good = {'kind': 'interface_up', 'command': 'show ip interface brief', 'interface': 'Lo0'}
        self.assertEqual(profiles.command_for(good, 'ios'), 'show ip interface brief')
        for command in ('configure terminal', 'show run | include x', 'show ip\nreload', 'reload'):
            with self.assertRaises(ValueError):
                profiles.command_for(dict(good, command=command), 'ios')

    def test_switched_off_checks_do_not_make_a_project_inconclusive(self):
        from topolog.engine import verdict
        nodes = [{'collection': 'collected', 'node_type': 'dynamips',
                  'checks': [{'status': 'passed'}, {'status': 'excluded'}]}]
        self.assertEqual(verdict(nodes), 'passed')


if __name__ == '__main__':
    unittest.main()
