from contextlib import closing
from pathlib import Path
import json
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from topolog.resultdir import records as R  # noqa: E402
from topolog.parallel import completed,LockedAPI
from topolog.engine import Runner,obtain_privilege
from topolog import reach
import test_batch as fixtures


class ParallelTests(unittest.TestCase):
    def test_bound_two_and_cancel_drain_before_return(self):
        cancel=threading.Event();barrier=threading.Barrier(2);started=[];closed=[]
        def worker(i):
            started.append(i)
            try:
                barrier.wait(timeout=2);cancel.set()
                time.sleep(.02)
                return i
            finally:closed.append(i)
        with self.assertRaises(InterruptedError):
            with closing(completed(range(8),worker,workers=10,cancel=cancel)) as jobs:list(jobs)
        self.assertEqual(set(started),{0,1})
        self.assertEqual(set(closed),{0,1})

    def test_runner_parallel_sessions_preserve_all_records(self):
        barrier=threading.Barrier(2)
        def collect(api,node,folder,*args,**kwargs):
            barrier.wait(timeout=2)
            return fixtures.collected(api,node,folder,*args,**kwargs)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'input';root.mkdir();fixtures.project(root,'p',fixtures.P)
            result=Runner(fixtures.FakeAPI(),Path(temp)/'out',collector=collect,settle_seconds=0,reachability=False).run(root)
            nodes=result['projects'][0]['nodes']
            self.assertTrue(all(n['collection']=='collected' for n in nodes))
            self.assertEqual(len(nodes),2)
            for n in nodes:self.assertEqual(json.loads((R(Path(temp)/'out')/fixtures.P/n['node_id']/'collection.json').read_text())['collection'],'collected')

    def test_cancel_preserves_drained_worker_results_and_stops_after_exit(self):
        cancel=threading.Event();barrier=threading.Barrier(2);closed=set();lock=threading.Lock()
        class API(fixtures.FakeAPI):
            def call(self,path,*args,**kwargs):
                if path.endswith('/stop'):
                    with lock:self_test.assertEqual(len(closed),2)
                return super().call(path,*args,**kwargs)
        self_test=self
        def collect(api,node,folder,*args,**kwargs):
            barrier.wait(timeout=2)
            result=fixtures.collected(api,node,folder,*args,**kwargs)
            cancel.set();time.sleep(.02)
            with lock:closed.add(node['node_id'])
            return result
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'input';root.mkdir();fixtures.project(root,'p',fixtures.P)
            result=Runner(API(),Path(temp)/'out',collector=collect,settle_seconds=0,cancel=cancel,reachability=False).run(root)
            self.assertTrue(result['cancelled'])
            self.assertTrue(all(n['collection']=='collected' for n in result['projects'][0]['nodes']))

    def test_api_and_password_requests_do_not_overlap(self):
        lock=threading.Lock();active=[0];peak=[0]
        class API:
            def call(self):
                with lock:active[0]+=1;peak[0]=max(peak[0],active[0])
                time.sleep(.01)
                with lock:active[0]-=1
        api=LockedAPI(API())
        with closing(completed(range(4),lambda _:api.call())) as jobs:list(jobs)
        self.assertEqual(peak[0],1)
        passwords=[];prompts=[]
        class Console:
            enable_pending=False
            def enter_enable(self,value):return value=='fixture-secret'
        def provider(name):prompts.append(name);time.sleep(.01);return 'fixture-secret'
        with closing(completed(range(2),lambda i:obtain_privilege(Console(),str(i),password_provider=provider,known_passwords=passwords))) as jobs:
            self.assertTrue(all(result[0] for result in jobs))
        self.assertEqual(len(prompts),1)

    def test_ping_concurrency_evidence_isolation_and_all_sessions_closed(self):
        barrier=threading.Barrier(2);closed=[]
        nodes=[{'node_id':str(i),'name':'R'+str(i),'platform':'ios','collection':'collected','commands':[]} for i in range(2)]
        class API:
            host='127.0.0.1'
            def call(self,path):return {'console':5000+int(path[-1])}
        class Console:
            prompt='R1>';wire=b'fixture'
            def __init__(self,host,port,**kwargs):self.port=port
            def connect(self):pass
            def command(self,cmd):barrier.wait(timeout=2);return 'Success rate is 100 percent'
            def close(self):closed.append(self.port)
        plans={n['node_id']:[{'kind':'ping','target':'192.0.2.1'}] for n in nodes}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for n in nodes:(root/n['node_id']).mkdir()
            with patch('topolog.features.plan_pings',return_value=plans):
                result=reach.run(API(),{'project_id':'p','nodes':nodes},root,Console)
            self.assertEqual(result['nodes'],2)
            for n in nodes:self.assertEqual((root/n['node_id']/'01.txt').read_text(),'Success rate is 100 percent')
        self.assertEqual(set(closed),{5000,5001})


if __name__=='__main__':unittest.main()
