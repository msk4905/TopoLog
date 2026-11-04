"""Sequential lifecycle, per-node evidence, explicit verification and export."""
import hashlib
import ipaddress
import json
import re
from pathlib import Path
import time
import uuid
import zipfile
from urllib.error import HTTPError
from datetime import datetime, timezone
from . import profiles
from .transport import Console, CLI_ERROR, EnableSetupRequired
from . import runtime


def now():
    return datetime.now(timezone.utc).isoformat()


def guid(value):
    return str(uuid.UUID(str(value)))


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def error_detail(exc):
    """Bounded API response detail for actionable diagnostics; never log headers."""
    if isinstance(exc, HTTPError):
        detail = 'HTTP {}'.format(exc.code)
        try:
            data = json.loads(exc.read(8192).decode('utf-8', errors='replace'))
            message = data.get('message', data.get('detail', '')) if isinstance(data, dict) else ''
            if isinstance(message, str):
                # Avoid persisting accidental authentication material from a server error.
                message = re.sub(r'(?i)(password|authorization|token|secret)\s*[:=]\s*\S+', r'\1=[redacted]', message)
                detail += ': ' + message[:1000]
        except (OSError, ValueError):
            pass
        return detail
    return '{}: {}'.format(type(exc).__name__, str(exc))


def recovery_hint(detail):
    if re.search(r'Dynamips identifier \d+ is already used', detail, re.I):
        return ('GNS3 may retain a reserved router ID after an earlier creation failure. '
                'Check the first error in the GNS3 server log, then restart the affected backend '
                'after saving other work. TopoLog repairs the local Dynamips configuration before collection; '
                'restart GNS3 and its Desktop backend, wait for the VM connection, then retry. '
                'project files and router IDs do not need to be edited.')
    if 'Could not find Dynamips' in detail:
        return ('The backend cannot locate Dynamips. TopoLog checks the configured local Dynamips path before collection. '
                'After automatic repair, restart GNS3 and its Desktop backend, wait for the VM connection, then retry. '
                'A custom or remote backend requires checking that server installation.')
    if 'is not allowed on this remote server' in detail:
        return ('The Desktop GNS3 server is not running in local mode (/v2/version local=false), so it refuses '
                'absolute image paths outside its images folder. The GNS3 GUI starts its own server with --local, '
                'but it reuses a gns3server.exe that was started another way. Close GNS3, make sure no gns3server.exe '
                'remains, start GNS3 again, then retry. Project files and images do not need to be edited.')
    if 'No path to a VPCS executable has been set' in detail:
        return ('The backend cannot locate VPCS. TopoLog sets the local VPCS path before collection; '
                'restart GNS3 and its Desktop backend after the repair, then retry.')
    return ''


PORT_IN_USE = re.compile(r"bind on address \('([^']*)',\s*(\d+)\).{0,200}?address already in use", re.I | re.S)


def diagnose_start_failure(api, node, detail):
    """Read-only explanation of a node port bind conflict.

    Never changes ports, project files or other projects' nodes; it only lists
    opened projects on the same compute that report the same port.
    """
    match = PORT_IN_USE.search(detail or '')
    if not match:
        return None
    port = int(match.group(2))
    role = 'console' if port == node.get('console') else 'aux' if port == node.get('aux') else 'unknown'
    compute = node.get('compute_id') or 'local'
    holders, lookup = [], 'complete'
    try:
        for project in api.call('/v2/projects') or []:
            if project.get('status') != 'opened' or not project.get('project_id'):
                continue
            for other in api.call('/v2/projects/' + project['project_id'] + '/nodes') or []:
                if other.get('node_id') == node.get('node_id') or (other.get('compute_id') or 'local') != compute:
                    continue
                if port in (other.get('console'), other.get('aux')):
                    holders.append({'project_id': project['project_id'], 'project_name': project.get('name', ''),
                                    'node_name': other.get('name', ''), 'status': other.get('status', ''),
                                    'role': 'console' if other.get('console') == port else 'aux'})
    except Exception as exc:
        lookup = 'failed: ' + error_detail(exc)
    diagnosis = {'kind': 'port_in_use', 'port': port, 'port_role': role, 'compute_id': compute,
                 'bind_address': match.group(1), 'holders': holders, 'holder_lookup': lookup}
    if holders:
        names = ', '.join('{} ({})'.format(h['node_name'], h['project_name'] or h['project_id']) for h in holders[:5])
        diagnosis['hint'] = ('Port {} on compute {} is also assigned to {}. Stop that node or close that project in GNS3, '
                             'then retry. TopoLog does not stop other projects or change console ports.').format(port, compute, names)
    elif lookup == 'complete':
        diagnosis['hint'] = ('No opened GNS3 project on compute {} reports port {}. A leftover process on that compute '
                             '(for example an earlier QEMU console) may still hold it. Save other work, restart the GNS3 VM '
                             'or backend, then retry. Changing the node console port is a project edit left to the user.').format(compute, port)
    else:
        diagnosis['hint'] = ('Port {} on compute {} is in use; the owner could not be listed ({}). '
                             'TopoLog did not change ports or stop other work.').format(port, compute, lookup)
    return diagnosis


def wait_computes(api, required, timeout=300, cancel=None, progress=None):
    progress = progress or (lambda message: None)
    deadline = time.monotonic() + timeout
    while True:
        if cancel and cancel.is_set():
            raise InterruptedError('Compute readiness wait cancelled')
        computes = api.call('/v2/computes')
        connected = {c['compute_id'] for c in computes if c.get('connected') is True}
        missing = sorted(set(required) - connected)
        if not missing:
            return
        remaining = deadline - time.monotonic()
        progress('Waiting for GNS3 compute/VM: {} ({}s remaining)'.format(', '.join(missing), max(0, int(remaining))))
        if remaining <= 0:
            raise TimeoutError('GNS3 compute/VM not connected: ' + ', '.join(missing))
        if cancel:
            cancel.wait(min(2, remaining))
        else:
            time.sleep(min(2, remaining))


def discover(root):
    root = Path(root).resolve(strict=True)
    entries, seen = [], set()
    for path in sorted(root.rglob('*.gns3')):
        record = {'file': str(path.relative_to(root)), 'status': 'pending'}
        try:
            if not path.resolve().is_relative_to(root):
                raise ValueError('Project symlink escapes selected folder')
            content = path.read_bytes()
            data = json.loads(content.decode('utf-8-sig'))
            pid = guid(data['project_id'])
            record.update(project_id=pid, name=data.get('name', path.stem), source_sha256=hashlib.sha256(content).hexdigest())
            if pid in seen:
                raise ValueError('Duplicate project UUID')
            seen.add(pid)
        except (OSError, ValueError, KeyError) as exc:
            record.update(status='excluded', reason=str(exc))
        entries.append(record)
    if not entries:
        raise ValueError('No .gns3 projects found; extract archives including project-files first')
    return root, entries


def draft(root):
    root, entries = discover(root)
    result = {'schema': 'topolog-rules-v1', 'projects': {}}
    for entry in entries:
        if entry['status'] != 'pending':
            continue
        data = json.loads((root / entry['file']).read_text(encoding='utf-8-sig'))
        nodes = {}
        for node in data.get('topology', {}).get('nodes', []):
            nid = guid(node['node_id'])
            # Hints only: hypervisor/template name never proves the guest OS.
            nodes[nid] = {'name': node.get('name', ''), 'platform': 'auto',
                          'profiles': ['common'], 'checks': []}
        result['projects'][entry['project_id']] = {'name': entry['name'], 'profiles': [], 'nodes': nodes}
    return result


def load_rules(path):
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    return validate_rules(data)


def validate_rules(data):
    if data.get('schema') != 'topolog-rules-v1' or not isinstance(data.get('projects'), dict):
        raise ValueError('Expected topolog-rules-v1 project/node criteria file')
    result = {}
    for pid, project in data['projects'].items():
        pid = guid(pid)
        if pid in result or not isinstance(project, dict):
            raise ValueError('Duplicate or invalid project criteria')
        nodes = project.get('nodes', {})
        if not isinstance(nodes, dict):
            raise ValueError('Project nodes must be an object')
        normalized = {}
        for nid, node in nodes.items():
            nid = guid(nid)
            if nid in normalized or not isinstance(node, dict) or not isinstance(node.get('checks', []), list):
                raise ValueError('Invalid or duplicate node criteria')
            if node.get('platform', 'auto') not in ('auto', 'ios', 'asa', 'vpcs'):
                raise ValueError('Invalid node platform')
            for check in node.get('checks', []):
                if not isinstance(check, dict):
                    raise ValueError('Each criterion must be an object')
                # Validate structure before any project state changes.
                candidates = [node['platform']] if node.get('platform', 'auto') != 'auto' else ['ios', 'asa', 'vpcs']
                valid = False
                for candidate in candidates:
                    try:
                        profiles.command_for(check, candidate)
                        valid = True
                    except (KeyError, TypeError, ValueError):
                        pass
                if not valid:
                    raise ValueError('Invalid check for node ' + nid)
            normalized[nid] = node
        for group in [project.get('profiles', [])] + [n.get('profiles', []) for n in normalized.values()]:
            if not isinstance(group, list) or any(p not in profiles.PROFILES for p in group):
                raise ValueError('Invalid profile selection')
        result[pid] = {**project, 'nodes': normalized}
    return result


IOS_TERMINAL_LENGTH = re.compile(r'(?im)^\s*Length:\s*(\d+)\s+lines?\b')


def disable_ios_paging(console, terminal=None):
    """Disable IOS --More-- for this console session only.

    `terminal length` is an EXEC session setting and is not written to the
    running or startup configuration. The original value is read first and
    restored after collection. If it cannot be read, paging stays enabled and
    the transport keeps answering --More-- prompts.
    """
    terminal = terminal if terminal is not None else {'paging': 'unchanged'}
    reply = console.command('show terminal | include Length')
    match = IOS_TERMINAL_LENGTH.search(reply)
    if not match or CLI_ERROR.search(reply):
        terminal['reason'] = 'Current terminal length not identified'
        return terminal
    terminal['original_length'] = int(match.group(1))
    if terminal['original_length'] == 0:
        terminal['paging'] = 'already_disabled'
        return terminal
    terminal['paging'] = 'disable_requested'
    reply = console.command('terminal length 0')
    if CLI_ERROR.search(reply):
        terminal['paging'] = 'unchanged'
        terminal['reason'] = 'terminal length 0 rejected'
        return terminal
    terminal['paging'] = 'disabled_for_session'
    return terminal


def restore_ios_paging(console, terminal):
    if hasattr(console, 'restore_terminal_length'):
        reply = console.restore_terminal_length(terminal['original_length'])
    else:
        reply = console.command('terminal length {}'.format(terminal['original_length']))
    terminal['paging'] = 'restore_failed' if CLI_ERROR.search(reply) else 'restored'
    if terminal['paging'] == 'restore_failed':
        terminal['reason'] = 'Original terminal length restoration rejected by CLI'


DEFAULT_SWITCH_LABEL = '기본 동작 스위치'


def describe_switch(node):
    """Built-in GNS3 Ethernet switch: its whole configuration is the API port map.

    It has no console CLI, so the port map is the collected evidence. All ports
    in access VLAN 1 is GNS3's default behaviour; anything else is recorded as
    configured ports. No operational behaviour is judged for either case.
    """
    ports = (node.get('properties') or {}).get('ports_mapping')
    if not isinstance(ports, list) or not ports:
        return {'collection': 'partial', 'platform': 'ethernet_switch', 'switch_profile': 'unknown',
                'reason': 'GNS3 기본 스위치 · API에서 포트 정보를 읽지 못했습니다.',
                'checks': [{'status': 'not_applicable', 'reason': '내장 스위치는 콘솔 명령 판정 대상이 아닙니다.'}]}
    rows = [{'name': p.get('name', 'port {}'.format(p.get('port_number'))), 'port_number': p.get('port_number'),
             'type': p.get('type', ''), 'vlan': p.get('vlan'), 'ethertype': p.get('ethertype', '')} for p in ports]
    default = all(r['type'] == 'access' and str(r['vlan']) == '1' for r in rows)
    if default:
        summary = '포트 {}개 · 모두 access VLAN 1(GNS3 기본값)'.format(len(rows))
        reason = DEFAULT_SWITCH_LABEL + ' · 특별한 설정 없음 · ' + summary
    else:
        groups = {}
        for r in rows:
            groups.setdefault('{} VLAN {}'.format(r['type'] or 'unknown', r['vlan']), []).append(str(r['name']))
        summary = ' / '.join('{}: {}'.format(k, ', '.join(v)) for k, v in sorted(groups.items()))
        reason = '포트 설정이 있는 기본 스위치 · API 설정 수집 · 동작 판정 없음 · ' + summary
    return {'collection': 'collected', 'platform': 'ethernet_switch', 'switch_profile': 'default' if default else 'configured',
            'switch_ports': rows, 'reason': reason, 'evidence_file': 'switch-ports.json',
            'checks': [{'status': 'not_applicable', 'reason': ('기본 동작 스위치: 판정할 설정이 없습니다.' if default else
                        '포트 VLAN 설정은 기록했으며 내장 스위치의 운영 동작은 판정하지 않습니다.')}]}


BUILTIN_TYPES = ('ethernet_switch', 'ethernet_hub', 'cloud', 'nat', 'frame_relay_switch', 'atm_switch')
BUILTIN_LABELS = {'ethernet_hub': 'GNS3 내장 허브', 'cloud': 'GNS3 Cloud(외부 네트워크 연결)', 'nat': 'GNS3 NAT 노드',
                  'frame_relay_switch': 'GNS3 Frame Relay 스위치', 'atm_switch': 'GNS3 ATM 스위치'}


def describe_builtin(node):
    """Built-in GNS3 nodes without a console: their API settings are the collected evidence."""
    if node.get('node_type') == 'ethernet_switch':
        return describe_switch(node)
    properties = node.get('properties') or {}
    ports = properties.get('ports_mapping') or properties.get('mappings') or []
    names = [str(p.get('interface') or p.get('name') or '') for p in ports if isinstance(p, dict)] if isinstance(ports, list) else []
    label = BUILTIN_LABELS.get(node.get('node_type'), 'GNS3 내장 노드')
    detail = (' · 연결 인터페이스: ' + ', '.join(n for n in names if n)) if any(names) else ''
    return {'collection': 'collected', 'platform': node.get('node_type'), 'switch_profile': 'builtin',
            'builtin_ports': ports if isinstance(ports, list) else [], 'evidence_file': 'builtin-ports.json',
            'reason': label + ' · API 설정 수집 · 콘솔이 없어 동작 판정 대상 아님' + detail,
            'checks': [{'status': 'not_applicable', 'reason': label + ': 콘솔 명령으로 확인할 동작이 없습니다.'}]}


def parse_vpcs(text):
    """Address and gateway from a VPCS startup.vpc.

    Accepts 'ip A G LEN' (what VPCS saves), 'ip A/LEN [G]' and 'ip A M G'.
    """
    for line in (text or '').splitlines():
        tokens = line.split()
        if not tokens or tokens[0] != 'ip' or len(tokens) < 2:
            continue
        if tokens[1] == 'dhcp':
            return {'dhcp': True}
        try:
            if '/' in tokens[1]:
                value = ipaddress.IPv4Interface(tokens[1])
                gateway = tokens[2] if len(tokens) > 2 and tokens[2].count('.') == 3 else None
                return {'address': str(value), 'gateway': gateway}
            address = str(ipaddress.IPv4Address(tokens[1]))
            rest = tokens[2:]
            bits = next((r for r in rest if r.isdigit()), None)
            dotted = [r for r in rest if r.count('.') == 3]
            masks = [r for r in dotted if r.startswith('255.') or r == '0.0.0.0']
            gateway = next((r for r in dotted if r not in masks), None)
            if masks and not bits:
                bits = str(ipaddress.IPv4Network('0.0.0.0/' + masks[0]).prefixlen)
            return {'address': address + '/' + (bits or '24'), 'gateway': gateway}
        except ValueError:
            return {}
    return {}


def obtain_privilege(console, name, enable_password=None, password_provider=None, known_passwords=None, progress=None):
    """Enable with a bounded candidate order; secrets are never written to results.

    Order: configured default password, then no password, then passwords the
    user typed earlier in this run (memory only), then one masked user prompt.
    """
    progress = progress or (lambda message: None)
    record = {'attempted': True, 'authentication_bytes': 'omitted', 'attempts': []}
    candidates = []
    if enable_password:
        candidates.append(('default', enable_password))
    candidates.append(('none', None))
    for value in known_passwords or []:
        candidates.append(('earlier_input', value))
    seen, ordered = set(), []
    for source, value in candidates:
        if (value or '') not in seen:
            seen.add(value or '');ordered.append((source, value))
    state_ok = True

    def attempt(source, value):
        nonlocal state_ok
        try:
            ok = bool(console.enter_enable(value))
            record['attempts'].append({'source': source, 'result': 'elevated' if ok else 'rejected'})
            return ok
        except EnableSetupRequired:
            record['attempts'].append({'source': source, 'result': 'setup_required'})
            record['setup_required'] = True
        except (ValueError, TimeoutError, ConnectionError, OSError) as exc:
            record['attempts'].append({'source': source, 'result': 'error', 'error': type(exc).__name__})
        if getattr(console, 'enable_pending', False):
            try:
                console.restore_privilege()
            except Exception:
                state_ok = False
        if getattr(console, 'enable_pending', False):
            state_ok = False
        return False

    progress('Requesting privileged EXEC (one attempt)')
    elevated = False
    for source, value in ordered:
        if attempt(source, value):
            elevated, record['source'] = True, source
            break
        if not state_ok or record.get('setup_required'):
            break
    if not elevated and state_ok and not record.get('setup_required'):
        if password_provider:
            supplied = password_provider(name)
            if supplied:
                if attempt('user_input', supplied):
                    elevated, record['source'] = True, 'user_input'
                    if known_passwords is not None and supplied not in known_passwords:
                        known_passwords.append(supplied)
            else:
                record['user_prompt'] = 'cancelled'
        else:
            record['user_prompt'] = 'unavailable'
    record['elevated'] = elevated
    if not state_ok:
        record['state'] = 'uncertain'
    return elevated, record


def disable_asa_paging(console, terminal):
    """ASA: session-only 'terminal pager 0', restored to the value 'show pager' reported."""
    reply = console.command('show pager')
    match = re.search(r'pager lines (\d+)', reply)
    if not match:
        if re.search(r'no pager', reply):
            terminal['paging'] = 'already_disabled'
        return
    terminal['original_length'] = int(match.group(1))
    terminal['paging'] = 'disable_requested'
    reply = console.command('terminal pager 0')
    terminal['paging'] = 'disable_failed' if CLI_ERROR.search(reply) else 'disabled_for_session'


def restore_asa_paging(console, terminal):
    reply = console.command('terminal pager {}'.format(terminal['original_length']))
    terminal['paging'] = 'restore_failed' if CLI_ERROR.search(reply) else 'restored'
    if terminal['paging'] == 'restore_failed':
        terminal['reason'] = 'Original ASA pager length restoration rejected by CLI'


def collect_node(api, node, folder, spec, selected, ready_timeout=120, cancel=None, console_factory=Console, progress=None, enable=False, enable_password=None, password_provider=None, known_passwords=None):
    progress = progress or (lambda message: None)
    folder.mkdir(parents=True)
    result = {'node_id': node['node_id'], 'name': node.get('name', ''), 'node_type': node['node_type'],
              'collection': 'unsupported', 'platform': 'unknown', 'commands': [], 'checks': [],
              'profiles': [], 'startup_configuration': {'status': 'not_collected'}}
    console = None
    try:
        if node['node_type'] in BUILTIN_TYPES:
            result.update(describe_builtin(node))
            if result.get('switch_ports') is not None:
                save(folder / 'switch-ports.json', {'source': 'GNS3 API node properties.ports_mapping', 'ports': result['switch_ports']})
            elif result.get('builtin_ports') is not None:
                save(folder / 'builtin-ports.json', {'source': 'GNS3 API node properties', 'ports': result['builtin_ports']})
            return result
        if node.get('console_type') != 'telnet':
            result['reason'] = 'No supported console driver; API configuration is retained'
            return result
        console = console_factory(node.get('console_host') if node.get('console_host') not in (None, '', '0.0.0.0', '::') else api.host,
                                  node['console'], ready_timeout=ready_timeout, cancel=cancel, progress=progress)
        result['collection'] = 'failed'
        console.connect()
        def execute(command, optional=False):
            existing = next((c for c in result['commands'] if c['command'] == command), None)
            if existing:
                return existing
            record = {'command': command, 'started_at': now(), 'status': 'failed',
                      'file': '{:02d}.txt'.format(len(result['commands']) + 1)}
            if optional: record['optional'] = True
            result['commands'].append(record)
            progress('Command: ' + command)
            text = console.command(command)
            # ASA leaves its pager prompt plus 14 erase spaces in the echoed stream.
            text = re.sub(r'<--- More ---> {0,14}', '', text)
            (folder / record['file']).write_text(text, encoding='utf-8')
            record.update(status='collected', text=text, cli_error=bool(CLI_ERROR.search(text)), finished_at=now())
            return record
        version = '' if node['node_type'] == 'vpcs' else execute('show version')['text']
        if node['node_type'] == 'vpcs':
            try:
                raw = api.raw('/v2/projects/{}/nodes/{}/files/startup.vpc'.format(guid(node['project_id']), guid(node['node_id'])))
                (folder / 'startup.vpc').write_bytes(raw)
                result['vpcs_config'] = parse_vpcs(raw.decode('utf-8', errors='replace'))
                result['startup_configuration'] = {'status': 'collected', 'file': 'startup.vpc', 'source': 'api', 'collected_at': now()}
            except Exception:
                result['startup_configuration']['reason'] = 'VPCS startup.vpc unavailable from API'
        os_type = profiles.platform(node, version, spec.get('platform'))
        result['platform'] = os_type
        if os_type == 'unknown':
            result.update(collection='unsupported', reason='Guest OS not identified; specify a verified driver')
            return result
        if os_type in ('ios', 'asa') and getattr(console, 'prompt', '').endswith('>'):
            if enable:
                elevated, result['privilege'] = obtain_privilege(console, node.get('name', '장비'), enable_password,
                                                                 password_provider, known_passwords, progress)
                if not elevated:
                    privilege = result['privilege']
                    if privilege.get('setup_required'):
                        reason = '장비가 새 enable 암호 설정을 요구합니다. 설정 변경이 되므로 TopoLog는 입력하지 않았습니다. 장비에서 enable 암호를 설정한 뒤 다시 실행하세요.'
                    elif privilege.get('state') == 'uncertain':
                        reason = 'enable 시도 후 콘솔 상태를 확인하지 못해 추가 암호를 보내지 않았습니다.'
                    elif privilege.get('user_prompt') == 'cancelled':
                        reason = 'Enable 권한을 얻지 못했고 암호 입력을 취소했습니다.'
                    else:
                        reason = 'Enable 권한을 얻지 못했습니다. 암호를 확인하세요. 같은 암호를 반복해 보내지 않습니다.'
                    result.update(collection='requires_privilege', reason=reason)
                    return result
            else:
                result.update(collection='requires_privilege', reason='Privileged EXEC prompt (#) required for configuration and policy collection; current prompt is unprivileged (>). No enable/password was sent.')
                return result
        if os_type == 'ios':
            result['terminal'] = {'paging': 'unchanged'}
            disable_ios_paging(console, result['terminal'])
        if os_type == 'asa':
            result['terminal'] = {'paging': 'unchanged', 'platform': 'asa'}
            disable_asa_paging(console, result['terminal'])
        config = execute('show running-config') if os_type in ('ios', 'asa') else None
        detected = profiles.detect_profiles(profiles.body(config['text'], config['command'])) if config and not config['cli_error'] else ['common']
        combined = list(dict.fromkeys(list(selected) + spec.get('profiles', []) + detected + (['asa'] if os_type == 'asa' else [])))
        result.update(profiles=combined, detected_profile_candidates=detected)
        for command in profiles.commands(os_type, combined):
            execute(command)
        if config and not config['cli_error']:
            from . import features
            analysis = features.analyze(profiles.body(config['text'],config['command']),os_type)
            result['analysis'] = analysis
            for command in features.commands(analysis):
                execute(command, optional=True)
        for check in spec.get('checks', []):
            try:
                command = profiles.command_for(check, os_type)
            except (ValueError, KeyError, TypeError):
                # Driver mismatch is recorded as inconclusive, never a pass.
                continue
            execute(command)
        props = node.get('properties', {})
        config_path = None
        if node['node_type'] == 'dynamips' and isinstance(props.get('dynamips_id'), int):
            config_path = 'configs/i{}_startup-config.cfg'.format(props['dynamips_id'])
        elif node['node_type'] == 'iou':
            config_path = 'startup-config.cfg'
        if config_path:
            try:
                raw = api.raw('/v2/projects/{}/nodes/{}/files/{}'.format(guid(node['project_id']), guid(node['node_id']), config_path))
                if not raw:
                    raise ValueError('Empty configuration')
                (folder / 'startup.cfg').write_bytes(raw)
                result['startup_configuration'] = {'status': 'collected', 'file': 'startup.cfg', 'source':'api', 'collected_at': now()}
            except Exception:
                result['startup_configuration']['reason'] = 'Saved startup file unavailable from API'
        if result['startup_configuration']['status']!='collected' and os_type in ('ios','asa'):
            startup=execute('show startup-config',optional=True)
            content=profiles.body(startup['text'],'show startup-config').strip()
            if (content and not startup['cli_error'] and re.search(r'(?im)^(?:hostname |version |ASA Version |: Saved)',content)
                    and not re.search(r'(?i)non-volatile configuration.*not present|startup-config.*not (?:present|found)',content)):
                (folder/'startup.cfg').write_text(content+'\n',encoding='utf-8')
                result['startup_configuration']={'status':'collected','file':'startup.cfg','source':'console','collected_at':now()}
        result['collection'] = 'partial' if any(c.get('cli_error') and not c.get('optional') for c in result['commands']) else 'collected'
    except (InterruptedError, KeyboardInterrupt):
        result.update(collection='cancelled', reason='Collection cancelled by user')
    except Exception as exc:
        result['reason'] = '{}: {}'.format(type(exc).__name__, str(exc))
    finally:
        if console:
            terminal = result.get('terminal', {})
            if terminal.get('paging') in ('disabled_for_session', 'disable_requested'):
                try:
                    if terminal.get('platform') == 'asa':
                        restore_asa_paging(console, terminal)
                    else:
                        restore_ios_paging(console, terminal)
                except Exception as exc:
                    terminal.update(paging='restore_failed', reason=error_detail(exc))
                if terminal['paging'] == 'restore_failed':
                    result['cleanup_errors'] = [terminal['reason']]
                    if result['collection'] == 'collected':
                        result['collection'] = 'partial'
            if getattr(console,'enable_changed',False) or getattr(console,'enable_pending',False):
                try:
                    console.restore_privilege()
                    result['privilege']['restoration']='restored'
                except Exception:
                    result.setdefault('cleanup_errors',[]).append('Original EXEC privilege restoration failed')
                    result.setdefault('privilege',{})['restoration']='failed'
                    if result['collection']=='collected':result['collection']='partial'
            if getattr(console,'authentication_redacted',False):
                result['session_evidence_note']='Enable authentication receive bytes omitted; remaining RX preserved'
            for command in result['commands']:
                if 'finished_at' not in command:
                    command.update(finished_at=now(), reason=result.get('reason', 'Command incomplete'))
                    if result['collection'] == 'cancelled':
                        command['status'] = 'cancelled'
            console.close()
            (folder / 'session.rx.bin').write_bytes(console.wire)
            if getattr(console, 'pager_resends', 0):
                result['pager_key_resends'] = console.pager_resends
        if result['collection'] != 'not_applicable' and node['node_type'] not in BUILTIN_TYPES:
            for check in spec.get('checks', []):
                result['checks'].append({'criterion': check, **profiles.evaluate(check, result['commands'], result['platform'])})
        if not result['checks']:
            result['checks'].append({'status': 'unverified', 'reason': 'No explicit expected behavior configured'})
        save(folder / 'collection.json', result)
    return result


def verdict(nodes):
    # Built-in switches have no judged behaviour; they never block or create a pass.
    nodes = [n for n in nodes if n.get('node_type') not in BUILTIN_TYPES]
    if any(c['status'] == 'failed' for n in nodes for c in n['checks']):
        return 'failed'
    if not nodes or any(n['collection'] != 'collected' or not n['checks'] or any(c['status'] != 'passed' for c in n['checks']) for n in nodes):
        return 'inconclusive'
    return 'passed'


class Runner:
    def __init__(self, api, output, rules=None, collector=collect_node, settle_seconds=10, ready_timeout=120, cancel=None, progress=None, compute_timeout=300, enable=True, enable_password=None, password_provider=None, capture=False, reachability=True, console_factory=Console):
        self.api, self.output = api, Path(output)
        self.rules, self.collector = rules or {}, collector
        self.settle_seconds, self.ready_timeout = settle_seconds, ready_timeout
        self.cancel, self.progress = cancel, progress or (lambda message: None)
        self.compute_timeout = compute_timeout
        self.enable,self.enable_password=enable,enable_password
        self.password_provider,self.capture=password_provider,capture
        self.reachability,self.console_factory=reachability,console_factory
        # Passwords typed during this run stay in memory only and are tried on later devices.
        self.known_passwords=[]

    def check_cancel(self):
        if self.cancel and self.cancel.is_set():
            raise InterruptedError('Batch cancelled')

    def run(self, root):
        runtime.ensure_current()
        root, entries = discover(root)
        if self.output == root or self.output.resolve().is_relative_to(root):
            raise ValueError('Output must be outside project input')
        if Path(str(self.output) + '.zip').exists():
            raise FileExistsError('Output ZIP exists')
        self.output.mkdir(parents=True, exist_ok=False)
        save(self.output / 'criteria-used.json', {'schema': 'topolog-rules-v1', 'projects': self.rules})
        report = {'schema': 'topolog-batch-v2', 'started_at': now(), 'projects': entries,
                  'runtime': runtime.metadata(),
                  'unmatched_rule_projects': sorted(set(self.rules) - {x.get('project_id') for x in entries})}
        try:
            for entry in entries:
                if entry['status'] == 'excluded':
                    continue
                self.check_cancel()
                pid = entry['project_id']
                prefix = '/v2/projects/' + pid
                folder = self.output / pid
                folder.mkdir()
                opened, started = False, []
                entry.update(nodes=[], cleanup_errors=[])
                self.progress('Project: ' + entry['name'])
                try:
                    content = (root / entry['file']).read_bytes()
                    if hashlib.sha256(content).hexdigest() != entry['source_sha256']:
                        raise ValueError('Project changed after discovery')
                    source = json.loads(content.decode('utf-8-sig'))
                    save(folder / 'source-project.json', source)
                    required = {n.get('compute_id') or 'local' for n in source.get('topology', {}).get('nodes', [])}
                    if required:
                        wait_computes(self.api, required, self.compute_timeout, self.cancel, self.progress)
                    registered = self.api.call('/v2/projects')
                    before = next((p for p in registered if p['project_id'] == pid), None)
                    if before is None:
                        # Local controller must be able to access this file path.
                        opened = True
                        loaded = self.api.call('/v2/projects/load', 'POST', {'path': str(root / entry['file'])})
                        if loaded['project_id'] != pid:
                            raise ValueError('Loaded project identity differs from selected file')
                    elif before['status'] != 'opened':
                        opened = True
                        self.api.call(prefix + '/open', 'POST')
                    nodes = self.api.call(prefix + '/nodes')
                    spec = self.rules.get(pid, {})
                    expected = spec.get('nodes', {})
                    entry['missing_rule_nodes'] = sorted(set(expected) - {n['node_id'] for n in nodes})
                    records = {}
                    for node in nodes:
                        nid = guid(node['node_id'])
                        record = {'node_id': nid, 'name': node.get('name', ''), 'node_type': node['node_type'],
                                  'collection': 'pending', 'checks': [{'status': 'inconclusive', 'reason': 'Startup not completed'}]}
                        records[nid] = record
                        entry['nodes'].append(record)
                    for node in nodes:
                        self.check_cancel()
                        nid = guid(node['node_id'])
                        record = records[nid]
                        if node.get('status') == 'suspended':
                            record.update(collection='not_run', reason='Suspended node preserved')
                            continue
                        try:
                            if node.get('status') != 'started':
                                # Journal before POST: server may start even if response times out.
                                started.append(nid)
                                self.api.call(prefix + '/nodes/' + nid + '/start', 'POST')
                            record['collection'] = 'pending'
                        except (InterruptedError, KeyboardInterrupt):
                            record.update(collection='cancelled', reason='Node startup cancelled')
                            raise
                        except Exception as exc:
                            record['collection'] = 'failed'
                            record['reason'] = 'Node startup failed: ' + error_detail(exc)
                            self.progress(record['reason'])
                            diagnosis = diagnose_start_failure(self.api, node, record['reason'])
                            if diagnosis:
                                record['start_diagnosis'] = diagnosis
                                record['recovery_hint'] = diagnosis['hint']
                                self.progress('Port conflict: ' + diagnosis['hint'])
                    deadline = time.monotonic() + self.settle_seconds
                    while time.monotonic() < deadline:
                        self.check_cancel()
                        time.sleep(min(.2, max(0, deadline - time.monotonic())))
                    for node in nodes:
                        self.check_cancel()
                        nid = guid(node['node_id'])
                        record = records[nid]
                        if record['collection'] != 'pending':
                            continue
                        if record['node_type'] in BUILTIN_TYPES:
                            try:
                                fresh = self.api.call(prefix + '/nodes/' + nid)
                            except Exception:
                                fresh = node
                            record.update(describe_builtin(fresh))
                            if record.get('switch_ports') is not None:
                                save(folder / nid / 'switch-ports.json', {'source': 'GNS3 API node properties.ports_mapping', 'ports': record['switch_ports']})
                            elif record.get('builtin_ports') is not None:
                                save(folder / nid / 'builtin-ports.json', {'source': 'GNS3 API node properties', 'ports': record['builtin_ports']})
                            self.progress('Node result: {} / {} / {}'.format(record['name'], record['collection'], record.get('reason', '')))
                            continue
                        self.progress('Node: ' + record['name'])
                        try:
                            fresh = self.api.call(prefix + '/nodes/' + nid)
                            if fresh.get('node_id') != nid:
                                raise ValueError('Server returned a different node')
                            fresh['project_id'] = pid
                            record.update(self.collector(self.api, fresh, folder / nid, expected.get(nid, {}), spec.get('profiles', []),
                                                         ready_timeout=self.ready_timeout, cancel=self.cancel, progress=self.progress,
                                                         enable=self.enable,enable_password=self.enable_password,password_provider=self.password_provider,
                                                         known_passwords=self.known_passwords))
                            save(folder / 'result.json', entry)
                            save(self.output / 'summary.json', report)
                            self.progress('Node result: {} / {} / {}'.format(record['name'], record['collection'], record.get('reason', '')))
                            if record['collection'] == 'cancelled':
                                raise InterruptedError('Collection cancelled')
                            self.check_cancel()
                        except (InterruptedError, KeyboardInterrupt):
                            if record['collection'] == 'pending':
                                record.update(collection='cancelled', reason='Collection cancelled by user')
                            raise
                        except Exception as exc:
                            record.update(collection='failed', reason='Collection failed: ' + error_detail(exc))
                            self.progress(record['reason'])
                    topology = {'nodes': self.api.call(prefix + '/nodes'),
                         'links': self.api.call(prefix + '/links'), 'drawings': self.api.call(prefix + '/drawings')}
                    save(folder / 'topology.json', topology)
                    entry['topology_links'] = topology['links']
                    entry['topology_ports'] = {n.get('node_id'): [{k: p.get(k) for k in ('adapter_number', 'port_number', 'name')}
                                                                  for p in n.get('ports', []) or []] for n in topology['nodes']}
                    if self.reachability:
                        from . import reach
                        try:
                            reach.run(self.api, entry, folder, self.console_factory, self.progress, self.cancel,
                                      ready_timeout=min(60, self.ready_timeout))
                        except (InterruptedError, KeyboardInterrupt):
                            raise
                        except Exception as exc:
                            entry['reachability'] = {'status': 'failed', 'reason': error_detail(exc)}
                    from .topology import reconstruct, capture_native
                    reconstruct(topology,folder/'topology-reconstructed.svg')
                    entry['topology_images']={'reconstructed':{'status':'generated','file':'topology-reconstructed.svg','kind':'API 좌표·연결 기반 재구성'}}
                    if self.capture:
                        self.progress('프로젝트 토폴로지 사진을 저장합니다.')
                        entry['topology_images']['native']=capture_native(entry['name'],root/entry['file'],folder/'capture',self.cancel)
                        self.progress('토폴로지 사진: 저장 완료' if entry['topology_images']['native']['status']=='captured' else '실제 토폴로지 사진을 저장하지 못했습니다. 보고서에서 원인을 확인하세요.')
                    else:entry['topology_images']['native']={'status':'unavailable','reason':'실제 GNS3 캡처를 사용하지 않은 실행입니다.'}
                    entry['topology_links'] = topology['links']
                    from . import features
                    features.apply(entry, expected)
                    save(folder / 'auto-criteria.json', {'schema':'topolog-auto-v1','nodes':
                         {n['node_id']:n.get('automatic_checks',[]) for n in entry['nodes'] if n.get('node_type') not in BUILTIN_TYPES}})
                    outcome = verdict(entry['nodes'])
                    if entry['missing_rule_nodes'] and outcome != 'failed':
                        outcome = 'inconclusive'
                    entry.update(status='completed', verdict=outcome)
                except (InterruptedError, KeyboardInterrupt):
                    entry.update(status='cancelled', verdict='inconclusive', reason='Batch cancelled by user')
                    report['cancelled'] = True
                except Exception as exc:
                    entry.update(status='error', verdict='inconclusive', reason=error_detail(exc))
                    self.progress('Project incomplete: ' + entry['reason'])
                    hint = recovery_hint(entry['reason'])
                    if hint:
                        entry['recovery_hint'] = hint
                        self.progress('Recovery: ' + hint)
                finally:
                    for record in entry['nodes']:
                        if record['collection'] == 'pending':
                            record.update(collection='not_run', reason='Project interrupted before collection',
                                          checks=[{'status': 'unverified', 'reason': 'Collection not attempted'}])
                        node_folder = folder / record['node_id']
                        save(node_folder / 'collection.json', record)
                    for nid in reversed(started):
                        try:
                            self.api.call(prefix + '/nodes/' + nid + '/stop', 'POST')
                        except Exception:
                            entry['cleanup_errors'].append('Could not stop ' + nid)
                    if opened:
                        try:
                            self.api.call(prefix + '/close', 'POST')
                        except Exception:
                            entry['cleanup_errors'].append('Could not close project')
                    if entry['cleanup_errors']:
                        entry['status'] = 'cleanup_failed'
                    save(folder / 'result.json', entry)
                    save(self.output / 'summary.json', report)
                if report.get('cancelled'):
                    break
        except (InterruptedError, KeyboardInterrupt):
            report['cancelled'] = True
        finally:
            if report.get('cancelled'):
                for entry in entries:
                    if entry['status'] == 'pending':
                        entry.update(status='not_run', verdict='inconclusive', reason='Batch cancelled before this project')
            report['finished_at'] = now()
            report['verdict'] = 'inconclusive'
            if any(p.get('verdict') == 'failed' for p in entries):
                report['verdict'] = 'failed'
            elif entries and not report['unmatched_rule_projects'] and not report.get('cancelled') and all(p['status'] == 'completed' and p.get('verdict') == 'passed' for p in entries):
                report['verdict'] = 'passed'
            save(self.output / 'summary.json', report)
            export(self.output, report)
        return report


def export(output, report):
    lines = ['# TopoLog 일괄 검증 결과', '', '전체 판정: ' + report['verdict'], '',
             '판정은 명시한 기준에 한정됩니다. API 구성 스냅샷은 실제 화면 스크린샷이 아닙니다.', '']
    if report.get('assessment'):
        lines += ['기존 증거 재판정 (장비 재실행 없음)',
                  '재판정 시각: ' + report['assessment']['assessed_at'],
                  '원본 결과: ' + report['assessment']['source_directory'], '']
    for project in report['projects']:
        lines += ['## ' + project.get('name', project['file']),
                  '상태: {} / 판정: {}'.format(project['status'], project.get('verdict', 'inconclusive')), project.get('reason', '')]
        if project.get('recovery_hint'):
            lines.append(project['recovery_hint'])
        for key,label in [('native','실제 GNS3 스크린샷'),('reconstructed','API 기반 재구성 토폴로지')]:
            image=project.get('topology_images',{}).get(key,{})
            if image.get('status') in ('captured','generated') and image.get('file'):
                from urllib.parse import quote
                path=quote(str(project.get('project_id',''))+'/'+image['file'],safe='/')
                lines.extend(['', '### '+label, '!['+label+']('+path+')', ''])
        for node in project.get('nodes', []):
            if node.get('analysis'):
                lines.append('기능 탐지: ' + ', '.join(node['analysis']['features']))
            lines += ['- {}: {} / OS: {} / 유형: {}'.format(node['name'], node['collection'], node.get('platform', 'unknown'), ', '.join(node.get('profiles', [])))]
            if node.get('reason'):
                lines.append('  - ' + node['reason'])
            lines.extend('  - 복구 오류: ' + detail for detail in node.get('cleanup_errors', []))
            for check in node['checks']:
                if check.get('basis'):
                    lines.append('  - 자동 기준 근거: ' + check['basis'])
                lines.append('  - {}: {}'.format(check['status'], check.get('reason', check.get('command', ''))))
                if check.get('criterion'):
                    lines.append('    기대값: ' + json.dumps(check['criterion'], ensure_ascii=False))
                if 'observed' in check:
                    lines.append('    관측값: ' + json.dumps(check['observed'], ensure_ascii=False))
                if check.get('evidence'):
                    lines.append('    근거: {}/{}/{}'.format(project.get('project_id', ''), node['node_id'], check['evidence']))
        lines.extend('- 기준 대상 노드 없음: ' + nid for nid in project.get('missing_rule_nodes', []))
        lines.extend('- 복구 오류: ' + error for error in project.get('cleanup_errors', []))
    lines.extend('- 기준 대상 프로젝트 없음: ' + pid for pid in report['unmatched_rule_projects'])
    text = '\n'.join(lines)
    (output / 'report.md').write_text(text, encoding='utf-8')
    from . import portfolio
    try:
        portfolio.write(report, output)
    except Exception as exc:
        # The portfolio document is a presentation layer; evidence and verdicts stay intact.
        (output / 'report-document-error.txt').write_text('{}: {}'.format(type(exc).__name__, exc), encoding='utf-8')
    from .reporting import render
    files = sorted({p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file()} |
                   {'index.html', 'ai_prompt.md', 'manifest.json'})
    (output / 'index.html').write_text(render(report, files), encoding='utf-8')
    (output / 'ai_prompt.md').write_text('''# 개인 AI에서 보고서 편집하기

원하는 문서 방향: [포트폴리오 / 학습 정리 / 기술 기록 중 선택하거나 직접 입력]

첨부한 report.docx(또는 report-document.md), report.md, summary.json, 원본 명령 출력과 설정을 근거로 프로젝트별 문서를 다듬어 주세요. report.docx는 TopoLog가 수집·판정 결과로 만든 초안입니다.

- 이름으로 실습 종류를 단정하지 말고 수집한 설정·기능·연결을 바탕으로 정리하세요.
- 목표는 자료에 명시된 경우만 사실로 쓰고, 추론은 추론임을 밝히세요.
- 설정, 실제 관측값, 검증 기준, 판정과 증거 파일을 연결하세요. passed는 해당 조건의 충족만 의미합니다.
- 실제 GNS3 Screenshot과 API 기반 재구성 그림의 출처를 구분하세요.
- 미수집·미지원·미검증·실패를 유지하고, 실행하지 않은 시험이나 본인 기여도를 만들지 마세요.
- DHCP 풀·정적 NAT·ACL 적용·STP 인스턴스 확인을 종단 통신이나 전체 정책의 성공으로 확대하지 마세요.
- 인증 정보·장비 설정의 민감한 값은 공개 문서에 싣기 전에 가리세요.

프로젝트 개요 → 구성과 기능 → 관측 및 검증 → 확인된 결과와 남은 항목 순으로 작성해 주세요.
''', encoding='utf-8')
    save(output / 'manifest.json', {'files': [{'path': p.relative_to(output).as_posix(), 'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(output.rglob('*')) if p.is_file() and p != output / 'manifest.json']})
    with zipfile.ZipFile(str(output) + '.zip', 'x', zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(output.rglob('*')):
            if file.is_file():
                archive.write(file, file.relative_to(output).as_posix())
