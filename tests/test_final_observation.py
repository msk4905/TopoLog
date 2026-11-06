"""Final samples of tunnel/HSRP state, bounded re-checks and two-session final observation."""
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from topolog import final_routes, verify, features
from topolog.transport import Console as RealConsole

BRIEF_DOWN = 'Interface IP-Address OK? Method Status Protocol\nSerial1/1 192.168.12.1 YES NVRAM up up\nTunnel15 172.16.15.1 YES NVRAM up down\nR1#'
BRIEF_UP = BRIEF_DOWN.replace('up down', 'up up')
SPEAK = 'Interface   Grp  Pri P State   Active          Standby         Virtual IP\nVl1         1    0   P Speak   172.16.1.1      unknown         172.16.1.254\nS2#'
STANDBY = SPEAK.replace('P Speak   172.16.1.1      unknown ', 'P Standby 172.16.1.1      local   ')


class API:
    host = '127.0.0.1'
    def call(self, path, *args, **kwargs):
        return {'console': 5000}


def factory(script, log=None, active=None):
    """script: {command: [replies in order]}; the last reply repeats."""
    lock = threading.Lock()
    class Console:
        prompt = 'R1#'
        wire = b''
        enable_changed = enable_pending = False
        cancel = None
        def __init__(self, *args, **kwargs):pass
        def connect(self):
            if active is not None:
                with lock:
                    active['now'] += 1;active['peak'] = max(active['peak'], active['now'])
                time.sleep(.05)
        def close(self):
            if active is not None:
                with lock:active['now'] -= 1
        def command(self, command):
            if log is not None:log.append(command)
            if command == 'show terminal':return 'Length: 24 lines, Width: 80 columns\nR1#'
            replies = script.get(command, ['R1#'])
            with lock:
                reply = replies.pop(0) if len(replies) > 1 else replies[0]
            return reply
    return lambda *a, **k: Console()


def node(nid, commands, platform='ios', name=None):
    return {'node_id': nid, 'name': name or nid, 'platform': platform, 'collection': 'collected',
            'commands': [{'command': c, 'text': t, 'status': 'collected', 'file': '{:02d}.txt'.format(i + 1)} for i, (c, t) in enumerate(commands)]}


def run(nodes, script, log=None, active=None, cancel=None, **kwargs):
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        for n in nodes:(folder / n['node_id']).mkdir()
        final_routes.refresh(API(), {'project_id': 'p', 'nodes': nodes}, folder, factory(script, log, active), lambda x: None,
                             cancel, True, None, None, [], **kwargs)
        return sorted(p.name for p in folder.rglob('*') if p.is_file())


class FinalObservationTests(unittest.TestCase):
    def test_tunnel_initial_up_down_is_resampled_and_initial_kept(self):
        n = node('r1', [('show ip interface brief', BRIEF_DOWN), ('show ip route', 'C 172.16.15.0/24 is directly connected, Tunnel15\nR1#')])
        run([n], {'show ip interface brief': [BRIEF_UP]}, stabilize_delay=0)
        briefs = [c for c in n['commands'] if c['command'] == 'show ip interface brief']
        self.assertEqual(len(briefs), 2)
        self.assertIn('up down', briefs[0]['text'])                 # initial sample retained
        self.assertEqual(briefs[1]['phase'], 'final_routing')
        result = verify.evaluate({'kind': 'interface_up', 'command': 'show ip interface brief', 'interface': 'Tunnel15'}, n['commands'])
        self.assertEqual(result['status'], 'passed')
        self.assertNotIn('rechecks', n['final_routing'])

    def test_all_up_brief_is_not_reread(self):
        log = []
        n = node('r1', [('show ip interface brief', BRIEF_UP), ('show ip route', 'R1#')])
        run([n], {}, log)
        self.assertNotIn('show ip interface brief', log)

    def test_transient_states_are_rechecked_with_bound_and_reason(self):
        log = []
        r1 = node('r1', [('show ip interface brief', BRIEF_DOWN)])
        s2 = node('s2', [('show standby brief', SPEAK)])
        run([r1, s2], {'show ip interface brief': [BRIEF_DOWN, BRIEF_DOWN, BRIEF_DOWN], 'show standby brief': [SPEAK, STANDBY]},
            log, stabilize_delay=0, stabilize_rounds=2)
        tunnel = [c for c in r1['commands'] if c['command'] == 'show ip interface brief']
        self.assertEqual(len(tunnel), 4)                             # initial + final + two bounded re-checks
        self.assertEqual(tunnel[-1]['attempt'], 3)
        self.assertIn('Tunnel15', tunnel[-1]['sample_reason'])
        self.assertEqual(r1['final_routing']['attempts'], 3)
        hsrp = [c for c in s2['commands'] if c['command'] == 'show standby brief']
        self.assertEqual([c.get('attempt', 1) for c in hsrp], [1, 1, 2])
        rule = {'kind': 'hsrp', 'command': 'show standby brief', 'interface': 'Vlan1', 'group': '1', 'virtual_ip': '172.16.1.254'}
        self.assertEqual(features.evaluate(rule, s2['commands'], 'ios')['status'], 'passed')
        last = verify.evaluate({'kind': 'interface_up', 'command': 'show ip interface brief', 'interface': 'Tunnel15'}, r1['commands'])
        self.assertEqual(last['status'], 'failed')                   # still down after the bound: reported, not waived

    def test_wait_is_cancellable(self):
        cancel = threading.Event()
        r1 = node('r1', [('show ip interface brief', BRIEF_DOWN)])
        timer = threading.Timer(.05, cancel.set);timer.start()
        started = time.monotonic()
        with self.assertRaises(InterruptedError):
            run([r1], {'show ip interface brief': [BRIEF_DOWN]}, cancel=cancel, stabilize_delay=5)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(r1['final_routing']['status'], 'completed')  # first final sample kept

    def test_failed_recheck_keeps_previous_final_sample(self):
        r1 = node('r1', [('show ip interface brief', BRIEF_DOWN)])
        calls = {'n': 0}
        base = factory({'show ip interface brief': [BRIEF_DOWN]})
        def flaky(*args, **kwargs):
            calls['n'] += 1
            console = base()
            if calls['n'] > 1:
                console.connect = lambda: (_ for _ in ()).throw(TimeoutError('console busy'))
            return console
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp);(folder / 'r1').mkdir()
            final_routes.refresh(API(), {'project_id': 'p', 'nodes': [r1]}, folder, flaky, lambda x: None, None, True, None, None, [],
                                 stabilize_delay=0, stabilize_rounds=1)
        self.assertEqual(r1['final_routing']['rechecks'][0]['status'], 'failed')
        self.assertEqual(r1['final_routing']['status'], 'recheck_failed')
        self.assertEqual(r1['collection'], 'partial')

    def test_final_sessions_run_two_at_a_time(self):
        active = {'now': 0, 'peak': 0}
        nodes = [node('r{}'.format(i), [('show ip route', 'R1#')]) for i in range(5)]
        run(nodes, {}, active=active, workers=2)
        self.assertEqual(active['peak'], 2)
        self.assertTrue(all(n['final_routing']['status'] == 'completed' for n in nodes))
        active = {'now': 0, 'peak': 0}
        run([node('r{}'.format(i), [('show ip route', 'R1#')]) for i in range(3)], {}, active=active, workers=1)
        self.assertEqual(active['peak'], 1)


class PromptQuietTests(unittest.TestCase):
    def console(self, device):
        a, b = socket.socketpair()
        c = RealConsole('127.0.0.1', 1, command_timeout=5)
        from topolog.transport import TelnetCodec
        c.sock, c.codec, c.prompt = a, TelnetCodec(), 'R1#'
        a.settimeout(c.IDLE_POLL)
        threading.Thread(target=device, args=(b,), daemon=True).start()
        return c

    def test_exact_prompt_is_accepted_after_short_silence(self):
        def device(sock):
            sock.recv(100);sock.sendall(b'show clock\r\n*00:00:01 UTC\r\nR1#');time.sleep(1)
        c = self.console(device)
        started = time.monotonic()
        text = c.command('show clock')
        self.assertLess(time.monotonic() - started, .2)
        self.assertIn('00:00:01', text)
        self.assertEqual(c.sock.gettimeout(), c.IDLE_POLL)

    def test_prompt_like_line_followed_by_more_output_is_not_truncated(self):
        def device(sock):
            sock.recv(100);sock.sendall(b'show run\r\nbanner\r\nR1#');time.sleep(.02)
            sock.sendall(b'\r\nmore output\r\nR1#');time.sleep(1)
        c = self.console(device)
        self.assertIn('more output', c.command('show run'))


if __name__ == '__main__':
    unittest.main()
