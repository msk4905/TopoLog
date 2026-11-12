"""Expected-behaviour editor: the automatic checks of each device, switched off, changed or extended.

The automatic checks and the value candidates come from collected configurations
(the chosen result folder), or, for a project never collected, from the startup
configurations saved in its project folder. Nothing is inferred from results:
the editor only lists what the configuration-based checks would test. Saving
writes the criteria file TopoLog uses on every following run and reassessment.
"""
import copy
import ipaddress
import json
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from .criteria import CriteriaDocument
from . import features, settings

# Kinds a user can add: label → (kind, fields, command per platform or None when the kind has its own command).
ADD_KINDS = {
    '인터페이스 상태 up/up': ('interface_up', ['interface'], {'ios': 'show ip interface brief', 'asa': 'show interface ip brief'}),
    '인터페이스 주소': ('interface_address', ['interface', 'address'], {'ios': 'show ip interface brief', 'asa': 'show interface ip brief'}),
    'OSPF 이웃 FULL': ('ospf_neighbors', ['neighbors'], None),
    '경로(코드 지정)': ('route', ['prefix', 'protocol', 'next_hop'], None),
    '목적지로 가는 경로': ('route_covered', ['address'], {'ios': 'show ip route', 'asa': 'show route'}),
    'Ping 응답': ('ping', ['target', 'min_percent'], None),
    'BGP 이웃 연결': ('bgp_peer', ['address'], {'ios': 'show ip bgp summary'}),
    'EIGRP 이웃': ('eigrp_neighbor', ['address'], {'ios': 'show ip eigrp neighbors'}),
    'VLAN 활성': ('vlan_active', ['vlan'], {'ios': 'show vlan brief'}),
    'Trunk 포트': ('trunk', ['interface'], {'ios': 'show interfaces trunk'}),
    '출력에 문구 포함': ('contains', ['command', 'expected'], None),
}
FIELD_LABELS = {'interface': '인터페이스', 'address': '주소', 'neighbors': '이웃 Router ID (쉼표로 구분)', 'prefix': 'prefix (CIDR)',
                'protocol': '경로 코드 (C, S, R, O, O IA, D, B 등)', 'next_hop': 'next hop (선택)', 'target': '목적지 주소',
                'min_percent': '최소 성공률 (1~100)', 'vlan': 'VLAN 번호', 'command': 'show 명령', 'expected': '포함할 문구 (쉼표로 구분)',
                'peer': '이웃', 'gateway': '게이트웨이', 'mode': '모드', 'priority': '우선순위', 'root': 'Root Bridge (True/False)',
                'source': '출발', 'into': '대상 프로토콜', 'group': '그룹', 'virtual_ip': '가상 IP', 'inside': 'inside 인터페이스',
                'outside': 'outside 인터페이스', 'pool': '풀', 'network': '네트워크', 'local': '내부 주소', 'global': '외부 주소',
                'acl': 'ACL', 'direction': '방향 (in/out)', 'router_id': 'Router ID'}
LIST_FIELDS = {'neighbors', 'expected', 'inside', 'outside'}
INT_FIELDS = {'vlan', 'priority'}
FLOAT_FIELDS = {'min_percent'}
BOOL_FIELDS = {'root'}
HIDDEN_FIELDS = set(features.VOLATILE_KEYS) | {'kind', 'command'}
ROUTE_CODES = ['C', 'L', 'S', 'R', 'O', 'O IA', 'O E1', 'O E2', 'O N1', 'O N2', 'D', 'D EX', 'B']


def default_path():
    """The criteria file TopoLog applies on every run (created on the first save)."""
    return settings.settings_path().parent / 'criteria.json'


# ---------- data: automatic checks and value candidates ----------

def basis_from_result(folder):
    """{project_id: {'name', 'nodes': {node_id: {'name','platform','checks','analysis','commands'}}, 'links'}} from a result."""
    from .resultdir import records, top
    data = json.loads((records(top(Path(folder))) / 'summary.json').read_text(encoding='utf-8'))
    projects = {}
    for project in data.get('projects', []):
        nodes = {}
        for node in project.get('nodes', []):
            nodes[node['node_id']] = {'name': node.get('name', ''), 'platform': node.get('platform', 'unknown'),
                                      'checks': [c['criterion'] for c in node.get('automatic_checks', []) if c.get('criterion')],
                                      'analysis': node.get('analysis') or {},
                                      'commands': sorted({c['command'] for c in node.get('commands', []) if c.get('command', '').startswith('show ')})}
        projects[project['project_id']] = {'name': project.get('name', ''), 'nodes': nodes, 'source': 'result'}
    return projects


def basis_from_startup(root, entry):
    """Automatic checks a project would get from the startup configurations saved in its folder (never collected)."""
    path = Path(root) / entry['file']
    topology = json.loads(path.read_text(encoding='utf-8-sig')).get('topology', {})
    nodes, saved = [], {}
    for node in topology.get('nodes', []):
        props = node.get('properties', {})
        text = None
        if node.get('node_type') == 'dynamips' and isinstance(props.get('dynamips_id'), int):
            config = path.parent / 'project-files' / 'dynamips' / node['node_id'] / 'configs' / 'i{}_startup-config.cfg'.format(props['dynamips_id'])
            if config.is_file():
                text = config.read_text(encoding='utf-8', errors='replace')
        record = {'node_id': node['node_id'], 'name': node.get('name', ''), 'node_type': node.get('node_type'),
                  'platform': 'ios' if text else 'unknown', 'commands': []}
        if text:
            record['commands'] = [{'command': 'show running-config', 'text': text, 'status': 'collected', 'cli_error': False, 'file': ''}]
        nodes.append(record)
        saved[node['node_id']] = record
    project = {'nodes': nodes, 'topology_links': topology.get('links', [])}
    features.apply(project)
    return {'name': entry.get('name', ''), 'source': 'startup',
            'nodes': {nid: {'name': n['name'], 'platform': n['platform'],
                            'checks': [c['criterion'] for c in n.get('automatic_checks', []) if c.get('criterion')],
                            'analysis': n.get('analysis') or {}, 'commands': []} for nid, n in saved.items()}}


def candidates(project, node_id):
    """Values to pick from for each field, taken from the project's configurations."""
    nodes = project.get('nodes', {})
    own = nodes.get(node_id, {}).get('analysis', {})
    def addresses(analysis):
        return [i for i in analysis.get('interfaces', []) if i.get('address')]
    others, prefixes, router_ids = [], set(), set()
    for nid, node in nodes.items():
        for interface in addresses(node.get('analysis', {})):
            ip = str(ipaddress.IPv4Interface(interface['address']).ip)
            prefixes.add(str(ipaddress.IPv4Interface(interface['address']).network))
            if nid != node_id:
                others.append(ip)
        for process in node.get('analysis', {}).get('processes', []):
            if process.get('router_id') and nid != node_id:
                router_ids.add(process['router_id'])
    mine = [str(ipaddress.IPv4Interface(i['address']).ip) for i in addresses(own)]
    interfaces = [i['name'] for i in own.get('interfaces', [])]
    commands = nodes.get(node_id, {}).get('commands', [])
    return {'interface': interfaces, 'address': mine + others, 'target': others + mine, 'next_hop': others,
            'neighbors': sorted(router_ids), 'router_id': sorted(router_ids), 'prefix': sorted(prefixes, key=lambda p: ipaddress.IPv4Network(p)),
            'protocol': ROUTE_CODES, 'vlan': [str(v) for v in sorted(own.get('vlans', []), key=lambda v: int(v) if str(v).isdigit() else 0)],
            'command': commands, 'min_percent': ['100', '80', '60', '20'], 'root': ['True', 'False'], 'direction': ['in', 'out']}


def parse_field(name, text, original=None):
    """Field text → rule value of the right type (list, int, float, bool or text)."""
    text = (text or '').strip()
    if not text and not isinstance(original, list) and name not in LIST_FIELDS:
        return ''
    if name in LIST_FIELDS or isinstance(original, list):
        return [x.strip() for x in text.replace('\n', ',').split(',') if x.strip()]
    if name in BOOL_FIELDS or isinstance(original, bool):
        return text.lower() in ('true', '1', '예', 'yes')
    if name in INT_FIELDS or (isinstance(original, int) and not isinstance(original, bool)):
        return int(text)
    if name in FLOAT_FIELDS or isinstance(original, float):
        return float(text)
    return text


def show_field(value):
    return ', '.join(map(str, value)) if isinstance(value, list) else '' if value is None else str(value)


def new_rule(label, values, platform):
    """A rule of an ADD_KINDS label from field texts, with its command for the device's platform."""
    kind, fields, commands = ADD_KINDS[label]
    rule = {'kind': kind}
    for name in fields:
        value = parse_field(name, values.get(name, ''))
        if value in ('', []) and name in ('next_hop', 'min_percent'):
            continue
        if value in ('', []):
            raise ValueError(FIELD_LABELS.get(name, name) + '을(를) 입력하세요.')
        rule[name] = value
    if kind == 'ping' and 'min_percent' not in rule:
        rule['min_percent'] = 100.0
    if commands:
        command = commands.get(platform) or commands.get('ios')
        rule['command'] = command
    return rule


def rows(spec, automatic):
    """Display rows for one device: (key, state, rule, source) in automatic order, then user additions."""
    spec = spec or {}
    disabled = set(spec.get('disabled', []))
    replaced = {c.get('replaces'): c for c in spec.get('checks', []) if c.get('replaces')}
    result = []
    for rule in automatic:
        key = features.rule_key(rule)
        if key in replaced:
            result.append((key, '바꿈', {k: v for k, v in replaced[key].items() if k != 'replaces'}, 'changed'))
        else:
            result.append((key, '제외' if key in disabled else '확인', rule, 'automatic'))
    for index, rule in enumerate(spec.get('checks', [])):
        if not rule.get('replaces'):
            result.append(('user:{}'.format(index), '추가', rule, 'added'))
    return result


# ---------- window ----------

def open_editor(parent, root, current_path=None, on_saved=None, initial_directory=None, result_folder=None):
    """Editor window. Saving writes current_path (default: the criteria file TopoLog applies) and calls on_saved(path)."""
    from .reporting import expectation
    path = Path(current_path) if current_path else default_path()
    document = CriteriaDocument(root, str(path) if path.is_file() else None)
    from .engine import discover
    _, entries = discover(root)
    state = {'basis': {}, 'source': None, 'target': None}

    def load_basis(folder):
        basis = {}
        if folder:
            try:
                basis.update(basis_from_result(folder))
                state['source'] = str(folder)
            except (OSError, ValueError, KeyError):
                state['source'] = None
        for entry in entries:
            if entry.get('status') == 'pending' and entry['project_id'] not in basis:
                try:
                    basis[entry['project_id']] = basis_from_startup(root, entry)
                except (OSError, ValueError, KeyError):
                    pass
        state['basis'] = basis

    load_basis(result_folder)
    window = tk.Toplevel(parent)
    window.title('TopoLog · 기대값 편집')
    window.geometry('1080x680')
    window.transient(parent)
    frame = ttk.Frame(window, padding=12)
    frame.pack(fill='both', expand=True)
    source_text = tk.StringVar()

    def describe_source():
        source_text.set(('자동 확인 목록과 값 후보의 기준: ' + state['source']) if state['source'] else
                        '자동 확인 목록과 값 후보의 기준: 수집 결과 없음 · 프로젝트 폴더에 저장된 시작 설정으로 만들었습니다(없으면 직접 추가만 가능).')
    describe_source()
    top_bar = ttk.Frame(frame)
    top_bar.pack(fill='x')
    ttk.Label(top_bar, textvariable=source_text, wraplength=820, justify='left').pack(side='left', fill='x', expand=True)

    def choose_result():
        folder = filedialog.askdirectory(parent=window, title='기준으로 삼을 TopoLog 결과 폴더', initialdir=initial_directory)
        if folder:
            load_basis(folder)
            describe_source()
            show_node()
    ttk.Button(top_bar, text='기준 결과 바꾸기', command=choose_result).pack(side='right')
    ttk.Label(frame, text='장비를 고르면 TopoLog가 설정에서 만든 자동 확인 항목이 나옵니다. 항목을 제외하거나 값을 바꾸거나 새 항목을 추가하세요. '
              '저장하면 다음 수집과 보고서 다시 정리부터 바로 적용됩니다. 바꾸거나 추가한 항목은 보고서에 \'사용자 지정\'으로 표시됩니다.',
              wraplength=1040, justify='left').pack(anchor='w', pady=(6, 0))

    bottom = ttk.Frame(frame)
    bottom.pack(side='bottom', fill='x', pady=(8, 0))
    status = tk.StringVar(value='')
    ttk.Label(bottom, textvariable=status).pack(side='left')
    pane = ttk.Panedwindow(frame, orient='horizontal')
    pane.pack(fill='both', expand=True, pady=8)
    left, right = ttk.Frame(pane), ttk.Frame(pane)
    pane.add(left, weight=1)
    pane.add(right, weight=3)
    tree = ttk.Treeview(left, show='tree', selectmode='browse')
    bar = ttk.Scrollbar(left, orient='vertical', command=tree.yview)
    tree.configure(yscrollcommand=bar.set)
    bar.pack(side='right', fill='y')
    tree.pack(fill='both', expand=True)
    targets = {}
    for pid, project in document.data['projects'].items():
        parent_id = tree.insert('', 'end', text=project.get('name', pid), open=False)
        for nid, node in project.get('nodes', {}).items():
            targets[tree.insert(parent_id, 'end', text=node.get('name') or nid)] = (pid, nid)
    heading = tk.StringVar(value='왼쪽에서 장비를 고르세요.')
    ttk.Label(right, textvariable=heading, font=('TkDefaultFont', 10, 'bold')).pack(anchor='w')
    actions = ttk.Frame(right)
    actions.pack(side='bottom', fill='x', pady=(6, 0))
    box = ttk.Frame(right)
    box.pack(fill='both', expand=True, pady=(4, 0))
    table = ttk.Treeview(box, columns=('state', 'text'), show='headings', selectmode='browse')
    table.heading('state', text='상태')
    table.heading('text', text='확인 내용')
    table.column('state', width=70, anchor='center', stretch=False)
    table.column('text', width=640)
    scroll = ttk.Scrollbar(box, orient='vertical', command=table.yview)
    table.configure(yscrollcommand=scroll.set)
    scroll.pack(side='right', fill='y')
    table.pack(fill='both', expand=True)
    shown = {}

    def spec():
        pid, nid = state['target']
        return document.data['projects'][pid]['nodes'][nid]

    def automatic():
        pid, nid = state['target']
        return state['basis'].get(pid, {}).get('nodes', {}).get(nid, {}).get('checks', [])

    def platform():
        pid, nid = state['target']
        recorded = state['basis'].get(pid, {}).get('nodes', {}).get(nid, {}).get('platform')
        chosen = spec().get('platform', 'auto')
        return chosen if chosen != 'auto' else (recorded if recorded in ('ios', 'asa', 'vpcs') else 'ios')

    def show_node(event=None):
        selected = tree.selection()
        if selected and selected[0] in targets:
            state['target'] = targets[selected[0]]
        if not state['target']:
            return
        pid, nid = state['target']
        node_spec = spec()
        heading.set('{} · {}'.format(document.data['projects'][pid].get('name', pid), node_spec.get('name') or nid))
        # Keep the selected row selected after a change, so several actions can follow on one item.
        previous = table.index(table.selection()[0]) if table.selection() and event is None else None
        table.delete(*table.get_children())
        shown.clear()
        for key, label, rule, source in rows(node_spec, automatic()):
            item = table.insert('', 'end', values=(label, expectation(rule)))
            shown[item] = (key, rule, source)
        children = table.get_children()
        if previous is not None and children:
            table.selection_set(children[min(previous, len(children) - 1)])
        if not shown:
            table.insert('', 'end', values=('', '자동 확인 항목이 없습니다. \'항목 추가\'로 직접 추가할 수 있습니다.'))
    tree.bind('<<TreeviewSelect>>', show_node)

    def current():
        selected = table.selection()
        return shown.get(selected[0]) if selected else None

    def changed(message):
        status.set(message + ' · 저장하면 적용됩니다.')
        show_node()

    def toggle():
        row = current()
        if not row:
            return
        key, rule, source = row
        node_spec = spec()
        if source == 'automatic':
            disabled = node_spec.setdefault('disabled', [])
            if key in disabled:
                disabled.remove(key)
                changed('다시 확인합니다')
            else:
                disabled.append(key)
                changed('확인에서 제외했습니다')
        elif source == 'changed':
            node_spec['checks'] = [c for c in node_spec.get('checks', []) if c.get('replaces') != key]
            changed('바꾼 값을 되돌렸습니다')

    def remove():
        row = current()
        if not row or row[2] != 'added':
            status.set('직접 추가한 항목만 삭제할 수 있습니다. 자동 항목은 \'확인 제외\'를 쓰세요.')
            return
        index = int(row[0].split(':')[1])
        node_spec = spec()
        node_spec['checks'] = [c for i, c in enumerate(node_spec.get('checks', [])) if i != index]
        changed('추가한 항목을 삭제했습니다')

    def edit(adding=False):
        if not state['target']:
            return
        row = None if adding else current()
        if not adding and not row:
            status.set('바꿀 항목을 고르세요.')
            return
        pid, nid = state['target']
        options = candidates(state['basis'].get(pid, {}), nid)
        dialog = tk.Toplevel(window)
        dialog.title('항목 추가' if adding else '값 바꾸기')
        dialog.transient(window)
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=14)
        body.pack(fill='both', expand=True)
        inputs = {}
        kind_box = None
        fields_frame = ttk.Frame(body)

        def draw(fields, values):
            for child in fields_frame.winfo_children():
                child.destroy()
            inputs.clear()
            for row_index, name in enumerate(fields):
                ttk.Label(fields_frame, text=FIELD_LABELS.get(name, name)).grid(row=row_index, column=0, sticky='w', padx=(0, 10), pady=3)
                box = ttk.Combobox(fields_frame, values=options.get(name, []), width=48)
                box.set(show_field(values.get(name)))
                box.grid(row=row_index, column=1, sticky='ew', pady=3)
                inputs[name] = box
        if adding:
            ttk.Label(body, text='종류').pack(anchor='w')
            kind_box = ttk.Combobox(body, values=list(ADD_KINDS), state='readonly', width=30)
            kind_box.set(next(iter(ADD_KINDS)))
            kind_box.pack(anchor='w', pady=(0, 8))
            kind_box.bind('<<ComboboxSelected>>', lambda e: draw(ADD_KINDS[kind_box.get()][1], {}))
            draw(ADD_KINDS[kind_box.get()][1], {})
        else:
            original = row[1]
            ttk.Label(body, text=expectation(original), wraplength=520, justify='left').pack(anchor='w', pady=(0, 8))
            draw([k for k in original if k not in HIDDEN_FIELDS], original)
        fields_frame.pack(fill='x')
        ttk.Label(body, text='목록에서 고르거나 직접 입력하세요. 여러 값은 쉼표로 구분합니다.', foreground='#5b6672').pack(anchor='w', pady=(8, 0))
        message = tk.StringVar()
        ttk.Label(body, textvariable=message, foreground='#a33a2a', wraplength=520).pack(anchor='w')

        def accept():
            try:
                node_spec = spec()
                if adding:
                    rule = new_rule(kind_box.get(), {k: v.get() for k, v in inputs.items()}, platform())
                    candidate = node_spec.get('checks', []) + [rule]
                else:
                    key, original, source = row
                    base = original
                    rule = copy.deepcopy(base)
                    for name, box in inputs.items():
                        value = parse_field(name, box.get(), base.get(name))
                        if value in ('', []) and name not in ('next_hop',):
                            raise ValueError(FIELD_LABELS.get(name, name) + '을(를) 입력하세요.')
                        rule[name] = value
                    if source == 'added':
                        index = int(key.split(':')[1])
                        candidate = [rule if i == index else c for i, c in enumerate(node_spec.get('checks', []))]
                    else:
                        rule['replaces'] = key
                        candidate = [c for c in node_spec.get('checks', []) if c.get('replaces') != key] + [rule]
                document.update(pid, nid, node_spec.get('platform', 'auto'), node_spec.get('profiles', ['common']), candidate)
                document.data['projects'][pid]['nodes'][nid]['disabled'] = node_spec.get('disabled', [])
            except (ValueError, TypeError, KeyError) as exc:
                message.set('입력값을 확인하세요: ' + str(exc))
                return
            dialog.destroy()
            changed('항목을 추가했습니다' if adding else '값을 바꿨습니다')
        buttons = ttk.Frame(body)
        buttons.pack(fill='x', pady=(10, 0))
        ttk.Button(buttons, text='확인', command=accept).pack(side='left')
        ttk.Button(buttons, text='취소', command=dialog.destroy).pack(side='right')
        dialog._topolog_dialog = {'inputs': inputs, 'kind': kind_box, 'accept': accept, 'draw': draw, 'message': message}
        return dialog

    for label, command in (('확인 제외 / 다시 확인', toggle), ('값 바꾸기', edit), ('항목 추가', lambda: edit(True)), ('추가 항목 삭제', remove)):
        ttk.Button(actions, text=label, command=command).pack(side='left', padx=(0, 8))
    table.bind('<Double-1>', lambda e: edit())

    def save_document():
        try:
            document.write(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror('기대값 편집', str(exc), parent=window)
            return
        status.set('저장했습니다. 다음 수집과 보고서 다시 정리부터 적용됩니다: ' + str(path))
        if on_saved:
            on_saved(str(path))
    ttk.Button(bottom, text='닫기', command=window.destroy).pack(side='right')
    ttk.Button(bottom, text='저장', command=save_document).pack(side='right', padx=(0, 8))
    window._topolog_editor = {'tree': tree, 'table': table, 'targets': targets, 'shown': shown, 'state': state, 'document': document,
                              'toggle': toggle, 'edit': edit, 'remove': remove, 'save': save_document, 'show_node': show_node,
                              'status': status, 'path': path}
    return window
