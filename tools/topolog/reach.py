"""Second console pass: reachability pings derived from the collected configurations.

Runs after every node in a project was collected, because targets depend on the
whole topology. Only ping and read-only show commands are sent; privilege and
terminal settings are not changed (IOS/VPCS ping works in user EXEC).

A device that is still booting (an ASA in particular), a routing table that has
not converged yet or a configuration that is applied late makes the first ping
fail although the configuration is right. Pings that get no answer are therefore
sent again after growing waits (RETRY_DELAYS) inside a time budget, and stop as
soon as they answer. Targets that still fail get a last attempt after every
device has finished, together with a route lookup on the source and the result
of the opposite-direction ping. Every attempt is kept as its own evidence file;
the latest attempt is what the report judges, and earlier failures stay visible.
"""
import re
from contextlib import closing
import time
from datetime import datetime, timezone
from . import verify
from .resultdir import node_dir
from .projectcopy import api_id
from .transport import Console, CLI_ERROR

SHOW_AFTER_TRAFFIC = ('show crypto isakmp sa', 'show crypto ipsec sa')
# Seconds to wait before each further round of the pings that got no answer.
RETRY_DELAYS = (5, 10, 20, 30, 45)
# Upper limit for those rounds on one device (waits and pings together).
RETRY_BUDGET = 150


def route_command(target):
    return ('show ipv6 route ' if ':' in target else 'show ip route ') + target


def route_lookup(text):
    """'Routing entry for …' of a single-destination route lookup, or None when the table has no entry."""
    entry = re.search(r'Routing entry for (\S+)', text)
    if not entry:
        return None
    via = re.search(r'Known via "([^"]+)"', text)
    return entry[1] + (' · ' + via[1] if via else '')


def run(api, entry, folder, console_factory=Console, progress=None, cancel=None, ready_timeout=60,
        retry_delays=RETRY_DELAYS, retry_budget=RETRY_BUDGET, workers=2, retry_delay=None):
    from . import features
    if retry_delay is not None:
        # Older callers asked for one retry after a fixed wait.
        retry_delays = (retry_delay,)
    progress = progress or (lambda message: None)
    plans = features.plan_pings(entry)
    records = {n['node_id']: n for n in entry.get('nodes', [])}
    summary = {'planned': sum(len(v) for v in plans.values()), 'nodes': 0, 'errors': []}
    from .parallel import completed, LockedAPI
    from .progress import message
    worker_api = LockedAPI(api)

    def wait(seconds):
        if cancel:
            if cancel.wait(seconds):
                raise InterruptedError('Ping retry wait cancelled')
        else:
            time.sleep(seconds)

    def session(nid, record, notify):
        fresh = worker_api.call('/v2/projects/' + api_id(entry) + '/nodes/' + nid)
        host = fresh.get('console_host') if fresh.get('console_host') not in (None, '', '0.0.0.0', '::') else api.host
        console = console_factory(host, fresh['console'], ready_timeout=ready_timeout, cancel=cancel, progress=notify)
        console.connect()
        if record.get('platform') == 'ios' and not getattr(console, 'prompt', '>').endswith(('>', '#')):
            raise ValueError('Unexpected console prompt')
        return console

    def executor(console, nid, record, done, phase='reachability'):
        def execute(command, attempt=1, reason=None):
            if cancel and cancel.is_set():
                raise InterruptedError('Reachability cancelled')
            started = datetime.now(timezone.utc).isoformat()
            name = '{:02d}.txt'.format(len(record['commands']) + 1)
            item = {'command': command, 'file': name, 'status': 'pending', 'phase': phase,
                    'optional': True, 'started_at': started, 'attempt': attempt}
            if reason:
                item['attempt_reason'] = reason
            record['commands'].append(item)
            target = folder / node_dir(record) / name
            try:
                text = console.command(command)
                text = re.sub(r'<--- More ---> {0,14}', '', text)
                item.update(text=text, status='collected', cli_error=bool(CLI_ERROR.search(text)))
                target.write_text(text, encoding='utf-8')
                done.append(command)
            except BaseException as exc:
                item.update(status='cancelled' if isinstance(exc, (InterruptedError, KeyboardInterrupt)) else 'failed', reason=type(exc).__name__)
                target.write_text('Command did not complete: ' + type(exc).__name__, encoding='utf-8')
                raise
            finally:
                item['finished_at'] = datetime.now(timezone.utc).isoformat()
            return item
        return execute

    def answered(rule, record, item):
        return verify.evaluate(rule, [item], record)['status'] == 'passed'

    def run_node(item):
        nid, rules = item
        record = records.get(nid)
        if not rules or not record or record.get('collection') not in ('collected', 'partial'):
            return None
        platform = record.get('platform')
        if platform not in ('ios', 'vpcs'):
            return None
        if cancel and cancel.is_set():
            raise InterruptedError('Reachability cancelled')

        def notify(text):
            label = message(text)
            if label:
                progress('[' + record.get('name', nid) + '] ' + label)
        notify('Reachability: ' + record.get('name', nid))
        console = None
        done = []
        rounds = []
        try:
            console = session(nid, record, notify)
            for rule in rules:
                flow = rule.get('diagnostic_flow')
                if platform == 'ios' and flow:
                    rule['source'] = flow['source']
                    record.setdefault('ping_sources', {})[rule['target']] = flow['source']
            commands = {verify.ping_command(rule, platform): rule for rule in rules}
            execute = executor(console, nid, record, done)
            pending = []
            for command, rule in commands.items():
                result = execute(command)
                if not result['cli_error'] and not answered(rule, record, result):
                    pending.append((command, rule))
            # Boot, routing convergence or late configuration: wait longer each round, stop once answered.
            deadline = time.monotonic() + retry_budget
            for attempt, delay in enumerate(retry_delays, 2):
                if not pending or time.monotonic() + delay > deadline:
                    break
                notify('경로 준비를 기다린 뒤 ping을 다시 확인합니다: {} · {}초 후 {}번째 시도'.format(record.get('name', nid), delay, attempt))
                wait(delay)
                rounds.append({'attempt': attempt, 'wait_seconds': delay, 'targets': len(pending)})
                still = []
                for command, rule in pending:
                    result = execute(command, attempt, 'delayed_retry')
                    if not result['cli_error'] and not answered(rule, record, result):
                        still.append((command, rule))
                pending = still
            has_crypto = any(c['command'] in SHOW_AFTER_TRAFFIC for c in record.get('commands', []))
            if platform == 'ios' and has_crypto:
                for command in SHOW_AFTER_TRAFFIC:
                    execute(command)
            record['reachability'] = {'status': 'completed', 'commands': len(done), 'retry_rounds': rounds,
                                      'retried_targets': rounds[0]['targets'] if rounds else 0,
                                      'unanswered_after_retries': len(pending), 'retry_budget_seconds': retry_budget}
            return record.get('name', nid), True
        except (InterruptedError, KeyboardInterrupt):
            record['reachability'] = {'status': 'cancelled', 'commands': len(done), 'retry_rounds': rounds}
            raise
        except Exception as exc:
            record['reachability'] = {'status': 'failed', 'commands': len(done), 'retry_rounds': rounds,
                                      'reason': '{}: {}'.format(type(exc).__name__, exc)[:300]}
            return record.get('name', nid), False
        finally:
            if console:
                try:
                    console.close()
                    (folder / node_dir(record) / 'reachability.rx.bin').write_bytes(bytes(console.wire))
                except Exception:
                    pass

    entry['reachability'] = summary
    with closing(completed(plans.items(), run_node, workers, cancel)) as jobs:
        for result in jobs:
            if result is None:
                continue
            name, ok = result
            if ok:
                summary['nodes'] += 1
            else:
                summary['errors'].append(name)
    summary['errors'].sort()
    follow_up(plans, records, folder, session, executor, progress, message, summary)
    return summary


def follow_up(plans, records, folder, session, executor, progress, message, summary):
    """Last attempt for pings still unanswered once every device has finished, with other evidence beside it.

    The opposite-direction ping is read from the target device's own results (no
    extra command). On IOS the source's route to the target is looked up. The
    ping itself is then sent one more time; its result is the one judged.
    """
    for nid, rules in plans.items():
        record = records.get(nid)
        if not record or (record.get('reachability') or {}).get('status') != 'completed':
            continue
        platform = record.get('platform')
        failing = []
        for rule in rules:
            command = verify.ping_command(rule, platform)
            outcome = verify.evaluate(rule, record.get('commands', []), record)
            if outcome['status'] != 'passed' and 'observed' in outcome:
                failing.append((command, rule))
        if not failing:
            continue

        def notify(text):
            label = message(text)
            if label:
                progress('[' + record.get('name', nid) + '] ' + label)
        notify('모든 장비 확인 뒤 응답 없는 ping을 다시 확인합니다: ' + record.get('name', nid))
        console = None
        done = []
        notes = record.setdefault('ping_followup', {})
        try:
            console = session(nid, record, notify)
            execute = executor(console, nid, record, done, 'reachability_followup')
            for command, rule in failing:
                note = {'started_at': datetime.now(timezone.utc).isoformat()}
                reverse = opposite(rule, nid, records, plans)
                if reverse:
                    note['reverse'] = reverse
                if platform == 'ios':
                    lookup = execute(route_command(rule['target']))
                    note['route'] = {'command': lookup['command'], 'file': lookup['file'],
                                     'observed': None if lookup['cli_error'] else route_lookup(lookup['text'])}
                previous = max([c.get('attempt', 1) for c in record['commands'] if c['command'] == command] or [1])
                last = execute(command, previous + 1, 'after_all_devices')
                note['final_attempt'] = {'attempt': previous + 1, 'file': last['file']}
                notes[command] = note
            summary.setdefault('followed_up', []).append(record.get('name', nid))
        except (InterruptedError, KeyboardInterrupt):
            raise
        except Exception as exc:
            summary.setdefault('followup_errors', []).append('{}: {}'.format(record.get('name', nid), type(exc).__name__))
        finally:
            if console:
                try:
                    console.close()
                    (folder / node_dir(record) / 'reachability-followup.rx.bin').write_bytes(bytes(console.wire))
                except Exception:
                    pass


def opposite(rule, nid, records, plans):
    """Result of the ping the target device itself sent toward this source, when it sent one."""
    origin_id = rule.get('origin_node')
    origin = records.get(origin_id)
    if not origin or origin.get('platform') not in ('ios', 'vpcs'):
        return None
    back = next((r for r in plans.get(origin_id, []) if r.get('origin_node') == nid), None)
    if not back:
        return None
    outcome = verify.evaluate(back, origin.get('commands', []), origin)
    if 'observed' not in outcome:
        return None
    return {'node_id': origin_id, 'name': origin.get('name', ''), 'target': back['target'],
            'status': 'answered' if outcome['status'] == 'passed' else 'unanswered', 'file': outcome.get('evidence')}
