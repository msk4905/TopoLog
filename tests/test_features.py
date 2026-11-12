import copy
import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from topolog import features

def config(rid,address,area='0',extra=''):
    return ('interface Serial1/1\n ip address '+address+' 255.255.255.0\n!\n'
            'router ospf 1\n router-id '+rid+'\n network 10.0.0.0 0.0.0.255 area '+area+'\n'+extra)

def node(nid,text,neighbor=''):
    return {'node_id':nid,'node_type':'dynamips','platform':'ios','collection':'collected','checks':[],
            'commands':[{'command':'show running-config','status':'collected','file':'config.txt','text':text},
                        {'command':'show ip interface brief','status':'collected','file':'if.txt',
                         'text':'Serial1/1 '+('10.0.0.1' if nid=='a' else '10.0.0.2')+' YES manual up up'},
                        {'command':'show ip ospf neighbor','status':'collected','file':'ospf.txt','text':neighbor}]}

class FeatureTests(unittest.TestCase):
    def project(self,extra='',area='0.0.0.0'):
        return {'nodes':[node('a',config('1.1.1.1','10.0.0.1'), '2.2.2.2 0 FULL/- 00:30'),
                         node('b',config('2.2.2.2','10.0.0.2',area,extra),'1.1.1.1 0 FULL/- 00:30')],
                'topology_links':[{'nodes':[{'node_id':'a'},{'node_id':'b'}]}]}

    def test_no_manual_input_derives_neighbors_from_configuration(self):
        project=self.project()
        features.apply(project)
        for n in project['nodes']:
            self.assertTrue(n['checks'])
            self.assertTrue(all(c['status']=='passed' for c in n['checks']))
            self.assertTrue(any(c.get('criterion',{}).get('kind')=='ospf_neighbors' for c in n['checks']))

    def test_missing_observed_neighbor_is_not_used_as_expected(self):
        project=self.project()
        project['nodes'][0]['commands'][-1]['text']='Neighbor ID State\n'
        features.apply(project)
        check=next(c for c in project['nodes'][0]['checks'] if c.get('criterion',{}).get('kind')=='ospf_neighbors')
        self.assertEqual(check['criterion']['neighbors'],['2.2.2.2'])
        self.assertEqual(check['status'],'failed')

    def test_passive_area_mismatch_and_disconnected_links_are_conservative(self):
        for project in [self.project(extra=' passive-interface Serial1/1\n'),self.project(area='1')]:
            features.apply(project)
            self.assertFalse(any(c.get('criterion',{}).get('kind')=='ospf_neighbors' for c in project['nodes'][0]['checks']))
            self.assertTrue(any(c['status']=='unverified' for c in project['nodes'][0]['checks']))
        project=self.project();project['topology_links']=[];features.apply(project)
        self.assertFalse(any(c.get('criterion',{}).get('kind')=='ospf_neighbors' for c in project['nodes'][0]['checks']))

    def test_many_features_and_unknown_configuration_are_preserved(self):
        text=('vlan 10\ninterface FastEthernet0/1\n switchport mode trunk\n standby 1 ip 192.0.2.1\n'
              'spanning-tree mode pvst\nip dhcp pool CLIENTS\nip nat inside source list 1 interface FastEthernet0/0 overload\n'
              'access-list 1 permit any\ncrypto isakmp policy 10\nrouter eigrp 1\nrouter bgp 65001\ncustom-feature abc\n')
        analysis=features.analyze(text)
        for name in ('vlan','trunk','hsrp','stp','dhcp','nat','acl','vpn','eigrp','bgp','other'):
            self.assertIn(name,analysis['features'])
        self.assertIn('custom-feature',analysis['unclassified'])
        self.assertIn('show spanning-tree',features.commands(analysis))

    def test_vlan_trunk_hsrp_evaluation_uses_operational_output(self):
        rules=[({'kind':'vlan_active','command':'show vlan brief','vlan':10},'10 USERS active Fa0/1'),
               ({'kind':'trunk','command':'show interfaces trunk','interface':'FastEthernet0/1'},'Fa0/1 on 802.1q trunking 1'),
               ({'kind':'hsrp','command':'show standby brief','interface':'FastEthernet0/1','group':'1','virtual_ip':'192.0.2.1'},
                'Fa0/1 1 110 P Active local 192.0.2.3 192.0.2.1')]
        for rule,text in rules:
            evidence=[{'command':rule['command'],'status':'collected','file':'x.txt','text':text}]
            self.assertEqual(features.evaluate(rule,evidence,'ios')['status'],'passed')
            evidence[0]['text']=''
            self.assertEqual(features.evaluate(rule,evidence,'ios')['status'],'failed')

    def test_user_checks_are_added_to_automatic_ones_and_switched_off_ones_stay_visible(self):
        project=self.project()
        features.apply(project)
        automatic=[c['criterion'] for c in project['nodes'][0]['checks'] if c.get('criterion')]
        off,changed=automatic[0],automatic[1]
        edited=dict(changed,replaces=features.rule_key(changed))
        added={'kind':'contains','command':'show ip ospf neighbor','expected':['FULL']}
        features.apply(project,{'a':{'disabled':[features.rule_key(off)],'checks':[edited,added]}})
        checks=project['nodes'][0]['checks']
        by=lambda rule:[c for c in checks if c.get('criterion')==rule]
        self.assertEqual(by(off)[0]['status'],'excluded')
        self.assertIn('제외', by(off)[0]['reason'])
        self.assertEqual([c['status'] for c in by(changed)][0],'excluded')       # the automatic copy, replaced
        user=[c for c in checks if c.get('origin')=='user' and c['status']!='excluded']
        self.assertEqual(len(user),2)
        self.assertNotIn('replaces',user[0]['criterion'])
        self.assertTrue(project['nodes'][0]['automatic_checks'])

    def test_duplicate_router_ids_are_not_accepted_as_expected_neighbors(self):
        project=self.project()
        project['nodes'][1]['commands'][0]['text']=config('1.1.1.1','10.0.0.2')
        features.apply(project)
        self.assertFalse(any(c.get('criterion',{}).get('kind')=='ospf_neighbors' for c in project['nodes'][0]['checks']))
        self.assertTrue(any(c['status']=='unverified' for c in project['nodes'][0]['checks']))

    def test_rip_remote_prefix_requires_connected_domain(self):
        a='interface Serial1/1\n ip address 10.0.0.1 255.255.255.0\nrouter rip\n version 2\n no auto-summary\n network 10.0.0.0\n network 172.16.0.0\n'
        b='interface Serial1/1\n ip address 10.0.0.2 255.255.255.0\ninterface Loopback0\n ip address 172.16.1.1 255.255.255.0\nrouter rip\n version 2\n no auto-summary\n network 10.0.0.0\n network 172.16.0.0\n'
        project={'nodes':[node('a',a),node('b',b)],'topology_links':[{'nodes':[{'node_id':'a'},{'node_id':'b'}]}]}
        features.apply(project)
        checks=[c for c in project['nodes'][0]['checks'] if c.get('criterion',{}).get('kind')=='route']
        self.assertEqual(checks[0]['criterion']['prefix'],'172.16.1.0/24')
        self.assertEqual(checks[0]['status'],'inconclusive')

if __name__=='__main__':unittest.main()
