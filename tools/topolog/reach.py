"""Second console pass: reachability pings derived from the collected configurations.

Runs after every node in a project was collected, because targets depend on the
whole topology. Only ping and read-only show commands are sent; privilege and
terminal settings are not changed (IOS/VPCS ping works in user EXEC).
"""
import re
from . import verify
from .transport import Console

SHOW_AFTER_TRAFFIC = ('show crypto isakmp sa', 'show crypto ipsec sa')


def run(api, entry, folder, console_factory=Console, progress=None, cancel=None, ready_timeout=60):
    from . import features
    progress = progress or (lambda message: None)
    plans = features.plan_pings(entry)
    records = {n['node_id']: n for n in entry.get('nodes', [])}
    summary = {'planned': sum(len(v) for v in plans.values()), 'nodes': 0, 'errors': []}
    for nid, rules in plans.items():
        record = records.get(nid)
        if not rules or not record or record.get('collection') not in ('collected', 'partial'):
            continue
        platform = record.get('platform')
        if platform not in ('ios', 'vpcs'):
            continue
        if cancel and cancel.is_set():
            raise InterruptedError('Reachability cancelled')
        progress('Reachability: ' + record.get('name', nid))
        console = None
        done = []
        try:
            fresh = api.call('/v2/projects/' + entry['project_id'] + '/nodes/' + nid)
            host = fresh.get('console_host') if fresh.get('console_host') not in (None, '', '0.0.0.0', '::') else api.host
            console = console_factory(host, fresh['console'], ready_timeout=ready_timeout, cancel=cancel, progress=progress)
            console.connect()
            if platform == 'ios' and not getattr(console, 'prompt', '>').endswith(('>', '#')):
                raise ValueError('Unexpected console prompt')
            commands = [verify.ping_command(rule, platform) for rule in rules]
            has_crypto = any(c['command'] in SHOW_AFTER_TRAFFIC for c in record.get('commands', []))
            if platform == 'ios' and has_crypto:
                commands += list(SHOW_AFTER_TRAFFIC)
            for command in dict.fromkeys(commands):
                if cancel and cancel.is_set():
                    raise InterruptedError('Reachability cancelled')
                text = console.command(command)
                text = re.sub(r'<--- More ---> {0,14}', '', text)
                name = '{:02d}.txt'.format(len(record['commands']) + 1)
                (folder / nid / name).write_text(text, encoding='utf-8')
                record['commands'].append({'command': command, 'file': name, 'status': 'collected', 'phase': 'reachability',
                                           'text': text, 'cli_error': False, 'optional': True})
                done.append(command)
            record['reachability'] = {'status': 'completed', 'commands': len(done)}
            summary['nodes'] += 1
        except (InterruptedError, KeyboardInterrupt):
            record['reachability'] = {'status': 'cancelled', 'commands': len(done)}
            raise
        except Exception as exc:
            record['reachability'] = {'status': 'failed', 'commands': len(done), 'reason': '{}: {}'.format(type(exc).__name__, exc)[:300]}
            summary['errors'].append(record.get('name', nid))
        finally:
            if console:
                try:
                    console.close()
                    (folder / nid / 'reachability.rx.bin').write_bytes(bytes(console.wire))
                except Exception:
                    pass
    entry['reachability'] = summary
    return summary
