"""Second console pass: reachability pings derived from the collected configurations.

Runs after every node in a project was collected, because targets depend on the
whole topology. Only ping and read-only show commands are sent; privilege and
terminal settings are not changed (IOS/VPCS ping works in user EXEC).
"""
import re
from contextlib import closing
import time
from datetime import datetime, timezone
from . import verify
from .transport import Console, CLI_ERROR

SHOW_AFTER_TRAFFIC = ('show crypto isakmp sa', 'show crypto ipsec sa')


def run(api, entry, folder, console_factory=Console, progress=None, cancel=None, ready_timeout=60, retry_delay=5, workers=2):
    from . import features
    progress = progress or (lambda message: None)
    plans = features.plan_pings(entry)
    records = {n['node_id']: n for n in entry.get('nodes', [])}
    summary = {'planned': sum(len(v) for v in plans.values()), 'nodes': 0, 'errors': []}
    from .parallel import completed,LockedAPI
    from .progress import message
    worker_api=LockedAPI(api)
    def run_node(item):
        nid,rules=item
        record = records.get(nid)
        if not rules or not record or record.get('collection') not in ('collected', 'partial'):
            return None
        platform = record.get('platform')
        if platform not in ('ios', 'vpcs'):
            return None
        if cancel and cancel.is_set():
            raise InterruptedError('Reachability cancelled')
        def notify(text):
            label=message(text)
            if label:progress('['+record.get('name',nid)+'] '+label)
        notify('Reachability: ' + record.get('name', nid))
        console = None
        done = []
        try:
            fresh = worker_api.call('/v2/projects/' + entry['project_id'] + '/nodes/' + nid)
            host = fresh.get('console_host') if fresh.get('console_host') not in (None, '', '0.0.0.0', '::') else api.host
            console = console_factory(host, fresh['console'], ready_timeout=ready_timeout, cancel=cancel, progress=notify)
            console.connect()
            if platform == 'ios' and not getattr(console, 'prompt', '>').endswith(('>', '#')):
                raise ValueError('Unexpected console prompt')
            for rule in rules:
                flow=rule.get('diagnostic_flow')
                if platform=='ios' and flow:
                    rule['source']=flow['source']
                    record.setdefault('ping_sources',{})[rule['target']]=flow['source']
            commands = {verify.ping_command(rule, platform):rule for rule in rules}
            def execute(command,attempt=1):
                if cancel and cancel.is_set():raise InterruptedError('Reachability cancelled')
                started=datetime.now(timezone.utc).isoformat()
                name='{:02d}.txt'.format(len(record['commands'])+1)
                item={'command':command,'file':name,'status':'pending','phase':'reachability',
                      'optional':True,'started_at':started,'attempt':attempt}
                record['commands'].append(item)
                try:
                    text=console.command(command)
                    text=re.sub(r'<--- More ---> {0,14}', '', text)
                    item.update(text=text,status='collected',cli_error=bool(CLI_ERROR.search(text)))
                    (folder/nid/name).write_text(text,encoding='utf-8')
                    done.append(command)
                except BaseException as exc:
                    item.update(status='cancelled' if isinstance(exc,(InterruptedError,KeyboardInterrupt)) else 'failed',reason=type(exc).__name__)
                    (folder/nid/name).write_text('Command did not complete: '+type(exc).__name__,encoding='utf-8')
                    raise
                finally:item['finished_at']=datetime.now(timezone.utc).isoformat()
                return item
            retry=[]
            for command,rule in commands.items():
                item=execute(command)
                outcome=verify.evaluate(rule,[item],record)
                if outcome['status']!='passed' and not item['cli_error']:retry.append((command,rule))
            if retry:
                notify('경로 준비를 기다린 뒤 ping을 다시 확인합니다: '+record.get('name',nid))
                if cancel:
                    if cancel.wait(retry_delay):raise InterruptedError('Ping retry wait cancelled')
                else:time.sleep(retry_delay)
                for command,rule in retry:execute(command,2)
            has_crypto=any(c['command'] in SHOW_AFTER_TRAFFIC for c in record.get('commands',[]))
            if platform=='ios' and has_crypto:
                for command in SHOW_AFTER_TRAFFIC:execute(command)
            record['reachability'] = {'status': 'completed', 'commands': len(done), 'retried_targets':len(retry), 'retry_delay_seconds':retry_delay if retry else 0, 'max_ping_attempts':2}
            return record.get('name',nid),True
        except (InterruptedError, KeyboardInterrupt):
            record['reachability'] = {'status': 'cancelled', 'commands': len(done)}
            raise
        except Exception as exc:
            record['reachability'] = {'status': 'failed', 'commands': len(done), 'reason': '{}: {}'.format(type(exc).__name__, exc)[:300]}
            return record.get('name',nid),False
        finally:
            if console:
                try:
                    console.close()
                    (folder / nid / 'reachability.rx.bin').write_bytes(bytes(console.wire))
                except Exception:
                    pass
    entry['reachability'] = summary
    with closing(completed(plans.items(),run_node,workers,cancel)) as jobs:
        for result in jobs:
            if result is None:continue
            name,ok=result
            if ok:summary['nodes']+=1
            else:summary['errors'].append(name)
    summary['errors'].sort()
    return summary
