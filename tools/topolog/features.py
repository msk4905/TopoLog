"""Configuration-derived expectations, independent of observed operational success.

Detectors are composable. Unsupported intent stays explicitly unverified; raw
configuration and all command evidence remain in the normal collection files.
"""
import ipaddress
import re
from . import profiles

DETECTORS = {
    'static': (r'^ip route ', ['show ip route']),
    'ospf': (r'^router ospf\b', ['show ip ospf neighbor', 'show ip ospf interface brief']),
    'rip': (r'^router rip\b', ['show ip protocols', 'show ip rip database']),
    'redistribution': (r'^\s+redistribute\b', ['show ip protocols', 'show route-map']),
    'vlan': (r'^(?:vlan \d|\s+switchport access vlan)', ['show vlan brief']),
    'trunk': (r'^\s+switchport (?:mode trunk|trunk)', ['show interfaces trunk']),
    'stp': (r'^(?:spanning-tree|\s+spanning-tree)', ['show spanning-tree']),
    'dhcp': (r'^ip dhcp (?:pool|excluded-address)', ['show ip dhcp pool', 'show ip dhcp binding']),
    'nat': (r'^(?:ip nat|\s+ip nat)', ['show ip nat translations', 'show ip nat statistics']),
    'acl': (r'^(?:access-list|ip access-list|\s+ip access-group)', ['show access-lists','show ip interface']),
    'hsrp': (r'^\s+standby\b', ['show standby brief']),
    # Default ASA lines such as "crypto ca trustpoint" or "crypto ipsec security-association
    # pmtu-aging" are not a VPN; only maps, IKE policies, tunnel groups and tunnels are.
    'vpn': (r'^(?:crypto map|crypto isakmp|crypto ikev[12] (?:policy|enable)|tunnel-group|interface Tunnel)',
            ['show crypto isakmp sa', 'show crypto ipsec sa', 'show crypto session']),
    'eigrp': (r'^router eigrp\b', ['show ip eigrp neighbors', 'show ip protocols']),
    'isis': (r'^(?:router isis\b|\s+ip router isis)', ['show isis neighbors', 'show clns neighbors', 'show ip protocols']),
    'bgp': (r'^router bgp\b', ['show ip bgp summary']),
    'ipv6': (r'^(?:ipv6 (?:unicast-routing|router|route)|\s+ipv6 (?:address|rip|ospf|eigrp|router))',
             ['show ipv6 interface brief', 'show ipv6 route', 'show ipv6 protocols']),
    'ssh': (r'^(?:ip ssh\b|ssh \d)', ['show ip ssh']),
    'vtp': (r'^vtp ', ['show vtp status']),
}
ASA_COMMANDS = {'ospf': ['show ospf neighbor'], 'static': ['show route'], 'ssh': ['show ssh'],
                'vpn': ['show crypto isakmp sa', 'show crypto ipsec sa']}
EXTRA_COMMANDS = {'ipv6': {'ospf': 'show ipv6 ospf neighbor', 'eigrp': 'show ipv6 eigrp neighbors', 'isis': 'show isis neighbors'}}
LABELS = {'static':'정적 경로','ospf':'OSPF','rip':'RIP','redistribution':'재분배','vlan':'VLAN','trunk':'Trunk',
          'stp':'STP','dhcp':'DHCP','nat':'NAT','acl':'ACL','hsrp':'HSRP','vpn':'VPN',
          'eigrp':'EIGRP','bgp':'BGP','ipv6':'IPv6','isis':'IS-IS','ssh':'SSH','vtp':'VTP',
          'interface':'인터페이스','routing':'경로 학습','reachability':'도달성(ping)','other':'기타 설정'}
# Management/default lines: preserved as raw configuration, not functions to verify.
MANAGEMENT = re.compile(r'^(?:upgrade |voice |default-value |username |enable |aaa |banner |alias |ip ssh |ip scp |'
                        r'ip name-server|ntp |clock |snmp-server |file |hw-module |license |diagnostic |redundancy|'
                        r'security |login |crypto pki |crypto ca |crypto key |crypto ipsec security-association |'
                        r'crypto ipsec ikev[12] transform-set|vtp |'
                        # ASA appliance defaults
                        r':|ASA |names$|name |mtu |icmp |arp |timeout |pager |ftp |user-identity |ssh |telnet |'
                        r'console |threat-detection |dynamic-access-policy-record |class-map |policy-map |'
                        r'service-policy |prompt |call-home|Cryptochecksum|passwd |http |dns |same-security-traffic |'
                        r'no |firewall |webvpn|group-policy |object-group |object |nat |access-group |route )')


def interface_key(value):
    value = value.lower()
    # 'show standby brief' abbreviates Vlan1 to Vl1 and Tunnel0 to Tu0.
    for long, short in [('fastethernet','fa'),('gigabitethernet','gi'),('tengigabitethernet','te'),
                        ('ethernet','eth'),('serial','se'),('loopback','lo'),('port-channel','po'),
                        ('vlan','vl'),('tunnel','tu')]:
        value = value.replace(long, short)
    return value


def analyze(config, platform='ios'):
    marker = re.search(r'^Current configuration[^\n]*\n', config, re.M)
    if marker: config = config[marker.end():]
    config = re.sub(r'<--- More ---> {0,14}', '', config)   # ASA pager residue
    found = [name for name, (pattern, _) in DETECTORS.items() if re.search(pattern, config, re.M)]
    interfaces, processes, vlans, hsrp = [], [], set(), []
    current = None
    for raw in config.splitlines():
        line = raw.strip()
        if not line or line.startswith(('!', '*')):
            continue
        if not raw[0].isspace():
            current = None
            if line.startswith('interface '):
                current = {'name': line.split(None,1)[1], 'shutdown':False, 'passive':False}
                interfaces.append(current)
            elif re.match(r'router (ospf|rip)\b', line):
                current = {'protocol': line.split()[1], 'networks':[], 'passive':[], 'passive_default':False, 'active':[], 'platform':platform}
                processes.append(current)
            vlan = re.fullmatch(r'vlan (\d+)', line)
            if vlan and 1 <= int(vlan[1]) <= 4094:
                vlans.add(int(vlan[1]))
        elif current is not None:
            if 'name' in current:
                m = re.fullmatch(r'ip address (\S+) (\d+\.\d+\.\d+\.\d+)(?: standby \S+)?', line)
                if m:
                    try:
                        current['address'] = str(ipaddress.IPv4Interface(m[1]+'/'+m[2]))
                    except ValueError:
                        pass
                if line == 'shutdown': current['shutdown'] = True
                if line == 'switchport mode trunk': current['trunk'] = True
                m = re.fullmatch(r'switchport access vlan (\d+)', line)
                if m: vlans.add(int(m[1]))
                m = re.fullmatch(r'ip ospf (\d+) area (\S+)', line)
                if m: current['ospf_area'] = m[2]
                m = re.fullmatch(r'standby (?:(\d+) )?ip (\S+)', line)
                if m: hsrp.append({'interface':current['name'],'group':m[1] or '0','virtual_ip':m[2]})
            else:
                if line=='version 2': current['rip_version']=2
                if line=='no auto-summary': current['no_auto_summary']=True
                if line.startswith('router-id '): current['router_id'] = line.split()[1]
                if line == 'passive-interface default': current['passive_default'] = True
                elif line.startswith('passive-interface '): current['passive'].append(interface_key(line.split()[-1]))
                elif line.startswith('no passive-interface '): current['active'].append(interface_key(line.split()[-1]))
                if line.startswith('network '): current['networks'].append(line.split()[1:])
    baseline = re.compile(r'^(?:no |service |hostname |version |boot-|logging |ip (?:cef|source-route|forward-protocol|tcp |http |domain |icmp )|ipv6 |interface |router |vlan |spanning-tree|access-list|crypto|line |control-plane|archive|memory-size|multilink|voice |mgcp |gatekeeper|upgrade |end$|Building |Current |\w+[>#])')
    known = re.compile(r'^(?:ip (?:dhcp|nat|access-list|route)|username |enable |aaa )')
    other = sorted({line.split()[0] for line in config.splitlines()
                    if line and not line[0].isspace() and not line.startswith(('!', '*'))
                    and not baseline.search(line) and not known.search(line) and not MANAGEMENT.search(line)})
    unsupported_router = re.findall(r'^router (?!ospf\b|rip\b|eigrp\b|bgp\b|isis\b)(\S+)', config, re.M)
    if other or unsupported_router: found.append('other')
    from .services import derive
    return {'features':list(dict.fromkeys(found)), 'raw':config[:200000], 'interfaces':interfaces,'processes':processes,'service_rules':derive(config),
            'vlans':sorted(vlans),'hsrp':hsrp,'unclassified':other+unsupported_router,'platform':platform,
            'has_static_routes':bool(re.search(r'^ip route ',config,re.M)),
            'stp_disabled':bool(re.search(r'^no spanning-tree vlan ',config,re.M))}


def commands(analysis):
    if analysis['platform'] == 'asa':
        return list(dict.fromkeys(cmd for feature in analysis['features'] for cmd in ASA_COMMANDS.get(feature, [])))
    if analysis['platform'] != 'ios': return []
    selected = [cmd for feature in analysis['features'] if feature in DETECTORS for cmd in DETECTORS[feature][1]]
    if 'ipv6' in analysis['features']:
        for protocol, command in EXTRA_COMMANDS['ipv6'].items():
            if re.search(r'ipv6 (?:router )?' + protocol, analysis.get('raw','')):
                selected.append(command)
    return list(dict.fromkeys(selected))


def _area(interface, process):
    if interface.get('shutdown') or not interface.get('address'): return None
    key = interface_key(interface['name'])
    if key in process['passive'] or (process['passive_default'] and key not in process['active']): return None
    address = ipaddress.IPv4Interface(interface['address']).ip
    def area(value):
        return str(int(ipaddress.IPv4Address(value))) if '.' in value else str(int(value))
    if process['protocol']=='ospf' and 'ospf_area' in interface:
        try: return area(interface['ospf_area'])
        except ValueError: return None
    for tokens in process['networks']:
        try:
            if process['protocol']=='ospf' and len(tokens)==4 and tokens[2]=='area':
                wildcard = int(ipaddress.IPv4Address(tokens[1]))
                if process.get('platform')=='asa':
                    wildcard = (~wildcard) & 0xFFFFFFFF   # ASA network statements use a netmask
                if int(address) & ~wildcard == int(ipaddress.IPv4Address(tokens[0])) & ~wildcard:
                    return area(tokens[3])
            elif process['protocol']=='rip':
                first = int(tokens[0].split('.')[0]); bits = 8 if first<128 else (16 if first<192 else 24)
                if address in ipaddress.IPv4Network(tokens[0]+'/'+str(bits),strict=False): return 'rip'
        except (ValueError, IndexError):
            continue
    return None


def evaluate(rule, evidence, platform):
    if rule['kind'] in ('ospf_neighbors','route','ping','contains'):
        return profiles.evaluate(rule,evidence,platform)
    command = rule['command']
    item = next((x for x in reversed(evidence) if x['command']==command),None)
    if not item or item.get('status')!='collected' or item.get('cli_error'): return {'status':'inconclusive','reason':'자동 검증에 필요한 명령 증거가 없습니다.','command':command}
    text = profiles.body(item['text'],command)
    observed = None
    if rule['kind'] in ('dhcp_pool','static_nat','acl_binding','stp_instance'):
        from .services import evaluate as service_evaluate
        passed,observed=service_evaluate(rule,text)
        return {'status':'inconclusive' if passed is None else ('passed' if passed else 'failed'),
                'command':command,'evidence':item['file'],'observed':observed}
    if rule['kind']=='interface_address':
        row = next((x.split() for x in text.splitlines() if x.split() and interface_key(x.split()[0])==interface_key(rule['interface'])),None)
        if row and len(row)>1: observed = row[1]
        passed = observed==rule['address']
    elif rule['kind']=='vlan_active':
        row = re.search(r'^\s*'+str(rule['vlan'])+r'\s+\S+\s+(\S+)',text,re.M)
        observed = row[1] if row else 'absent'; passed = observed=='active'
    elif rule['kind']=='trunk':
        rows = [x for x in text.splitlines() if x.split() and interface_key(x.split()[0])==interface_key(rule['interface'])]
        observed = next((x.strip() for x in rows if 'trunking' in x.lower()),None); passed = observed is not None
    elif rule['kind']=='hsrp':
        rows = [x.split() for x in text.splitlines() if x.split() and interface_key(x.split()[0])==interface_key(rule['interface'])]
        observed = next((x for x in rows if len(x)>6 and x[1]==rule['group']),None)
        passed = bool(observed and rule['virtual_ip'] in observed and any(x.lower() in ('active','standby') for x in observed))
    else: return {'status':'unverified','reason':'이 기능의 의도·동작 검증은 아직 자동 해석하지 않습니다.'}
    return {'status':'passed' if passed else 'failed','command':command,'evidence':item['file'],'observed':observed}


def _prepare(project):
    """Per-node analyses, parsed configs and an L2-aware link test for a project."""
    from . import verify
    nodes = project.get('nodes',[])
    analyses, parsed = {}, {}
    for node in nodes:
        config = next((c for c in node.get('commands',[]) if c['command']=='show running-config' and c['status']=='collected' and not c.get('cli_error')),None)
        if config:
            text = profiles.body(config['text'],config['command'])
            analyses[node['node_id']] = analyze(text,node.get('platform','unknown'))
            parsed[node['node_id']] = verify.parse(text,node.get('platform','unknown'))
    adjacency = {n['node_id']:set() for n in nodes}
    for link in project.get('topology_links',[]):
        ends=[n.get('node_id') for n in link.get('nodes',[])]
        if len(ends)==2 and all(x in adjacency for x in ends):
            adjacency[ends[0]].add(ends[1]);adjacency[ends[1]].add(ends[0])
    def linked(a,b):
        # Only allow direct links or paths through non-routing L2 switches.
        frontier=[a]; seen={a}
        while frontier:
            x=frontier.pop()
            for y in adjacency.get(x,[]):
                if y==b:return True
                item=next((n for n in nodes if n['node_id']==y),{})
                if y not in seen and item.get('node_type') in ('ethernet_switch','ethernet_hub'):
                    seen.add(y);frontier.append(y)
        return False
    for node in nodes:
        if node['node_id'] in analyses:node['analysis']=analyses[node['node_id']]
    return analyses, parsed, linked


def plan_pings(project):
    """{node_id: [ping rule]} derived from the collected configurations."""
    from . import verify
    _, parsed, linked = _prepare(project)
    return verify.derive(project, parsed, linked)[1]


def apply(project, specifications=None):
    from . import verify
    nodes = project.get('nodes',[]); specs = specifications or {}
    analyses, parsed, linked = _prepare(project)
    derived, pings = verify.derive(project, parsed, linked)
    states = {n['node_id']: verify.interface_states(n.get('commands', [])) for n in nodes}
    for node in nodes:
        analysis=analyses.get(node['node_id'])
        if not analysis:
            _vpcs_and_ping(node, pings.get(node['node_id'], []), specs, states)
            continue
        node['analysis']=analysis; automatic=[]; covered=set()
        def add(rule,feature,reason):
            automatic.append({'criterion':rule,'origin':'configuration','basis':reason,
                              **evaluate(rule,node.get('commands',[]),node.get('platform','unknown'))})
            covered.add(feature)
        brief = 'show interface ip brief' if analysis['platform']=='asa' else 'show ip interface brief'
        if analysis['platform'] in ('ios','asa'):
            for feature,rule in analysis.get('service_rules',[]):
                add(rule,feature,'설정에 지정된 서비스 구성과 운영 출력 비교 · 종단 통신은 별도 범위')
            if 'stp' in analysis['features'] and not analysis.get('stp_disabled'):
                for vlan in analysis['vlans']:
                    add({'kind':'stp_instance','command':'show spanning-tree','vlan':vlan},'stp','설정된 VLAN의 STP 인스턴스 활성 여부')
            for interface in analysis['interfaces']:
                if interface.get('address') and not interface.get('shutdown'):
                    add({'kind':'interface_address','command':brief,'interface':interface['name'],
                         'address':str(ipaddress.IPv4Interface(interface['address']).ip)},'address','running-config의 인터페이스 주소와 운영 출력 비교')
                if interface.get('trunk') and not interface.get('shutdown'):
                    add({'kind':'trunk','command':'show interfaces trunk','interface':interface['name']},'trunk','명시된 trunk 모드의 운영 상태')
            for vlan in analysis['vlans']:
                add({'kind':'vlan_active','command':'show vlan brief','vlan':vlan},'vlan','설정에서 요구한 VLAN의 활성 상태')
            for group in analysis['hsrp']:
                add({'kind':'hsrp','command':'show standby brief',**group},'hsrp','설정된 가상 IP·그룹과 HSRP 참여 상태')
            expected=[]
            for process in analysis['processes']:
                if process['protocol']!='ospf':continue
                ids=[p.get('router_id') for a in analyses.values() for p in a['processes'] if p['protocol']=='ospf' and p.get('router_id')]
                if process.get('router_id') and ids.count(process['router_id'])!=1:continue
                for peer_id,peer in analyses.items():
                    if peer_id==node['node_id'] or not linked(node['node_id'],peer_id):continue
                    for remote in peer['processes']:
                        if remote['protocol']!='ospf' or not remote.get('router_id') or ids.count(remote['router_id'])!=1:continue
                        if any(_area(a,process) is not None and _area(a,process)==_area(b,remote)
                               and ipaddress.IPv4Interface(a['address']).network==ipaddress.IPv4Interface(b['address']).network
                               for a in analysis['interfaces'] for b in peer['interfaces'] if a.get('address') and b.get('address')):
                            expected.append(remote['router_id'])
            if expected and len(expected)==len(set(expected)):
                # Multi-router broadcast networks may legitimately use 2WAY; do not assert FULL there.
                if all(sum(1 for peer in analyses.values() for i in peer['interfaces'] if i.get('address') and
                           ipaddress.IPv4Interface(i['address']).network==ipaddress.IPv4Interface(a['address']).network)<=2
                       for a in analysis['interfaces'] if a.get('address') and not a['name'].lower().startswith('loopback')):
                    add({'kind':'ospf_neighbors','neighbors':sorted(set(expected))},'ospf','연결 관계와 양쪽 설정의 동일 서브넷·area·명시적 router-id, passive 제외')
            def exact_rip(a):
                return any(p['protocol']=='rip' and p.get('rip_version')==2 and p.get('no_auto_summary') for p in a['processes'])
            if ('rip' in analysis['features'] and exact_rip(analysis) and not analysis['has_static_routes']
                    and not any(f in analysis['features'] for f in ('ospf','redistribution','eigrp','bgp'))):
                domain={node['node_id']}; changed=True
                while changed:
                    changed=False
                    for a_id in list(domain):
                        for b_id,b in analyses.items():
                            if b_id in domain or not exact_rip(b) or not linked(a_id,b_id):continue
                            if any(_area(i,p)=='rip' and _area(j,q)=='rip'
                                   and ipaddress.IPv4Interface(i['address']).network==ipaddress.IPv4Interface(j['address']).network
                                   for p in analyses[a_id]['processes'] for q in b['processes']
                                   if p['protocol']==q['protocol']=='rip'
                                   for i in analyses[a_id]['interfaces'] for j in b['interfaces'] if i.get('address') and j.get('address')):
                                domain.add(b_id);changed=True
                direct={str(ipaddress.IPv4Interface(i['address']).network) for i in analysis['interfaces'] if i.get('address') and not i.get('shutdown')}
                prefixes=set()
                for peer_id in domain-{node['node_id']}:
                    peer=analyses[peer_id]
                    if any(f in peer['features'] for f in ('ospf','redistribution','eigrp','bgp')):continue
                    for interface in peer['interfaces']:
                        if interface.get('address') and any(_area(interface,{**p,'passive':[],'passive_default':False})=='rip'
                                                          for p in peer['processes'] if p['protocol']=='rip'):
                            prefix=str(ipaddress.IPv4Interface(interface['address']).network)
                            if prefix not in direct:prefixes.add(prefix)
                for prefix in sorted(prefixes)[:128]:
                    add({'kind':'route','prefix':prefix,'protocol':'R'},'rip','동일 연결 RIP 도메인의 상대 설정에 광고된 원격 prefix, 직접 연결 제외')
        for feature,rule,basis in derived.get(node['node_id'],[]):
            automatic.append({'criterion':rule,'origin':'configuration','basis':basis,'feature':feature,
                              **verify.qualify(verify.evaluate(rule,node.get('commands',[]),node),rule,states)})
            covered.add(feature)
            if feature=='routing':covered.update(f for f in ('ospf','rip','eigrp','isis') if f in analysis['features'])
            if feature=='redistribution':covered.add('redistribution')
        if _ran_reachability(node):
            for rule in pings.get(node['node_id'],[]):
                automatic.append({'criterion':rule,'origin':'configuration','feature':'reachability',
                                  'basis':'라우팅 도메인으로 경로가 있어야 하는 원격 장비 주소로의 ping · 정책 차단 가능 시 판정 보류',
                                  **verify.qualify(verify.evaluate(rule,node.get('commands',[]),node),rule,states)})
        for feature in analysis['features']:
            if feature not in covered:
                automatic.append({'status':'unverified','origin':'configuration','feature':feature,
                                  'reason':LABELS.get(feature,feature)+': 설정과 가능한 명령을 보존했으며 전체 기능·정책 기대값은 자동 확정하지 않았습니다.'})
        node['automatic_checks']=automatic
        if not specs.get(node['node_id'],{}).get('checks'):
            node['checks']=automatic or [{'status':'unverified','reason':'설정에서 자동 판정할 조건을 찾지 못했습니다.'}]
    project['detected_features']=sorted({feature for a in analyses.values() for feature in a['features']})


def _ran_reachability(node):
    return bool(node.get('reachability')) or any(c.get('phase')=='reachability' for c in node.get('commands',[]))


def _vpcs_and_ping(node, pings, specs, states=None):
    """VPCS: the saved startup address/gateway and pings from the host."""
    from . import verify
    if node.get('platform')!='vpcs':return
    automatic=[]
    vpc=node.get('vpcs_config') or {}
    if vpc.get('address'):
        rule={'kind':'vpcs_ip','command':'show ip','address':vpc['address'],'gateway':vpc.get('gateway')}
        automatic.append({'criterion':rule,'origin':'configuration','feature':'vpcs','basis':'VPCS 저장 설정의 주소·게이트웨이와 운영 출력 비교',
                          **verify.evaluate(rule,node.get('commands',[]),node)})
    if _ran_reachability(node):
        for rule in pings:
            automatic.append({'criterion':rule,'origin':'configuration','feature':'reachability','basis':'기본 게이트웨이와 원격 라우터 주소로의 ping',
                              **verify.qualify(verify.evaluate(rule,node.get('commands',[]),node),rule,states or {})})
    if automatic:
        node['automatic_checks']=automatic
        if not specs.get(node['node_id'],{}).get('checks'):node['checks']=automatic

