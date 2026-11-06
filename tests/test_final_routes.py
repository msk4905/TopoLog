from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from topolog.final_routes import refresh
from topolog import preflight, verify
from test_preflight import API

class Console:
    prompt='F2#'
    wire=b'fixture'
    enable_changed=False
    enable_pending=False
    cancel=None
    def __init__(self,*args,**kwargs):self.sent=[]
    def connect(self):pass
    def close(self):pass
    def command(self,cmd):
        self.sent.append(cmd)
        if cmd=='show pager':return 'pager lines 23'
        if cmd=='show route':return 'O 172.16.1.0 255.255.255.0 [110/20] via 192.168.12.1'
        return 'F2#'

class FinalRoutesTests(unittest.TestCase):
    def run_fixture(self,console):
        class API:
            host='127.0.0.1'
            def call(self,path):return {'console':5000}
        node={'node_id':'n','platform':'asa','name':'F2','collection':'collected',
              'commands':[{'command':'show route','text':'C 192.168.12.0 255.255.255.0 is directly connected','status':'collected','file':'01.txt'}]}
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);(folder/'n').mkdir()
            refresh(API(),{'project_id':'p','nodes':[node]},folder,lambda *a,**k:console,lambda x:None,None,True,None,None,[])
        return node

    def test_final_observation_is_used_and_initial_evidence_retained(self):
        console=Console();node=self.run_fixture(console)
        self.assertTrue(node['commands'][0]['text'].startswith('C '))
        self.assertEqual(node['commands'][1]['phase'],'final_routing')
        result=verify.evaluate({'kind':'route_covered','command':'show route','address':'172.16.1.1'},node['commands'],node)
        self.assertEqual(result['status'],'passed')
        self.assertEqual(node['final_routing']['terminal']['paging'],'restored')
        self.assertEqual(console.sent.count('show route'),1)

    def test_missing_route_remains_failed(self):
        class Missing(Console):
            def command(self,cmd):
                if cmd=='show route':return 'C 192.168.12.0 255.255.255.0 is directly connected, inside'
                return 'pager lines 23' if cmd=='show pager' else 'F2#'
        node=self.run_fixture(Missing())
        self.assertEqual(verify.evaluate({'kind':'route_covered','command':'show route','address':'172.16.1.1'},node['commands'],node)['status'],'failed')

    def test_restore_rejection_is_not_success(self):
        class Rejected(Console):
            def command(self,cmd):return '% Invalid input detected at ^ marker.' if cmd=='terminal pager 23' else super().command(cmd)
        node=self.run_fixture(Rejected())
        self.assertEqual(node['collection'],'partial')
        self.assertEqual(node['final_routing']['status'],'cleanup_failed')

    def test_server_wait_uses_vm_budget(self):
        waits=[]
        def wait(condition,timeout,*args,**kwargs):waits.append(timeout);return API(vm_connected=False) if len(waits)==1 else True
        with patch('topolog.preflight._wait',side_effect=wait):
            preflight.prepare(API,True,is_running=lambda:False,find_executable=lambda:Path('gns3.exe'),start=lambda x:None)
        self.assertEqual(waits,[90,90])
