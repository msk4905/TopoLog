"""Bounded final observation after all peers were collected and pinged.

Earlier evidence remains intact: every final sample is appended with its phase,
attempt, time and reason, and the latest sample is what later analysis reads.
Missing routes or unstable states are never invented, waived or replaced by an
older success. Waiting is bounded and cancellable.
"""
from .resultdir import node_dir
from .projectcopy import api_id
import re
from contextlib import closing
from .transport import CLI_ERROR

ROUTE_COMMANDS = {'asa': ('show route', 'show ospf neighbor'),
                  'ios': ('show ip route', 'show ipv6 route', 'show ip ospf neighbor', 'show ip eigrp neighbors',
                          'show isis neighbors', 'show ip bgp summary', 'show ipv6 ospf neighbor', 'show ipv6 eigrp neighbors')}
BRIEF = {'asa': 'show interface ip brief', 'ios': 'show ip interface brief'}
STANDBY = 'show standby brief'
# HSRP Speak/Learn are election phases that normally settle within the hold time.
TRANSIENT_HSRP = ('speak', 'learn')
STABILIZE_DELAY = 6
STABILIZE_ROUNDS = 2


def _latest(node, command):
    items = [c for c in node.get('commands', []) if c.get('command') == command]
    item = items[-1] if items else None
    return item if item and item.get('status') == 'collected' and not item.get('cli_error') else None


def _brief_rows(text):
    for line in (text or '').splitlines():
        fields = line.split()
        if len(fields) >= 6 and fields[2] in ('YES', 'NO'):
            yield fields


def pending_interfaces(node, command):
    """Addressed interfaces that were neither up/up nor administratively down."""
    item = _latest(node, command)
    if not item:
        return []
    return [f[0] for f in _brief_rows(item['text'])
            if f[1] != 'unassigned' and 'administratively' not in f and (f[-2].lower(), f[-1].lower()) != ('up', 'up')]


def transient(node):
    """[(command, reason)] for states that are expected to settle shortly."""
    platform = node.get('platform')
    found = []
    brief = BRIEF.get(platform)
    item = _latest(node, brief) if brief else None
    if item:
        tunnels = [f[0] for f in _brief_rows(item['text']) if f[0].lower().startswith('tunnel') and f[1] != 'unassigned'
                   and 'administratively' not in f and (f[-2].lower(), f[-1].lower()) != ('up', 'up')]
        if tunnels:
            found.append((brief, 'Tunnel 상태 재확인: ' + ', '.join(tunnels[:4])))
    item = _latest(node, STANDBY) if platform == 'ios' else None
    if item:
        states = [line.split() for line in item['text'].splitlines()]
        rows = [r[0] + ' ' + next(x for x in r if x.lower() in TRANSIENT_HSRP) for r in states
                if len(r) >= 5 and any(x.lower() in TRANSIENT_HSRP for x in r)]
        if rows:
            found.append((STANDBY, 'HSRP 상태 재확인: ' + ', '.join(rows[:4])))
    return found


def plan(node):
    platform = node.get('platform')
    if platform not in ('ios', 'asa') or node.get('collection') not in ('collected', 'partial'):
        return []
    observed = {c.get('command') for c in node.get('commands', [])}
    commands = [c for c in ROUTE_COMMANDS[platform] if c in observed]
    if BRIEF[platform] in observed and pending_interfaces(node, BRIEF[platform]):
        commands.insert(0, BRIEF[platform])
    if platform == 'ios' and STANDBY in observed:
        commands.append(STANDBY)
    return commands


def _session(api, entry, folder, node, commands, factory, progress, cancel, enable, password, provider, known,
             record, attempt=1, reason=None):
    from .engine import obtain_privilege, disable_asa_paging, restore_asa_paging, disable_ios_paging, restore_ios_paging, now
    platform = node.get('platform')
    console = None
    terminal = {}
    try:
        fresh = api.call('/v2/projects/' + api_id(entry) + '/nodes/' + node['node_id'])
        host = fresh.get('console_host')
        if host in (None, '', '0.0.0.0', '::'):
            host = api.host
        console = factory(host, fresh['console'], ready_timeout=30, command_timeout=12, cancel=cancel, progress=progress)
        console.connect()
        if platform == 'asa' and not getattr(console, 'prompt', '').endswith('#'):
            if not enable:
                raise ValueError('Privileged EXEC required for final routing observation')
            elevated, record['privilege'] = obtain_privilege(console, node.get('name', 'ASA'), password, provider, known, progress)
            if not elevated:
                raise ValueError('Final routing privilege unavailable')
        if platform == 'asa':
            disable_asa_paging(console, terminal)
        else:
            disable_ios_paging(console, terminal)
        for command in commands:
            if cancel and cancel.is_set():
                raise InterruptedError('Final routing observation cancelled')
            started = now()
            text = console.command(command)
            text = re.sub(r'<--- More ---> {0,14}', '', text)
            name = '{:02d}.txt'.format(len(node['commands']) + 1)
            (folder / node_dir(node) / name).write_text(text, encoding='utf-8')
            item = {'command': command, 'file': name, 'text': text, 'status': 'collected',
                    'started_at': started, 'finished_at': now(), 'phase': 'final_routing',
                    'optional': True, 'cli_error': bool(CLI_ERROR.search(text))}
            if attempt > 1:
                item.update(attempt=attempt, sample_reason=reason)
            node['commands'].append(item)
    finally:
        if console:
            previous_cancel = getattr(console, 'cancel', None)
            console.cancel = None
            try:
                if terminal.get('paging') in ('disabled_for_session', 'disable_requested'):
                    if platform == 'asa':
                        restore_asa_paging(console, terminal)
                    else:
                        restore_ios_paging(console, terminal)
                if terminal.get('paging') == 'restore_failed':
                    raise ValueError(terminal.get('reason', 'Pager restore failed'))
                if getattr(console, 'enable_changed', False) or getattr(console, 'enable_pending', False):
                    console.restore_privilege()
                if record.get('privilege'):
                    record['privilege']['restoration'] = 'restored'
            except Exception as exc:
                node.setdefault('cleanup_errors', []).append('Final routing cleanup: ' + str(exc))
                node['collection'] = 'partial'
                record['status'] = 'cleanup_failed'
            finally:
                console.cancel = previous_cancel
                console.close()
                suffix = '' if attempt == 1 else '-{}'.format(attempt)
                (folder / node_dir(node) / 'final-routing{}.rx.bin'.format(suffix)).write_bytes(bytes(console.wire))
            record['terminal'] = terminal


def refresh(api, entry, folder, factory, progress, cancel, enable, password, provider, known, workers=2,
            stabilize_delay=STABILIZE_DELAY, stabilize_rounds=STABILIZE_ROUNDS):
    from .engine import now
    from .parallel import completed, LockedAPI
    worker_api = LockedAPI(api)
    work = [(node, plan(node)) for node in entry.get('nodes', [])]
    work = [(node, commands) for node, commands in work if commands]

    def observe(item):
        node, commands = item
        if cancel and cancel.is_set():
            raise InterruptedError('Final routing observation cancelled')
        record = {'status': 'pending', 'started_at': now(), 'attempts': 1, 'commands': list(commands)}
        node['final_routing'] = record
        notify = lambda text: progress('[' + node.get('name', '') + '] ' + str(text))
        try:
            progress('최종 상태 확인: ' + node.get('name', 'ASA'))
            _session(worker_api, entry, folder, node, commands, factory, notify, cancel, enable, password, provider, known, record)
            if record['status'] == 'pending':
                if any(c.get('cli_error') for c in node['commands'] if c.get('phase') == 'final_routing'):
                    record.update(status='failed', reason='Final routing show command rejected')
                    node['collection'] = 'partial'
                else:
                    record['status'] = 'completed'
        except InterruptedError:
            record['status'] = 'cancelled'
            raise
        except Exception as exc:
            record.update(status='failed', reason=type(exc).__name__ + ': ' + str(exc))
            node['collection'] = 'partial'
        finally:
            record['finished_at'] = now()
        return node

    with closing(completed(work, observe, workers, cancel)) as jobs:
        for _ in jobs:
            pass

    # Bounded re-samples only for states known to settle (tunnel line protocol, HSRP election).
    for round_index in range(stabilize_rounds):
        pending = [(node, transient(node)) for node, _ in work
                   if node.get('final_routing', {}).get('status') == 'completed']
        pending = [(node, found) for node, found in pending if found]
        if not pending:
            break
        progress('상태가 바뀌는 중인 장비 {}대를 {}초 뒤 다시 확인합니다.'.format(len(pending), stabilize_delay))
        if cancel:
            if cancel.wait(stabilize_delay):
                raise InterruptedError('Final state wait cancelled')
        else:
            import time
            time.sleep(stabilize_delay)

        def recheck(item, attempt=round_index + 2):
            node, found = item
            if cancel and cancel.is_set():
                raise InterruptedError('Final state recheck cancelled')
            record = node['final_routing']
            sample = {'attempt': attempt, 'started_at': now(), 'reasons': [r for _, r in found],
                      'commands': [c for c, _ in found], 'status': 'pending'}
            record.setdefault('rechecks', []).append(sample)
            notify = lambda text: progress('[' + node.get('name', '') + '] ' + str(text))
            before=len(node.get('commands',[]))
            try:
                _session(worker_api, entry, folder, node, sample['commands'], factory, notify, cancel, enable, password,
                         provider, known, sample, attempt, '; '.join(sample['reasons']))
                if sample['status'] == 'pending':
                    rejected=any(c.get('cli_error') for c in node['commands'][before:])
                    sample.update(status='failed' if rejected else 'completed')
                    if rejected:sample['reason']='Final state recheck command rejected'
            except InterruptedError:
                sample['status'] = 'cancelled'
                raise
            except Exception as exc:
                # A failed re-sample keeps the earlier final sample as the latest evidence.
                sample.update(status='failed', reason=type(exc).__name__ + ': ' + str(exc))
            finally:
                sample['finished_at'] = now()
                record['attempts'] = attempt
                record['finished_at']=sample['finished_at']
                if sample['status']!='completed':
                    record['status']='cancelled' if sample['status']=='cancelled' else ('cleanup_failed' if sample['status']=='cleanup_failed' else 'recheck_failed')
                    record['reason']=sample.get('reason','Final state recheck incomplete')
                    if node.get('collection')=='collected':node['collection']='partial'
            return node

        with closing(completed(pending, recheck, workers, cancel)) as jobs:
            for _ in jobs:
                pass
