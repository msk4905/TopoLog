from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from topolog import asa_diagnostics as diagnostics, verify, reporting, portfolio, features
import test_policy_and_names as fixtures
from test_final_routes import Console


class DiagnosticsTests(unittest.TestCase):
    def test_final_action_required(self):
        self.assertEqual(diagnostics.trace_result('Phase: 1\nResult: ALLOW')['action'],'unknown')
        self.assertEqual(diagnostics.trace_result('Phase: 1\nResult: ALLOW\nResult:\nAction: drop\nDrop-reason: (acl-drop) Flow denied')['action'],'drop')

    def test_only_new_exact_icmp_denies(self):
        flow={'input':'inside','reply_input':'outside','source':'10.0.12.1','destination':'3.3.3.3','reverse_supported':True}
        line='%ASA-4-106023: Deny icmp src inside:10.0.12.1 dst outside:3.3.3.3 (type 8, code 0) by access-group x'
        self.assertEqual(diagnostics.new_denies(line,line,flow),[])
        self.assertEqual(diagnostics.new_denies('',line,flow),[line])
        self.assertEqual(diagnostics.new_denies('',line.replace('3.3.3.3','3.3.3.4'),flow),[])
        self.assertEqual(diagnostics.new_denies('',line.replace('type 8','type 3'),flow),[])

    def test_command_validation(self):
        flow={'input':'inside','reply_input':'outside','source':'10.0.12.1','destination':'3.3.3.3'}
        self.assertEqual(diagnostics.trace_command(flow),'packet-tracer input inside icmp 10.0.12.1 8 0 3.3.3.3 detailed')
        self.assertIn('icmp 3.3.3.3 0 0 10.0.12.1',diagnostics.trace_command(flow,True))
        with self.assertRaises(ValueError):diagnostics.trace_command(dict(flow,input='inside\nreload'))

    def test_ping_reports_no_percent_and_preserve_result(self):
        rule={'kind':'ping','target':'1.1.1.1','min_percent':40}
        for rate,status,label in [(40,'passed','ping 성공'),(0,'failed','응답 없음'),(20,'failed','확인 필요')]:
            check=verify.evaluate(rule,[{'command':verify.ping_command(rule,'ios'),'file':'1.txt','status':'collected','text':f'Success rate is {rate} percent'}],{'platform':'ios'})
            self.assertEqual(check['status'],status)
            self.assertEqual(check['observed']['success_percent'],rate)
            self.assertEqual(reporting.ping_observation(check),label)
            self.assertEqual(portfolio._observed(check),label)
            self.assertNotIn('%',reporting.expectation(rule))

    def test_automatic_trace_reuses_exact_probe_and_restores_paging(self):
        entry=fixtures.PolicyNamesTests().build();entry['project_id']='p'
        plans=features.plan_pings(entry);source=entry['nodes'][0];asa=entry['nodes'][1]
        rule=next(r for r in plans['a'] if r['target']=='3.3.3.3')
        rule['source']=rule['diagnostic_flow']['source'];source['ping_sources']={rule['target']:rule['source']}
        command=verify.ping_command(rule,'ios')
        source['commands'].append({'command':command,'text':'Success rate is 0 percent','status':'collected','file':'02.txt'})
        asa['commands'].append({'command':'show logging','text':'','status':'collected','file':'02.txt'})
        class Trace(Console):
            def command(self,cmd):
                self.sent.append(cmd)
                if cmd=='show pager':return 'pager lines 23'
                if cmd.startswith('packet-tracer'):return 'Result:\nAction: drop\nDrop-reason: (acl-drop) Flow denied'
                return 'F2#'
        console=Trace()
        class API:
            host='127.0.0.1'
            def call(self,path):return {'console':5000}
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);(folder/'b').mkdir()
            diagnostics.run(API(),entry,folder,lambda *a,**k:console,lambda x:None,None,True,None,None,[])
            check=verify.evaluate(rule,source['commands'],source)
            self.assertEqual(check['status'],'inconclusive')
            self.assertIn('ASA 접근 규칙(ACL)이 요청을 차단함',check['reason'])
            self.assertEqual(check['asa_diagnostic']['request']['action'],'drop')
            for evidence in check['additional_evidence']:self.assertTrue((folder/evidence['node_id']/evidence['file']).is_file())
        self.assertIn('terminal pager 23',console.sent)
        self.assertLess(console.sent.index('show logging'),next(i for i,c in enumerate(console.sent) if c.startswith('packet-tracer')))
        self.assertFalse(any(c.startswith(('logging ','clear ','configure ')) for c in console.sent))

    def test_old_unsourced_ping_does_not_gain_synthetic_evidence(self):
        entry=fixtures.PolicyNamesTests().build()
        with patch('topolog.asa_diagnostics.features.plan_pings',return_value=features.plan_pings(entry)):
            diagnostics.run(None,entry,None,lambda *a,**k:self.fail('Unexpected ASA connection'),lambda x:None,None,True,None,None,[])


if __name__=='__main__':unittest.main()
