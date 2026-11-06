"""Explain conservative ICMP policy limits along an unambiguous topology path.

Configuration-derived explanations are not packet-tracer or drop-log evidence.
"""
from collections import deque
import ipaddress
import re

def config(node):
    return next((x.get('text','').replace('\r','') for x in reversed(node.get('commands',[]))
                 if x.get('command')=='show running-config' and x.get('status')=='collected' and not x.get('cli_error')),'')

def addresses(cfg,peer):
    for i in cfg['interfaces']:
        if i.get('shutdown') or not i.get('address'):continue
        a=ipaddress.IPv4Interface(i['address'])
        if any(not j.get('shutdown') and j.get('address') and ipaddress.IPv4Interface(j['address']).network==a.network for j in peer['interfaces']):yield i

def near_address(source,destination,parsed,linked):
    if not linked(source,destination):return None
    choices=list(addresses(parsed[destination],parsed[source]))
    if len(choices)!=1:return None
    i=choices[0]
    return str(ipaddress.IPv4Interface(i['address']).ip),i['name']

def path_between(source,destination,parsed,linked):
    queue=deque([[source]]);found=[];distance=None
    while queue:
        path=queue.popleft()
        if distance is not None and len(path)>distance:break
        current=path[-1]
        if current==destination:
            found.append(path);distance=len(path)
            if len(found)>1:return None
            continue
        for other in parsed:
            if other not in path and linked(current,other) and any(addresses(parsed[current],parsed[other])):
                queue.append(path+[other])
        if len(queue)>512:return None
    return found[0] if len(found)==1 else None

def interface_policy(raw,name):
    block=re.search(r'(?m)^interface '+re.escape(name)+r'\s*\n((?:[ \t]+[^\n]*\n|\n)*)',raw+'\n')
    if not block:return None
    level=re.search(r'(?m)^\s+security-level (\d+)\s*$',block[1])
    label=re.search(r'(?m)^\s+nameif (\S+)\s*$',block[1])
    return (int(level[1]),label[1]) if level and label else None

def inspected(raw):
    active=re.search(r'(?m)^service-policy (\S+) global\s*$',raw)
    if not active:return False
    block=re.search(r'(?m)^policy-map '+re.escape(active[1])+r'\s*\n((?:[ \t]+[^\n]*\n|\n)*)',raw+'\n')
    return bool(block and re.search(r'(?m)^\s+inspect icmp\s*$',block[1]) and
                'class inspection_default' in block[1] and
                re.search(r'(?m)^class-map inspection_default\s*\n\s+match default-inspection-traffic',raw))

def context(source,destination,parsed,nodes,linked):
    path=path_between(source,destination,parsed,linked)
    if not path:return {'policy':True,'classification':'policy_review','reason':'토폴로지에서 단일 통신 경로를 확정할 수 없습니다.'}
    generic=any(parsed[n]['nat_inside'] or parsed[n]['crypto_peers'] or 'acl' in nodes[n].get('analysis',{}).get('features',[]) for n in path)
    for index,nid in enumerate(path):
        if parsed[nid]['platform']!='asa':continue
        raw=config(nodes[nid]);label=nodes[nid].get('name','ASA')
        if index==len(path)-1:
            limited=bool(re.search(r'(?m)^icmp (?:permit|deny) ',raw))
            return {'policy':limited,'classification':'policy_review' if limited else 'direct_interface',
                    'reason':label+'의 진입 인터페이스 주소에 대한 응답 확인'}
        if index==0 or generic or re.search(r'(?m)^access-group |^\s*nat\s|^crypto map .+ interface ',raw):
            return {'policy':True,'classification':'policy_review','reason':label+' 통과 규칙·변환·암호화 정책을 추가 확인해야 합니다.'}
        incoming=list(addresses(parsed[nid],parsed[path[index-1]]));outgoing=list(addresses(parsed[nid],parsed[path[index+1]]))
        if len(incoming)!=1 or len(outgoing)!=1:break
        a=interface_policy(raw,incoming[0]['name']);b=interface_policy(raw,outgoing[0]['name'])
        if not a or not b:break
        reason=None
        if a[0]<b[0]:reason=f'{label}: {a[1]}({a[0]})에서 {b[1]}({b[0]})로의 통신에 허용 ACL이 없습니다.'
        elif a[0]>b[0] and not inspected(raw):reason=f'{label}: ICMP 검사와 반환 트래픽 허용 ACL이 설정되어 있지 않습니다.'
        elif a[0]==b[0] and 'same-security-traffic permit inter-interface' not in raw:reason=f'{label}: 동일 보안 수준 인터페이스 간 통신 허용이 설정되어 있지 않습니다.'
        if reason:return {'policy':True,'classification':'policy_limited','reason':reason,'path':[nodes[n].get('name',n) for n in path]}
        # A configured inspection rule alone does not prove every traffic class matches it.
        return {'policy':True,'classification':'policy_review','reason':label+' 경유 ICMP의 실제 허용 여부는 정책과 패킷 근거를 함께 확인해야 합니다.'}
    if any(parsed[n]['platform']=='asa' for n in path):generic=True
    return {'policy':bool(generic),'classification':'policy_review' if generic else 'reachability',
            'reason':'경로상의 정책 확인 필요' if generic else '라우팅 경로의 응답 확인'}


def trace_flow(source,destination,target,parsed,nodes,linked):
    """One IPv4, non-tunnel, unambiguous path with one transit ASA only."""
    if ':' in target or parsed[source]['platform']!='ios':return None
    path=path_between(source,destination,parsed,linked)
    if not path or len(path)<3:return None
    firewalls=[(i,n) for i,n in enumerate(path) if parsed[n]['platform']=='asa']
    if len(firewalls)!=1:return None
    index,asa=firewalls[0]
    if index in (0,len(path)-1):return None
    # Upstream NAT or tunnels can change the address actually arriving at ASA.
    for n in path:
        raw=config(nodes[n])
        if parsed[n]['crypto_peers'] or any(i['name'].lower().startswith('tunnel') for i in parsed[n]['interfaces']):return None
        if n!=asa and (parsed[n]['nat_inside'] or re.search(r'(?m)^\s*nat\s',raw)):return None
    sources=list(addresses(parsed[source],parsed[path[1]]))
    incoming=list(addresses(parsed[asa],parsed[path[index-1]]))
    outgoing=list(addresses(parsed[asa],parsed[path[index+1]]))
    if any(len(x)!=1 for x in (sources,incoming,outgoing)):return None
    raw=config(nodes[asa]);a=interface_policy(raw,incoming[0]['name']);b=interface_policy(raw,outgoing[0]['name'])
    if not a or not b:return None
    if not all(re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,63}',name) for name in (a[1],b[1])):return None
    return {'asa_id':asa,'source':str(ipaddress.IPv4Interface(sources[0]['address']).ip),
            'destination':str(ipaddress.IPv4Address(target)),'input':a[1],'reply_input':b[1],
            'path':path,'scope':'single_topology_path_explicit_ping_source',
            'reverse_supported':not bool(re.search(r'(?m)^\s*nat\s',raw))}
