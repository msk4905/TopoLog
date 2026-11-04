"""Tk folder selection, criteria editing and cancellable collection."""
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, simpledialog
import webbrowser
from . import settings
from .cli import connect, new_output
from .engine import Runner, discover, load_rules
from .server_config import prepare as prepare_server
from .criteria import reassess
from .criteria_editor import open_editor
from .viewer import open_report, latest_report
from . import runtime
from . import preflight
from .transport import API


def launch(run_loop=True):
    window = tk.Tk()
    if not run_loop:
        window.withdraw()
    try:
        version = (Path(__file__).resolve().parents[2] / 'VERSION').read_text(encoding='utf-8').strip()
    except OSError:
        version = '개발판'
    window.title('TopoLog ' + version + ' · 일괄 수집')
    window.geometry('1040x710')
    window.minsize(820, 560)
    frame = ttk.Frame(window, padding=18)
    frame.pack(fill='both', expand=True)
    frame.columnconfigure(1, weight=1)
    defaults = settings.load()
    projects = tk.StringVar(value=defaults['project_directory'])
    output = tk.StringVar(value=defaults['output_directory'])
    rules = tk.StringVar()
    status = tk.StringVar(value='GNS3가 꺼져 있으면 수집 시작 시 자동으로 실행하고 연동 VM 연결을 기다립니다. 일괄 사진 저장에는 프로젝트가 열리지 않은 GNS3 창 하나를 사용합니다.')
    events, cancellation = queue.Queue(), threading.Event()
    state = {'running': False, 'result': latest_report(defaults['output_directory']), 'closing': False}
    controls, actions = [], {}

    def append(text):
        listing.configure(state='normal')
        listing.insert('end', str(text) + '\n')
        listing.see('end')
        listing.configure(state='disabled')

    from .progress import wrap
    notify = wrap(lambda text: events.put(('log',text)))
    def request_password(name):
        request={'name':name,'ready':threading.Event(),'password':None}
        events.put(('password',request))
        while not request['ready'].wait(.1):
            if cancellation.is_set():raise InterruptedError('Cancelled while requesting password')
        return request.pop('password',None)

    def select_folder(variable):
        value = filedialog.askdirectory(parent=window, initialdir=variable.get() if Path(variable.get()).is_dir() else str(Path.home()))
        if value:
            variable.set(value)

    for row, (label, variable) in enumerate([('GNS3 프로젝트 폴더', projects), ('결과 저장 폴더', output)]):
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w', pady=6)
        entry = ttk.Entry(frame, textvariable=variable)
        entry.grid(row=row, column=1, sticky='ew', padx=8)
        button = ttk.Button(frame, text='폴더 선택', command=lambda v=variable: select_folder(v))
        button.grid(row=row, column=2)
        controls.extend([entry, button])
    ttk.Label(frame, text='검증 기준 파일 (선택)').grid(row=2, column=0, sticky='w', pady=6)
    entry = ttk.Entry(frame, textvariable=rules)
    entry.grid(row=2, column=1, sticky='ew', padx=8)
    def select_rules():
        name = filedialog.askopenfilename(parent=window, filetypes=[('JSON', '*.json')])
        if name:
            rules.set(name)
    button = ttk.Button(frame, text='파일 선택', command=select_rules)
    button.grid(row=2, column=2)
    controls.extend([entry, button])
    allow_enable = tk.BooleanVar(value=True)
    default_password = tk.StringVar()
    auto_start = tk.BooleanVar(value=defaults.get('auto_start_gns3', True))
    capture = tk.BooleanVar(value=defaults.get('capture_screenshot', True))
    auth = ttk.Frame(frame)
    auth.grid(row=3, column=0, columnspan=3, sticky='ew', pady=6)
    toggle = ttk.Checkbutton(auth, text='필요 시 enable 권한 전환', variable=allow_enable)
    toggle.pack(side='left')
    ttk.Label(auth, text='기본 enable 암호(선택)').pack(side='left', padx=(12, 4))
    password_entry = ttk.Entry(auth, textvariable=default_password, show='*', width=16)
    password_entry.pack(side='left')
    ttk.Label(auth, text='저장하지 않음 · 실패하면 장비별로 입력을 요청').pack(side='left', padx=6)
    info = ttk.Frame(frame)
    info.grid(row=4, column=0, columnspan=3, sticky='ew', pady=8)
    starter = ttk.Checkbutton(info, text='GNS3가 꺼져 있으면 자동 실행(연동 VM 포함)', variable=auto_start)
    starter.pack(side='left')
    shooter = ttk.Checkbutton(info, text='실제 GNS3 토폴로지 사진 저장', variable=capture)
    shooter.pack(side='left', padx=(12, 0))
    ttk.Label(info, text='켜면 프로젝트마다 GNS3 창이 열리고 저장 창이 잠깐 표시되어 작업에 방해될 수 있습니다. 끄면 재구성 그림만 만듭니다.',
              wraplength=520).pack(side='left', padx=8)
    controls.extend([toggle, password_entry, starter, shooter])
    buttons = ttk.Frame(frame)
    buttons.grid(row=5, column=0, columnspan=3, sticky='w')
    listing = tk.Text(frame, height=16, wrap='word', state='disabled')
    listing.grid(row=6, column=0, columnspan=2, sticky='nsew', pady=12)
    scrollbar = ttk.Scrollbar(frame, command=listing.yview)
    scrollbar.grid(row=6, column=2, sticky='ns', pady=12)
    listing.configure(yscrollcommand=scrollbar.set)
    frame.rowconfigure(6, weight=1)

    def scan():
        try:
            root, destination = settings.validate(projects.get(), output.get())
            _, entries = discover(root)
            append('프로젝트 {}개 · 입력 {} · 출력 {}'.format(len(entries), root, destination))
            for item in entries:
                append('{}: {} {}'.format(item['file'], item['status'], item.get('reason', '')))
            return root, destination
        except Exception as exc:
            messagebox.showerror('경로 확인', str(exc), parent=window)

    def saved_criteria(path):
        rules.set(path)
        append('기준 저장: ' + path)

    def edit_criteria():
        selected = scan()
        if selected:
            try:
                return open_editor(window, selected[0], rules.get() or None,
                                   on_saved=saved_criteria, initial_directory=str(selected[1]))
            except Exception as exc:
                messagebox.showerror('기준 편집', str(exc), parent=window)

    def start_worker(operation, label):
        state['running'] = True
        cancellation.clear()
        for widget in controls:
            widget.configure(state='disabled')
        stop.configure(state='normal')
        result_button.configure(state='disabled')
        status.set(label)
        def worker():
            try:
                folder, result = operation()
                events.put(('done', (folder, result['verdict'], result.get('cancelled', False))))
            except InterruptedError:
                events.put(('cancelled', None))
            except Exception as exc:
                events.put(('error', str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def run():
        try:
            runtime.ensure_current()
        except ValueError as exc:
            messagebox.showerror('프로그램 업데이트',str(exc),parent=window)
            return
        selected = scan()
        if not selected:
            return
        try:
            configured = load_rules(rules.get() or None)
            root, destination = selected
            settings.save(root, destination, auto_start_gns3=auto_start.get(), capture_screenshot=capture.get())
        except Exception as exc:
            messagebox.showerror('기준 확인', str(exc), parent=window)
            return
        launch = auto_start.get()
        if not launch and preflight.gui_running() is False:
            answer = messagebox.askyesnocancel('GNS3 준비', 'GNS3가 실행되어 있지 않습니다.\n\n예: 지금 GNS3를 실행하고 GNS3·연동 VM이 준비될 때까지 기다립니다.\n아니요: 그대로 진행합니다(실제 GNS3 사진은 저장되지 않음).\n취소: 수집하지 않습니다.', parent=window)
            if answer is None:
                return
            launch = answer
        folder = new_output(destination)
        enable, password, shoot = allow_enable.get(), (default_password.get() or None), capture.get()
        def collect():
            _, found = discover(root)
            preflight.prepare(API, launch, progress=notify, cancel=cancellation,
                              needs_vm=preflight.projects_need_vm(root, found))
            api = connect(progress=notify, cancel=cancellation)
            if cancellation.is_set():
                raise InterruptedError('Collection cancelled before project startup')
            _, entries = discover(root)
            prepare_server(api, root, entries, progress=notify)
            if cancellation.is_set():
                raise InterruptedError('Collection cancelled before project startup')
            result = Runner(api, folder, configured, cancel=cancellation,
                            progress=notify, enable=enable, enable_password=password, password_provider=request_password, capture=shoot).run(root)
            return folder, result
        start_worker(collect, '수집 중입니다. 중지하면 상태를 복구하고 결과를 보존합니다.')

    def reassess_saved():
        try:
            load_rules(rules.get() or None)
            destination = Path(output.get()).expanduser().resolve()
            source = filedialog.askdirectory(parent=window, title='summary.json이 있는 기존 결과 폴더 선택')
            if not source:
                return
            source, criteria_path = Path(source), rules.get() or None
            if not (source / 'summary.json').is_file():
                raise ValueError('summary.json이 있는 일괄 결과 폴더를 선택하세요.')
            folder = new_output(destination)
            start_worker(lambda: (folder, reassess(source, folder, criteria_path)),
                         '기존 증거를 다시 판정합니다. 장비 명령은 실행하지 않습니다.')
            stop.configure(state='disabled')
        except Exception as exc:
            messagebox.showerror('기존 결과 판정', str(exc), parent=window)

    def request_stop():
        cancellation.set()
        stop.configure(state='disabled')
        status.set('중지 요청을 받았습니다. 복구와 결과 저장을 기다려 주세요.')

    for key, label, command in [('scan', '경로 확인', scan), 
                                ('edit', '기대값 편집', edit_criteria), ('run', '일괄 수집', run),
                                ('reassess', '기존 결과 다시 판정', reassess_saved)]:
        button = ttk.Button(buttons, text=label, command=command)
        button.pack(side='left', padx=(0, 6))
        controls.append(button)
        actions[key] = button
    stop = ttk.Button(buttons, text='중지', command=request_stop, state='disabled')
    stop.pack(side='left')
    ttk.Label(frame, textvariable=status, wraplength=780).grid(row=7, column=0, columnspan=3, sticky='w')
    def open_result():
        if state['result']:
            path = (state['result'] / 'index.html').resolve()
            try:
                append('보고서 열기 요청: ' + str(path))
                browser = open_report(path)
                append('브라우저 실행 요청 전달: ' + str(browser))
            except (OSError, webbrowser.Error) as exc:
                append('보고서는 저장됐지만 브라우저를 열지 못했습니다: {}\n{}'.format(exc, path))
    result_button = ttk.Button(frame, text='최근 결과 열기', command=open_result, state='normal' if state['result'] else 'disabled')
    result_button.grid(row=8, column=1, sticky='e', pady=8)

    def close():
        if state['running']:
            state['closing'] = True
            request_stop()
        else:
            window.destroy()

    def bring_to_front():
        # A background window only flashes in the taskbar; raise it above other apps for the prompt.
        try:
            window.deiconify()
            window.lift()
            window.attributes('-topmost', True)
            window.focus_force()
            window.bell()
        except tk.TclError:
            pass
        status.set('장비 권한 암호 입력이 필요합니다. 화면 앞의 입력 창을 확인하세요.')

    def release_front():
        try:
            window.attributes('-topmost', False)
        except tk.TclError:
            pass

    def poll():
        try:
            while True:
                kind, value = events.get_nowait()
                if kind == 'password':
                    try:
                        if not state['closing']:
                            bring_to_front()
                            value['password']=simpledialog.askstring('장비 권한',value['name']+'의 enable 암호를 입력하세요. 취소하면 이 장비를 미수집으로 남깁니다.',show='*',parent=window)
                    finally:
                        release_front()
                        value['ready'].set()
                    continue
                if kind == 'log':
                    append(value)
                    continue
                state['running'] = False
                for widget in controls:
                    widget.configure(state='normal')
                stop.configure(state='disabled')
                if kind == 'done':
                    state['result'], outcome, cancelled = value
                    status.set(('중지됨' if cancelled else '완료') + ' · 판정: ' + {'passed':'기준 충족','failed':'기준 불일치','inconclusive':'확인 필요'}.get(outcome,outcome))
                    append('결과: ' + str(state['result']))
                elif kind == 'cancelled':
                    status.set('프로젝트 기동 전에 중지했습니다.')
                else:
                    status.set('실행 오류')
                    append(value)
                result_button.configure(state='normal' if state['result'] else 'disabled')
                if kind == 'done' and not value[2] and not state['closing']:
                    open_result()
                if state['closing']:
                    window.destroy()
                    return
        except queue.Empty:
            pass
        window.after(100, poll)
    window.protocol('WM_DELETE_WINDOW', close)
    window.after(100, poll)
    window._topolog = {'projects': projects, 'output': output, 'rules': rules, 'status': status,
                       'actions': actions, 'state': state, 'stop': stop, 'result': result_button,
                       'events': events, 'cancel': cancellation, 'edit': edit_criteria, 'close': close,
                       'default_password': default_password, 'auto_start': auto_start,
                       'capture': capture, 'bring_to_front': bring_to_front}
    if run_loop:
        window.mainloop()
    return window
