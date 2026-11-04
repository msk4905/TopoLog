"""Portfolio-style project report (Word .docx and matching Markdown).

Layout follows the user's reference document: title and info table, overview,
key skills, representative-project table, per-project sections (구성과 목적,
GNS3 토폴로지, 주요 설정, 동작 원리, 검증 결과, 콘솔 검증 출력, 참고), the full
project list and evidence notes. Every statement about a project is generated
from collected configuration and judged checks; fields the tool cannot know
(e.g. 지원 분야) are left as clearly marked placeholders. Only the standard
library is used so the report works on a plain Python install.
"""
from collections import Counter
from datetime import datetime, timezone
import html
import ipaddress
import json
from pathlib import Path
import re
import struct
import zipfile

from . import profiles, verify

TEMPLATE = Path(__file__).resolve().parent / 'docx_template'
FONT = 'Malgun Gothic'
MONO = 'Consolas'
PAGE_WIDTH_EMU = 5715000          # 6.25 in, the widest image in the reference document
MAX_REPRESENTATIVE = 6
BUILTIN = verify.BUILTIN_TYPES

FEATURE_NAMES = {'ospf': 'OSPF', 'rip': 'RIP', 'eigrp': 'EIGRP', 'isis': 'IS-IS', 'bgp': 'BGP', 'static': '정적 라우팅',
                 'redistribution': '재분배', 'vlan': 'VLAN', 'trunk': '802.1Q Trunk', 'stp': 'STP', 'vtp': 'VTP',
                 'hsrp': 'HSRP', 'nat': 'NAT/PAT', 'acl': 'ACL', 'vpn': 'GRE·IPsec 터널', 'dhcp': 'DHCP', 'ipv6': 'IPv6',
                 'ssh': 'SSH 원격 관리', 'asa': 'Cisco ASA'}
SKILLS = [
    ('동적 라우팅', ('rip', 'ospf', 'eigrp', 'isis', 'bgp'), '를 구성하고 인접 관계와 경로 학습 상태를 확인했다.'),
    ('라우팅 재분배', ('redistribution',), '서로 다른 라우팅 도메인 사이의 재분배를 구성하고, 재분배된 원격 대역의 경로와 종단 간 통신을 확인했다.'),
    ('정적 라우팅', ('static',), '정적 경로와 기본 경로를 구성하고 라우팅 테이블 설치 여부를 확인했다.'),
    ('스위칭', ('vlan', 'trunk', 'stp', 'vtp'), '를 구성하고 VLAN 활성, Trunk 상태, Root Bridge 선출을 확인했다.'),
    ('게이트웨이 이중화', ('hsrp',), 'HSRP 가상 게이트웨이를 구성하고 그룹 참여 상태(Active/Standby)를 확인했다.'),
    ('주소 변환·보안', ('nat', 'acl', 'vpn', 'ssh', 'asa'), None),
    ('IPv6', ('ipv6',), 'IPv6 주소와 라우팅을 구성하고 IPv6 경로 학습과 통신을 확인했다.'),
    ('서비스', ('dhcp',), 'DHCP 주소 풀을 구성하고 운영 상태를 확인했다.'),
]
PRINCIPLES = {
    'ospf': 'OSPF는 링크 상태 정보를 교환해 각 라우터가 같은 토폴로지 데이터베이스를 만들고 SPF 알고리즘으로 최단 경로를 계산한다. '
            '이웃은 Hello 패킷으로 발견하며 같은 서브넷·Area·타이머가 맞아야 FULL 상태의 인접 관계가 형성된다.',
    'rip': 'RIP은 홉 수를 메트릭으로 사용하는 거리 벡터 프로토콜로, 주기적으로 라우팅 테이블을 이웃에게 광고한다. '
           'RIPv2는 서브넷 마스크를 함께 전달하므로 no auto-summary와 함께 쓰면 서브넷 단위 경로가 유지된다.',
    'eigrp': 'EIGRP는 대역폭·지연을 기반으로 한 복합 메트릭과 DUAL 알고리즘을 사용한다. 같은 AS 번호의 이웃과 Hello로 인접 관계를 만들고 '
             '대체 경로(Feasible Successor)를 미리 계산해 빠르게 수렴한다.',
    'isis': 'IS-IS는 OSI 계층의 링크 상태 프로토콜로 Level-1(Area 내부)과 Level-2(Area 간) 인접 관계를 구분한다. '
            'NET 주소로 라우터와 Area를 식별하며 IP 경로는 TLV로 함께 광고한다.',
    'bgp': 'BGP는 TCP 179번으로 피어와 세션을 맺고 경로 속성(AS-PATH 등)을 기준으로 경로를 선택한다. 세션이 Established 상태가 되어야 경로를 교환한다.',
    'redistribution': '재분배는 한 프로토콜이 학습한 경로를 다른 프로토콜의 경로로 다시 광고하는 기능이다. 프로토콜마다 메트릭 체계가 달라 '
                      '재분배 시 seed metric을 지정해야 하며, 양방향 재분배에서는 route-map·태그로 피드백 루프를 막는 것이 일반적이다.',
    'static': '정적 경로는 관리자가 목적지와 다음 홉을 직접 지정한다. 다음 홉에 도달할 수 있을 때만 라우팅 테이블에 설치되며 '
              '기본 경로(0.0.0.0/0)는 더 구체적인 경로가 없는 모든 목적지에 사용된다.',
    'nat': 'NAT는 내부 주소를 외부 주소로 변환한다. PAT(overload)는 하나의 외부 주소와 서로 다른 포트 번호로 여러 내부 흐름을 구분하고, '
           '정적 NAT·포트 포워딩은 외부 요청을 지정한 내부 서버로 전달한다.',
    'vpn': 'GRE는 원래 패킷을 새 IP 헤더로 감싸 논리적인 점대점 링크를 만들고, IPsec은 IKE(ISAKMP)로 피어를 인증한 뒤 '
           'IPsec SA로 트래픽을 보호한다. 터널은 출발지와 목적지 사이의 underlay 경로가 있어야 동작한다.',
    'vlan': 'VLAN은 하나의 스위치를 여러 브로드캐스트 도메인으로 나누고, 802.1Q Trunk는 태그를 붙여 여러 VLAN을 하나의 링크로 전달한다.',
    'stp': 'STP는 Bridge ID가 가장 낮은 스위치를 Root Bridge로 선출하고, 루프를 만드는 중복 포트를 차단 상태로 두어 L2 루프를 막는다.',
    'hsrp': 'HSRP는 여러 라우터가 하나의 가상 IP를 공유하고 Priority가 높은 라우터가 Active로 게이트웨이 역할을 맡는다. Active 장애 시 Standby가 이어받는다.',
    'ipv6': 'IPv6 라우팅 프로토콜은 링크 로컬 주소(FE80::/10)로 이웃과 통신하고 다음 홉을 지정한다. IPv6 재분배에서는 include-connected로 '
            '연결 대역까지 함께 전달한다.',
    'asa': 'Cisco ASA는 인터페이스마다 nameif와 security-level을 지정하고, 높은 보안 수준에서 낮은 수준으로의 트래픽을 기본 허용하며 '
           '반대 방향은 ACL로 허용해야 한다. OSPF 등 동적 라우팅에도 참여할 수 있다.',
    'ssh': 'SSH는 암호화된 원격 관리 세션을 제공한다. Cisco IOS에서는 도메인 이름과 RSA 키가 있어야 SSH가 활성화된다.',
    'acl': 'ACL은 인터페이스·방향별로 패킷을 허용하거나 차단하며, NAT·IPsec에서는 대상 트래픽을 지정하는 용도로도 쓰인다.',
    'dhcp': 'DHCP 서버는 풀에서 주소·게이트웨이·DNS를 자동 할당하며, 제외 주소로 고정 장비의 주소 충돌을 피한다.',
}


# ---------------------------------------------------------------- data

def _project_topology(output, project):
    path = Path(output) / str(project.get('project_id', '')) / 'topology.json'
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _title_from_drawings(topology):
    best = None
    for drawing in topology.get('drawings', []) or []:
        svg = drawing.get('svg') or ''
        for size, text in re.findall(r'<text[^>]*font-size="([\d.]+)"[^>]*>(.*?)</text>', svg, re.S):
            text = html.unescape(re.sub(r'<[^>]+>', '', text)).strip()
            if text and (best is None or float(size) > best[0]):
                best = (float(size), text)
    return best[1] if best and best[0] >= 14 else None


def _config(node):
    item = next((c for c in node.get('commands', []) if c['command'] == 'show running-config'
                 and c['status'] == 'collected' and not c.get('cli_error')), None)
    return profiles.body(item['text'], item['command']) if item else ''


def _features(node):
    found = list((node.get('analysis') or {}).get('features') or [])
    if node.get('platform') == 'asa':
        found.append('asa')
    return [f for f in found if f != 'other']


def _device_counts(project, topology):
    nodes = {n.get('node_id'): n for n in topology.get('nodes', [])} or {n['node_id']: n for n in project.get('nodes', [])}
    counts = Counter()
    for record in project.get('nodes', []):
        kind = record.get('node_type')
        if kind == 'vpcs':
            counts['PC'] += 1
        elif kind in ('ethernet_switch', 'ethernet_hub'):
            counts['L2 스위치'] += 1
        elif kind == 'cloud':
            counts['Cloud'] += 1
        elif record.get('platform') == 'asa':
            counts['ASAv'] += 1
        elif set(_features(record)) & {'vlan', 'trunk', 'stp', 'vtp'} and not set(_features(record)) & {'ospf', 'rip', 'eigrp', 'isis', 'bgp', 'static'}:
            counts['스위치'] += 1
        elif kind in ('dynamips', 'iou', 'qemu'):
            counts['라우터'] += 1
    links = topology.get('links') or project.get('topology_links') or []
    serial = sum(1 for link in links if link.get('link_type') == 'serial')
    order = ['라우터', '스위치', 'ASAv', 'L2 스위치', 'PC', 'Cloud']
    parts = ['{} {}'.format(k, counts[k]) for k in order if counts[k]]
    link_text = '링크 {}개'.format(len(links)) + (' (직렬 {})'.format(serial) if serial else '')
    return parts, link_text, counts, len(links)


def _checks(node):
    return [c for c in node.get('checks', []) if c.get('status') != 'not_applicable']


def _observed(check):
    value = check.get('observed')
    if isinstance(value, dict):
        if 'success_percent' in value:
            return '{}% 응답'.format(value['success_percent'])
        return ', '.join('{} {}'.format(k, v) for k, v in value.items())
    return str(value) if value not in (None, '') else (check.get('reason') or '')


KIND_LABELS = {'interface_up': '인터페이스 상태', 'interface_address': '인터페이스 주소', 'route_covered': '원격 대역 경로',
               'route6_covered': 'IPv6 원격 경로', 'route': 'RIP 경로', 'static_route': '정적 경로', 'ospf_neighbors': 'OSPF 이웃',
               'eigrp_neighbor': 'EIGRP 이웃', 'isis_neighbor': 'IS-IS 이웃', 'bgp_peer': 'BGP 피어', 'ipv6_neighbor': 'IPv6 이웃',
               'redistribution': '재분배', 'ssh_enabled': 'SSH 활성', 'isakmp_sa': 'ISAKMP SA', 'ipsec_sa': 'IPsec SA',
               'nat_interfaces': 'NAT 인터페이스', 'static_nat': '정적 NAT', 'acl_binding': 'ACL 적용', 'dhcp_pool': 'DHCP 풀',
               'stp_root': 'STP Root', 'stp_instance': 'STP 인스턴스', 'vtp_mode': 'VTP 모드', 'trunk': 'Trunk', 'vlan_active': 'VLAN',
               'hsrp': 'HSRP', 'ping': 'ping 도달성', 'vpcs_ip': 'PC 주소', 'ipv6_address': 'IPv6 주소'}
STATUS_WORD = {'passed': '충족', 'failed': '미충족', 'inconclusive': '판정 보류', 'unverified': '미검증'}


PER_DEVICE = ('ospf_neighbors', 'eigrp_neighbor', 'isis_neighbor', 'bgp_peer', 'ipv6_neighbor', 'isakmp_sa', 'ipsec_sa',
              'stp_root', 'hsrp', 'ping', 'nat_interfaces', 'static_nat', 'redistribution', 'vpcs_ip', 'ssh_enabled')
ORDER = ['ospf_neighbors', 'eigrp_neighbor', 'isis_neighbor', 'bgp_peer', 'ipv6_neighbor', 'route', 'route_covered',
         'route6_covered', 'static_route', 'redistribution', 'isakmp_sa', 'ipsec_sa', 'nat_interfaces', 'static_nat',
         'stp_root', 'trunk', 'vlan_active', 'hsrp', 'ping', 'vpcs_ip', 'ssh_enabled', 'interface_up', 'interface_address']
MAX_ROWS = 12


def _verification_rows(project):
    """(장비, 명령, 확인 결과) rows: per-device for relationships, aggregated for tables and interfaces."""
    grouped = {}
    for node in project.get('nodes', []):
        if node.get('node_type') in BUILTIN:
            continue
        for check in _checks(node):
            rule = check.get('criterion') or {}
            kind = rule.get('kind') or check.get('feature') or 'other'
            grouped.setdefault(kind, {}).setdefault(node.get('name', ''), []).append(check)
    rows = []
    for kind in sorted(grouped, key=lambda k: ORDER.index(k) if k in ORDER else len(ORDER)):
        devices = grouped[kind]
        units = [(name, items) for name, items in devices.items()] if kind in PER_DEVICE else [(', '.join(devices), [c for items in devices.values() for c in items])]
        for name, items in units:
            command = next((c.get('command') for c in items if c.get('command')), '') or (items[0].get('criterion') or {}).get('command', '')
            status = Counter(c['status'] for c in items)
            judged = '충족 {}/{}'.format(status['passed'], len(items))
            extra = [STATUS_WORD[s] + ' ' + str(status[s]) for s in ('failed', 'inconclusive', 'unverified') if status[s]]
            if extra:
                judged += ' · ' + ', '.join(extra)
            text = _summary(kind, items) if kind in PER_DEVICE else _aggregate(kind, items, len(devices))
            rows.append((name, command or '설정 분석', text + ' · ' + judged, kind, status))
    # Keep the table readable: relationship and result rows first, interface rows last.
    return rows[:MAX_ROWS]


def _aggregate(kind, items, devices):
    passed = [c for c in items if c['status'] == 'passed']
    if kind in ('route_covered', 'route', 'route6_covered'):
        codes = Counter(str(_observed(c)).split(' ')[0] for c in passed)
        return '장비 {}대 · 원격 대역 경로 {}/{}'.format(devices, len(passed), len(items)) + \
            (' (' + ', '.join('{} {}'.format(k, v) for k, v in codes.most_common()) + ')' if codes else '')
    if kind == 'static_route':
        return '정적 경로 {}개 중 {}개가 S 경로로 설치'.format(len(items), len(passed))
    if kind == 'interface_up':
        return '연결·Loopback·Tunnel 인터페이스 {}개 중 {}개 up/up'.format(len(items), len(passed))
    if kind == 'interface_address':
        return '설정 주소 {}개 중 {}개가 운영 출력과 일치'.format(len(items), len(passed))
    if kind == 'ipv6_address':
        return 'IPv6 주소 {}개 중 {}개 일치'.format(len(items), len(passed))
    if kind == 'trunk':
        return 'Trunk 포트 {}개 중 {}개 trunking'.format(len(items), len(passed))
    if kind == 'vlan_active':
        return 'VLAN {}개 중 {}개 active'.format(len(items), len(passed))
    return _summary(kind, items)


def _summary(kind, items):
    passed = [c for c in items if c['status'] == 'passed']
    rules = [c.get('criterion') or {} for c in items]
    if kind == 'ospf_neighbors':
        observed = items[0].get('observed') or {}
        return 'OSPF 이웃 ' + ', '.join('{}({})'.format(k, str(v).split('/')[0]) for k, v in observed.items()) if isinstance(observed, dict) else 'OSPF 이웃'
    if kind == 'eigrp_neighbor':
        return 'EIGRP 이웃 ' + ', '.join(r.get('peer') or r.get('address', '') for r in rules)
    if kind == 'isis_neighbor':
        return 'IS-IS 이웃 ' + ', '.join(str(r.get('peer')) for r in rules)
    if kind == 'bgp_peer':
        return 'BGP 피어 ' + ', '.join(r.get('address', '') for r in rules)
    if kind in ('route_covered', 'route', 'route6_covered'):
        codes = Counter(str(_observed(c)).split(' ')[0] for c in passed)
        return '원격 대역 {}개 중 {}개 경로 보유'.format(len(items), len(passed)) + (' (' + ', '.join('{} {}'.format(k, v) for k, v in codes.items()) + ')' if codes else '')
    if kind == 'static_route':
        return '정적 경로 ' + ', '.join(r.get('prefix', '') for r in rules)
    if kind == 'ping':
        return 'ping ' + ', '.join('{}({})'.format(r.get('target'), _observed(c)) for r, c in zip(rules, items))[:300]
    if kind == 'interface_up':
        return '연결 인터페이스 {}개 up/up {}개'.format(len(items), len(passed))
    if kind == 'interface_address':
        return '인터페이스 주소 {}개 일치 {}개'.format(len(items), len(passed))
    if kind == 'redistribution':
        return '재분배 ' + ', '.join('{} → {}'.format(r.get('source'), r.get('into')) for r in rules)
    if kind == 'isakmp_sa':
        return 'ISAKMP SA ' + ', '.join(_observed(c) for c in items)
    if kind == 'ipsec_sa':
        return 'IPsec SA ' + ', '.join(_observed(c) for c in items)
    if kind == 'stp_root':
        return 'STP ' + ', '.join(_observed(c) for c in items)
    if kind == 'ssh_enabled':
        return _observed(items[0])
    if kind == 'vpcs_ip':
        return 'PC 주소 ' + _observed(items[0])
    return ', '.join(filter(None, (_observed(c) for c in items)))[:200] or kind


def _key_config(project):
    """[(label, [lines])] for the devices whose configuration defines the lab."""
    blocks = []
    for node in project.get('nodes', []):
        text = _config(node)
        if not text:
            continue
        cfg = verify.parse(text, node.get('platform', 'ios'))
        protocols = sorted({p['protocol'] for p in cfg['processes']})
        role = []
        if cfg['redistribute']:
            role.append(' ↔ '.join(p.upper() for p in protocols) + ' 경계')
        if cfg['nat_inside']:
            role.append('NAT 경계 라우터')
        if cfg['crypto_peers'] or any(i.get('tunnel_destination') for i in cfg['interfaces']):
            role.append('터널과 IPsec')
        if cfg['stp_priority']:
            role.append('Root Bridge 지정')
        score = len(role) * 10 + len(protocols) + len(cfg['static'])
        lines = _excerpt(text, cfg)
        if lines:
            label = '{} · {}'.format(node.get('name'), ', '.join(role) if role else (', '.join(FEATURE_NAMES.get(f, f) for f in _features(node)[:3]) or '설정'))
            blocks.append((score, label, lines))
    blocks.sort(key=lambda b: -b[0])
    return [(label, lines) for _, label, lines in blocks[:3]]


SECRET = re.compile(r'(?i)^(\s*(?:enable (?:secret|password)|username \S+ (?:secret|password)|password|key|'
                    r'crypto isakmp key|pre-shared-key|passwd|snmp-server community)\b).*$')


def _excerpt(text, cfg):
    keep, block_keep, last_interface = [], False, [None]
    interesting_interface = set()
    for interface in cfg['interfaces']:
        if interface.get('tunnel_destination') or interface.get('crypto_map') or interface['name'] in cfg['nat_inside'] + cfg['nat_outside']:
            interesting_interface.add(interface['name'])
    for raw in text.splitlines():
        if not raw.strip() or raw.strip().startswith('!'):
            block_keep = False
            continue
        top = not raw[0].isspace()
        line = raw.rstrip()
        if top and line.startswith('interface '):
            last_interface[0] = line
        elif top:
            last_interface[0] = None
        if top:
            block_keep = bool(re.match(r'(?:ipv6 )?router |crypto (?:isakmp policy|ipsec transform-set|map)|ip access-list|'
                                       r'interface (?:Tunnel|' + '|'.join(re.escape(n) for n in interesting_interface) + r')\b', line)) \
                if interesting_interface else bool(re.match(r'(?:ipv6 )?router |crypto (?:isakmp policy|ipsec transform-set|map)|ip access-list|interface Tunnel', line))
            if block_keep or re.match(r'ip route |ipv6 route |ip nat inside source|spanning-tree vlan \S+ priority|ipv6 unicast-routing|crypto isakmp key', line):
                keep.append(line)
            continue
        if block_keep and not re.match(r'\s+(?:log-adjacency-changes|log-adj-changes|serial restart-delay|duplex|speed)', line):
            keep.append(line)
        elif not block_keep and re.match(r'\s+(?:ip ospf (?:cost|\d+ area|network|priority)|standby |ip nat (?:inside|outside)|'
                                         r'switchport mode trunk|spanning-tree (?:portfast|bpduguard)|ipv6 (?:rip|ospf|eigrp)|bandwidth|delay )', line):
            current = last_interface[0]
            if current and (not keep or keep[-1] != current) and current not in keep:
                keep.append(current)
            keep.append(line)
    masked = [SECRET.sub(lambda m: m.group(1) + ' ********', line) for line in keep]
    return masked[:28]


def _topic(project, topology):
    title = _title_from_drawings(topology)
    if title:
        return title
    features = []
    for node in project.get('nodes', []):
        for f in _features(node):
            if f not in features:
                features.append(f)
    return ' · '.join(FEATURE_NAMES.get(f, f) for f in features[:4]) or 'GNS3 실습'


def _headline(project):
    """One-line key result for the overview table."""
    parts = []
    nodes = [n for n in project.get('nodes', []) if n.get('node_type') not in BUILTIN]
    checks = [c for n in nodes for c in _checks(n)]
    neighbors = [c for c in checks if (c.get('criterion') or {}).get('kind') in ('ospf_neighbors', 'eigrp_neighbor', 'isis_neighbor', 'bgp_peer', 'ipv6_neighbor')]
    if neighbors:
        parts.append('인접 관계 {}/{} 확인'.format(sum(c['status'] == 'passed' for c in neighbors), len(neighbors)))
    routes = [c for c in checks if (c.get('criterion') or {}).get('kind') in ('route_covered', 'route6_covered', 'route', 'static_route')]
    if routes:
        parts.append('경로 {}/{} 확인'.format(sum(c['status'] == 'passed' for c in routes), len(routes)))
    pings = [c for c in checks if (c.get('criterion') or {}).get('kind') == 'ping']
    if pings:
        parts.append('ping {}/{} 성공'.format(sum(c['status'] == 'passed' for c in pings), len(pings)))
    other = [c for c in checks if c not in neighbors + routes + pings]
    if other:
        parts.append('기타 조건 {}/{} 충족'.format(sum(c['status'] == 'passed' for c in other), len(other)))
    return ', '.join(parts) or '판정 조건 없음'


def build(report, output, files=()):
    """Document blocks for the whole report."""
    output = Path(output)
    projects = [p for p in report.get('projects', []) if p.get('status') in ('completed', 'cleanup_failed', 'error')]
    contexts = []
    for project in projects:
        topology = _project_topology(output, project)
        contexts.append((project, topology, _topic(project, topology)))
    dates = sorted(d for p in projects for d in re.findall(r'(20\d{6})', p.get('name', '')))
    period = ('{}-{}-{} ~ {}-{}-{}'.format(dates[0][:4], dates[0][4:6], dates[0][6:], dates[-1][:4], dates[-1][4:6], dates[-1][6:])
              if dates else str(report.get('started_at', ''))[:10]) + ' (실습 프로젝트 {}개)'.format(len(projects))
    platforms = Counter()
    for project in projects:
        for node in project.get('nodes', []):
            version = next((c['text'] for c in node.get('commands', []) if c['command'] == 'show version'), '')
            model = re.search(r'[Cc]isco (\d{4}\w*|7206VXR|36\d\d|37\d\d|26\d\d)', version)
            if node.get('platform') == 'ios':
                platforms['Cisco IOS 라우터' + ('(' + model.group(1) + ')' if model else '')] += 1
            elif node.get('platform') == 'asa':
                platforms['Cisco ASAv'] += 1
            elif node.get('platform') == 'vpcs':
                platforms['VPCS'] += 1
            elif node.get('node_type') in ('ethernet_switch', 'ethernet_hub'):
                platforms['GNS3 L2 스위치'] += 1
            elif node.get('node_type') == 'cloud':
                platforms['GNS3 Cloud'] += 1
    all_features = Counter(f for p in projects for n in p.get('nodes', []) for f in _features(n))
    blocks = [('title', 'GNS3 네트워크 실습 프로젝트'),
              ('subtitle', 'Cisco GNS3 기반 실습 · TopoLog 자동 수집·검증 결과'),
              ('info', [('지원 분야', '[직접 입력]'), ('실습 기간', period),
                        ('실습 환경', 'GNS3, ' + ', '.join(sorted(platforms)) if platforms else 'GNS3'),
                        ('검증 방법', '프로젝트마다 장비 설정·show 출력·ping 결과를 자동 수집하고, 설정과 연결에서 도출한 조건과 실제 출력을 대조')]),
              ('h1', '1. 프로젝트 개요', False)]
    feature_text = ', '.join(FEATURE_NAMES.get(f, f) for f, _ in all_features.most_common())
    total_checks = [c for p in projects for n in p.get('nodes', []) for c in _checks(n)]
    status = Counter(c['status'] for c in total_checks)
    blocks.append(('p', 'GNS3 가상 환경의 프로젝트 {}개에서 장비를 자동으로 기동하고 설정과 운영 출력을 수집했다. 탐지한 구성은 {}이다.'
                   .format(len(projects), feature_text or '기본 인터페이스 구성')))
    blocks.append(('p', '각 프로젝트는 저장된 장비 설정과 콘솔 출력으로 결과를 확인했다. 인접 관계, 라우팅 테이블, 터널·SA, 변환 테이블처럼 설정이 실제로 '
                   '반영되었는지 보여 주는 출력과 종단 간 ping 결과를 함께 기록했다. 자동 판정 조건 {}개 중 {}개를 충족했다{}.'
                   .format(len(total_checks), status['passed'],
                           ('(미충족 {}, 판정 보류 {}, 미검증 {})'.format(status['failed'], status['inconclusive'], status['unverified'])
                            if status['failed'] or status['inconclusive'] or status['unverified'] else ''))))
    blocks.append(('h1', '2. 핵심 역량', False))
    for lead, keys, tail in SKILLS:
        present = [FEATURE_NAMES.get(k, k) for k in keys if all_features.get(k)]
        if not present:
            continue
        if tail is None:
            checked = {'nat': 'NAT 인터페이스·변환 테이블', 'acl': 'ACL 적용 위치', 'vpn': '터널 상태와 ISAKMP·IPsec SA',
                       'ssh': 'SSH 활성 상태', 'asa': 'ASA 인터페이스와 라우팅 참여'}
            tail = '를 구성하고 ' + ', '.join(checked[k] for k in keys if all_features.get(k)) + '를 확인했다.'
        blocks.append(('bullet', lead, (', '.join(present) + tail) if tail.startswith('를') else tail))
    ranked = sorted(contexts, key=lambda c: (-len({f for n in c[0].get('nodes', []) for f in _features(n)}), c[0].get('name', '')))
    representative = ranked[:MAX_REPRESENTATIVE]
    representative.sort(key=lambda c: c[0].get('name', ''))
    blocks.append(('h1', '3. 대표 프로젝트 한눈에 보기', False))
    blocks.append(('table', ['No', '프로젝트', '주제', '핵심 검증 결과'], [
        ['{:02d}'.format(i + 1), p.get('name', ''), topic, _headline(p)] for i, (p, _, topic) in enumerate(representative)],
        [500, 1450, 3100, 4588], set()))
    for index, (project, topology, topic) in enumerate(representative, 1):
        number = '{:02d}'.format(index)
        parts, link_text, counts, _ = _device_counts(project, topology)
        blocks.append(('h1', '{}  {}'.format(number, topic), True))
        blocks.append(('meta', '프로젝트 {}  ·  {}'.format(project.get('name', ''), ' · '.join(parts + [link_text]))))
        blocks.append(('h2', '구성과 목적'))
        blocks.append(('p', _purpose(project, counts)))
        image = output / str(project.get('project_id', '')) / 'capture' / 'gns3-topology.png'
        native = (project.get('topology_images') or {}).get('native') or {}
        if native.get('status') == 'captured' and image.is_file():
            blocks.append(('image', image))
            blocks.append(('caption', '그림 {}-1. GNS3 토폴로지 (GNS3 자체 Screenshot)'.format(number)))
        else:
            blocks.append(('note', '토폴로지', '이 실행에는 GNS3 자체 Screenshot이 없습니다. 재구성 그림(topology-reconstructed.svg)은 결과 폴더에 있습니다.'))
        configs = _key_config(project)
        if configs:
            blocks.append(('h2', '주요 설정'))
            for label, lines in configs:
                blocks.append(('label', label))
                blocks.append(('code', lines))
                blocks.append(('spacer',))
        present = []
        for node in project.get('nodes', []):
            for f in _features(node):
                if f in PRINCIPLES and f not in present:
                    present.append(f)
        if present:
            blocks.append(('h2', '동작 원리'))
            for f in present[:3]:
                blocks.append(('p', PRINCIPLES[f]))
        rows = _verification_rows(project)
        blocks.append(('h2', '검증 결과'))
        if rows:
            blocks.append(('table', ['장비', '명령', '확인 결과'], [[r[0], r[1], r[2]] for r in rows[:14]], [1100, 2900, 5638], set()))
        else:
            blocks.append(('p', '자동 판정 조건이 없습니다. 원문 출력은 결과 폴더에 보존되어 있습니다.'))
        console = _console_excerpt(project)
        if console:
            blocks.append(('code', console))
            blocks.append(('caption', '그림 {}-2. 콘솔 검증 출력 (수집 원문 발췌, 화면 캡처 아님)'.format(number)))
        notes = _notes(project)
        if notes:
            blocks.append(('note', '참고', notes))
    blocks.append(('h1', '4. 전체 실습 목록', True))
    blocks.append(('p', '수집한 {}개 프로젝트를 이름순으로 정리했다. 대표 사례로 소개한 프로젝트는 음영으로 표시했으며, 모든 프로젝트의 설정·출력·판정 근거는 결과 폴더에 보관되어 있다.'
                   .format(len(contexts))))
    rows, shaded = [], set()
    rep_ids = {p.get('project_id') for p, _, _ in representative}
    for i, (project, topology, topic) in enumerate(sorted(contexts, key=lambda c: c[0].get('name', ''))):
        parts, _, _, _ = _device_counts(project, topology)
        rows.append([project.get('name', ''), topic, ' · '.join(parts)])
        if project.get('project_id') in rep_ids:
            shaded.add(i)
    blocks.append(('table', ['프로젝트', '실습 주제', '장비 구성'], rows, [1500, 5000, 3138], shaded))
    blocks.append(('h1', '5. 증빙 자료', False))
    blocks.append(('bullet', '장비 설정', '프로젝트별 GNS3 프로젝트 정보와 장비의 running-config·startup-config, VPCS 설정 파일'))
    blocks.append(('bullet', '실행 검증', 'TopoLog 결과 폴더({}): 프로젝트별 GNS3 자체 토폴로지 사진, show·ping 원문, 판정 근거(summary.json, collection.json)'
                   .format(output.name)))
    blocks.append(('bullet', '주소 정보', '문서의 IP 주소는 실습용 값이다. 암호·키 값은 문서에서 가렸으며 공개 전 원문 파일의 민감한 값도 확인해야 한다.'))
    return blocks


def _purpose(project, counts):
    nodes = [n for n in project.get('nodes', []) if n.get('node_type') not in BUILTIN]
    protocols = Counter(f for n in nodes for f in _features(n))
    boundary = [n.get('name') for n in nodes if verify.parse(_config(n), n.get('platform', 'ios'))['redistribute']] if nodes else []
    devices = ', '.join('{} {}대'.format(k, v) for k, v in counts.items() if v)
    text = '{}를 연결하고 '.format(devices or '장비')
    if protocols:
        text += ', '.join(FEATURE_NAMES.get(f, f) for f, _ in protocols.most_common(5)) + '를 구성했다.'
    else:
        text += '인터페이스 주소를 구성했다.'
    if boundary:
        text += ' {}가 서로 다른 라우팅 도메인 사이의 재분배를 담당한다.'.format(', '.join(boundary))
    asa = [n.get('name') for n in nodes if n.get('platform') == 'asa']
    if asa:
        text += ' {}는 Cisco ASA로 내부·외부 구간을 나눈다.'.format(', '.join(asa))
    return text + ' 실습 목적: [직접 입력]'


def _console_excerpt(project):
    """Short excerpt of the most telling outputs (neighbors, routes, ping)."""
    preferred = ('show ip ospf neighbor', 'show ospf neighbor', 'show ip eigrp neighbors', 'show isis neighbors', 'show ip bgp summary',
                 'show crypto isakmp sa', 'show interfaces trunk', 'show spanning-tree', 'show standby brief', 'show ip nat translations')
    lines = []
    for node in project.get('nodes', []):
        if node.get('node_type') in BUILTIN:
            continue
        commands = node.get('commands', [])
        chosen = next((c for p in preferred for c in commands if c['command'] == p and c['status'] == 'collected'), None)
        ping = next((c for c in reversed(commands) if c['command'].startswith('ping ') and c['status'] == 'collected'), None)
        for item in (chosen, ping):
            if not item:
                continue
            body = [l.rstrip() for l in profiles.body(item['text'], item['command']).splitlines() if l.strip()]
            if item['command'].startswith('ping '):
                body = [l for l in body if 'Success rate' in l or l.startswith(('!', '.')) or 'bytes from' in l][:3]
            lines.append('{}# {}'.format(node.get('name'), item['command']))
            lines.extend(body[:8])
        if len(lines) > 34:
            break
    return lines[:40]


def _notes(project):
    items = []
    for node in project.get('nodes', []):
        for check in _checks(node):
            if check['status'] in ('failed', 'inconclusive', 'unverified'):
                rule = check.get('criterion') or {}
                kind = rule.get('kind') or check.get('feature') or ''
                what = KIND_LABELS.get(kind, FEATURE_NAMES.get(kind, kind or '조건'))
                items.append('{} {} {}({})'.format(node.get('name'), what, STATUS_WORD.get(check['status']), (check.get('reason') or _observed(check))[:80]))
    if not items:
        return ''
    head = '자동 판정에서 충족하지 않았거나 판정을 보류한 조건 {}건: '.format(len(items))
    return head + '; '.join(items[:6]) + (' 외 {}건'.format(len(items) - 6) if len(items) > 6 else '') + '. 상세 근거는 HTML 보고서와 collection.json에 있다.'


# ---------------------------------------------------------------- docx

def _esc(text):
    return html.escape(str(text), quote=False)


def _run(text, size=20, bold=False, color=None, font=FONT):
    props = '<w:rFonts w:ascii="{0}" w:cs="{0}" w:eastAsia="{0}" w:hAnsi="{0}"/>'.format(font)
    if bold:
        props += '<w:b/><w:bCs/>'
    if color:
        props += '<w:color w:val="{}"/>'.format(color)
    props += '<w:sz w:val="{0}"/><w:szCs w:val="{0}"/>'.format(size)
    return '<w:r><w:rPr>{}</w:rPr><w:t xml:space="preserve">{}</w:t></w:r>'.format(props, _esc(text))


def _cell(text, width, fill=None, header=False):
    shade = '<w:shd w:fill="{}" w:color="auto" w:val="clear"/>'.format(fill) if fill else ''
    border = ''.join('<w:{} w:val="single" w:color="BFBFBF" w:sz="4"/>'.format(s) for s in ('top', 'left', 'bottom', 'right'))
    margin = '<w:tcMar><w:top w:type="dxa" w:w="70"/><w:left w:type="dxa" w:w="110"/><w:bottom w:type="dxa" w:w="70"/><w:right w:type="dxa" w:w="110"/></w:tcMar>'
    run = _run(text, 18, bold=header, color='FFFFFF' if header else '000000')
    return ('<w:tc><w:tcPr><w:tcW w:type="dxa" w:w="{}"/><w:tcBorders>{}</w:tcBorders>{}{}</w:tcPr>'
            '<w:p><w:pPr><w:spacing w:after="0" w:line="270"/></w:pPr>{}</w:p></w:tc>').format(width, border, shade, margin, run)


def _table(headers, rows, widths, shaded=(), label_column=False):
    border = ''.join('<w:{} w:val="single" w:color="auto" w:sz="4"/>'.format(s) for s in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'))
    out = ['<w:tbl><w:tblPr><w:tblW w:type="dxa" w:w="9638"/><w:tblBorders>{}</w:tblBorders></w:tblPr><w:tblGrid>{}</w:tblGrid>'
           .format(border, ''.join('<w:gridCol w:w="{}"/>'.format(w) for w in widths))]
    if headers:
        out.append('<w:tr><w:trPr><w:cantSplit/><w:tblHeader/></w:trPr>' + ''.join(_cell(h, w, '1F497D', True) for h, w in zip(headers, widths)) + '</w:tr>')
    for i, row in enumerate(rows):
        fill = 'EEF3F9' if i in shaded else None
        cells = [_cell(v, w, 'DCE6F1' if (label_column and j == 0) else fill) for j, (v, w) in enumerate(zip(row, widths))]
        out.append('<w:tr><w:trPr><w:cantSplit/></w:trPr>' + ''.join(cells) + '</w:tr>')
    out.append('</w:tbl>')
    return ''.join(out)


def png_size(path):
    data = Path(path).read_bytes()[:32]
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('Not a PNG')
    return struct.unpack('>II', data[16:24])


def _image(rid, index, width, height):
    cx = min(PAGE_WIDTH_EMU, width * 9525)
    cy = int(cx * height / max(1, width))
    if cy > 7000000:                                       # keep tall images on one page
        cy, cx = 7000000, int(7000000 * width / max(1, height))
    return ('<w:p><w:pPr><w:keepNext/><w:spacing w:after="40" w:before="80"/><w:jc w:val="center"/></w:pPr><w:r><w:drawing>'
            '<wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{cx}" cy="{cy}"/><wp:effectExtent t="0" r="0" b="0" l="0"/>'
            '<wp:docPr id="{i}" name="Picture {i}"/><wp:cNvGraphicFramePr><a:graphicFrameLocks xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" noChangeAspect="1"/></wp:cNvGraphicFramePr>'
            '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:nvPicPr><pic:cNvPr id="{i}" name="Picture {i}"/><pic:cNvPicPr><a:picLocks noChangeAspect="1"/></pic:cNvPicPr></pic:nvPicPr>'
            '<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>').format(cx=cx, cy=cy, i=index, rid=rid)


NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
      'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
      'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
      'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"')


def write_docx(blocks, destination, title='GNS3 네트워크 실습 프로젝트'):
    body, media, rels = [], [], []
    for block in blocks:
        kind = block[0]
        if kind == 'title':
            body.append('<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t xml:space="preserve">{}</w:t></w:r></w:p>'.format(_esc(block[1])))
        elif kind == 'subtitle':
            body.append('<w:p><w:pPr><w:pBdr><w:bottom w:val="single" w:color="4F81BD" w:sz="8" w:space="6"/></w:pBdr><w:spacing w:after="240"/></w:pPr>{}</w:p>'
                        .format(_run(block[1], 22, color='595959')))
        elif kind == 'info':
            body.append(_table([], [[k, v] for k, v in block[1]], [2200, 7438], label_column=True))
        elif kind == 'h1':
            body.append('<w:p><w:pPr><w:pStyle w:val="Heading1"/>{}</w:pPr><w:r><w:t xml:space="preserve">{}</w:t></w:r></w:p>'
                        .format('<w:pageBreakBefore/>' if block[2] else '', _esc(block[1])))
        elif kind == 'h2':
            body.append('<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t xml:space="preserve">{}</w:t></w:r></w:p>'.format(_esc(block[1])))
        elif kind == 'meta':
            body.append('<w:p><w:pPr><w:spacing w:after="160"/></w:pPr>{}</w:p>'.format(_run(block[1], 18, color='595959')))
        elif kind == 'p':
            body.append('<w:p><w:pPr><w:spacing w:after="120" w:line="300"/></w:pPr>{}</w:p>'.format(_run(block[1], 20)))
        elif kind == 'bullet':
            body.append('<w:p><w:pPr><w:pStyle w:val="ListParagraph"/><w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr>'
                        '<w:spacing w:after="60" w:line="290"/></w:pPr>{}{}</w:p>'.format(_run(block[1] + '  ', 20, True, '1F497D'), _run(block[2], 20)))
        elif kind == 'note':
            body.append('<w:p><w:pPr><w:spacing w:after="120" w:line="300"/></w:pPr>{}{}</w:p>'.format(_run(block[1] + '  ', 20, True, '1F497D'), _run(block[2], 20)))
        elif kind == 'label':
            body.append('<w:p><w:pPr><w:keepNext/><w:spacing w:after="40" w:before="120"/></w:pPr>{}</w:p>'.format(_run(block[1], 18, True, '1F497D')))
        elif kind == 'code':
            lines = block[1]
            for i, line in enumerate(lines):
                keep = '<w:keepNext/>' if i < len(lines) - 1 else '<w:keepNext w:val="false"/>'
                body.append('<w:p><w:pPr>{}<w:keepLines/><w:shd w:fill="F2F2F2" w:color="auto" w:val="clear"/><w:spacing w:after="0" w:line="250"/>'
                            '<w:ind w:left="120" w:right="120"/></w:pPr>{}</w:p>'.format(keep, _run(line, 17, font=MONO)))
        elif kind == 'spacer':
            body.append('<w:p><w:pPr><w:spacing w:after="80"/></w:pPr></w:p>')
        elif kind == 'image':
            path = Path(block[1])
            try:
                width, height = png_size(path)
            except (OSError, ValueError):
                continue
            index = len(media) + 1
            rid = 'rIdImg{}'.format(index)
            media.append(('media/image{}.png'.format(index), path.read_bytes()))
            rels.append('<Relationship Id="{}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image{}.png"/>'.format(rid, index))
            body.append(_image(rid, index, width, height))
        elif kind == 'caption':
            body.append('<w:p><w:pPr><w:spacing w:after="200" w:before="60"/><w:jc w:val="center"/></w:pPr>{}</w:p>'.format(_run(block[1], 17, color='595959')))
        elif kind == 'table':
            body.append(_table(block[1], block[2], block[3], block[4]))
    section = ('<w:sectPr><w:headerReference w:type="default" r:id="rIdHeader"/><w:footerReference w:type="default" r:id="rIdFooter"/>'
               '<w:pgSz w:w="11906" w:h="16838" w:orient="portrait"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="600" w:footer="600" w:gutter="0"/>'
               '<w:pgNumType/><w:docGrid w:linePitch="360"/></w:sectPr>')
    document = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {}><w:body>{}{}</w:body></w:document>'.format(NS, ''.join(body), section)
    relationships = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                     '<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                     '<Relationship Id="rIdNumbering" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
                     '<Relationship Id="rIdFootnotes" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/>'
                     '<Relationship Id="rIdEndnotes" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/endnotes" Target="endnotes.xml"/>'
                     '<Relationship Id="rIdSettings" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>'
                     '<Relationship Id="rIdFonts" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/fontTable" Target="fontTable.xml"/>'
                     '<Relationship Id="rIdHeader" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>'
                     '<Relationship Id="rIdFooter" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>'
                     '<Relationship Id="rIdTheme" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="theme/theme1.xml"/>'
                     + ''.join(rels) + '</Relationships>')
    content_types = ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default ContentType="image/png" Extension="png"/><Default ContentType="application/vnd.openxmlformats-package.relationships+xml" Extension="rels"/>'
                     '<Default ContentType="application/xml" Extension="xml"/>'
                     '<Override ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml" PartName="/word/document.xml"/>'
                     '<Override ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml" PartName="/word/styles.xml"/>'
                     '<Override ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml" PartName="/word/numbering.xml"/>'
                     '<Override ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml" PartName="/word/footnotes.xml"/>'
                     '<Override ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.endnotes+xml" PartName="/word/endnotes.xml"/>'
                     '<Override ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml" PartName="/word/settings.xml"/>'
                     '<Override ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.fontTable+xml" PartName="/word/fontTable.xml"/>'
                     '<Override ContentType="application/vnd.openxmlformats-officedocument.theme+xml" PartName="/word/theme/theme1.xml"/>'
                     '<Override ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml" PartName="/word/header1.xml"/>'
                     '<Override ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml" PartName="/word/footer1.xml"/>'
                     '<Override ContentType="application/vnd.openxmlformats-package.core-properties+xml" PartName="/docProps/core.xml"/>'
                     '<Override ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml" PartName="/docProps/app.xml"/></Types>')
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            '<dc:title>{}</dc:title><dc:creator>TopoLog</dc:creator><dcterms:created xsi:type="dcterms:W3CDTF">{s}</dcterms:created>'
            '<dcterms:modified xsi:type="dcterms:W3CDTF">{s}</dcterms:modified></cp:coreProperties>').format(_esc(title), s=stamp)
    app = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
           '<Application>TopoLog</Application></Properties>')
    root_rels = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                 '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                 '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>')
    empty_rels = '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', content_types)
        archive.writestr('_rels/.rels', root_rels)
        archive.writestr('docProps/core.xml', core)
        archive.writestr('docProps/app.xml', app)
        archive.writestr('word/document.xml', document)
        archive.writestr('word/_rels/document.xml.rels', relationships)
        for name in ('styles.xml', 'numbering.xml', 'settings.xml', 'fontTable.xml', 'footnotes.xml', 'endnotes.xml', 'header1.xml', 'footer1.xml'):
            archive.write(TEMPLATE / name, 'word/' + name)
        for name in ('header1.xml', 'footer1.xml'):
            archive.writestr('word/_rels/{}.rels'.format(name), empty_rels)
        archive.write(TEMPLATE / 'theme1.xml', 'word/theme/theme1.xml')
        for name, data in media:
            archive.writestr('word/' + name, data)


# ---------------------------------------------------------------- markdown

def write_markdown(blocks, destination, output):
    lines = []
    output = Path(output)
    for block in blocks:
        kind = block[0]
        if kind == 'title':
            lines += ['# ' + block[1], '']
        elif kind == 'subtitle':
            lines += ['**' + block[1] + '**', '']
        elif kind == 'info':
            lines += ['| 항목 | 내용 |', '| --- | --- |'] + ['| {} | {} |'.format(k, v) for k, v in block[1]] + ['']
        elif kind == 'h1':
            lines += ['## ' + block[1], '']
        elif kind == 'h2':
            lines += ['### ' + block[1], '']
        elif kind in ('meta', 'caption'):
            lines += ['*' + block[1] + '*', '']
        elif kind == 'p':
            lines += [block[1], '']
        elif kind in ('bullet',):
            lines += ['- **{}** {}'.format(block[1], block[2])]
        elif kind == 'note':
            lines += ['', '**{}** {}'.format(block[1], block[2]), '']
        elif kind == 'label':
            lines += ['**' + block[1] + '**', '']
        elif kind == 'code':
            lines += ['```'] + block[1] + ['```', '']
        elif kind == 'image':
            try:
                relative = Path(block[1]).relative_to(output).as_posix()
            except ValueError:
                relative = Path(block[1]).name
            lines += ['![]({})'.format(relative), '']
        elif kind == 'table':
            headers, rows = block[1], block[2]
            lines += ['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join('---' for _ in headers) + ' |']
            lines += ['| ' + ' | '.join(str(v).replace('|', '/') for v in row) + ' |' for row in rows] + ['']
    Path(destination).write_text('\n'.join(lines) + '\n', encoding='utf-8')


def write(report, output, files=()):
    """Create report.docx and report-document.md in output; returns their names."""
    blocks = build(report, output, files)
    write_docx(blocks, Path(output) / 'report.docx')
    write_markdown(blocks, Path(output) / 'report-document.md', output)
    return ['report.docx', 'report-document.md']
