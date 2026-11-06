from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from topolog import naming,features,verify,profiles
from topolog.final_routes import refresh
from test_verify import node,project,link
from test_final_routes import Console

ASA='''hostname F2
interface Ethernet0/0
 nameif inside
 security-level 100
 ip address 10.0.12.2 255.255.255.0
interface Ethernet0/1
 nameif outside
 security-level 0
 ip address 10.0.23.2 255.255.255.0
router ospf 1
 router-id 2.2.2.2
 network 10.0.12.0 255.255.255.0 area 0
 network 10.0.23.0 255.255.255.0 area 0
'''
def router(name,address,loop):
    return f'''hostname {name}
interface Ethernet0/0
 ip address {address} 255.255.255.0
interface Loopback0
 ip address {loop} 255.255.255.255
router ospf 1
 router-id {loop}
 network 10.0.0.0 0.255.255.255 area 0
 network {loop} 0.0.0.0 area 0
'''

class PolicyNamesTests(unittest.TestCase):
    def build(self,asa=ASA):
        return project([node('a','R1',router('R1','10.0.12.1','1.1.1.1')),
                        node('b','F2',asa,platform='asa'),node('c','R3',router('R3','10.0.23.3','3.3.3.3'))],
                       [link('a','b'),link('b','c')])

    def test_near_interface_selected_and_far_interface_not_probed(self):
        plans=features.plan_pings(self.build())
        self.assertIn('10.0.12.2',{r['target'] for r in plans['a']})
        self.assertNotIn('10.0.23.2',{r['target'] for r in plans['a']})
        direct=next(r for r in plans['a'] if r['target']=='10.0.12.2')
        self.assertFalse(direct['policy'])

    def test_transit_policy_explains_both_directions(self):
        plans=features.plan_pings(self.build())
        forward=next(r for r in plans['a'] if r['target']=='3.3.3.3')
        reverse=next(r for r in plans['c'] if r['target']=='1.1.1.1')
        self.assertEqual(forward['policy_context']['classification'],'policy_limited')
        self.assertIn('ICMP',forward['policy_context']['reason'])
        self.assertIn('ACL',reverse['policy_context']['reason'])
        cmd=verify.ping_command(forward,'ios')
        result=verify.evaluate(forward,[{'command':cmd,'status':'collected','text':'Success rate is 0 percent','file':'01.txt'}],{'platform':'ios'})
        self.assertEqual(result['status'],'inconclusive')
        self.assertEqual(result['classification'],'policy_limited')

    def test_custom_access_rule_is_not_inferred_as_default_denial(self):
        plans=features.plan_pings(self.build(ASA+'access-group custom in interface outside\n'))
        rule=next(r for r in plans['a'] if r['target']=='3.3.3.3')
        self.assertEqual(rule['policy_context']['classification'],'policy_review')

    def test_nat_configuration_requires_policy_review(self):
        plans=features.plan_pings(self.build(ASA+'object network LAN\n subnet 10.0.12.0 255.255.255.0\n nat (inside,outside) dynamic interface\n'))
        rule=next(r for r in plans['a'] if r['target']=='3.3.3.3')
        self.assertEqual(rule['policy_context']['classification'],'policy_review')

    def test_collection_date_is_preserved_in_korea_time(self):
        names=naming.report_files({'started_at':'2026-11-04T16:00:00+00:00','assessment':{'assessed_at':'2030-01-01'}})
        self.assertEqual(names['word'],'report(2026-11-05).docx')
        self.assertEqual(names['record'],'report(2026-11-05).md')

    def test_result_name_collision_and_old_result_discovery(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            class Clock:
                @staticmethod
                def now(tz):return datetime(2026,11,5,10,0,0,tzinfo=tz)
            with patch('topolog.naming.datetime',Clock):
                first=naming.new_output(root);first.mkdir()
                second=naming.new_output(root)
            self.assertEqual(first.name,'TopoLog_수집결과_2026-11-05_10-00-00')
            self.assertTrue(second.name.endswith('_02'))
            old=root/'batch_20261104_fixture';old.mkdir()
            self.assertEqual(set(naming.result_folders(root)),{first,old})

    def test_final_ios_route_is_collected_and_original_is_retained(self):
        class API:
            host='127.0.0.1'
            def call(self,path):return {'console':5000}
        class IOS(Console):
            prompt='R1>'
            def command(self,cmd):
                self.sent.append(cmd)
                if cmd=='show ip route':return 'O 172.16.1.0/24 [110/20] via 192.168.12.1'
                return 'R1>'
        record={'node_id':'n','name':'R1','platform':'ios','collection':'collected','commands':[
            {'command':'show ip route','status':'collected','text':'C 192.168.12.0/24 is directly connected','file':'01.txt'}]}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'n').mkdir();console=IOS()
            refresh(API(),{'project_id':'p','nodes':[record]},root,lambda *a,**k:console,lambda x:None,None,False,None,None,[])
        self.assertEqual(record['final_routing']['status'],'completed')
        self.assertEqual(len(record['commands']),2)
        rule={'kind':'route_covered','command':'show ip route','address':'172.16.1.1'}
        self.assertEqual(verify.evaluate(rule,record['commands'],record)['status'],'passed')

    def test_latest_rejected_snapshot_does_not_fall_back_to_old_success(self):
        evidence=[{'command':'show ip route','status':'collected','text':'O 172.16.1.0/24 [110/20] via 192.168.12.1','file':'01.txt'},
                  {'command':'show ip route','status':'collected','text':'% Invalid input','cli_error':True,'file':'02.txt'}]
        rule={'kind':'route_covered','command':'show ip route','address':'172.16.1.1'}
        self.assertEqual(verify.evaluate(rule,evidence,{'platform':'ios'})['status'],'inconclusive')
