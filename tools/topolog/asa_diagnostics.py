"""Bounded ASA simulations for failed, explicitly sourced ICMP probes.

Simulation is evidence about packet processing, never proof of intended policy.
Only existing buffered logs are read; logging configuration is never changed.
"""
from collections import Counter
import ipaddress
import re
import time
from . import features, verify
from .transport import CLI_ERROR


def trace_command(flow, reply=False):
    interface=flow['reply_input'] if reply else flow['input']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',interface):
        raise ValueError('Invalid ASA interface name')
    source=str(ipaddress.IPv4Address(flow['source']))
    target=str(ipaddress.IPv4Address(flow['destination']))
    if reply:source,target=target,source
    return 'packet-tracer input {} icmp {} {} 0 {} detailed'.format(interface,source,0 if reply else 8,target)


def trace_result(text):
    # A phase ALLOW is not the final action.
    blocks=re.split(r'(?im)^\s*Result:\s*$',text)
    if len(blocks)<2:return {'action':'unknown'}
    final=blocks[-1]
    action=re.search(r'(?im)^\s*Action:\s*(allow|drop)\b',final)
    reason=re.search(r'(?im)^\s*Drop-reason:\s*(.+)',final)
    return {'action':action.group(1).lower() if action else 'unknown',
            'drop_reason':reason.group(1).strip() if reason else ''}


def new_denies(before, after, flow):
    old=Counter(before.splitlines());matches=[]
    for line in after.splitlines():
        if old[line]:old[line]-=1;continue
        # Restrict correlation to a complete ASA ICMP deny flow, not counters.
        m=re.search(r'%ASA-\d-106023:.*?Deny icmp src ([\w.-]+):(\d+\.\d+\.\d+\.\d+) dst ([\w.-]+):(\d+\.\d+\.\d+\.\d+).*?\(type (\d+), code (\d+)\)',line,re.I)
        if not m:continue
        ingress,src,_,dst,kind,code=m.groups()
        forward=(ingress==flow['input'] and src==flow['source'] and dst==flow['destination'] and kind=='8')
        reverse=(flow.get('reverse_supported') and ingress==flow['reply_input'] and src==flow['destination'] and dst==flow['source'] and kind=='0')
        if (forward or reverse) and code=='0':matches.append(line)
    return matches[:10]


def run(api,entry,folder,factory,progress,cancel,enable,password,provider,known):
    from .engine import now, obtain_privilege, disable_asa_paging, restore_asa_paging
    nodes={n['node_id']:n for n in entry.get('nodes',[])};plans=features.plan_pings(entry);groups={}
    for nid,rules in plans.items():
        for rule in rules:
            flow=rule.get('diagnostic_flow')
            if not flow or rule.get('source')!=flow['source']:continue
            result=verify._ping(rule,nodes[nid].get('commands',[]),nodes[nid])
            if result['status']=='passed' or 'observed' not in result:continue
            groups.setdefault(flow['asa_id'],[]).append((nodes[nid],rule,flow))
    for aid,flows in groups.items():
        asa=nodes[aid];console=None;terminal={};deadline=time.monotonic()+90
        baseline=verify.latest(asa.get('commands',[]),'show logging')
        selected=flows[:8]
        for node,rule,_ in flows[8:]:
            node.setdefault('ping_diagnostics',{})[verify.ping_command(rule,'ios')]={'status':'skipped','reason':'ASA 추가 확인 개수 한도에 도달하였습니다.'}
        try:
            if cancel and cancel.is_set():raise InterruptedError('ASA diagnostics cancelled')
            progress('방화벽 ICMP 흐름을 확인합니다: '+asa.get('name',aid))
            fresh=api.call('/v2/projects/'+entry['project_id']+'/nodes/'+aid)
            host=fresh.get('console_host')
            if host in (None,'','0.0.0.0','::'):host=api.host
            console=factory(host,fresh['console'],ready_timeout=30,command_timeout=12,cancel=cancel,progress=progress)
            console.connect()
            if not getattr(console,'prompt','').endswith('#'):
                if not enable:raise ValueError('ASA privileged EXEC required')
                elevated,_=obtain_privilege(console,asa.get('name','ASA'),password,provider,known,progress)
                if not elevated:raise ValueError('ASA privileged EXEC unavailable')
            disable_asa_paging(console,terminal)
            def execute(command):
                if cancel and cancel.is_set():raise InterruptedError('ASA diagnostics cancelled')
                if time.monotonic()>deadline:raise TimeoutError('ASA diagnostics time budget exceeded')
                started=now();text=console.command(command)
                name='{:02d}.txt'.format(len(asa['commands'])+1)
                (folder/aid/name).write_text(text,encoding='utf-8')
                item={'command':command,'file':name,'text':text,'status':'collected','optional':True,
                      'phase':'asa_diagnostics','started_at':started,'finished_at':now(),'cli_error':bool(CLI_ERROR.search(text))}
                asa['commands'].append(item);return item
            # Read actual logs before synthetic packets can contribute entries.
            logs=execute('show logging')
            for node,rule,flow in selected:
                record={'status':'pending','asa_id':aid,'started_at':now(),'flow':flow,'evidence':[],
                        'scope':'ASA 모의 검사는 실제 응답이나 의도한 정책의 정상 여부를 확정하지 않습니다.'}
                node.setdefault('ping_diagnostics',{})[verify.ping_command(rule,'ios')]=record
                if baseline and not logs['cli_error']:
                    record['deny_logs']=new_denies(baseline['text'],logs['text'],flow)
                    record['log_scope']='수집 전후에 추가된 동일 주소·인터페이스·ICMP 유형 로그입니다. 개별 ping 패킷 식별은 하지 않습니다.'
                    record['evidence'].append({'node_id':aid,'file':logs['file'],'label':'기존 차단 로그'})
                for reply in (False,True) if flow.get('reverse_supported') else (False,):
                    item=execute(trace_command(flow,reply))
                    record['reply' if reply else 'request']=trace_result(item['text']) if not item['cli_error'] else {'action':'unknown'}
                    record['evidence'].append({'node_id':aid,'file':item['file'],'label':'응답 모의 검사' if reply else '요청 모의 검사'})
                record['status']='completed';record['finished_at']=now()
        except InterruptedError:
            raise
        except Exception as exc:
            for node,rule,_ in selected:
                record=node.setdefault('ping_diagnostics',{}).setdefault(verify.ping_command(rule,'ios'),{})
                if record.get('status')!='completed':record.update(status='failed',reason=type(exc).__name__+': '+str(exc))
        finally:
            if console:
                previous=getattr(console,'cancel',None);console.cancel=None
                try:
                    if terminal.get('paging') in ('disabled_for_session','disable_requested'):restore_asa_paging(console,terminal)
                    if terminal.get('paging')=='restore_failed':raise ValueError('ASA pager restoration failed')
                    if getattr(console,'enable_changed',False) or getattr(console,'enable_pending',False):console.restore_privilege()
                except Exception as exc:
                    asa.setdefault('cleanup_errors',[]).append('ASA diagnostics cleanup: '+str(exc));asa['collection']='partial'
                finally:
                    console.cancel=previous;console.close()
                    (folder/aid/'asa-diagnostics.rx.bin').write_bytes(bytes(console.wire))


def describe(record):
    if record.get('status')!='completed':return 'ASA 추가 확인 미완료'
    parts=[]
    if record.get('deny_logs'):parts.append('동일 흐름 차단 로그 확인')
    labels={'allow':'통과','drop':'차단','unknown':'결과 확인 필요'}
    for key,label in (('request','요청'),('reply','응답')):
        if key in record:parts.append('ASA '+label+' 모의 검사: '+labels[record[key]['action']])
    return ' · '.join(parts)+' · 실제 응답 및 의도한 정책의 정상 여부는 별도 확인이 필요합니다.'
