"""Local forms for explicit expectations; never infer success criteria from results."""
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from .criteria import CriteriaDocument, make_rule

KINDS = {'OSPF 이웃': 'ospf_neighbors', '경로': 'route', 'Ping': 'ping', '출력 문구': 'contains'}
FIELDS = {'ospf_neighbors': [('neighbors', '기대 Router ID (쉼표로 구분)')],
          'route': [('prefix', '기대 prefix (CIDR)'), ('protocol', '경로 코드 (R, O, O E2 등)'), ('next_hop', '기대 next hop (선택)')],
          'ping': [('target', '목적지 IPv4'), ('min_percent', '최소 성공률 (1~100)')],
          'contains': [('command', '수집할 show 명령'), ('expected', '포함할 문구 (한 줄에 하나)')]}


def open_editor(parent, root, current_path=None, on_saved=None, initial_directory=None):
    document = CriteriaDocument(root, current_path)
    window = tk.Toplevel(parent)
    window.title('TopoLog · 기대값 편집')
    window.geometry('980x650')
    window.transient(parent)
    window.grab_set()
    frame = ttk.Frame(window, padding=12)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='프로젝트와 장비를 선택해 기대값을 직접 지정하세요. 빈 기준은 미검증으로 유지됩니다.').pack(anchor='w')
    pane = ttk.Panedwindow(frame, orient='horizontal')
    pane.pack(fill='both', expand=True, pady=8)
    left, right = ttk.Frame(pane), ttk.Frame(pane)
    pane.add(left, weight=1)
    pane.add(right, weight=2)
    tree = ttk.Treeview(left, show='tree', selectmode='browse')
    scrollbar = ttk.Scrollbar(left, orient='vertical', command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side='right', fill='y')
    tree.pack(fill='both', expand=True)
    targets = {}
    for pid, project in document.data['projects'].items():
        parent_id = tree.insert('', 'end', text=project.get('name', pid), open=False)
        for nid, node in project.get('nodes', {}).items():
            item = tree.insert(parent_id, 'end', text=node.get('name') or nid)
            targets[item] = (pid, nid)
    heading = tk.StringVar(value='왼쪽에서 장비를 선택하세요.')
    ttk.Label(right, textvariable=heading).pack(anchor='w')
    platform = tk.StringVar(value='auto')
    ttk.Label(right, text='플랫폼 (auto 권장, 수집 시 실제 OS 확인)').pack(anchor='w')
    ttk.Combobox(right, textvariable=platform, state='readonly', values=('auto', 'ios', 'asa', 'vpcs')).pack(fill='x')
    profile_frame = ttk.Frame(right)
    profile_frame.pack(fill='x', pady=5)
    selected_profiles = {}
    for name in ('common', 'ospf', 'rip', 'redistribution', 'asa'):
        selected_profiles[name] = tk.BooleanVar(value=name == 'common')
        ttk.Checkbutton(profile_frame, text=name, variable=selected_profiles[name]).pack(side='left')
    checks_list = tk.Listbox(right, height=7, exportselection=False)
    checks_list.pack(fill='both', expand=True)
    kind = tk.StringVar(value='OSPF 이웃')
    selector = ttk.Combobox(right, textvariable=kind, values=tuple(KINDS), state='readonly')
    selector.pack(fill='x', pady=5)
    fields_frame = ttk.Frame(right)
    fields_frame.pack(fill='x')
    variables = {}
    state = {'target': None, 'checks': []}

    def render_fields(event=None):
        for widget in fields_frame.winfo_children():
            widget.destroy()
        variables.clear()
        for key, label in FIELDS[KINDS[kind.get()]]:
            ttk.Label(fields_frame, text=label).pack(anchor='w')
            if key == 'expected':
                widget = tk.Text(fields_frame, height=3)
                widget.pack(fill='x')
                variables[key] = widget
            else:
                value = tk.StringVar(value='100' if key == 'min_percent' else '')
                ttk.Entry(fields_frame, textvariable=value).pack(fill='x')
                variables[key] = value

    def refresh_checks():
        checks_list.delete(0, 'end')
        for rule in state['checks']:
            checks_list.insert('end', json.dumps(rule, ensure_ascii=False))

    def apply_node():
        if state['target']:
            document.update(*state['target'], platform.get(),
                            [key for key, value in selected_profiles.items() if value.get()], state['checks'])

    def select_node(event=None):
        selection = tree.selection()
        target = targets.get(selection[0]) if selection else None
        try:
            apply_node()
        except Exception as exc:
            messagebox.showerror('기준 확인', str(exc), parent=window)
            return
        state['target'] = target
        if not target:
            heading.set('장비를 선택하세요.')
            state['checks'] = []
            refresh_checks()
            return
        node = document.node(*target)
        heading.set(document.data['projects'][target[0]].get('name', '') + ' / ' + node.get('name', ''))
        platform.set(node.get('platform', 'auto'))
        for key, value in selected_profiles.items():
            value.set(key in node.get('profiles', []))
        state['checks'] = list(node.get('checks', []))
        refresh_checks()

    def add_rule(replace=False):
        try:
            if not state['target']:
                raise ValueError('장비를 먼저 선택하세요.')
            values = {key: value.get('1.0', 'end-1c') if isinstance(value, tk.Text) else value.get()
                      for key, value in variables.items()}
            rule = make_rule(KINDS[kind.get()], values)
            checks = list(state['checks'])
            if replace:
                selected = checks_list.curselection()
                if not selected:
                    raise ValueError('수정할 기준을 선택하세요.')
                checks[selected[0]] = rule
            else:
                checks.append(rule)
            document.update(*state['target'], platform.get(),
                            [k for k, v in selected_profiles.items() if v.get()], checks)
            state['checks'] = checks
            refresh_checks()
        except Exception as exc:
            messagebox.showerror('기준 확인', str(exc), parent=window)

    def edit_rule(event=None):
        selected = checks_list.curselection()
        if not selected:
            return
        rule = state['checks'][selected[0]]
        label = next((k for k, v in KINDS.items() if v == rule['kind']), None)
        if label is None:
            messagebox.showinfo('기준 편집', '이 기준은 기존 JSON 파일에서 편집할 수 있습니다. 저장 시 그대로 유지됩니다.', parent=window)
            return
        kind.set(label)
        render_fields()
        for key, value in variables.items():
            raw = rule.get(key, '')
            if isinstance(raw, list):
                raw = '\n'.join(raw) if key == 'expected' else ', '.join(raw)
            if isinstance(value, tk.Text):
                value.insert('1.0', str(raw))
            else:
                value.set(str(raw))

    def delete_rule():
        selected = checks_list.curselection()
        if selected:
            del state['checks'][selected[0]]
            refresh_checks()

    def save_document():
        try:
            apply_node()
            name = filedialog.asksaveasfilename(parent=window, defaultextension='.json',
                                               initialdir=initial_directory, initialfile='criteria.json', filetypes=[('JSON', '*.json')])
            if not name:
                return
            document.write(name)
            if on_saved:
                on_saved(name)
            window.destroy()
        except Exception as exc:
            messagebox.showerror('기준 저장', str(exc), parent=window)

    buttons = ttk.Frame(right)
    buttons.pack(fill='x', pady=8)
    for label, command in [('추가', add_rule), ('선택 기준 수정', lambda: add_rule(True)), ('삭제', delete_rule)]:
        ttk.Button(buttons, text=label, command=command).pack(side='left', padx=3)
    ttk.Button(frame, text='기준 파일 저장', command=save_document).pack(anchor='e')
    tree.bind('<<TreeviewSelect>>', select_node)
    selector.bind('<<ComboboxSelected>>', render_fields)
    checks_list.bind('<<ListboxSelect>>', edit_rule)
    render_fields()
    window._topolog = {'tree': tree, 'targets': targets, 'select': select_node, 'kind': kind,
                       'fields': variables, 'render': render_fields, 'add': add_rule, 'save': save_document,
                       'document': document, 'state': state}
    return window
