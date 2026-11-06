"""Device drivers and explicit verification criteria are separate concepts."""
import ipaddress
import re

PROFILES = {'common', 'ospf', 'rip', 'redistribution', 'asa'}
COMMANDS = {
    'ios': {'common': ['show ip interface brief', 'show ip route', 'show running-config'],
            'ospf': ['show ip ospf neighbor', 'show ip ospf interface brief'],
            'rip': ['show ip protocols', 'show ip rip database'],
            'redistribution': ['show ip protocols', 'show route-map']},
    'asa': {'common': ['show interface ip brief', 'show route', 'show running-config'],
            'asa': ['show access-list', 'show nat'],
            'ospf': ['show ospf neighbor'], 'rip': ['show running-config router'],
            'redistribution': ['show running-config router']},
    'vpcs': {'common': ['show ip']},
}


def platform(node, version='', override=None):
    if node.get('node_type') == 'vpcs':
        detected = 'vpcs'
    elif re.search(r'Adaptive Security Appliance|Cisco ASA', version, re.I):
        detected = 'asa'
    elif re.search(r'Cisco IOS|IOS XE|IOS-XE|IOS Software', version, re.I):
        detected = 'ios'
    else:
        detected = 'unknown'
    if override not in (None, 'auto', 'ios', 'asa', 'vpcs'):
        raise ValueError('Unknown platform override')
    if override not in (None, 'auto'):
        if detected != 'unknown' and detected != override:
            raise ValueError('Platform override conflicts with observed OS')
        return override
    return detected


def detect_profiles(config):
    profiles = ['common']
    if re.search(r'(?im)^\s*router ospf\b', config):
        profiles.append('ospf')
    if re.search(r'(?im)^\s*router rip\b', config):
        profiles.append('rip')
    if re.search(r'(?im)^\s*redistribute\b', config):
        profiles.append('redistribution')
    if re.search(r'(?im)^\s*(?:access-group|nat\s*\(|security-level)\b', config):
        profiles.append('asa')
    return profiles


def commands(os_type, profiles):
    selected = []
    for profile in ['common'] + list(profiles):
        if profile not in PROFILES:
            raise ValueError('Unknown verification profile: ' + str(profile))
        for command in COMMANDS.get(os_type, {}).get(profile, []):
            if command not in selected:
                selected.append(command)
    return selected


def _ipv4(value):
    return str(ipaddress.IPv4Address(value))


def command_for(rule, os_type):
    kind = rule.get('kind')
    if kind == 'ping':
        address = _ipv4(rule['target'])
        minimum = rule.get('min_percent', 100)
        if isinstance(minimum, bool) or not isinstance(minimum, (int, float)) or not 1 <= minimum <= 100:
            raise ValueError('min_percent must be 1..100')
        if os_type == 'vpcs':
            return 'ping ' + address + ' -c 5'
        if os_type == 'asa':
            return 'ping ' + address
        if os_type == 'ios':
            return 'ping ' + address + ' repeat 5 timeout 1'
        raise ValueError('No supported Ping driver')
    if kind == 'ospf_neighbors':
        expected = rule.get('neighbors')
        if not isinstance(expected, list) or not expected:
            raise ValueError('Expected OSPF router IDs are required')
        for value in expected:
            _ipv4(value)
        if os_type not in ('ios', 'asa'):
            raise ValueError('No supported OSPF driver')
        return 'show ip ospf neighbor' if os_type == 'ios' else 'show ospf neighbor'
    if kind == 'route':
        ipaddress.IPv4Network(rule['prefix'], strict=False)
        if rule.get('protocol') not in ('C', 'L', 'S', 'R', 'O', 'O IA', 'O E1', 'O E2', 'O N1', 'O N2', 'D', 'D EX', 'B'):
            raise ValueError('Expected route protocol code required')
        if 'next_hop' in rule:
            _ipv4(rule['next_hop'])
        if os_type not in ('ios', 'asa'):
            raise ValueError('No supported route-table driver')
        return 'show ip route' if os_type == 'ios' else 'show route'
    if kind == 'asa_packet':
        if os_type != 'asa' or rule.get('expected_action') not in ('allow', 'drop'):
            raise ValueError('ASA packet check requires expected_action allow/drop')
        interface = rule.get('interface', '')
        if not isinstance(interface, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,63}', interface):
            raise ValueError('Invalid ASA interface name')
        protocol = rule.get('protocol')
        if protocol not in ('tcp', 'udp'):
            raise ValueError('ASA packet check supports TCP/UDP')
        ports = []
        for key in ('source_port', 'destination_port'):
            value = rule.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
                raise ValueError('Invalid packet port')
            ports.append(value)
        return 'packet-tracer input {} {} {} {} {} {}'.format(
            interface, protocol, _ipv4(rule['source']), ports[0], _ipv4(rule['destination']), ports[1])
    if kind == 'contains':
        allowed = [c for group in COMMANDS.get(os_type, {}).values() for c in group]
        expected = rule.get('expected')
        if rule.get('command') not in allowed:
            raise ValueError('Only driver show commands can be asserted')
        if not isinstance(expected, list) or not expected or any(not isinstance(x, str) or not x.strip() for x in expected):
            raise ValueError('Expected non-empty strings required')
        return rule['command']
    raise ValueError('Unknown verification criterion')


def body(text, command):
    lines = []
    for line in text.splitlines():
        if line.strip() == command or re.search(r'[>#]\s*' + re.escape(command) + r'\s*$', line):
            continue
        if re.fullmatch(r'[\w.:/-]+[>#]\s*', line.strip()):
            continue
        lines.append(line)
    return '\n'.join(lines)


def route_rows(text):
    """IOS CIDR, classic uniform subnet headers, and ASA address/netmask rows."""
    routes, ambiguous = [], []
    length, current = None, None
    for line in text.splitlines():
        header = re.match(r'^\s*\d+\.\d+\.\d+\.\d+/(\d+) is (variably )?subnetted', line)
        if header:
            length = None if header.group(2) else int(header.group(1))
            current = None
            continue
        match = re.match(r'^\s*([A-Z][A-Z0-9*+ %]*?)\s+(\d+\.\d+\.\d+\.\d+)(?:/(\d+))?\s+(.*)$', line)
        if not match:
            if current and re.match(r'^\s+(?:\[|via\b)', line):
                current[2].extend(re.findall(r'\bvia\s+(\d+\.\d+\.\d+\.\d+)', line))
            continue
        protocol = ' '.join(match.group(1).replace('*', '').replace('+', '').split())
        address, bits, tail = match.group(2), match.group(3), match.group(4)
        mask = re.match(r'^(\d+\.\d+\.\d+\.\d+)\s+', tail)
        suffix = bits or (mask.group(1) if mask else length)
        if suffix is None:
            ambiguous.append(address)
            current = None
            continue
        try:
            prefix = ipaddress.IPv4Network(address + '/' + str(suffix), strict=False)
        except ValueError:
            ambiguous.append(address)
            current = None
            continue
        routes.append((prefix, protocol, re.findall(r'\bvia\s+(\d+\.\d+\.\d+\.\d+)', tail)))
        current = routes[-1]
    return routes, ambiguous


def evaluate(rule, evidence, os_type):
    try:
        command = command_for(rule, os_type)
    except (ValueError, TypeError, KeyError) as exc:
        return {'status': 'inconclusive', 'reason': str(exc)}
    item = next((x for x in reversed(evidence) if x['command'] == command), None)
    if not item or item['status'] != 'collected' or item.get('cli_error'):
        return {'status': 'inconclusive', 'reason': 'Complete error-free evidence unavailable', 'command': command}
    text = body(item['text'], command)
    kind = rule['kind']
    if kind == 'contains':
        passed = all(value in text for value in rule['expected'])
        observed_result = {value: value in text for value in rule['expected']}
    elif kind == 'ospf_neighbors':
        observed = {}
        for line in text.splitlines():
            fields = line.split()
            if len(fields) >= 3:
                try:
                    rid = _ipv4(fields[0])
                except ValueError:
                    continue
                state = next((token for token in fields[1:] if re.match(r'^(FULL|INIT|DOWN|ATTEMPT|2WAY|EXSTART|EXCHANGE|LOADING)\b', token)), 'unknown')
                observed[rid] = state
        passed = all(observed.get(rid, '').startswith('FULL') for rid in rule['neighbors'])
        observed_result = {rid: observed.get(rid, 'absent') for rid in rule['neighbors']}
    elif kind == 'route':
        routes, ambiguous = route_rows(text)
        if not routes and not re.search(r'Gateway of last resort|Codes:', text):
            return {'status': 'inconclusive', 'reason': 'Route table format not recognized', 'command': command}
        expected = ipaddress.IPv4Network(rule['prefix'], strict=False)
        if str(expected.network_address) in ambiguous:
            return {'status': 'inconclusive', 'reason': 'Route prefix length not established from evidence', 'command': command}
        passed = any(prefix == expected and protocol == rule['protocol'] and
                     ('next_hop' not in rule or rule['next_hop'] in hops) for prefix, protocol, hops in routes)
        observed_result = [{'prefix': str(prefix), 'protocol': protocol, 'next_hops': hops}
                           for prefix, protocol, hops in routes if prefix == expected]
    elif kind == 'asa_packet':
        # Intermediate phases may say ALLOW while the final result says DROP.
        result = re.search(r'(?is)\bResult:\s*(.*)\Z', text)
        action = re.search(r'(?im)^\s*Action:\s*(allow|drop)\s*$', result.group(1)) if result else None
        if not action:
            return {'status': 'inconclusive', 'reason': 'Final ASA packet-tracer result not recognized', 'command': command}
        passed = action.group(1).lower() == rule['expected_action']
        observed_result = action.group(1).lower()
    else:
        match = re.search(r'Success rate is (\d+) percent', text)
        if match:
            rate = int(match.group(1))
        elif os_type == 'vpcs':
            replies = re.findall(r'(?m)^.*bytes from ' + re.escape(rule['target']) + r'\s+.*', text)
            if len(replies) > 5 or (not replies and not re.search(r'timeout|not reachable', text, re.I)):
                return {'status': 'inconclusive', 'reason': 'Unrecognized VPCS ping result', 'command': command}
            rate = len(replies) * 20
        else:
            return {'status': 'inconclusive', 'reason': 'Unrecognized ping result', 'command': command}
        passed = rate >= rule.get('min_percent', 100)
        observed_result = {'success_percent': rate}
    return {'status': 'passed' if passed else 'failed', 'command': command,
            'evidence': item['file'], 'observed': observed_result}
