from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from topolog import reach,verify

class PingRetryTests(unittest.TestCase):
    def run_ping(self,responses,policy=False,cancel=None):
        node={'node_id':'n','name':'R1','platform':'ios','collection':'collected','commands':[]}
        rule={'kind':'ping','target':'192.0.2.1','policy':policy}
        entry={'project_id':'p','nodes':[node]}
        class API:
            host='127.0.0.1'
            def call(self,path):return {'console':5000}
        class Console:
            prompt='R1>'
            wire=b''
            def __init__(self,*a,**k):self.responses=iter(responses)
            def connect(self):pass
            def close(self):pass
            def command(self,cmd):
                value=next(self.responses)
                if isinstance(value,Exception):raise value
                return 'Success rate is '+str(value)+' percent (0/5)' if isinstance(value,int) else value
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'n').mkdir()
            with patch('topolog.features.plan_pings',return_value={'n':[rule]}),patch('topolog.reach.time.sleep') as wait:
                try:reach.run(API(),entry,root,Console,cancel=cancel,retry_delay=5)
                except (TimeoutError,InterruptedError):pass
            return node,verify.evaluate(rule,node['commands'],node),wait.call_count

    def test_first_failure_then_success_preserves_both(self):
        node,result,wait=self.run_ping([0,100])
        self.assertEqual(result['status'],'passed')
        self.assertEqual([x['attempt'] for x in node['commands']],[1,2])
        self.assertEqual(len(result['observed']['attempts']),2)
        self.assertEqual(wait,1)

    def test_success_does_not_retry(self):
        node,result,wait=self.run_ping([100])
        self.assertEqual(len(node['commands']),1)
        self.assertEqual(wait,0)

    def test_still_failing_is_bounded(self):
        node,result,wait=self.run_ping([0,0])
        self.assertEqual(result['status'],'failed')
        self.assertEqual(len(node['commands']),2)

    def test_policy_failure_remains_inconclusive_after_retry(self):
        node,result,wait=self.run_ping([0,0],True)
        self.assertEqual(result['status'],'inconclusive')
        self.assertEqual(len(node['commands']),2)

    def test_timeout_attempt_is_recorded_and_no_old_result_used(self):
        node,result,wait=self.run_ping([0,TimeoutError('fixture')])
        self.assertEqual(result['status'],'inconclusive')
        self.assertEqual(node['commands'][-1]['status'],'failed')
        self.assertEqual(node['reachability']['status'],'failed')

    def test_cancel_during_wait_does_not_send_second_ping(self):
        class Cancellation:
            def is_set(self):return False
            def wait(self,seconds):return True
        node,result,wait=self.run_ping([0],cancel=Cancellation())
        self.assertEqual(node['reachability']['status'],'cancelled')
        self.assertEqual(len(node['commands']),1)
