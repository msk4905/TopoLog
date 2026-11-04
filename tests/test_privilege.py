import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from topolog.transport import Console
from topolog.engine import collect_node
from test_batch import ScriptedSocket, FakeAPI, FakeConsole, N

class AuthSocket(ScriptedSocket):
    def sendall(self,data):
        if data==b'\x03':self.sent.append(data);self.pending.append(b'\r\nasa>')
        else:super().sendall(data)

class PrivilegeTests(unittest.TestCase):
    def test_password_is_requested_only_after_blank_enable_fails(self):
        class Prompted(FakeConsole):
            attempts=[]
            prompt='R1>'
            enable_changed=False
            def enter_enable(self,password=None):
                self.attempts.append(password)
                self.enable_changed=password=='fixture-secret'
                if self.enable_changed:self.prompt='R1#'
                return self.enable_changed
            def restore_privilege(self):
                self.prompt='R1>';self.enable_changed=False
        for supplied,outcome,attempts in [('fixture-secret','collected',[None,'fixture-secret']),(None,'requires_privilege',[None])]:
            Prompted.attempts=[]
            with self.subTest(supplied=bool(supplied)),tempfile.TemporaryDirectory() as temp:
                api=FakeAPI()
                with patch('builtins.input',side_effect=AssertionError('Unexpected unmasked input')):
                    from unittest.mock import Mock
                    provider=Mock(return_value=supplied)
                    result=collect_node(api,api.node(N),Path(temp)/'n',{},[],console_factory=Prompted,
                                        enable=True,password_provider=provider)
                self.assertEqual(result['collection'],outcome)
                self.assertEqual(Prompted.attempts,attempts)
                provider.assert_called_once_with(api.node(N)['name'])
                self.assertNotIn('fixture-secret',json.dumps(result))

    def test_asa_login_history_is_not_an_authentication_prompt(self):
        sock=AuthSocket({'':[b'User enable_1 logged in to F2\r\nFailed logins since the last login:',b' 0\r\nasa>']})
        console=self.console(sock)
        self.assertEqual(console.prompt,'asa>')

    def test_cancel_during_enable_aborts_pending_password_without_retry(self):
        event=threading.Event()
        class CancelSocket(AuthSocket):
            def sendall(self,data):
                super().sendall(data)
                if data==b'enable\r\n':event.set()
        sock=CancelSocket({'':[b'asa>'],'enable':[b'Password: ']})
        console=self.console(sock);console.cancel=event
        with self.assertRaises(InterruptedError):console.enter_enable('not-sent')
        console.restore_privilege()
        self.assertEqual(console.prompt,'asa>')
        self.assertNotIn(b'not-sent\r\n',sock.sent)
        self.assertFalse(console.enable_pending)
    def console(self,sock):
        console=Console('127.0.0.1',5000,command_timeout=.05)
        with patch('topolog.transport.socket.create_connection',return_value=sock):console.connect()
        return console

    def test_blank_enable_once_and_original_prompt_restored(self):
        sock=AuthSocket({'':[b'asa>'],'enable':[b'Password: '],'disable':[b'asa>']})
        console=self.console(sock)
        sock.responses['']=[b'asa#']
        self.assertTrue(console.enter_enable())
        console.restore_privilege()
        self.assertEqual(console.prompt,'asa>')
        self.assertEqual(sock.sent.count(b'enable\r\n'),1)
        self.assertNotIn(b'Password:',console.wire)

    def test_wrong_password_not_retried_and_auth_bytes_omitted(self):
        sock=AuthSocket({'':[b'asa>'],'enable':[b'Password: '],'fixture-secret':[b'fixture-secret\r\nPassword: ']})
        console=self.console(sock)
        self.assertFalse(console.enter_enable('fixture-secret'))
        self.assertEqual(sock.sent.count(b'fixture-secret\r\n'),1)
        # Remaining device attempts are answered with empty input, never the secret again.
        self.assertEqual(sock.sent[sock.sent.index(b'fixture-secret\r\n')+1],b'\r\n')
        self.assertEqual(console.prompt,'asa>')
        self.assertFalse(console.enable_pending)
        self.assertNotIn(b'fixture-secret',console.wire)

    def test_new_enable_password_setup_is_not_answered(self):
        from topolog.transport import EnableSetupRequired
        sock=AuthSocket({'':[b'asa>'],'enable':[b'The enable password is not set.  Please set it now.\r\nEnter  Password: ']})
        console=self.console(sock)
        with self.assertRaises(EnableSetupRequired):console.enter_enable('fixture-secret')
        self.assertNotIn(b'fixture-secret\r\n',sock.sent)
        self.assertIn(b'\x03',sock.sent)
        self.assertEqual(console.prompt,'asa>')

    def test_no_password_prompt_and_existing_privilege(self):
        sock=AuthSocket({'':[b'asa>'],'enable':[b'asa#'],'disable':[b'asa>']})
        console=self.console(sock)
        self.assertTrue(console.enter_enable())
        self.assertTrue(console.enter_enable())
        self.assertEqual(sock.sent.count(b'enable\r\n'),1)
        console.restore_privilege()

    def test_asa_collection_and_startup_fallback_hide_secret(self):
        responses={'':[b'asa>'],'show version':[b'Cisco Adaptive Security Appliance Software Version 9.9\r\nasa>'],
                   'enable':[b'Password: '],'fixture-secret':[b'fixture-secret\r\nasa#'],'disable':[b'asa>'],
                   'show running-config':[b'ASA Version 9.9\r\nhostname ASA\r\nasa#'],
                   'show startup-config':[b'ASA Version 9.9\r\nhostname ASA\r\nasa#'],
                   'show pager':[b'pager lines 24\r\nasa#'],'terminal pager 0':[b'asa#'],'terminal pager 24':[b'asa#']}
        for cmd in ('show interface ip brief','show route','show access-list','show nat'):
            responses[cmd]=[b'example operational output\r\nasa#']
        sock=AuthSocket(responses)
        with tempfile.TemporaryDirectory() as temp:
            api=FakeAPI();node={**api.node(N),'node_type':'qemu'}
            with patch('topolog.transport.socket.create_connection',return_value=sock):
                result=collect_node(api,node,Path(temp)/'asa',{},[],enable=True,enable_password='fixture-secret')
            self.assertEqual(result['collection'],'collected')
            self.assertEqual(result['privilege']['restoration'],'restored')
            self.assertEqual(result['startup_configuration']['source'],'console')
            self.assertEqual(result['terminal']['paging'],'restored')
            self.assertIn(b'terminal pager 0\r\n',sock.sent)
            self.assertIn(b'terminal pager 24\r\n',sock.sent)
            self.assertNotIn('fixture-secret',json.dumps(result))
            self.assertNotIn(b'fixture-secret',(Path(temp)/'asa/session.rx.bin').read_bytes())

    def test_startup_fallback_rejects_missing_configuration(self):
        class API(FakeAPI):
            def raw(self,*args):raise OSError('fixture unavailable API file')
        class Missing(FakeConsole):
            responses={**FakeConsole.responses,'show startup-config':'startup-config is not present\nR1#'}
        with tempfile.TemporaryDirectory() as temp:
            api=API();result=collect_node(api,api.node(N),Path(temp)/'n',{},[],console_factory=Missing)
            self.assertEqual(result['startup_configuration']['status'],'not_collected')
            self.assertFalse((Path(temp)/'n/startup.cfg').exists())

    def test_control_char_password_does_not_send_enable(self):
        sock=AuthSocket({'':[b'asa>']});console=self.console(sock)
        with self.assertRaises(ValueError):console.enter_enable('bad\nreload')
        self.assertNotIn(b'enable\r\n',sock.sent)


class PrivilegeOrderTests(unittest.TestCase):
    """Default password → no password → earlier input → one masked prompt."""
    def make(self, accepted, setup=False, broken=False):
        class Prompted(FakeConsole):
            attempts=[]
            prompt='R1>'
            enable_changed=False
            enable_pending=False
            def enter_enable(self,password=None):
                Prompted.attempts.append(password)
                if setup:
                    from topolog.transport import EnableSetupRequired
                    raise EnableSetupRequired('fixture')
                if broken:
                    self.enable_pending=True
                    raise TimeoutError('fixture')
                self.enable_changed=password==accepted
                if self.enable_changed:self.prompt='R1#'
                return self.enable_changed
            def restore_privilege(self):
                if broken:raise ValueError('fixture state')
                self.prompt='R1>';self.enable_changed=False;self.enable_pending=False
        return Prompted

    def run_node(self, console, provider, default=None, known=None):
        with tempfile.TemporaryDirectory() as temp:
            api=FakeAPI()
            return collect_node(api,api.node(N),Path(temp)/'n',{},[],console_factory=console,enable=True,
                                enable_password=default,password_provider=provider,known_passwords=known)

    def test_default_password_is_tried_first_without_prompt(self):
        from unittest.mock import Mock
        console=self.make('fixture-default');provider=Mock(return_value=None)
        result=self.run_node(console,provider,default='fixture-default')
        self.assertEqual(result['collection'],'collected')
        self.assertEqual(console.attempts,['fixture-default'])
        provider.assert_not_called()
        self.assertEqual(result['privilege']['source'],'default')
        self.assertNotIn('fixture-default',json.dumps(result))

    def test_failed_default_then_no_password_then_user_prompt(self):
        from unittest.mock import Mock
        console=self.make('fixture-user');provider=Mock(return_value='fixture-user');known=[]
        result=self.run_node(console,provider,default='fixture-default',known=known)
        self.assertEqual(result['collection'],'collected')
        self.assertEqual(console.attempts,['fixture-default',None,'fixture-user'])
        provider.assert_called_once()
        self.assertEqual(known,['fixture-user'])
        self.assertEqual([a['source'] for a in result['privilege']['attempts']],['default','none','user_input'])
        self.assertNotIn('fixture-user',json.dumps(result))

    def test_earlier_input_is_reused_before_prompting_again(self):
        from unittest.mock import Mock
        console=self.make('fixture-user');provider=Mock(return_value=None)
        result=self.run_node(console,provider,known=['fixture-user'])
        self.assertEqual(result['collection'],'collected')
        self.assertEqual(console.attempts,[None,'fixture-user'])
        provider.assert_not_called()

    def test_setup_request_never_prompts_or_sends_password(self):
        from unittest.mock import Mock
        console=self.make(None,setup=True);provider=Mock(return_value='fixture-user')
        result=self.run_node(console,provider,default='fixture-default')
        self.assertEqual(result['collection'],'requires_privilege')
        self.assertEqual(console.attempts,['fixture-default'])
        provider.assert_not_called()
        self.assertIn('새 enable 암호 설정', result['reason'])

    def test_uncertain_console_state_stops_further_passwords(self):
        from unittest.mock import Mock
        console=self.make(None,broken=True);provider=Mock(return_value='fixture-user')
        result=self.run_node(console,provider)
        self.assertEqual(result['collection'],'requires_privilege')
        self.assertEqual(console.attempts,[None])
        provider.assert_not_called()
        self.assertEqual(result['privilege']['state'],'uncertain')

    def test_cancelled_prompt_is_recorded(self):
        from unittest.mock import Mock
        result=self.run_node(self.make('x'),Mock(return_value=None))
        self.assertEqual(result['privilege']['user_prompt'],'cancelled')
        self.assertIn('취소', result['reason'])
