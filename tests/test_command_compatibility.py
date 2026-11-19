"""Synthetic console fixtures except files explicitly marked as saved real output."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from topolog import command_evidence, features, profiles, reporting, services, verify
from topolog.engine import collect_node

FIXTURES = Path(__file__).parent / 'fixtures' / 'command_compatibility'
VLAN = 'VLAN Name                             Status    Ports\n---- -------------------------------- --------- ------\n1    default                          active    Fa1/0\n12   VLAN0012                         active    Fa1/1\n'
CONFIG = 'hostname arbitrary\ninterface FastEthernet1/0\n switchport mode access\n switchport access vlan 12\n!\ninterface Vlan1\n no ip address\n!\n'


def record(command, text, **extra):
    return dict(command=command, text=text, file='01.txt', status='collected', **extra)


class CommandCompatibilityTests(unittest.TestCase):
    def collect(self, replies, config=CONFIG, checks=()):
        class Console:
            wire=b'SYNTHETIC console flow'; prompt='arbitrary#'
            def __init__(self,*a,**k): self.sent=[]
            def connect(self): pass
            def close(self): pass
            def command(self,cmd):
                self.sent.append(cmd)
                if cmd=='show version': return 'Cisco IOS Software, SYNTHETIC\narbitrary#'
                if cmd=='show running-config': return config+'arbitrary#'
                if cmd=='show terminal': return 'Length: 24 lines, Width: 80 columns\narbitrary#'
                if cmd=='show startup-config': return config+'arbitrary#'
                return replies.get(cmd,'arbitrary#')
        class API:
            host='127.0.0.1'
            def raw(self,path): raise ValueError('Synthetic unavailable API startup file')
        node=dict(node_id='00000000-0000-0000-0000-000000000001',project_id='00000000-0000-0000-0000-000000000002',node_type='dynamips',name='arbitrary',console_type='telnet',console=5001)
        console=Console()
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)/'node'
            result=collect_node(API(),node,folder,{'checks':list(checks)},[],console_factory=lambda *a,**k:console)
            for c in result['commands']:
                self.assertEqual((folder/c['file']).read_text(encoding='utf-8'),c['text'])
        return result,console.sent

    def vlan_check(self, result, vlan=12):
        return features.evaluate({'kind':'vlan_active','command':'show vlan brief','vlan':vlan},result['commands'],'ios')

    def test_standard_vlan_output_does_not_send_fallback(self):
        result,sent=self.collect({'show vlan brief':VLAN})
        self.assertNotIn('show vlan-switch brief',sent)
        self.assertEqual(self.vlan_check(result)['status'],'passed')

    def test_real_ambiguous_reply_falls_back_and_preserves_both_files(self):
        error=(FIXTURES/'vlan_ambiguous_real.txt').read_text(encoding='utf-8')
        result,sent=self.collect({'show vlan brief':error,'show vlan-switch brief':VLAN})
        self.assertEqual(sent.count('show vlan-switch brief'),1)
        first=next(c for c in result['commands'] if c['command']=='show vlan brief')
        fallback=next(c for c in result['commands'] if c['command']=='show vlan-switch brief')
        self.assertEqual(first['text'],error);self.assertTrue(first['cli_error'])
        self.assertEqual(fallback['fallback_for'],'show vlan brief')
        check=self.vlan_check(result)
        self.assertEqual(check['status'],'passed')
        self.assertEqual(check['command'],'show vlan-switch brief')
        self.assertEqual(check['evidence'],fallback['file'])
        rule={'kind':'vlan_active','command':'show vlan brief','vlan':12}
        result['checks']=[{'criterion':rule,**check}]
        html=reporting.render({'projects':[{'project_id':'project','name':'synthetic','status':'completed','nodes':[result]}]},[])
        self.assertIn('VLAN 12 활성 (show vlan-switch brief)',html)
        self.assertIn('/'+fallback['file']+'"',html)
        self.assertEqual(rule['command'],'show vlan brief')

    def test_user_required_vlan_probe_can_be_satisfied_by_alternative(self):
        rule={'kind':'vlan_active','command':'show vlan brief','vlan':12}
        result,_=self.collect({'show vlan brief':'% Ambiguous command','show vlan-switch brief':VLAN},config='hostname arbitrary\n',checks=[rule])
        self.assertEqual(result['collection'],'collected')
        self.assertEqual(result['checks'][0]['status'],'passed')

    def test_both_commands_rejected_are_inconclusive(self):
        result,sent=self.collect({c:'% Invalid input detected at ^ marker.\narbitrary#' for c in command_evidence.VLAN_COMMANDS})
        check=self.vlan_check(result)
        self.assertEqual(check['status'],'inconclusive')
        self.assertEqual(check['response_state'],'unsupported')
        self.assertEqual(check['command'],'show vlan-switch brief')
        self.assertEqual(len([c for c in sent if c in command_evidence.VLAN_COMMANDS]),2)

    def test_empty_primary_response_tries_alternative(self):
        result,_=self.collect({'show vlan brief':'arbitrary#','show vlan-switch brief':VLAN})
        self.assertEqual(self.vlan_check(result)['status'],'passed')

    def test_authorization_failure_does_not_trigger_alias_probe(self):
        result,sent=self.collect({'show vlan brief':'% Authorization failed.\narbitrary#'})
        self.assertNotIn('show vlan-switch brief',sent)
        self.assertEqual(self.vlan_check(result)['response_state'],'cli_error')

    def test_alias_only_legacy_json_is_accepted_without_new_metadata(self):
        result={'commands':[record('show vlan-switch brief',VLAN)]}
        self.assertEqual(self.vlan_check(result)['status'],'passed')
        self.assertEqual(self.vlan_check(result,13)['status'],'failed')

    def test_newer_failure_never_reuses_old_vlan_success(self):
        result={'commands':[record('show vlan-switch brief',VLAN),record('show vlan brief','% Ambiguous command')]}
        self.assertEqual(self.vlan_check(result)['status'],'inconclusive')

    def test_error_text_cannot_pass_when_cli_error_flag_is_absent(self):
        item=record('show ip interface brief','% Invalid command\nLoopback0 192.0.2.1 YES manual up up')
        result=verify.evaluate({'kind':'interface_up','command':item['command'],'interface':'Loopback0'},[item])
        self.assertEqual(result['status'],'inconclusive')
        self.assertEqual(profiles.evaluate({'kind':'contains','command':item['command'],'expected':['up']},[item],'ios')['status'],'inconclusive')

    def test_default_stp_and_vlan_database_are_collected_from_switching_config(self):
        for config in (CONFIG,'interface Vlan1\n no ip address\n','interface FastEthernet1/0\n switchport mode trunk\n'):
            with self.subTest(config=config):
                commands=features.commands(features.analyze(config))
                self.assertIn('show spanning-tree',commands)
                self.assertIn('show vlan brief',commands)
        self.assertNotIn('show spanning-tree',features.commands(features.analyze('interface FastEthernet0/0\n ip address 192.0.2.1 255.255.255.0\n')))

    def test_cross_switch_stp_root_checks_have_collection_commands(self):
        nodes=[]
        for number in range(1,6):
            cfg=CONFIG+('spanning-tree vlan 1 priority 0\n' if number==5 else '')
            nodes.append(dict(node_id=str(number),name='unrelated-'+str(number),platform='ios',commands=[record('show running-config',cfg)]))
        project={'nodes':nodes,'topology_links':[]}
        features.apply(project)
        for node in project['nodes']:
            stp=[c['criterion'] for c in node['checks'] if c.get('criterion',{}).get('kind')=='stp_root']
            self.assertTrue(stp)
            for rule in stp:self.assertIn(rule['command'],features.commands(node['analysis']))

    def test_real_acl_double_space_is_parsed_and_scope_is_preserved(self):
        text=(FIXTURES/'acl_interface_real.txt').read_text(encoding='utf-8')
        rule=dict(kind='acl_binding',command='show ip interface',interface='Serial1/1',acl='100',direction='in')
        self.assertEqual(services.evaluate(rule,text),(True,'100'))
        self.assertEqual(services.evaluate({**rule,'acl':'101'},text)[0],False)
        self.assertEqual(services.evaluate({**rule,'interface':'Missing0'},text)[0],None)

    def test_acl_whitespace_case_and_interface_boundaries(self):
        for gap in (' ','  ','\t',' \t '):
            text='FastEthernet0/0 is up, line protocol is up\n  inBOUND'+gap+'access'+gap+'list'+gap+'is'+gap+'100\nFastEthernet0/1 is up, line protocol is up\n  Inbound access list is 101\n'
            rule=dict(kind='acl_binding',interface='FastEthernet0/0',acl='100',direction='in')
            self.assertEqual(services.evaluate(rule,text),(True,'100'))
            self.assertEqual(services.evaluate({**rule,'direction':'out'},text)[0],None)

    def test_unknown_vlan_output_is_not_treated_as_missing_vlan(self):
        result={'commands':[record('show vlan brief','unrecognized format\narbitrary#')]}
        self.assertEqual(self.vlan_check(result)['status'],'inconclusive')

    def test_saved_real_stp_output_is_still_read(self):
        text=(FIXTURES/'stp_real.txt').read_text(encoding='utf-8')
        result=verify.evaluate({'kind':'stp_root','command':'show spanning-tree','vlan':1,'root':True},[record('show spanning-tree',text)])
        self.assertEqual(result['status'],'passed')

    def test_live_vlan_switch_output_is_used_as_the_check_evidence(self):
        text=(FIXTURES/'vlan_switch_live.txt').read_text(encoding='utf-8')
        result={'commands':[record('show vlan brief','% Ambiguous command'),
                            record('show vlan-switch brief',text)]}
        check=self.vlan_check(result)
        self.assertEqual(check['status'],'passed')
        self.assertEqual(check['command'],'show vlan-switch brief')

    def test_alternative_order_respects_the_requested_command_and_driver(self):
        self.assertEqual(command_evidence.alternatives('show vlan-switch brief','ios'),
                         ('show vlan-switch brief','show vlan brief'))
        for driver in ('asa','vpcs','unknown'):
            self.assertEqual(command_evidence.alternatives('show vlan brief',driver),('show vlan brief',))

    def test_requested_etherswitch_command_can_fall_back_to_standard_ios(self):
        rule={'kind':'vlan_active','command':'show vlan-switch brief','vlan':12}
        result,sent=self.collect({'show vlan-switch brief':'% Invalid input detected at ^ marker.',
                                  'show vlan brief':VLAN},config='hostname arbitrary\n',checks=[rule])
        self.assertEqual(result['collection'],'collected')
        self.assertEqual(result['checks'][0]['status'],'passed')
        self.assertEqual(result['checks'][0]['command'],'show vlan brief')
        self.assertEqual([c for c in sent if c in command_evidence.VLAN_COMMANDS],
                         ['show vlan-switch brief','show vlan brief'])
