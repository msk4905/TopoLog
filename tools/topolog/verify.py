"""Configuration-derived verification for routing, tunnels, services and reachability.

Every rule is derived from collected configuration and the GNS3 topology, then
judged only from collected device output. Nothing is assumed to have worked:
a rule without matching evidence is inconclusive, and a result that a policy
(NAT, ACL, firewall) may legitimately cause is never reported as a failure.
"""
import ipaddress
import re

ROUTING = ('ospf', 'rip', 'eigrp', 'isis')
BUILTIN_TYPES = ('ethernet_switch', 'ethernet_hub', 'cloud', 'nat', 'frame_relay_switch', 'atm_switch')
PING_MIN_PERCENT = 40      # 2 of 5 replies: the first packet is commonly lost to ARP.
MAX_PING_TARGETS = 16


def key(name):
    value = (name or '').lower().replace(' ', '')
    for long, short in [('tengigabitethernet', 'te'), ('gigabitethernet', 'gi'), ('fastethernet', 'fa'),
                        ('ethernet', 'eth'), ('serial', 'se'), ('loopback', 'lo'), ('tunnel', 'tu'),
                        ('port-channel', 'po'), ('management', 'ma')]:
        if value.startswith(long):
            return short + value[len(long):]
    return value


def _classful(address):
    first = int(str(address).split('.')[0])
    return 8 if first < 128 else 16 if first < 192 else 24


def _mask_bits(value):
    return ipaddress.IPv4Network('0.0.0.0/' + value).prefixlen


# ---------------------------------------------------------------- parsing

def parse(config, platform='ios'):
    """Structured view of the parts of a running-config this module verifies."""
    marker = re.search(r'^(?:Current configuration|: Saved|ASA Version)[^\n]*\n', config, re.M)
    if marker and platform != 'asa':
        config = config[marker.end():]
    config = re.sub(r'<--- More ---> {0,14}', '', config)
    result = {'platform': platform, 'hostname': None, 'interfaces': [], 'processes': [], 'static': [], 'static6': [],
              'redistribute': [], 'crypto_peers': [], 'bgp': [], 'ssh': False, 'nat_inside': [], 'nat_outside': [],
              'pat': False, 'vtp_mode': None, 'stp_priority': {}, 'ipv6_routing': False,
              'redistribute_filters': {}}
    block, interface, process = None, None, None
    for raw in config.splitlines():
        line = raw.strip()
        if not line or line.startswith('!'):
            continue
        top = not raw[0].isspace()
        if top:
            interface = process = None
            block = line
            m = re.match(r'hostname (\S+)', line)
            if m:
                result['hostname'] = m[1]
            if line.startswith('interface '):
                interface = {'name': line.split(None, 1)[1].strip(), 'shutdown': False, 'protocols': set(),
                             'ipv6': [], 'ipv6_protocols': set(), 'passive': set()}
                result['interfaces'].append(interface)
                continue
            m = re.match(r'(?:ipv6 )?router (ospf|rip|eigrp|isis|bgp)(?:\s+(\S+))?', line)
            if m:
                family = 6 if line.startswith('ipv6 ') else 4
                process = {'protocol': m[1], 'id': m[2], 'family': family, 'networks': [], 'passive': set(),
                           'passive_default': False, 'active': set(), 'redistribute': [], 'neighbors': [],
                           'router_id': None, 'ipv6_af': False, 'distribute_list': False}
                result['processes'].append(process)
                if m[1] == 'bgp':
                    result['bgp'].append(process)
                continue
            m = re.match(r'ip route (\S+) (\S+) (\S+)(?: (\S+))?', line)
            if m:
                try:
                    prefix = ipaddress.IPv4Network(m[1] + '/' + m[2], strict=False)
                    result['static'].append({'prefix': str(prefix), 'via': m[3]})
                except ValueError:
                    pass
            m = re.match(r'route (\S+) (\S+) (\S+) (\S+)', line)
            if m and platform == 'asa':
                try:
                    prefix = ipaddress.IPv4Network(('0.0.0.0' if m[2] == '0' else m[2]) + '/' +
                                                   ('0.0.0.0' if m[3] == '0' else m[3]), strict=False)
                    result['static'].append({'prefix': str(prefix), 'via': m[4]})
                except ValueError:
                    pass
            m = re.match(r'ipv6 route (\S+/\d+) (\S+)', line)
            if m:
                try:
                    result['static6'].append({'prefix': str(ipaddress.IPv6Network(m[1], strict=False)), 'via': m[2]})
                except ValueError:
                    pass
            if line == 'ipv6 unicast-routing':
                result['ipv6_routing'] = True
            if re.match(r'ip ssh\b', line) or (platform == 'asa' and re.match(r'ssh \d', line)):
                result['ssh'] = True
            if re.match(r'ip nat inside source (?:list|route-map) \S+ (?:interface|pool) \S+ overload', line):
                result['pat'] = True
            m = re.match(r'vtp mode (\S+)', line)
            if m:
                result['vtp_mode'] = m[1].lower()
            m = re.match(r'spanning-tree vlan ([\d,-]+) priority (\d+)', line)
            if m:
                for vlan in _vlan_list(m[1]):
                    result['stp_priority'][vlan] = int(m[2])
            continue
        if interface is not None:
            m = re.fullmatch(r'ip address (\S+) (\d+\.\d+\.\d+\.\d+)', line)
            if m:
                try:
                    interface['address'] = str(ipaddress.IPv4Interface(m[1] + '/' + m[2]))
                except ValueError:
                    pass
            m = re.fullmatch(r'ipv6 address (fe80:\S*) link-local', line, re.I)
            if m:
                try:
                    interface['link_local'] = str(ipaddress.IPv6Address(m[1]))
                except ValueError:
                    pass
            m = re.fullmatch(r'ipv6 address (\S+/\d+)(?: eui-64)?', line, re.I)
            if m:
                try:
                    value = ipaddress.IPv6Interface(m[1])
                    if not value.ip.is_link_local:
                        interface['ipv6'].append(str(value))
                except ValueError:
                    pass
            if line == 'shutdown':
                interface['shutdown'] = True
            if re.match(r'ip router isis\b', line):
                interface['protocols'].add('isis')
            m = re.match(r'ip ospf (\d+) area (\S+)', line)
            if m:
                interface['protocols'].add('ospf')
                interface['ospf_area'] = m[2]
            if re.match(r'ipv6 rip \S+ enable', line):
                interface['ipv6_protocols'].add('rip')
            if re.match(r'ipv6 ospf \d+ area', line):
                interface['ipv6_protocols'].add('ospf')
            if re.match(r'ipv6 eigrp \d+', line):
                interface['ipv6_protocols'].add('eigrp')
            if re.match(r'ipv6 router isis', line):
                interface['ipv6_protocols'].add('isis')
            m = re.match(r'tunnel source (\S+)', line)
            if m:
                interface['tunnel_source'] = m[1]
            m = re.match(r'tunnel destination (\S+)', line)
            if m:
                interface['tunnel_destination'] = m[1]
            m = re.match(r'crypto map (\S+)', line)
            if m:
                interface['crypto_map'] = m[1]
            m = re.match(r'nameif (\S+)', line)
            if m:
                interface['nameif'] = m[1]
            if line == 'ip nat inside':
                result['nat_inside'].append(interface['name'])
            if line == 'ip nat outside':
                result['nat_outside'].append(interface['name'])
            continue
        if process is not None:
            tokens = line.split()
            if tokens[0] == 'network':
                process['networks'].append(tokens[1:])
            elif tokens[0] == 'redistribute' and len(tokens) > 1:
                family = 6 if process['ipv6_af'] else process['family']
                process['redistribute'].append(tokens[1])
                result['redistribute'].append((process['protocol'], family, tokens[1]))
                reason = _selective(tokens)
                if reason:
                    result['redistribute_filters'][(process['protocol'], family, tokens[1])] = reason
            elif tokens[0] == 'distribute-list':
                process['distribute_list'] = True
            elif line == 'passive-interface default':
                process['passive_default'] = True
            elif tokens[0] == 'passive-interface' and len(tokens) > 1:
                process['passive'].add(key(tokens[-1]))
            elif tokens[:2] == ['no', 'passive-interface'] and len(tokens) > 2:
                process['active'].add(key(tokens[-1]))
            elif tokens[0] in ('router-id', 'eigrp') and tokens[-1].count('.') == 3:
                process['router_id'] = tokens[-1]
            elif tokens[0] == 'neighbor' and len(tokens) >= 4 and tokens[2] == 'remote-as':
                process['neighbors'].append({'address': tokens[1], 'remote_as': tokens[3]})
            elif tokens[:2] == ['address-family', 'ipv6']:
                process['ipv6_af'] = True
            continue
        if block and block.startswith('crypto map '):
            m = re.match(r'set peer (\S+)', line)
            if m:
                result['crypto_peers'].append(m[1])
    # Network statements → interface protocol membership.
    for interface in result['interfaces']:
        if not interface.get('address') or interface['shutdown']:
            continue
        address = ipaddress.IPv4Interface(interface['address']).ip
        for process in result['processes']:
            if process['family'] != 4 or process['protocol'] not in ('ospf', 'rip', 'eigrp'):
                continue
            if _network_covers(process, address, platform):
                interface['protocols'].add(process['protocol'])
                if process['protocol'] == 'eigrp':
                    interface['eigrp_as'] = process['id']
            k = key(interface['name'])
            if k in process['passive'] or (process['passive_default'] and k not in process['active']):
                interface['passive'].add(process['protocol'])
    for process in result['processes']:
        if process['distribute_list']:
            for protocol, family, source in result['redistribute']:
                if protocol == process['protocol']:
                    result['redistribute_filters'].setdefault((protocol, family, source), 'distribute-list 필터')
    for process in result['processes']:
        if process['protocol'] == 'isis' and process['ipv6_af']:
            for interface in result['interfaces']:
                if 'isis' in interface['protocols'] and interface['ipv6']:
                    interface['ipv6_protocols'].add('isis')
    return result


def _selective(tokens):
    """Why a redistribute statement may pass only part of the source routes, else None."""
    if 'route-map' in tokens:
        index = tokens.index('route-map')
        return 'route-map {} 조건'.format(tokens[index + 1] if index + 1 < len(tokens) else '')
    if tokens[1] == 'isis' and 'level-1-2' not in tokens:
        level = next((t for t in tokens if t in ('level-1', 'level-2')), None)
        return 'IS-IS {} 경로만 재분배'.format(level) if level else 'IS-IS 수준 미지정(기본 수준만 재분배될 수 있음)'
    if tokens[1] == 'ospf' and 'match' in tokens:
        return 'OSPF match 조건'
    return None


def _vlan_list(text):
    values = []
    for part in text.split(','):
        if '-' in part:
            a, b = part.split('-', 1)
            values.extend(range(int(a), int(b) + 1))
        elif part.isdigit():
            values.append(int(part))
    return [v for v in values if 1 <= v <= 4094][:64]


def _network_covers(process, address, platform):
    for tokens in process['networks']:
        try:
            base = ipaddress.IPv4Address(tokens[0])
            if process['protocol'] == 'ospf':
                if len(tokens) < 4 or tokens[2] != 'area':
                    continue
                if platform == 'asa':
                    network = ipaddress.IPv4Network(tokens[0] + '/' + str(_mask_bits(tokens[1])), strict=False)
                    if address in network:
                        return True
                else:
                    wildcard = int(ipaddress.IPv4Address(tokens[1]))
                    if int(address) & ~wildcard == int(base) & ~wildcard:
                        return True
            elif process['protocol'] == 'rip' or (process['protocol'] == 'eigrp' and len(tokens) == 1):
                if address in ipaddress.IPv4Network(tokens[0] + '/' + str(_classful(tokens[0])), strict=False):
                    return True
            elif process['protocol'] == 'eigrp' and len(tokens) >= 2:
                wildcard = int(ipaddress.IPv4Address(tokens[1]))
                if int(address) & ~wildcard == int(base) & ~wildcard:
                    return True
        except (ValueError, IndexError):
            continue
    return False


# ---------------------------------------------------------------- topology

def interface_links(project):
    """{(node_id, interface_key): (peer_node_id, peer_port_name)} from GNS3 links and port names."""
    topology = project.get('topology') or {}
    nodes = {n.get('node_id'): n for n in topology.get('nodes', [])}
    for nid, ports in (project.get('topology_ports') or {}).items():
        nodes.setdefault(nid, {'ports': ports})
    links = project.get('topology_links') or topology.get('links', [])
    result = {}

    def port_name(node, end):
        for port in (node or {}).get('ports', []) or []:
            if port.get('adapter_number') == end.get('adapter_number') and port.get('port_number') == end.get('port_number'):
                return port.get('name')
        return None
    for link in links:
        ends = link.get('nodes', [])
        if len(ends) != 2:
            continue
        names = [port_name(nodes.get(e.get('node_id')), e) for e in ends]
        for i in (0, 1):
            if names[i]:
                result[(ends[i].get('node_id'), key(names[i]))] = (ends[1 - i].get('node_id'), names[1 - i])
    return result


# ---------------------------------------------------------------- derivation

def _commands(platform):
    if platform == 'asa':
        return {'brief': 'show interface ip brief', 'route': 'show route', 'ospf': 'show ospf neighbor'}
    return {'brief': 'show ip interface brief', 'route': 'show ip route', 'ospf': 'show ip ospf neighbor'}


def derive(project, parsed, linked):
    """{node_id: [(feature, rule, basis)]} plus ping plans {node_id: [rule]}."""
    nodes = {n['node_id']: n for n in project.get('nodes', [])}
    links = interface_links(project)
    policy = any(p['nat_inside'] or p['crypto_peers'] for p in parsed.values()) or \
        any(nodes[n].get('platform') == 'asa' for n in parsed) or \
        any('acl' in (nodes[n].get('analysis', {}).get('features') or []) for n in parsed)
    rules = {nid: [] for nid in parsed}
    pings = {nid: [] for nid in nodes}

    def add(nid, feature, rule, basis):
        rules[nid].append((feature, rule, basis))

    # Interfaces: status for connected/loopback/tunnel interfaces.
    for nid, cfg in parsed.items():
        commands = _commands(cfg['platform'])
        for interface in cfg['interfaces']:
            if interface['shutdown'] or not (interface.get('address') or interface['ipv6']):
                continue
            k = key(interface['name'])
            if (nid, k) in links or k.startswith(('lo', 'tu')):
                add(nid, 'interface', {'kind': 'interface_up', 'command': commands['brief'], 'interface': interface['name']},
                    'GNS3 링크로 연결됐거나 Loopback·Tunnel인 설정 인터페이스의 up/up 상태')
            for value in interface['ipv6']:
                add(nid, 'ipv6', {'kind': 'ipv6_address', 'command': 'show ipv6 interface brief', 'interface': interface['name'],
                                  'address': str(ipaddress.IPv6Interface(value).ip)}, 'running-config의 IPv6 주소와 운영 출력 비교')

    # Static routes are expected in the routing table as static entries.
    for nid, cfg in parsed.items():
        commands = _commands(cfg['platform'])
        for route in cfg['static'][:64]:
            add(nid, 'static', {'kind': 'static_route', 'command': commands['route'], 'prefix': route['prefix'], 'via': route['via']},
                '설정한 정적 경로가 라우팅 테이블에 S 경로로 설치됐는지')

    # Neighbor relationships on shared, directly linked subnets.
    def shared(a, b, protocol, family=4):
        for i in parsed[a]['interfaces']:
            for j in parsed[b]['interfaces']:
                if i['shutdown'] or j['shutdown']:
                    continue
                if family == 4:
                    if not (i.get('address') and j.get('address')):
                        continue
                    if protocol and not (protocol in i['protocols'] and protocol in j['protocols']):
                        continue
                    if protocol and (protocol in i['passive'] or protocol in j['passive']):
                        continue
                    if protocol == 'eigrp' and i.get('eigrp_as') != j.get('eigrp_as'):
                        continue
                    if ipaddress.IPv4Interface(i['address']).network == ipaddress.IPv4Interface(j['address']).network:
                        yield i, j
                else:
                    if protocol not in i['ipv6_protocols'] or protocol not in j['ipv6_protocols']:
                        continue
                    for x in i['ipv6']:
                        for y in j['ipv6']:
                            if ipaddress.IPv6Interface(x).network == ipaddress.IPv6Interface(y).network:
                                yield i, j
    for nid, cfg in parsed.items():
        for peer, other in parsed.items():
            if peer == nid or not linked(nid, peer):
                continue
            for i, j in shared(nid, peer, 'eigrp'):
                add(nid, 'eigrp', {'kind': 'eigrp_neighbor', 'command': 'show ip eigrp neighbors',
                                   'address': str(ipaddress.IPv4Interface(j['address']).ip), 'peer': nodes[peer].get('name')},
                    '연결된 상대 장비의 같은 EIGRP AS·동일 서브넷 인터페이스')
                break
            for i, j in shared(nid, peer, 'isis'):
                add(nid, 'isis', {'kind': 'isis_neighbor', 'command': 'show isis neighbors',
                                  'peer': other.get('hostname') or nodes[peer].get('name')},
                    '연결된 상대 장비의 IS-IS 활성 인터페이스')
                break
            for protocol, command in (('ospf', 'show ipv6 ospf neighbor'), ('eigrp', 'show ipv6 eigrp neighbors')):
                pair = next(shared(nid, peer, protocol, 6), None)
                if pair:
                    # OSPFv3 lists the neighbor router-id; IPv6 EIGRP lists the peer's
                    # link-local address on the local interface (no router-id column).
                    router_id = next((p['router_id'] for p in other['processes'] if p['family'] == 6 and
                                      p['protocol'] == protocol and p['router_id']), None) if protocol == 'ospf' else None
                    rule = {'kind': 'ipv6_neighbor', 'command': command, 'peer': nodes[peer].get('name'),
                            'router_id': router_id, 'interface': pair[0]['name']}
                    if pair[1].get('link_local'):
                        rule['peer_address'] = pair[1]['link_local']
                    add(nid, 'ipv6', rule, '연결된 상대 장비의 같은 IPv6 라우팅 프로토콜 인터페이스(같은 링크의 이웃 행)')
        for process in cfg['bgp']:
            for neighbor in process['neighbors'][:32]:
                add(nid, 'bgp', {'kind': 'bgp_peer', 'command': 'show ip bgp summary', 'address': neighbor['address']},
                    '설정한 BGP neighbor의 Established 상태')
        for protocol, family, source in cfg['redistribute']:
            if source in ('connected', 'static'):
                continue
            add(nid, 'redistribution', {'kind': 'redistribution', 'command': 'show ip protocols' if family == 4 else 'show ipv6 protocols',
                                        'into': protocol, 'source': source},
                '라우팅 프로세스가 설정한 재분배 대상을 운영 중인지')
        if cfg['ssh']:
            add(nid, 'ssh', {'kind': 'ssh_enabled', 'command': 'show ssh' if cfg['platform'] == 'asa' else 'show ip ssh'},
                'SSH 설정이 실제로 활성화됐는지(RSA 키 포함)')
        for peer in cfg['crypto_peers'][:8]:
            add(nid, 'vpn', {'kind': 'isakmp_sa', 'command': 'show crypto isakmp sa', 'peer': peer},
                'crypto map 피어와의 ISAKMP(IKE Phase 1) SA · 트래픽 확인 후 다시 수집한 출력 기준')
            add(nid, 'vpn', {'kind': 'ipsec_sa', 'command': 'show crypto ipsec sa', 'peer': peer},
                'crypto map 피어와의 IPsec(Phase 2) SA · 트래픽 확인 후 다시 수집한 출력 기준')
        if cfg['nat_inside'] and cfg['nat_outside']:
            add(nid, 'nat', {'kind': 'nat_interfaces', 'command': 'show ip nat statistics',
                             'inside': cfg['nat_inside'], 'outside': cfg['nat_outside']},
                'ip nat inside/outside로 지정한 인터페이스가 NAT에 반영됐는지')
        if cfg['vtp_mode']:
            add(nid, 'vtp', {'kind': 'vtp_mode', 'command': 'show vtp status', 'mode': cfg['vtp_mode']}, '설정한 VTP 모드')

    # STP root: the switch with the strictly lowest configured priority per VLAN.
    for vlan in sorted({v for cfg in parsed.values() for v in cfg['stp_priority']}):
        candidates = [(cfg['stp_priority'].get(vlan, 32768), nid) for nid, cfg in parsed.items()
                      if cfg['stp_priority'] or any(i['name'].lower().startswith('vlan') for i in cfg['interfaces'])]
        if len(candidates) < 2:
            continue
        candidates.sort()
        if candidates[0][0] < candidates[1][0]:
            root = candidates[0][1]
            add(root, 'stp', {'kind': 'stp_root', 'command': 'show spanning-tree', 'vlan': vlan, 'root': True},
                '가장 낮은 Priority({})를 설정한 스위치가 Root Bridge인지'.format(candidates[0][0]))
            for _, nid in candidates[1:]:
                add(nid, 'stp', {'kind': 'stp_root', 'command': 'show spanning-tree', 'vlan': vlan, 'root': False,
                                 'priority': candidates[0][0]},
                    'Root Bridge로 지정한 스위치(Priority {})를 Root로 인식하는지'.format(candidates[0][0]))

    # Route coverage and reachability from routing domains and redistribution.
    vertices = {}
    for nid, cfg in parsed.items():
        for interface in cfg['interfaces']:
            for protocol in interface['protocols']:
                if protocol in ROUTING:
                    vertices.setdefault((nid, protocol), []).append(interface)
    edges = {v: set() for v in vertices}
    for (a, p) in vertices:
        for (b, q) in vertices:
            if a != b and p == q and linked(a, b) and any(True for _ in shared(a, b, p)):
                edges[(a, p)].add((b, p))
    filtered = {}
    for nid, cfg in parsed.items():
        for protocol, family, source in cfg['redistribute']:
            if family == 4 and (nid, protocol) in vertices and (nid, source) in vertices:
                edges[(nid, source)].add((nid, protocol))      # source routes flow into protocol
                if (protocol, family, source) in cfg['redistribute_filters']:
                    filtered[((nid, source), (nid, protocol))] = '{} {}: {}'.format(
                        nodes[nid].get('name'), protocol, cfg['redistribute_filters'][(protocol, family, source)])
    origins = {}
    for (nid, protocol), interfaces in vertices.items():
        items = [(i, ipaddress.IPv4Interface(i['address'])) for i in interfaces if i.get('address')]
        if any(p == protocol and s == 'connected' and f == 4 for p, f, s in parsed[nid]['redistribute']):
            items += [(i, ipaddress.IPv4Interface(i['address'])) for i in parsed[nid]['interfaces']
                      if i.get('address') and not i['shutdown']]
        origins[(nid, protocol)] = items

    def reaching(target, strict=False, graph=None, skip=None):
        graph = edges if graph is None else graph
        skip = filtered if skip is None else skip
        seen, stack = {target}, [target]
        reverse = {v: {u for u in graph if v in graph[u] and not (strict and (u, v) in skip)} for v in graph}
        while stack:
            v = stack.pop()
            for u in reverse.get(v, ()):
                if u not in seen:
                    seen.add(u)
                    stack.append(u)
        return seen

    def why(sources, skip):
        reasons = sorted({r for (u, v), r in skip.items() if u in sources and v in sources})
        return '; '.join(reasons[:4]) or '선별 재분배'

    def return_path(nid, origin, graph, skip):
        """Reason when the origin reaches nid only through selective redistribution."""
        mine = [v for v in graph if v[0] == nid]
        broad, strict = set(), set()
        for vertex in [v for v in graph if v[0] == origin]:
            broad |= reaching(vertex, False, graph, skip)
            strict |= reaching(vertex, True, graph, skip)
        if any(v in broad for v in mine) and not any(v in strict for v in mine):
            return '반환 경로 ' + why(broad, skip)
        return None
    conditional = {}
    expected = {}
    for nid, cfg in parsed.items():
        if cfg['platform'] not in ('ios', 'asa'):
            continue
        own = [ipaddress.IPv4Interface(i['address']).network for i in cfg['interfaces'] if i.get('address') and not i['shutdown']]
        found, strict_found, broad = {}, set(), set()
        for vertex in [v for v in vertices if v[0] == nid]:
            broad |= reaching(vertex)
            strict = reaching(vertex, True)
            for source in sorted(reaching(vertex)):
                if source[0] == nid:
                    continue
                for interface, value in origins.get(source, []):
                    if any(value.ip in network for network in own):
                        continue
                    found.setdefault(str(value.ip), (source[0], interface['name'], str(value.network)))
                    if source in strict:
                        strict_found.add(str(value.ip))
        expected[nid] = found
        conditional[nid] = {address: why(broad, filtered) for address in found if address not in strict_found}
        commands = _commands(cfg['platform'])
        networks = {}
        for address, item in sorted(found.items()):
            networks.setdefault(item[2], (address, item))   # one route check per remote network
        for network, (address, (origin, name, _)) in sorted(networks.items())[:128]:
            rule = {'kind': 'route_covered', 'command': commands['route'], 'address': address,
                    'network': network, 'origin': '{} {}'.format(nodes[origin].get('name'), name),
                    'origin_node': origin, 'origin_interface': name}
            if address in conditional[nid]:
                rule['conditional'] = conditional[nid][address]
            add(nid, 'routing', rule, '같은 라우팅 도메인 또는 재분배로 연결된 도메인의 원격 대역에 대한 경로')
        # Tunnel destinations must be reachable through the underlay.
        for interface in cfg['interfaces']:
            destination = interface.get('tunnel_destination')
            if destination and not interface['shutdown']:
                add(nid, 'vpn', {'kind': 'route_covered', 'command': commands['route'], 'address': destination,
                                 'network': destination + '/32', 'origin': 'tunnel destination'},
                    '터널 목적지에 대한 underlay 경로')

    # IPv6: the same domain/redistribution model over IPv6 routing protocols.
    v6 = {}
    for nid, cfg in parsed.items():
        for interface in cfg['interfaces']:
            if interface['shutdown']:
                continue
            for protocol in interface['ipv6_protocols']:
                v6.setdefault((nid, protocol), []).append(interface)
    edges6 = {v: set() for v in v6}
    for (a, p) in v6:
        for (b, q) in v6:
            if a != b and p == q and linked(a, b) and any(True for _ in shared(a, b, p, 6)):
                edges6[(a, p)].add((b, p))
    filtered6 = {}
    for nid, cfg in parsed.items():
        for protocol, family, source in cfg['redistribute']:
            if family == 6 and (nid, protocol) in v6 and (nid, source) in v6:
                edges6[(nid, source)].add((nid, protocol))
                if (protocol, family, source) in cfg['redistribute_filters']:
                    filtered6[((nid, source), (nid, protocol))] = '{} IPv6 {}: {}'.format(
                        nodes[nid].get('name'), protocol, cfg['redistribute_filters'][(protocol, family, source)])
    expected6, conditional6 = {}, {}
    for nid, cfg in parsed.items():
        if cfg['platform'] != 'ios' or not any(v[0] == nid for v in v6):
            continue
        own = [ipaddress.IPv6Interface(x).network for i in cfg['interfaces'] for x in i['ipv6']]
        found, strict_found, broad = {}, set(), set()
        for vertex in [v for v in v6 if v[0] == nid]:
            seen = reaching(vertex, False, edges6, filtered6)
            strict = reaching(vertex, True, edges6, filtered6)
            broad |= seen
            for source in sorted(seen):
                if source[0] == nid:
                    continue
                redistributed_connected = any(p == source[1] and f == 6 and s == 'connected' for p, f, s in parsed[source[0]]['redistribute'])
                interfaces = v6[source] + ([i for i in parsed[source[0]]['interfaces'] if i['ipv6']] if redistributed_connected else [])
                for interface in interfaces:
                    for value in interface['ipv6']:
                        value = ipaddress.IPv6Interface(value)
                        if not any(value.ip in network for network in own):
                            found.setdefault(str(value.network), (str(value.ip), source[0], interface['name']))
                            if source in strict:
                                strict_found.add(str(value.network))
        expected6[nid] = found
        conditional6[nid] = {found[n][0]: why(broad, filtered6) for n in found if n not in strict_found}
        for network, (address, origin, name) in sorted(found.items())[:128]:
            rule = {'kind': 'route6_covered', 'command': 'show ipv6 route', 'address': address, 'network': network,
                    'origin': '{} {}'.format(nodes[origin].get('name'), name), 'origin_node': origin, 'origin_interface': name}
            if address in conditional6[nid]:
                rule['conditional'] = conditional6[nid][address]
            add(nid, 'ipv6', rule, '같은 IPv6 라우팅 도메인 또는 재분배로 연결된 도메인의 원격 IPv6 대역에 대한 경로')

    # Reachability (ping) plans: one representative address per remote router.
    for nid in sorted(set(expected) | set(expected6)):
        found = expected.get(nid, {})
        if parsed[nid]['platform'] != 'ios':
            continue
        per_router = {}
        for address, (origin, name, _) in sorted(found.items()):
            current = per_router.get(origin)
            if current is None or (key(name).startswith('lo') and not key(current[1]).startswith('lo')):
                per_router[origin] = (address, name)
        targets = [(address, origin, name) for origin, (address, name) in per_router.items()]
        # Tunnel peers: traffic through the tunnel also brings up on-demand IPsec SAs.
        for interface in parsed[nid]['interfaces']:
            if key(interface['name']).startswith('tu') and interface.get('address') and not interface['shutdown']:
                network = ipaddress.IPv4Interface(interface['address']).network
                for peer, other in parsed.items():
                    for j in other['interfaces']:
                        if peer != nid and j.get('address') and ipaddress.IPv4Interface(j['address']).network == network:
                            targets.insert(0, (str(ipaddress.IPv4Interface(j['address']).ip), peer, j['name']))
        per6 = {}
        for network, (address, origin, name) in sorted(expected6.get(nid, {}).items()):
            current = per6.get(origin)
            if current is None or (key(name).startswith('lo') and not key(current[1]).startswith('lo')):
                per6[origin] = (address, name)
        targets += [(address, origin, name) for origin, (address, name) in per6.items()]
        seen = set()
        from . import icmp_policy
        for address, origin, name in targets:
            if parsed[origin]['platform']=='asa':
                nearby=icmp_policy.near_address(nid,origin,parsed,linked)
                if nearby is None:continue
                address,name=nearby
            if address in seen or len(pings[nid]) >= MAX_PING_TARGETS:
                continue
            seen.add(address)
            context=icmp_policy.context(nid,origin,parsed,nodes,linked) if ':' not in address else {'policy':policy}
            flow=icmp_policy.trace_flow(nid,origin,address,parsed,nodes,linked)
            pinned=nodes[nid].get('ping_sources',{}).get(address)
            rule={'kind': 'ping', 'target': address, 'min_percent': PING_MIN_PERCENT,
                  'destination': '{} {}'.format(nodes[origin].get('name'), name), 'policy': context['policy'], 'policy_context':context, 'diagnostic_flow':flow, **({'source':pinned} if flow and pinned==flow['source'] else {}),
                  'origin_node': origin, 'origin_interface': name}
            if ':' in address:
                reason=conditional6.get(nid,{}).get(address) or return_path(nid,origin,edges6,filtered6)
            else:
                reason=conditional.get(nid,{}).get(address) or return_path(nid,origin,edges,filtered)
            if reason:
                rule['conditional']=reason
            pings[nid].append(rule)
    for nid, node in nodes.items():
        vpc = node.get('vpcs_config') or {}
        if node.get('platform') != 'vpcs' or not vpc.get('gateway'):
            continue
        pings[nid].append({'kind': 'ping', 'target': vpc['gateway'], 'min_percent': PING_MIN_PERCENT,
                           'destination': 'default gateway', 'policy': False})
        gateway = next((g for g, cfg in parsed.items() for i in cfg['interfaces']
                        if i.get('address') and str(ipaddress.IPv4Interface(i['address']).ip) == vpc['gateway']), None)
        for rule in pings.get(gateway, [])[:8]:
            pings[nid].append({**{k:v for k,v in rule.items() if k not in ('source','diagnostic_flow','conditional')}, 'policy': policy})
    return rules, pings


# ---------------------------------------------------------------- evaluation

def latest(evidence, command):
    items = [x for x in evidence if x['command'] == command]
    item=items[-1] if items else None
    return item if item and item.get('status')=='collected' and not item.get('cli_error') else None


def route_rows(text):
    routes = []
    length, current = None, None
    for line in text.splitlines():
        header = re.match(r'^\s*\d+\.\d+\.\d+\.\d+/(\d+) is (variably )?subnetted', line)
        if header:
            length = None if header.group(2) else int(header.group(1))
            continue
        m = re.match(r'^\s*([A-Za-z][A-Za-z0-9*+ %]{0,8}?)\s+(\d+\.\d+\.\d+\.\d+)(?:/(\d+))?(?:\s+(.*))?$', line)
        if not m:
            continue
        code = ' '.join(m[1].replace('*', '').replace('+', '').split())
        address, bits, tail = m[2], m[3], (m[4] or '')
        mask = re.match(r'^(\d+\.\d+\.\d+\.\d+)(?:\s|$)', tail)
        suffix = bits or (str(_mask_bits(mask[1])) if mask else (str(length) if length is not None else None))
        if suffix is None:
            suffix = str(_classful(address))
        try:
            routes.append((ipaddress.IPv4Network(address + '/' + suffix, strict=False), code))
        except ValueError:
            continue
    return routes


def redistribution_entries(block):
    """Source entries of one 'show ip/ipv6 protocols' process block, all output layouts."""
    entries, lines = [], block.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if re.match(r'Redistributing:', stripped):
            # One line, or a list continued on following lines after a trailing comma.
            text, j = stripped.split(':', 1)[1], index
            while text.rstrip().endswith(',') and j + 1 < len(lines):
                j += 1
                text += ' ' + lines[j].strip()
            entries += [x.strip() for x in text.split(',') if x.strip()]
        elif re.match(r'Redistributing External Routes from', stripped) or stripped == 'Redistribution:':
            for following in lines[index + 1:]:
                if not following.strip():
                    break
                if len(following) - len(following.lstrip()) <= indent:
                    break
                item = following.strip().rstrip(',')
                if item.lower() == 'none':
                    continue
                item = re.sub(r'^Redistributing protocol\s+', '', item)
                entries.append(item)
    return entries


def ipv6_neighbor_rows(text):
    """Rows of 'show ipv6 eigrp neighbors' and 'show ipv6 ospf neighbor' with their local interface."""
    rows, lines = [], text.splitlines()
    for index, line in enumerate(lines):
        m = re.match(r'^\s*\d+\s+(?:Link-local address:|([0-9A-Fa-f]*:[0-9A-Fa-f:]+))\s+(\S+)\s+\d+\s', line)
        if m:
            address = m[1]
            if not address:
                following = next((x.strip() for x in lines[index + 1:index + 3] if x.strip()), '')
                address = following.split()[0] if re.match(r'[0-9A-Fa-f]*:[0-9A-Fa-f:]+', following) else None
            rows.append({'interface': m[2], 'address': address})
            continue
        m = re.match(r'^\s*(\d+\.\d+\.\d+\.\d+)\s+\d+\s+(\S+)\s+\S+\s+\S+\s+(\S+)\s*$', line)
        if m:
            rows.append({'router_id': m[1], 'state': m[2], 'interface': m[3]})
    return rows


def _same6(a, b):
    try:
        return ipaddress.IPv6Address(a) == ipaddress.IPv6Address(b)
    except ValueError:
        return False


def interface_states(evidence):
    """{(family, interface_key): (status, protocol)} from the latest interface summaries."""
    states = {}
    for command in ('show ip interface brief', 'show interface ip brief'):
        item = latest(evidence, command)
        if not item:
            continue
        for line in item['text'].splitlines():
            fields = line.split()
            if len(fields) >= 6 and fields[2] in ('YES', 'NO'):
                status = 'administratively down' if 'administratively' in fields else fields[-2]
                states[(4, key(fields[0]))] = (status, fields[-1])
    item = latest(evidence, 'show ipv6 interface brief')
    if item:
        for line in item['text'].splitlines():
            m = re.match(r'^(\S+)\s+\[([^/\]]+)/([^\]]+)\]', line)
            if m:
                states[(6, key(m[1]))] = (m[2].strip(), m[3].strip())
    return states


def qualify(result, rule, states):
    """A failed derived expectation whose premise is not met becomes inconclusive; the record is kept."""
    if result.get('status') != 'failed':
        return result
    origin, name = rule.get('origin_node'), rule.get('origin_interface')
    if origin and name:
        family = 6 if rule['kind'] == 'route6_covered' or ':' in str(rule.get('target', '')) else 4
        observed = states.get(origin, {})
        state = observed.get((family, key(name))) or observed.get((4, key(name)))
        if state and state != ('up', 'up'):
            return {**result, 'status': 'inconclusive', 'classification': 'origin_inactive',
                    'reason': '출발 장비 인터페이스 {}가 수집 시 {} 상태여서 이 대역이 광고되지 않을 수 있습니다. 기대 범위에서 단정하지 않으며 경로·응답 기록은 그대로 보존합니다.'.format(name, ' / '.join(state))}
    if rule.get('conditional'):
        return {**result, 'status': 'inconclusive', 'classification': 'selective_redistribution',
                'reason': '선별 재분배 조건({})에 따라 제외될 수 있는 대역이어서 설정만으로 기대하지 않습니다. 경로 없음·응답 없음 기록은 그대로 보존합니다.'.format(rule['conditional'])}
    return result


def _row(text, interface):
    for line in text.splitlines():
        fields = line.split()
        if fields and key(fields[0]) == key(interface):
            return fields
    return None


def evaluate(rule, evidence, node=None):
    kind = rule['kind']
    if kind == 'ping':
        return _ping(rule, evidence, node)
    command = rule['command']
    item = latest(evidence, command)
    if not item:
        return {'status': 'inconclusive', 'reason': '자동 검증에 필요한 명령 증거가 없습니다.', 'command': command}
    text = re.sub(r'<--- More ---> {0,14}', '', item['text'])
    base = {'command': command, 'evidence': item['file']}

    def done(passed, observed):
        return {'status': 'passed' if passed else 'failed', **base, 'observed': observed}
    if kind == 'interface_up':
        row = _row(text, rule['interface'])
        if not row:
            return done(False, '인터페이스가 출력에 없습니다.')
        tail = ' '.join(row[-3:]).lower()
        status = 'administratively down' if 'administratively' in tail else ' / '.join(row[-2:])
        return done(row[-1].lower() == 'up' and row[-2].lower() == 'up', status)
    if kind == 'ipv6_address':
        compact = str(ipaddress.IPv6Address(rule['address'])).lower()
        found = [a.lower() for a in re.findall(r'([0-9A-Fa-f:]+:[0-9A-Fa-f:]*)', text)]
        normalized = set()
        for value in found:
            try:
                normalized.add(str(ipaddress.IPv6Address(value)))
            except ValueError:
                pass
        return done(compact in normalized, compact if compact in normalized else '주소 없음')
    if kind == 'route6_covered':
        address = ipaddress.IPv6Address(rule['address'])
        best = None
        for line in text.splitlines():
            m = re.match(r'^\s*([A-Z][A-Z0-9]{0,3}(?: [A-Z0-9]{1,3})?)\s+([0-9A-Fa-f:]+/\d+)', line)
            if not m:
                continue
            try:
                network = ipaddress.IPv6Network(m[2], strict=False)
            except ValueError:
                continue
            if address in network and (best is None or network.prefixlen > best[0].prefixlen):
                best = (network, m[1])
        return done(best is not None, '{} {}'.format(best[1], best[0]) if best else '경로 없음')
    if kind in ('static_route', 'route_covered'):
        routes = route_rows(text)
        if not routes and not re.search(r'Gateway of last resort|Codes:', text):
            return {'status': 'inconclusive', 'reason': '라우팅 테이블 형식을 해석하지 못했습니다.', **base}
        if kind == 'static_route':
            target = ipaddress.IPv4Network(rule['prefix'])
            match = [(p, c) for p, c in routes if p == target]
            observed = ', '.join('{} {}'.format(c, p) for p, c in match) or '경로 없음'
            return done(any(c.startswith('S') for p, c in match), observed)
        address = ipaddress.IPv4Address(rule['address'])
        covering = sorted([(p, c) for p, c in routes if address in p], key=lambda x: -x[0].prefixlen)
        if not covering:
            return done(False, '경로 없음')
        prefix, code = covering[0]
        return done(True, '{} {}'.format(code, prefix))
    if kind == 'eigrp_neighbor':
        return done(re.search(r'(?<![\d.])' + re.escape(rule['address']) + r'(?![\d.])', text) is not None,
                    rule['address'] if rule['address'] in text else '이웃 없음')
    if kind == 'isis_neighbor':
        rows = [line for line in text.splitlines() if line.split() and line.split()[0].lower() == str(rule['peer']).lower()]
        state = next((w for line in rows for w in line.split() if w.upper() in ('UP', 'INIT', 'DOWN')), None)
        return done(state is not None and state.upper() == 'UP', state or '이웃 없음')
    if kind == 'ipv6_neighbor':
        if rule.get('router_id'):
            hit = re.search(r'(?m)^\s*' + re.escape(rule['router_id']) + r'\s+\S+\s+(\S+)', text)
            return done(bool(hit and hit[1].upper().startswith('FULL')), hit[1] if hit else '이웃 없음')
        if rule.get('interface'):
            rows = ipv6_neighbor_rows(text)
            local = [r for r in rows if key(r['interface']) == key(rule['interface'])]
            if 'ospf' in command:
                full = [r for r in local if (r.get('state') or '').upper().startswith('FULL')]
                return done(bool(full), '{} {}'.format(full[0]['interface'], full[0]['state']) if full else
                            ('{} {}'.format(local[0]['interface'], local[0].get('state')) if local else '해당 인터페이스 이웃 없음'))
            wanted = rule.get('peer_address')
            match = [r for r in local if not wanted or (r.get('address') and _same6(r['address'], wanted))]
            if match:
                return done(True, '{} {}'.format(match[0]['interface'], match[0].get('address') or '링크로컬 이웃'))
            return done(False, '{} 이웃 {}'.format(local[0]['interface'], local[0].get('address')) if local else '해당 인터페이스 이웃 없음')
        return done(bool(re.search(r'(?im)^\s*\d+\s+(?:Link-local address:|FE80::)', text) or re.search(r'FE80::', text, re.I)),
                    'IPv6 이웃 행' if 'fe80' in text.lower() else '이웃 없음')
    if kind == 'bgp_peer':
        row = next((line.split() for line in text.splitlines() if line.split() and line.split()[0] == rule['address']), None)
        if not row:
            return done(False, 'neighbor 없음')
        return done(row[-1].isdigit(), 'State/PfxRcd ' + row[-1])
    if kind == 'redistribution':
        blocks = [b for b in re.split(r'(?m)^(?=(?:IPv6 )?Routing Protocol is )', text)
                  if re.match(r'(?:IPv6 )?Routing Protocol is "' + re.escape(rule['into']) + r'\b', b)]
        if not blocks:
            return done(False, rule['into'] + ' 프로세스 출력 없음')
        entries = [entry for block in blocks for entry in redistribution_entries(block)]
        sources = {re.split(r'[\s,]+', entry)[0].lower() for entry in entries if entry.strip()}
        return done(rule['source'].lower() in sources, ', '.join(entries[:8]) if entries else '재분배 항목 없음')
    if kind == 'ssh_enabled':
        if command == 'show ssh':
            return done(bool(re.search(r'\d+\.\d+\.\d+\.\d+', text)), '허용 호스트 목록' if re.search(r'\d+\.\d+\.\d+\.\d+', text) else '허용 호스트 없음')
        hit = re.search(r'SSH (Enabled|Disabled)[^\n]*', text)
        return done(bool(hit and hit[1] == 'Enabled'), hit[0] if hit else 'SSH 상태 없음')
    if kind == 'isakmp_sa':
        row = next((line for line in text.splitlines() if rule['peer'] in line), None)
        return done(bool(row and re.search(r'QM_IDLE|ACTIVE|MM_ACTIVE', row)), ' '.join(row.split()) if row else 'SA 없음')
    if kind == 'ipsec_sa':
        block = re.search(r'current_peer\s+' + re.escape(rule['peer']) + r'.*?(?=current_peer|\Z)', text, re.S)
        if not block:
            return done(False, 'SA 없음')
        spi = re.search(r'(?:inbound|outbound) (?:esp|ah) sas:\s*\n\s*spi:\s*(0x[0-9A-Fa-f]+)', block[0])
        packets = re.search(r'#pkts encaps:\s*(\d+)', block[0])
        observed = ('spi ' + spi[1] if spi else 'SA 없음') + (' · encaps ' + packets[1] if packets else '')
        return done(bool(spi), observed)
    if kind == 'nat_interfaces':
        def listed(label):
            m = re.search(label + r' interfaces?:\s*\n((?:\s+\S[^\n]*\n?)+)', text, re.I)
            return [key(x) for x in re.split(r'[,\s]+', m[1]) if x] if m else []
        inside, outside = listed('Inside'), listed('Outside')
        ok = all(key(x) in inside for x in rule['inside']) and all(key(x) in outside for x in rule['outside'])
        return done(ok, 'inside {} · outside {}'.format(', '.join(inside) or '-', ', '.join(outside) or '-'))
    if kind == 'vtp_mode':
        hit = re.search(r'VTP Operating Mode\s*:\s*(\S+)', text)
        return done(bool(hit and hit[1].lower() == rule['mode']), hit[1] if hit else '모드 없음')
    if kind == 'stp_root':
        # Catalyst lists "VLAN0001" at column 0; EtherSwitch modules indent " VLAN1 is executing".
        block = re.search(r'(?ims)^[ \t]*VLAN0*' + str(rule['vlan']) + r'\b(.*?)(?=^[ \t]*VLAN\d|\Z)', text)
        if not block:
            block = re.search(r'(?is)(Spanning tree \d+ is executing.*)', text) if rule['vlan'] == 1 else None
        if not block:
            return done(False, 'VLAN {} 출력 없음'.format(rule['vlan']))
        root = bool(re.search(r'This bridge is the root|We are the root', block[0], re.I))
        if rule['root']:
            return done(root, 'Root Bridge' if root else 'Root 아님')
        priority = re.search(r'Root ID\s+Priority\s+(\d+)|root[^\n]*priority (\d+)', block[0], re.I)
        value = int(next(g for g in priority.groups() if g)) if priority else None
        ok = value is not None and value in (rule['priority'], rule['priority'] + rule['vlan'])
        return done(ok and not root, 'Root Priority {}'.format(value) if value is not None else 'Root 정보 없음')
    if kind == 'vpcs_ip':
        address = re.search(r'IP/MASK\s*:\s*(\S+)', text)
        gateway = re.search(r'GATEWAY\s*:\s*(\S+)', text)
        observed = '{} · GW {}'.format(address[1] if address else '-', gateway[1] if gateway else '-')
        ok = bool(address and address[1] == rule['address'] and (not rule.get('gateway') or (gateway and gateway[1] == rule['gateway'])))
        return done(ok, observed)
    return {'status': 'unverified', 'reason': '이 조건은 자동 해석하지 않습니다.'}


def ping_command(rule, platform):
    target = rule['target']
    if platform == 'vpcs':
        return 'ping ' + target + ' -c 5'
    if ':' in target:
        return 'ping ipv6 ' + target + ' repeat 5 timeout 1'
    source=(' source '+str(ipaddress.IPv4Address(rule['source']))) if rule.get('source') else ''
    return 'ping ' + target + source + ' repeat 5 timeout 1'


def _ping(rule, evidence, node):
    result=_ping_result(rule,evidence,node)
    diagnostic=(node or {}).get('ping_diagnostics',{}).get(result.get('command'))
    if diagnostic and result.get('status')!='passed':
        from .asa_diagnostics import describe
        result['reason']=(result.get('reason','')+' '+describe(diagnostic)).strip()
        result['asa_diagnostic']=diagnostic
        result['additional_evidence']=diagnostic.get('evidence',[])
    return result


def _ping_result(rule, evidence, node):
    platform = (node or {}).get('platform', 'ios')
    command = ping_command(rule, platform)
    attempts=[x for x in evidence if x.get('command')==command]
    item=attempts[-1] if attempts else None
    if not item:
        return {'status': 'inconclusive', 'reason': '도달성 확인(ping)을 실행하지 못했습니다.', 'command': command}
    if item.get('status')!='collected' or item.get('cli_error'):
        return {'status':'inconclusive','command':command,'evidence':item.get('file'),'reason':'마지막 ping 명령을 정상적으로 완료하지 못했습니다.'}
    text = item['text']
    m = re.search(r'Success rate is (\d+) percent', text)
    if m:
        rate = int(m[1])
    elif platform == 'vpcs':
        replies = len(re.findall(r'bytes from ' + re.escape(rule['target']), text))
        if not replies and not re.search(r'timeout|not reachable|host .* unreachable', text, re.I):
            return {'status': 'inconclusive', 'reason': 'VPCS ping 결과를 해석하지 못했습니다.', 'command': command, 'evidence': item['file']}
        rate = min(100, replies * 20)
    else:
        return {'status': 'inconclusive', 'reason': 'ping 결과를 해석하지 못했습니다.', 'command': command, 'evidence': item['file']}
    observed = {'success_percent': rate, 'destination': rule.get('destination'), 'attempts':[{'attempt':x.get('attempt',i+1),'evidence':x.get('file'),'started_at':x.get('started_at')} for i,x in enumerate(attempts)]}
    if rate >= rule.get('min_percent', PING_MIN_PERCENT):
        return {'status': 'passed', 'command': command, 'evidence': item['file'], 'observed': observed}
    if rule.get('policy') and rule.get('policy_context',{}).get('classification')=='policy_limited':
        return {'status':'inconclusive','classification':'policy_limited','command':command,'evidence':item['file'],'observed':observed,
                'reason':'방화벽 설정 참고: '+rule['policy_context']['reason']+' ICMP 응답은 해당 설정과 함께 해석합니다.'}
    if rule.get('policy'):
        return {'status': 'inconclusive', 'command': command, 'evidence': item['file'], 'observed': observed,
                'reason': '응답 없음. 이 프로젝트에는 NAT·ACL·방화벽·VPN 정책이 있어 의도한 차단인지 설정만으로 판단하지 않습니다.'}
    return {'status': 'failed', 'command': command, 'evidence': item['file'], 'observed': observed}
