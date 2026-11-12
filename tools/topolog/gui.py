"""Step-by-step setup, then one run screen for batch collection.

Setup pages: folders → enable privilege → GNS3 pictures → report detail range
→ confirm and run. The confirm page lists the projects with a check box each;
only the checked ones are collected. GNS3 and its linked VM are always started when needed.
Passwords stay in memory for this run only. Detailed messages are kept in a
separate log view and a log file; the main screen shows plain progress.
"""
from datetime import datetime
from pathlib import Path
import os
import queue
import threading
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, simpledialog
from tkinter import font as tkfont
import webbrowser
from . import settings
from . import resources
from .cli import connect, new_output
from .engine import Runner, discover, load_rules
from .server_config import prepare as prepare_server
from .criteria import reassess
from .criteria_editor import open_editor
from .viewer import open_report, latest_report
from . import runtime
from . import preflight
from .transport import API

STEPS = ['폴더', 'enable 권한', '토폴로지 사진', '보고서 범위', '확인·실행']
SETTINGS_WIDTH = 210
COLORS = {'bg': '#f4f6f8', 'panel': '#ffffff', 'side': '#1f3b4d', 'side_text': '#c9d6df', 'side_active': '#ffffff',
          'accent': '#176b78', 'muted': '#5b6b76', 'warn_bg': '#fff4d6', 'warn': '#7a5600', 'error': '#a12a2a', 'line': '#d9e0e5'}
STATUS_LABELS = {'pending': '수집 대기', 'excluded': '제외', 'completed': '완료', 'error': '오류', 'cancelled': '중지',
                 'cleanup_failed': '정리 오류', 'not_run': '실행 안 함'}
PHOTO_LABELS = {'captured': '저장', 'unavailable': '없음'}


def fit(buttons):
    """Same minimum width for a group of buttons; a longer label still widens its own button, so text is never cut."""
    from tkinter import font as tkfont
    if not buttons:
        return
    measure = tkfont.Font(root=buttons[0], family=_font_family(), size=10, weight='bold')
    unit = max(1, measure.measure('0'))
    width = max(-(-measure.measure(b.cget('text')) // unit) for b in buttons) + 1
    for button in buttons:
        button.configure(width=-width)   # negative ttk width = minimum, the label's natural size wins


def shorten_path(text, limit, measure):
    """Fit a path into `limit` pixels by replacing whole middle folders with '…'; drive and last folder stay."""
    if not text or measure(text) <= limit:
        return text
    separator = '\\' if '\\' in text else '/'
    parts = text.split(separator)
    if len(parts) < 3:
        return text
    head, tail = [parts[0]], parts[1:]
    while len(tail) > 1:
        tail = tail[1:]
        candidate = separator.join(head + ['…'] + tail)
        if measure(candidate) <= limit:
            return candidate
    # Only the last folder is left: keep it whole even when it is longer than the limit.
    return separator.join(head + ['…'] + tail)


PROJECT_STEPS = [('프로젝트 열기', ('프로젝트 ',)), ('장비 시작', ('장비 시작',)), ('설정 수집', ('장비 ',)),
                 ('사진·ping 확인', ('GNS3 사진 저장·도달성', '도달성 확인')), ('최종 확인', ('최종 경로', '방화벽 흐름', '토폴로지 저장')),
                 ('정지·저장', ('장비 상태 복원', '프로젝트 처리 완료'))]


def project_steps(stage):
    """'✓ 프로젝트 열기 → ✓ 장비 시작 → ▶ 설정 수집 → …' for a stage label of the engine; '' outside a project."""
    current = None
    for index, (label, prefixes) in reversed(list(enumerate(PROJECT_STEPS))):
        if index == 2:
            matched = stage.startswith('프로젝트 ') and ' · 장비 ' in stage
        elif index == 0:
            matched = stage.startswith('프로젝트 ') and ' · 장비 ' not in stage and not stage.startswith('프로젝트 처리 완료')
        else:
            matched = stage.startswith(prefixes)
        if matched:
            current = index
            break
    if current is None:
        return ''
    marks = []
    for index, (label, _) in enumerate(PROJECT_STEPS):
        mark = '✓' if index < current or (index == current and stage.startswith('프로젝트 처리 완료')) else ('▶' if index == current else '○')
        marks.append(mark + ' ' + label)
    return '   '.join(marks)


def log_directory():
    return settings.settings_path().parent / 'logs'


def _font_family():
    return 'Malgun Gothic' if os.name == 'nt' else 'TkDefaultFont'


def _checkmark_style(window, style):
    """Use a tick rather than the clam theme's ambiguous cross."""
    images = []
    for disabled, selected in ((False, False), (False, True), (True, False), (True, True)):
        image = tk.PhotoImage(master=window, width=22, height=22)
        border = '#aeb8bf' if disabled else COLORS['accent']
        image.put(border, to=(1, 1, 20, 20))
        image.put('#eef1f3' if disabled else '#ffffff', to=(2, 2, 19, 19))
        if selected:
            # Pixel strokes keep the check legible regardless of installed fonts.
            for x, y in [(x, x + 6) for x in range(5, 9)] + [(x, 22 - x) for x in range(8, 17)]:
                image.put(border, to=(x, y - 1, x + 2, y + 2))
        images.append(image)
    window._topolog_check_images = images
    style.element_create('TopoLog.check', 'image', images[0],
                         ('disabled', 'selected', images[3]), ('disabled', images[2]),
                         ('selected', images[1]), sticky='w')
    layout = [('Checkbutton.padding', {'sticky': 'nswe', 'children': [
        ('TopoLog.check', {'side': 'left', 'sticky': 'w'}),
        ('Checkbutton.focus', {'side': 'left', 'sticky': 'w', 'children': [
            ('Checkbutton.label', {'sticky': 'nswe'})]})]})]
    # Every check box in every TopoLog window (wizard, title editor, criteria editor) uses the tick.
    for name in ('Panel.TCheckbutton', 'TCheckbutton'):
        style.layout(name, layout)


def _image(name, max_width, max_height):
    """PhotoImage of a bundled example picture scaled down to fit, or None."""
    path = resources.asset(name)
    try:
        image = tk.PhotoImage(file=str(path))
    except (tk.TclError, OSError):
        return None
    factor = max(1, -(-image.width() // max_width), -(-image.height() // max_height))
    return image.subsample(factor, factor) if factor > 1 else image


def launch(run_loop=True):
    window = tk.Tk()
    if not run_loop:
        window.withdraw()
    version = resources.version()
    window.title('TopoLog ' + version)
    window.geometry('1100x780')
    window.minsize(960, 700)
    window.configure(background=COLORS['bg'])
    family = _font_family()
    style = ttk.Style(window)
    try:
        style.theme_use('clam')
    except tk.TclError:
        pass
    style.configure('.', font=(family, 10), background=COLORS['bg'])
    style.configure('Panel.TFrame', background=COLORS['panel'])
    style.configure('Panel.TLabel', background=COLORS['panel'])
    style.configure('Panel.TCheckbutton', background=COLORS['panel'])
    _checkmark_style(window, style)
    style.configure('Panel.TRadiobutton', background=COLORS['panel'])
    style.configure('Title.TLabel', background=COLORS['panel'], font=(family, 17, 'bold'), foreground='#17303f')
    style.configure('Lead.TLabel', background=COLORS['panel'], foreground=COLORS['muted'], font=(family, 10))
    style.configure('Caption.TLabel', background=COLORS['panel'], foreground=COLORS['muted'], font=(family, 9))
    style.configure('Key.TLabel', background=COLORS['panel'], foreground=COLORS['muted'], font=(family, 9))
    style.configure('Value.TLabel', background=COLORS['panel'], font=(family, 10, 'bold'))
    style.configure('Big.TLabel', background=COLORS['panel'], font=(family, 13, 'bold'), foreground='#17303f')
    style.configure('Warn.TLabel', background=COLORS['warn_bg'], foreground=COLORS['warn'], font=(family, 10, 'bold'), padding=8)
    style.configure('Error.TLabel', background=COLORS['panel'], foreground=COLORS['error'])
    # Same font size and padding for every button so a row of buttons lines up at one height.
    style.configure('Accent.TButton', font=(family, 10, 'bold'), padding=(14, 6), foreground='#ffffff', background=COLORS['accent'])
    style.map('Accent.TButton', background=[('disabled', '#9fb5bb'), ('active', '#125761')])
    style.configure('TButton', padding=(14, 6))
    style.configure('Compact.TButton', padding=(8, 6))
    # Frequently used buttons: light accent fill and accent text, below the filled main action.
    style.configure('Soft.TButton', font=(family, 10, 'bold'), padding=(14, 6), foreground=COLORS['accent'], background='#e1eff1')
    style.map('Soft.TButton', background=[('disabled', '#eef1f3'), ('active', '#cde4e8')], foreground=[('disabled', '#9aa8b0')])
    # Stop: red text so it is not pressed by mistake for another button.
    style.configure('Danger.TButton', foreground=COLORS['error'])
    style.map('Danger.TButton', foreground=[('disabled', '#9aa8b0')])
    style.configure('TMenubutton', padding=(14, 6))
    style.configure('Treeview', rowheight=24, font=(family, 10))
    style.configure('Treeview.Heading', font=(family, 10, 'bold'))
    style.configure('Horizontal.TProgressbar', thickness=16, background=COLORS['accent'])

    defaults = settings.load()
    projects = tk.StringVar(value=defaults['project_directory'])
    output = tk.StringVar(value=defaults['output_directory'])
    from .criteria_editor import default_path as criteria_default
    # The criteria file the editor saves is applied to every run without choosing it again.
    rules = tk.StringVar(value=str(criteria_default()) if criteria_default().is_file() else '')
    allow_enable = tk.BooleanVar(value=defaults.get('allow_enable', True))
    default_password = tk.StringVar()
    capture = tk.BooleanVar(value=defaults.get('capture_screenshot', True))
    detail_scope = tk.StringVar(value=defaults.get('detail_scope', 'representative'))
    status = tk.StringVar(value='설정을 확인한 뒤 수집 시작을 누르세요.')
    folder_message = tk.StringVar(value='')
    progress_text = tk.StringVar(value='진행 대기')
    project_text = tk.StringVar(value='—')
    task_text = tk.StringVar(value='—')
    time_text = tk.StringVar(value='—')
    percent = tk.DoubleVar(value=0)
    percent_text = tk.StringVar(value='0%')
    notice_text = tk.StringVar(value='')
    outcome_text = tk.StringVar(value='')
    events, cancellation = queue.Queue(), threading.Event()
    from .pausing import PauseGate
    gate = PauseGate()
    gate.listeners.append(lambda paused, where: events.put(('progress', {'gate_paused': paused, 'pause_where': where})))
    state = {'running': False, 'result': latest_report(defaults['output_directory']), 'closing': False, 'selected': None,
             'retry_source': None, 'step': 0, 'entries': [], 'log': [], 'log_file': None, 'pausable': False}
    controls, actions = [], {}
    from .run_progress import RunProgress
    tracker = RunProgress()
    tracker.active = False

    # ---------- layout: side step list + content + navigation ----------
    side = tk.Frame(window, background=COLORS['side'], width=210)
    side.pack(side='left', fill='y')
    side.pack_propagate(False)
    tk.Label(side, text='TopoLog', background=COLORS['side'], foreground='#ffffff', font=(family, 18, 'bold')).pack(anchor='w', padx=20, pady=(22, 2))
    tk.Label(side, text='GNS3 실습 수집·정리\n' + version, background=COLORS['side'], foreground=COLORS['side_text'],
             font=(family, 9), justify='left').pack(anchor='w', padx=20, pady=(0, 22))
    step_labels = []
    for index, name in enumerate(STEPS):
        label = tk.Label(side, text='{}  {}'.format(index + 1, name), background=COLORS['side'], foreground=COLORS['side_text'],
                         font=(family, 11), anchor='w', padx=20, pady=8)
        label.pack(fill='x')
        label.bind('<Button-1>', lambda event, step=index: step_clicked(step))
        label.configure(cursor='hand2')
        step_labels.append(label)
    body = ttk.Frame(window, padding=(22, 18, 22, 12))
    body.pack(side='left', fill='both', expand=True)
    card = ttk.Frame(body, style='Panel.TFrame', padding=(24, 20))
    card.pack(fill='both', expand=True)
    nav = ttk.Frame(body, padding=(0, 12, 0, 0))
    nav.pack(side='bottom', fill='x', before=card)
    pages = []
    keep_images = window._topolog_images = []   # Tk deletes an image once Python drops it

    def page(title, lead):
        frame = ttk.Frame(card, style='Panel.TFrame')
        ttk.Label(frame, text=title, style='Title.TLabel').pack(anchor='w')
        lead_label = ttk.Label(frame, text=lead, style='Lead.TLabel', wraplength=760, justify='left')
        lead_label.pack(anchor='w', pady=(6, 16))
        # Follow the page width so the lead wraps instead of running past the edge in a narrow window.
        frame.bind('<Configure>', lambda event, label=lead_label: label.configure(wraplength=min(760, max(200, event.width - 8))), add='+')
        pages.append(frame)
        return frame

    def picture(parent, name, caption, width=400, height=250, side_pack=None):
        holder = ttk.Frame(parent, style='Panel.TFrame')
        image = _image(name, width, height)
        if image is not None:
            keep_images.append(image)
            tk.Label(holder, image=image, background=COLORS['panel'], borderwidth=1, relief='solid').pack(anchor='w')
        ttk.Label(holder, text=caption, style='Caption.TLabel', wraplength=width, justify='left').pack(anchor='w', pady=(4, 0))
        if side_pack:
            holder.pack(side=side_pack, anchor='n', padx=(0, 18))
        else:
            holder.pack(anchor='w', pady=(14, 0))
        return holder

    def select_folder(variable):
        value = filedialog.askdirectory(parent=window, initialdir=variable.get() if Path(variable.get()).is_dir() else str(Path.home()))
        if value:
            variable.set(value)
            folder_message.set('')

    # Page 1: folders
    first = page('프로젝트 폴더와 결과 저장 폴더', 'GNS3 프로젝트(.gns3)가 들어 있는 폴더를 고르고, 결과를 저장할 폴더를 지정합니다. '
                 '결과 폴더는 프로젝트 폴더 밖이어야 하며, 실행할 때마다 날짜가 붙은 새 폴더와 ZIP이 만들어집니다. 원본 프로젝트는 바꾸지 않습니다.')
    grid = ttk.Frame(first, style='Panel.TFrame')
    grid.pack(fill='x')
    grid.columnconfigure(1, weight=1)
    for row, (label, variable) in enumerate([('GNS3 프로젝트 폴더', projects), ('결과 저장 폴더', output)]):
        ttk.Label(grid, text=label, style='Panel.TLabel').grid(row=row, column=0, sticky='w', pady=6)
        entry = ttk.Entry(grid, textvariable=variable)
        entry.grid(row=row, column=1, sticky='ew', padx=10, pady=6, ipady=4)
        button = ttk.Button(grid, text='폴더 선택', command=lambda v=variable: select_folder(v))
        button.grid(row=row, column=2, sticky='ew', pady=6)
        controls.extend([entry, button])
    check_row = ttk.Frame(first, style='Panel.TFrame')
    check_row.pack(fill='x', pady=(10, 0))
    ttk.Label(check_row, textvariable=folder_message, style='Panel.TLabel', wraplength=700).pack(side='left')
    picture(first, 'wizard-folders.png', '설명용 그림: 프로젝트 폴더 아래의 .gns3 파일을 모두 찾고, 결과는 별도 폴더에 실행마다 새로 저장합니다.', 560, 230)

    # Page 2: enable
    second = page('enable 권한', '설정을 읽으려면 장비에서 특권 모드(enable)가 필요한 경우가 있습니다. 켜 두면 먼저 암호 없이 전환을 시도하고, '
                  '암호가 필요한 장비에서만 아래 기본 암호를 사용하거나 그 장비의 암호를 물어봅니다. 수집 뒤에는 원래 권한으로 되돌립니다.')
    toggle = ttk.Checkbutton(second, text='필요할 때 enable 권한으로 전환', variable=allow_enable, style='Panel.TCheckbutton')
    toggle.pack(anchor='w')
    password_row = ttk.Frame(second, style='Panel.TFrame')
    password_row.pack(anchor='w', pady=(10, 0))
    ttk.Label(password_row, text='기본 enable 암호(선택)', style='Panel.TLabel').pack(side='left')
    password_entry = ttk.Entry(password_row, textvariable=default_password, show='*', width=22)
    password_entry.pack(side='left', padx=10)
    ttk.Label(second, text='비워 두면 암호 없음으로 처리합니다. 입력한 암호는 이번 실행 동안 메모리에만 두며 설정·로그·결과 파일에 저장하지 않습니다.',
              style='Caption.TLabel', wraplength=720).pack(anchor='w', pady=(6, 0))

    def sync_password(*_):
        password_entry.configure(state='normal' if allow_enable.get() and not state['running'] else 'disabled')
    allow_enable.trace_add('write', sync_password)
    controls.extend([toggle, password_entry])
    picture(second, 'wizard-enable.png', '설명용 그림: 사용자 모드(>)에서 enable로 특권 모드(#)에 들어가 설정을 읽고, 끝나면 원래 모드로 돌아갑니다.', 560, 220)

    # Page 3: pictures
    third = page('실제 GNS3 토폴로지 사진', '켜면 프로젝트마다 GNS3 화면에서 해당 프로젝트를 열고 GNS3 자체 Screenshot 기능으로 사진을 저장합니다. '
                 '끄더라도 GNS3 API의 좌표·연결로 그린 재구성 그림은 항상 만들어지며, 두 그림은 보고서와 결과 폴더에서 구분됩니다.')
    shooter = ttk.Checkbutton(third, text='실제 토폴로지 사진 저장', variable=capture, style='Panel.TCheckbutton')
    shooter.pack(anchor='w')
    controls.append(shooter)
    ttk.Label(third, text='사진을 저장하는 몇 초 동안 GNS3 창과 저장 창이 앞에 나타납니다. 이때 마우스·키보드로 창을 조작하면 저장이 취소될 수 있으니 '
              '잠시 기다려 주세요. 저장이 끝나면 보던 창을 다시 앞으로 돌려놓습니다.', style='Caption.TLabel', wraplength=740).pack(anchor='w', pady=(6, 8))
    ttk.Label(third, text='저장하지 못한 사진이 있으면 수집이 끝난 뒤 다시 저장할지 묻습니다(실행 화면의 \'사진 다시 저장\' 버튼으로도 할 수 있습니다). '
              '서브넷·cost 주석 사본과 사진 제목은 수집이 끝난 뒤 열리는 사진 제목 정리 창에서 고릅니다.',
              style='Caption.TLabel', wraplength=740).pack(anchor='w', pady=(2, 8))
    pair = ttk.Frame(third, style='Panel.TFrame')
    pair.pack(anchor='w')
    picture(pair, 'example-gns3-native.png', '실제 결과 예시: GNS3 Screenshot 기능으로 저장한 사진 (토폴로지_사진/GNS3_실제_사진)', 380, 260, 'left')
    picture(pair, 'example-reconstructed.png', '실제 결과 예시: 같은 프로젝트의 재구성 그림, 항상 생성 (토폴로지_사진/재구성_그림)', 380, 260, 'left')

    # Page 4: report detail range
    fourth = page('보고서 상세 정리 범위', 'Word 문서와 문서용 Markdown에서 프로젝트별 상세 정리(사진·주요 설정·동작 원리·수집 출력)를 어디까지 넣을지 정합니다. '
                  'HTML 보고서, 수집 원문과 원문 다운로드는 선택과 관계없이 모든 프로젝트를 담습니다.')
    for value, label, hint in [('representative', '대표 프로젝트만 (최대 6개)', '탐지한 기능 종류가 많은 순서로 최대 6개를 고릅니다(같으면 이름순). 중요도나 완성도를 평가해 고르는 것이 아닙니다.'),
                               ('all', '전체 프로젝트', '수집한 모든 프로젝트를 상세 정리합니다. 문서가 길어질 수 있습니다.')]:
        radio = ttk.Radiobutton(fourth, text=label, value=value, variable=detail_scope, style='Panel.TRadiobutton')
        radio.pack(anchor='w', pady=(4, 0))
        ttk.Label(fourth, text=hint, style='Caption.TLabel', wraplength=700).pack(anchor='w', padx=(24, 0))
        controls.append(radio)
    picture(fourth, 'wizard-scope.png', '설명용 그림: 대표만 선택해도 나머지 프로젝트는 전체 목록과 HTML 보고서, 원문 파일에 그대로 남습니다.', 560, 220)

    # Page 5: confirm and run. Left: settings; right: project list, progress and run buttons; bottom: result buttons.
    fifth = page('확인 및 실행', '설정을 확인하고 수집할 프로젝트를 고른 뒤 시작하세요. GNS3와 연동 VM이 꺼져 있으면 자동으로 실행하고 준비될 때까지 기다립니다.')
    result_row = ttk.Frame(fifth, style='Panel.TFrame')
    result_row.pack(side='bottom', fill='x')
    ttk.Separator(result_row, orient='horizontal').pack(fill='x', pady=(10, 8))
    side_bar = ttk.Frame(result_row, style='Panel.TFrame')
    side_bar.pack(fill='x')
    ttk.Label(side_bar, text='결과', style='Value.TLabel').pack(side='left', padx=(0, 4))
    columns = ttk.Frame(fifth, style='Panel.TFrame')
    columns.pack(fill='both', expand=True)
    settings_column = ttk.Frame(columns, style='Panel.TFrame', width=SETTINGS_WIDTH)
    settings_column.pack(side='left', fill='y')
    settings_column.pack_propagate(False)
    ttk.Separator(columns, orient='vertical').pack(side='left', fill='y', padx=16)
    work_column = ttk.Frame(columns, style='Panel.TFrame')
    work_column.pack(side='left', fill='both', expand=True)

    # Settings change sits beside the heading of the settings it changes, so a tall column never hides it.
    run_bar = ttk.Frame(settings_column, style='Panel.TFrame')
    run_bar.pack(fill='x', pady=(0, 2))
    ttk.Label(run_bar, text='수집 설정', style='Value.TLabel').pack(side='left')
    summary_values = {}
    path_labels = []
    tip = {'window': None}
    measure_value = tkfont.Font(root=window, family=family, size=10, weight='bold')

    def hide_tip(event=None):
        if tip['window'] is not None:
            tip['window'].destroy()
            tip['window'] = None

    def show_tip(event, text):
        hide_tip()
        top = tk.Toplevel(window)
        top.wm_overrideredirect(True)
        top.geometry('+{}+{}'.format(event.x_root + 12, event.y_root + 16))
        tk.Label(top, text=text, background='#ffffe8', foreground='#1d2b33', relief='solid', borderwidth=1,
                 font=(family, 9), padx=6, pady=3).pack()
        tip['window'] = top

    def open_path(text):
        if os.name == 'nt' and text and Path(text).is_dir():
            os.startfile(text)

    def fit_paths(event=None):
        limit = settings_column.winfo_width() - 8
        if limit < 40:
            return
        for label, value in path_labels:
            label.configure(text=shorten_path(value.get(), limit, measure_value.measure))

    for key in ['프로젝트 폴더', '결과 저장 폴더', 'enable 권한', '토폴로지 사진', '보고서 범위', '대상 프로젝트', '기대값']:
        value = tk.StringVar()
        summary_values[key] = value
        ttk.Label(settings_column, text=key, style='Key.TLabel').pack(anchor='w', pady=(5, 0))
        if key.endswith('폴더'):
            # A long path is shortened in the middle; hovering shows it whole and a click opens it.
            label = ttk.Label(settings_column, style='Value.TLabel', cursor='hand2')
            label.bind('<Enter>', lambda event, v=value: show_tip(event, v.get()))
            label.bind('<Leave>', hide_tip)
            label.bind('<Button-1>', lambda event, v=value: open_path(v.get()))
            path_labels.append((label, value))
            value.trace_add('write', lambda *args: window.after_idle(fit_paths))
        else:
            label = ttk.Label(settings_column, textvariable=value, style='Value.TLabel', wraplength=SETTINGS_WIDTH - 8, justify='left')
        label.pack(anchor='w')
    settings_column.bind('<Configure>', fit_paths)

    pick_text = tk.StringVar(value='')
    ttk.Label(work_column, textvariable=pick_text, style='Value.TLabel').pack(anchor='w')
    pick_bar = ttk.Frame(work_column, style='Panel.TFrame')
    pick_bar.pack(fill='x', pady=(4, 6))
    run_row = ttk.Frame(work_column, style='Panel.TFrame')
    run_row.pack(side='bottom', fill='x', pady=(8, 0))
    progress_controls = run_row
    notice = ttk.Label(work_column, textvariable=notice_text, style='Warn.TLabel', wraplength=520)
    status_box = ttk.Frame(work_column, style='Panel.TFrame')
    status_box.pack(side='bottom', fill='x')
    status_box.columnconfigure(1, weight=1)
    ttk.Label(status_box, textvariable=project_text, style='Value.TLabel').grid(row=0, column=0, sticky='w', padx=(0, 10), pady=(8, 0))
    ttk.Label(status_box, textvariable=task_text, style='Panel.TLabel', wraplength=420).grid(row=0, column=1, sticky='w', pady=(8, 0))
    # Where the current project is in its own sequence, and what each device is doing right now.
    steps_text = tk.StringVar(value='')
    activity_text = tk.StringVar(value='')
    steps_label = ttk.Label(status_box, textvariable=steps_text, style='Caption.TLabel', wraplength=520)
    steps_label.grid(row=1, column=0, columnspan=2, sticky='w', pady=(4, 0))
    activity_label = ttk.Label(status_box, textvariable=activity_text, style='Panel.TLabel', wraplength=520, justify='left')
    activity_label.grid(row=2, column=0, columnspan=2, sticky='w', pady=(2, 0))
    status_box.bind('<Configure>', lambda event: [label.configure(wraplength=max(200, event.width - 8)) for label in (steps_label, activity_label)])
    bar_row = ttk.Frame(status_box, style='Panel.TFrame')
    bar_row.grid(row=3, column=0, columnspan=2, sticky='ew', pady=(6, 2))
    bar_row.columnconfigure(0, weight=1)
    progress_bar = ttk.Progressbar(bar_row, variable=percent, maximum=100, mode='determinate', style='Horizontal.TProgressbar')
    progress_bar.grid(row=0, column=0, sticky='ew')
    ttk.Label(bar_row, textvariable=percent_text, style='Value.TLabel', width=5).grid(row=0, column=1, padx=(10, 0))
    ttk.Label(status_box, textvariable=time_text, style='Caption.TLabel').grid(row=4, column=0, columnspan=2, sticky='w')
    ttk.Label(status_box, textvariable=status, style='Panel.TLabel', wraplength=520).grid(row=5, column=0, columnspan=2, sticky='w', pady=(4, 0))
    ttk.Label(status_box, textvariable=outcome_text, style='Value.TLabel', wraplength=520).grid(row=6, column=0, columnspan=2, sticky='w')
    tree_frame = ttk.Frame(work_column, style='Panel.TFrame')
    tree_frame.pack(fill='both', expand=True)
    tree = ttk.Treeview(tree_frame, columns=('status', 'photo'), height=5)
    tree.heading('#0', text='프로젝트')
    tree.heading('status', text='상태')
    tree.heading('photo', text='GNS3 사진')
    tree.column('#0', width=200)
    tree.column('status', width=220)
    tree.column('photo', width=90, minwidth=90, stretch=False, anchor='center')
    tree.pack(side='left', fill='both', expand=True)
    tree_scroll = ttk.Scrollbar(tree_frame, command=tree.yview)
    tree_scroll.pack(side='right', fill='y')
    tree.configure(yscrollcommand=tree_scroll.set)

    # Detailed log: separate window, also written to a file.
    log_window = {'top': None, 'text': None}

    def append(text):
        line = '[{}] {}'.format(datetime.now().strftime('%H:%M:%S'), text)
        state['log'].append(line)
        if state['log_file']:
            try:
                with open(state['log_file'], 'a', encoding='utf-8') as stream:
                    stream.write(line + '\n')
            except OSError:
                state['log_file'] = None
        widget = log_window['text']
        if widget is not None:
            try:
                widget.configure(state='normal')
                widget.insert('end', line + '\n')
                widget.see('end')
                widget.configure(state='disabled')
            except tk.TclError:
                log_window['text'] = None

    def show_log():
        if log_window['top'] is not None:
            try:
                log_window['top'].deiconify()
                log_window['top'].lift()
                return
            except tk.TclError:
                pass
        top = tk.Toplevel(window)
        top.title('TopoLog 상세 기록')
        top.geometry('860x460')
        text = tk.Text(top, wrap='word', font=('Consolas' if os.name == 'nt' else 'TkFixedFont', 9))
        scroll = ttk.Scrollbar(top, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        text.pack(fill='both', expand=True)
        text.insert('end', '\n'.join(state['log']) + ('\n' if state['log'] else ''))
        text.configure(state='disabled')
        text.see('end')
        bar = ttk.Frame(top, padding=6)
        bar.pack(fill='x')
        ttk.Label(bar, text='기록 파일: ' + (str(state['log_file']) if state['log_file'] else '이번 실행부터 저장됩니다')).pack(side='left')
        def close_log():
            log_window.update(top=None, text=None)
            top.destroy()
        top.protocol('WM_DELETE_WINDOW', close_log)
        log_window.update(top=top, text=text)

    def open_log_folder():
        folder = log_directory()
        folder.mkdir(parents=True, exist_ok=True)
        if os.name == 'nt':
            os.startfile(str(folder))
        else:
            webbrowser.open(folder.as_uri())

    from .progress import wrap
    notify = wrap(lambda text: events.put(('log', text)))

    def request_password(name):
        request = {'name': name, 'ready': threading.Event(), 'password': None}
        events.put(('password', request))
        while not request['ready'].wait(.1):
            if cancellation.is_set():
                raise InterruptedError('Cancelled while requesting password')
        return request.pop('password', None)

    def report_error(error=None, feedback=False):
        """Error report (opened automatically on a program error) or feedback (opened by the person).

        Shows exactly what would be sent; nothing leaves the PC until the person ticks
        agreement and presses send. Only one such window is open at a time.
        """
        from . import errorreport
        current = state.get('report_window')
        try:
            if current is not None and current.winfo_exists():
                current.lift()
                if error:
                    append('오류 보고 창이 이미 열려 있어 이 오류는 기록에만 남깁니다: ' + str(error)[:300])
                return current
        except tk.TclError:
            pass
        context = {'build': 'windows-exe' if resources.FROZEN else 'source', 'step': STEPS[state['step']]}
        top = tk.Toplevel(window)
        top.title('피드백 보내기' if feedback else '오류 보고')
        top.geometry('760x600')
        top.transient(window)
        frame = ttk.Frame(top, padding=14)
        frame.pack(fill='both', expand=True)
        lead = ('사용하면서 불편했던 점이나 문제를 적어 개발자에게 보냅니다. ' if feedback else
                '프로그램 오류가 발생했습니다. 아래 내용을 개발자에게 보내면 원인을 찾는 데 도움이 됩니다. ')
        ttk.Label(frame, text=lead + '보내는 곳: Sentry(미국 서버, 30일 보관). 실행 기록에는 장비 설정·IP·프로젝트 이름이 들어 있을 수 있고, '
                  '알려진 암호·키 형식과 사용자 폴더 이름은 가렸습니다. 동의하지 않으면 아무것도 보내지 않습니다.',
                  wraplength=720, justify='left').pack(anchor='w')
        # Buttons, result and agreement are packed from the bottom first so a long report never pushes them off the window.
        buttons = ttk.Frame(frame)
        buttons.pack(side='bottom', fill='x', pady=(10, 0))
        agree = tk.BooleanVar(value=False)
        attach = tk.BooleanVar(value=True)
        result = tk.StringVar(value='')
        ttk.Label(frame, textvariable=result, wraplength=720, justify='left').pack(side='bottom', anchor='w', pady=(6, 0))
        ttk.Checkbutton(frame, text='위 내용을 개발자에게 보내는 데 동의합니다', variable=agree).pack(side='bottom', anchor='w')
        comment = None
        if feedback:
            ttk.Label(frame, text='내용(필수)').pack(anchor='w', pady=(10, 2))
            comment = tk.Text(frame, height=6, wrap='word')
            comment.pack(fill='x')
            ttk.Checkbutton(frame, text='상세 실행 기록 함께 보내기(아래 내용)', variable=attach).pack(anchor='w', pady=(8, 0))
        box = ttk.Frame(frame)
        box.pack(fill='both', expand=True, pady=10)
        view = tk.Text(box, wrap='word', font=('Consolas' if os.name == 'nt' else 'TkFixedFont', 9))
        bar = ttk.Scrollbar(box, command=view.yview)
        view.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        view.pack(fill='both', expand=True)

        def current_report():
            if feedback:
                text = comment.get('1.0', 'end').strip() or '(내용 없음)'
                return errorreport.build(text, state['log'] if attach.get() else (), dict(context, kind='feedback'), kind='feedback')
            return errorreport.build(error or state.get('last_error') or '오류 내용 없음', state['log'], context)

        def show(*_):
            view.configure(state='normal')
            view.delete('1.0', 'end')
            report = current_report()
            view.insert('end', report['log'] or '(실행 기록을 보내지 않습니다)' if feedback else report['preview'])
            view.configure(state='disabled')
        show()
        attach.trace_add('write', show)
        outcome = queue.Queue()

        def done():
            try:
                kind, value = outcome.get_nowait()
            except queue.Empty:
                top.after(100, done)
                return
            if kind == 'sent':
                result.set('보냈습니다. 보고 번호: ' + value)
                append(('피드백' if feedback else '오류 보고') + '를 보냈습니다: ' + value)
            else:
                result.set('보내지 못했습니다(' + value + '). 보낼 내용은 ' + str(state.get('report_file')) + ' 에 저장했습니다. 이 파일을 직접 전달해 주세요.')
                append('보고 전송 실패: ' + value)
                send.state(['!disabled'])

        def submit():
            if not agree.get():
                return
            if feedback and not comment.get('1.0', 'end').strip():
                result.set('보낼 내용을 적어 주세요.')
                return
            report = current_report()
            send.state(['disabled'])
            result.set('보내는 중입니다.')
            try:
                state['report_file'] = errorreport.keep(report, log_directory())
            except OSError:
                state['report_file'] = None

            def work():
                try:
                    outcome.put(('sent', errorreport.send(report)))
                except Exception as exc:
                    outcome.put(('failed', type(exc).__name__))
            threading.Thread(target=work, daemon=True).start()
            top.after(100, done)
        send = ttk.Button(buttons, text='보내기', style='Accent.TButton', command=submit, state='disabled')
        send.pack(side='left')
        ttk.Button(buttons, text='닫기', command=top.destroy).pack(side='right')
        agree.trace_add('write', lambda *_: send.state(['!disabled'] if agree.get() else ['disabled']))
        state['report_window'] = top
        return top

    def callback_failed(kind, value, trace):
        """An exception inside a button or window handler: logged, shown, and offered as an error report."""
        import traceback
        text = ''.join(traceback.format_exception(kind, value, trace))
        append('프로그램 오류: ' + text)
        state['last_error'] = text
        if not state['closing']:
            report_error(text)
    window.report_callback_exception = callback_failed

    # ---------- navigation ----------
    back = ttk.Button(nav, text='이전', command=lambda: go(state['step'] - 1))
    back.pack(side='left')
    forward = ttk.Button(nav, text='다음', style='Accent.TButton', command=lambda: go(state['step'] + 1))
    forward.pack(side='right')
    log_button = ttk.Button(nav, text='상세 기록 보기', command=show_log)
    log_button.pack(side='right', padx=(0, 10))

    def scope_label():
        from .report_options import LABELS
        return LABELS.get(detail_scope.get(), detail_scope.get())

    def refresh_summary():
        summary_values['프로젝트 폴더'].set(projects.get())
        summary_values['결과 저장 폴더'].set(output.get())
        summary_values['enable 권한'].set(('사용 · 기본 암호 ' + ('입력됨(저장 안 함)' if default_password.get() else '없음')) if allow_enable.get() else '사용 안 함')
        summary_values['토폴로지 사진'].set('GNS3 사진 + 자동 주석 + 재구성 그림' if capture.get() else '재구성 그림만')
        summary_values['보고서 범위'].set(scope_label())
        valid = [e for e in state['entries'] if e.get('status') == 'pending']
        chosen = len(state['selected']) if state['selected'] is not None else len(valid)
        overlap = len(state.get('duplicates') or [])
        summary_values['대상 프로젝트'].set('{}개{}{}'.format(chosen, ' (선택 범위)' if state['selected'] is not None else '',
                                                    ' · 같은 ID라 임시 사본으로 수집 {}개'.format(overlap) if overlap else ''))
        try:
            saved = load_rules(rules.get() or None)
            off = sum(len(n.get('disabled', [])) for p in saved.values() for n in p.get('nodes', {}).values())
            own = sum(len(n.get('checks', [])) for p in saved.values() for n in p.get('nodes', {}).values())
            summary_values['기대값'].set('자동 확인' + (' · 사용자 지정 {}개 · 제외 {}개'.format(own, off) if own or off else ''))
        except (OSError, ValueError):
            summary_values['기대값'].set('기대값 파일을 읽지 못했습니다 · 자동 확인만 사용')

    def selectable():
        return [e.get('project_id') for e in state['entries'] if e.get('status') == 'pending' and e.get('project_id')]

    def picked(pid):
        return state['selected'] is None or pid in state['selected']

    def fill_tree():
        tree.delete(*tree.get_children())
        boxes = window._topolog_check_images
        for entry in state['entries']:
            pid = entry.get('project_id')
            label = STATUS_LABELS.get(entry.get('status'), entry.get('status', ''))
            if entry.get('status') == 'excluded':
                label += ' · 프로젝트 파일을 읽지 못함'
            elif not picked(pid):
                label = '선택 안 함'
            options = {}
            if entry.get('status') == 'pending' and pid:
                # The same tick images as the check boxes; greyed while a run is going.
                options['image'] = boxes[(2 if state['running'] else 0) + (1 if picked(pid) else 0)]
            tree.insert('', 'end', iid=pid or entry['file'], text=entry.get('name', Path(entry['file']).stem),
                        values=(label, '' if entry.get('status') == 'excluded' else '—'), **options)
        count = sum(1 for pid in selectable() if picked(pid))
        pick_text.set('수집할 프로젝트 · {}/{}개 선택'.format(count, len(selectable())))

    def set_picked(ids):
        ids = set(ids) & set(selectable())
        state['selected'] = None if ids == set(selectable()) else ids
        state['retry_source'] = state['retry_source'] if ids else None
        refresh_summary()
        fill_tree()

    def toggle_project(event=None):
        if state['running']:
            return
        row = tree.identify_row(event.y) if event is not None and getattr(event, 'y', None) is not None else tree.focus()
        if not row or row not in selectable():
            return
        current = set(selectable()) if state['selected'] is None else set(state['selected'])
        current ^= {row}
        set_picked(current)
        tree.focus(row)
        tree.selection_set(row)
    tree.bind('<ButtonRelease-1>', toggle_project)
    tree.bind('<space>', toggle_project)
    pick_none = ttk.Button(pick_bar, style='Compact.TButton', text='전체 해제', command=lambda: set_picked([]))
    pick_none.pack(side='right')
    pick_all = ttk.Button(pick_bar, style='Compact.TButton', text='전체 선택', command=lambda: set_picked(selectable()))
    pick_all.pack(side='right', padx=(0, 8))
    controls.extend([pick_all, pick_none])

    def go(step):
        step = max(0, min(len(STEPS) - 1, step))
        if step > state['step'] and state['step'] == 0:
            # The folder scan runs on this thread; clicks queued meanwhile must not skip a page.
            forward.state(['disabled'])
            try:
                ok = scan()
                window.update()
            finally:
                forward.state(['!disabled'])
            if not ok:
                return
            if not check_duplicates(ok[0]):
                return
        state['step'] = step
        for index, frame in enumerate(pages):
            if index == step:
                frame.pack(fill='both', expand=True)
            else:
                frame.pack_forget()
        for index, label in enumerate(step_labels):
            active = index == step
            label.configure(foreground=COLORS['side_active'] if active else COLORS['side_text'],
                            font=(family, 11, 'bold' if active else 'normal'),
                            background='#2b5168' if active else COLORS['side'])
        back.configure(state='disabled' if step == 0 or state['running'] else 'normal')
        for widget in nav_extra:
            if step == len(STEPS) - 1:
                widget.pack(side='left', padx=(8, 0))
            else:
                widget.pack_forget()
        if step == len(STEPS) - 1:
            forward.pack_forget()
            refresh_summary()
            if not state['running']:
                fill_tree()
        else:
            forward.pack(side='right')
            forward.configure(text='다음' if step < len(STEPS) - 2 else '설정 확인')

    def scan():
        """Validate folders and list projects; shown once, not repeated at start."""
        try:
            root, destination = settings.validate(projects.get(), output.get())
            if resources.is_inside_bundle(destination):
                raise ValueError('결과 저장 폴더를 TopoLog 프로그램 폴더 밖으로 지정하세요.')
            _, entries = discover(root)
        except Exception as exc:
            folder_message.set('')
            messagebox.showerror('폴더 확인', str(exc), parent=window)
            return None
        state['entries'] = entries
        if state['selected'] is not None:
            # Ticks survive a re-scan for the projects that are still there.
            keep = set(state['selected']) & set(selectable())
            state['selected'] = None if (state['selected'] and not keep) or keep == set(selectable()) else keep
        valid = sum(1 for e in entries if e.get('status') == 'pending')
        excluded = len(entries) - valid
        folder_message.set('프로젝트 {}개 확인됨{}'.format(valid, ' · 읽지 못한 파일 {}개는 제외'.format(excluded) if excluded else ''))
        key = (str(root), str(destination), tuple((e['file'], e.get('status'), e.get('source_sha256')) for e in entries))
        if state.get('last_scan') != key:
            # The list is written once per change, not again when the run starts.
            state['last_scan'] = key
            append('프로젝트 폴더 확인: {} · 프로젝트 {}개 · 결과 폴더 {}'.format(root, valid, destination))
            for item in entries:
                if item.get('status') == 'excluded':
                    append('제외: {} · {}'.format(item['file'], item.get('reason', '')))
        return root, destination

    def check_duplicates(root):
        """Tell before the run which projects share a GNS3 ID and will be collected through a temporary copy."""
        found = settings.duplicate_projects(root, state['entries'])
        inside = [e for e in state['entries'] if e.get('status') == 'pending' and e.get('source_project_id')]
        state['duplicates'] = found + [{'file': e['file'], 'project_id': e['project_id']} for e in inside]
        if not state['duplicates']:
            return True
        label = {e['file']: e.get('name') or Path(e['file']).stem for e in state['entries']}
        names = ', '.join(label.get(item['file'], item['file']) for item in state['duplicates'][:6]) + (' 외' if len(state['duplicates']) > 6 else '')
        ok = messagebox.askokcancel('같은 GNS3 ID를 쓰는 프로젝트', (
            '프로젝트 {}개가 다른 프로젝트와 같은 GNS3 프로젝트 ID를 씁니다(폴더를 복사해 만든 프로젝트는 구성을 바꿔도 ID가 같습니다): {}\n\n'
            'GNS3는 한 ID에 프로젝트 하나만 열기 때문에, TopoLog는 이 프로젝트들을 결과 폴더 옆 임시 폴더에 복사하고 사본에만 새 ID를 붙여 수집합니다. '
            '원본 프로젝트 파일은 바꾸지 않고, 사본은 수집이 끝나면 지웁니다. 장비 디스크까지 복사하므로 시간이 더 걸리고 디스크 공간이 필요합니다.\n\n'
            '확인: 이대로 진행\n취소: 돌아가기').format(len(state['duplicates']), names), parent=window)
        if ok:
            append('같은 GNS3 ID를 쓰는 프로젝트 {}개는 임시 사본으로 수집합니다: {}'.format(len(state['duplicates']), names))
        return bool(ok)

    # ---------- run-screen actions ----------
    def choose_projects(retry=False):
        selected = scan()
        if not selected:
            return
        from .selection import retry_ids
        previous = None
        initial = state['selected']
        if retry:
            previous = filedialog.askdirectory(parent=window, title='다시 수집할 기존 결과 폴더')
            if not previous:
                return
            try:
                initial = retry_ids(previous)
            except (OSError, ValueError) as exc:
                messagebox.showerror('재수집', str(exc), parent=window)
                return
        if not retry:
            return
        ids = set(initial) & set(selectable())
        if not ids:
            messagebox.showinfo('재수집', '선택한 결과에 다시 수집할 프로젝트가 없거나, 현재 프로젝트 폴더에 그 프로젝트가 없습니다.', parent=window)
            return
        state['retry_source'] = previous
        set_picked(ids)
        go(len(STEPS) - 1)
        status.set('이전 결과에서 다시 수집할 프로젝트 {}개를 체크했습니다. 목록에서 바꿀 수 있습니다.'.format(len(ids)))

    def regenerate():
        folder = filedialog.askdirectory(parent=window, title='보고서를 다시 만들 결과 폴더')
        if not folder:
            return
        from .presentation import refresh_report
        try:
            open_report(refresh_report(folder, detail_scope.get()))
            append('보고서 다시 만들기: {} · {}'.format(folder, scope_label()))
        except (OSError, ValueError) as exc:
            messagebox.showerror('보고서', str(exc), parent=window)
            report_error('보고서 다시 만들기 실패: {}: {}'.format(type(exc).__name__, exc))

    def edit_titles(after=None):
        from .resultdir import is_result, top
        folder = state['result'] if state['result'] and is_result(state['result']) else None
        if folder is None:
            chosen = filedialog.askdirectory(parent=window, title='주석 제목을 정리할 결과 폴더')
            if not chosen:
                return
            folder = top(chosen)
        if not is_result(folder):
            messagebox.showerror('GNS3 사진 주석', 'TopoLog 결과 폴더(TopoLog_수집결과_...)를 선택하세요.', parent=window)
            return
        from .title_editor import open_editor
        try:
            editor = open_editor(window, folder, on_done=lambda report: append('주석 제목 저장·보고서 갱신: ' + str(folder)))
        except (OSError, ValueError) as exc:
            messagebox.showerror('GNS3 사진 주석', str(exc), parent=window)
            report_error('사진 제목 정리 창 열기 실패: {}: {}'.format(type(exc).__name__, exc))
            return
        state['title_editor'] = editor
        if after:
            # Runs once when the editor window itself closes, whether titles were saved or not.
            editor.bind('<Destroy>', lambda event: event.widget is editor and after(), add='+')
        return editor

    def share():
        if not messagebox.askyesno('공개 검토용 사본', '암호·키의 알려진 형식을 가린 별도 사본입니다. 이미지·사용자 정의 값은 자동으로 가릴 수 없으므로 '
                                   '공개 전에 반드시 직접 확인하세요.', parent=window):
            return
        folder = filedialog.askdirectory(parent=window, title='공개 검토용 사본의 원본 결과 폴더')
        if not folder:
            return
        from .sharing import create
        target = new_output(Path(output.get()), '공개검토')
        start_worker(lambda: (target, create(folder, target)), '원본을 유지하고 공개 검토용 사본을 만듭니다.', pausable=False)

    def saved_criteria(path):
        rules.set(path)
        append('기대값 저장 · 다음 수집과 다시 정리부터 적용: ' + path)
        refresh_summary()

    def edit_criteria():
        selected = scan()
        if selected:
            try:
                return open_editor(window, selected[0], rules.get() or None, on_saved=saved_criteria, initial_directory=str(selected[1]),
                                   result_folder=state['result'])
            except Exception as exc:
                messagebox.showerror('기대값 편집', str(exc), parent=window)


    def start_worker(operation, label, pausable=True):
        state.update(running=True, has_run=True, pausable=pausable)
        tracker.reset(label)
        percent.set(0)
        percent_text.set('0%')
        outcome_text.set('')
        cancellation.clear()
        gate.resume()
        for widget in controls:
            widget.configure(state='disabled')
        for key, button in tool_buttons.items():
            if key not in ('logs', 'feedback') and button.winfo_exists():
                button.state(['disabled'])
        back.configure(state='disabled')
        stop.configure(state='normal')
        pause_button.configure(state='normal' if pausable else 'disabled', text='일시중지')
        result_button.configure(state='disabled')
        titles_button.configure(state='disabled')
        status.set(label)
        log_directory().mkdir(parents=True, exist_ok=True)
        state['log_file'] = log_directory() / ('run-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.log')
        append('시작: ' + label)

        def worker():
            try:
                folder, result = operation()
                events.put(('done', (folder, result, result.get('cancelled', False))))
            except InterruptedError:
                events.put(('cancelled', None))
            except Exception as exc:
                events.put(('error', '{}: {}'.format(type(exc).__name__, exc)))
        threading.Thread(target=worker, daemon=True).start()

    def run():
        try:
            runtime.ensure_current()
        except ValueError as exc:
            messagebox.showerror('프로그램 업데이트', str(exc), parent=window)
            return
        selected = scan()
        if not selected:
            return
        try:
            configured = load_rules(rules.get() or None)
            root, destination = selected
            settings.save(root, destination, capture_screenshot=capture.get(), allow_enable=allow_enable.get(),
                          detail_scope=detail_scope.get())
        except Exception as exc:
            messagebox.showerror('기대값 확인', str(exc), parent=window)
            return
        from .selection import select
        try:
            select(state['entries'], state['selected'])
        except ValueError as exc:
            messagebox.showerror('프로젝트 선택', str(exc), parent=window)
            return
        ids = None if state['selected'] is None else set(state['selected'])
        retry_source = state['retry_source']
        folder = new_output(destination)
        enable, shoot, scope = allow_enable.get(), capture.get(), detail_scope.get()
        state['run_root'] = root
        password = (default_password.get() or None) if enable else None

        def collect():
            _, found = discover(root)
            found = select(found, ids)
            # GNS3 and its linked VM start automatically; this is not optional.
            preflight.prepare(API, True, progress=notify, cancel=cancellation, needs_vm=preflight.projects_need_vm(root, found))
            api = connect(progress=notify, cancel=cancellation)
            if cancellation.is_set():
                raise InterruptedError('Collection cancelled before project startup')
            _, entries = discover(root)
            entries = select(entries, ids)
            prepare_server(api, root, entries, progress=notify)
            if cancellation.is_set():
                raise InterruptedError('Collection cancelled before project startup')
            result = Runner(api, folder, configured, cancel=cancellation, progress=notify, enable=enable, enable_password=password,
                            password_provider=request_password, capture=shoot, project_ids=ids, retry_source=retry_source,
                            progress_state=lambda event: events.put(('progress', event)), detail_scope=scope, pause=gate,
                            annotate=True).run(root)
            return folder, result
        start_worker(collect, '수집 중입니다. 중지하면 장비 상태를 복구하고 그때까지의 결과를 보존합니다.')
        fill_tree()

    def reassess_saved():
        try:
            load_rules(rules.get() or None)
            destination = Path(output.get()).expanduser().resolve()
            source = filedialog.askdirectory(parent=window, title='기존 결과 폴더(TopoLog_수집결과_...) 선택')
            if not source:
                return
            from .resultdir import is_result, top
            source, criteria_path = top(source), rules.get() or None
            if not is_result(source):
                raise ValueError('TopoLog 결과 폴더(TopoLog_수집결과_...)를 선택하세요.')
            folder = new_output(destination, '재검토')
            start_worker(lambda: (folder, reassess(source, folder, criteria_path)),
                         '기존 수집 자료를 다시 정리합니다. 장비 명령은 실행하지 않습니다.', pausable=False)
        except Exception as exc:
            messagebox.showerror('기존 결과 다시 정리', str(exc), parent=window)

    def request_stop():
        cancellation.set()
        stop.configure(state='disabled')
        pause_button.configure(state='disabled')
        status.set('중지 요청을 받았습니다. 장비 상태 복구와 결과 저장을 기다려 주세요.')

    def toggle_pause():
        if gate.requested:
            gate.resume()
            pause_button.configure(text='일시중지')
            status.set('수집을 계속합니다.')
            append('계속 요청')
        else:
            gate.request()
            pause_button.configure(text='계속')
            status.set('현재 작업이 끝나는 지점에서 일시중지합니다. 진행 중인 명령·저장 창은 끝까지 마칩니다.')
            append('일시중지 요청')

    start_button = ttk.Button(progress_controls, text='수집 시작', style='Accent.TButton', command=run)
    start_button.pack(side='left')
    pause_button = ttk.Button(progress_controls, text='일시중지', command=toggle_pause, state='disabled')
    pause_button.pack(side='left', padx=(8, 0))
    stop = ttk.Button(progress_controls, text='중지', style='Danger.TButton', command=request_stop, state='disabled')
    stop.pack(side='left', padx=(8, 0))
    change = ttk.Button(run_bar, text='설정 변경', style='Compact.TButton', command=lambda: go(0))
    change.pack(side='right')
    nav_extra = []

    def open_result():
        if state['result']:
            path = (state['result'] / 'index.html').resolve()
            try:
                append('보고서 열기: ' + str(path))
                browser = open_report(path)
                append('브라우저 실행 요청: ' + str(browser))
            except (OSError, webbrowser.Error) as exc:
                append('보고서는 저장됐지만 브라우저를 열지 못했습니다: {} · {}'.format(exc, path))
                status.set(status.get().split(' · 브라우저')[0]+' · 브라우저를 열지 못했습니다. 결과 폴더의 index.html을 직접 여세요.')

    def open_folder():
        if state['result'] and os.name == 'nt':
            os.startfile(str(state['result']))
    folder_button = ttk.Button(side_bar, text='결과 폴더', style='Soft.TButton', command=open_folder)
    folder_button.pack(side='left', padx=(8, 0))
    result_button = ttk.Button(side_bar, text='보고서 열기', style='Soft.TButton', command=open_result, state='normal' if state['result'] else 'disabled')
    result_button.pack(side='left', padx=(8, 0))
    # Picture titles belong with the result, not with the maintenance tools.
    titles_button = ttk.Button(side_bar, text='사진 제목 정리', style='Soft.TButton', command=edit_titles, state='normal' if state['result'] else 'disabled')
    titles_button.pack(side='left', padx=(8, 0))
    actions['titles'] = titles_button
    # Enabled only when the last run left GNS3 pictures unsaved; not part of `controls` so a run's end does not enable it.
    # Sits above the project list, next to its 'GNS3 사진' column.
    retake_button = ttk.Button(side_bar, text='사진 다시 저장', command=lambda: retake(), state='disabled')
    retake_button.pack(side='left', padx=(8, 0))
    actions['retake'] = retake_button

    def retake():
        """Open the projects whose GNS3 picture was not saved and save the pictures again; rebuilds that result."""
        folder = state['result']
        root = state.get('run_root') or projects.get()
        if not folder or state['running']:
            return
        from .engine import retake_pictures

        def operation():
            preflight.prepare(API, True, progress=notify, cancel=cancellation, needs_vm=False)
            api = connect(progress=notify, cancel=cancellation)
            return folder, retake_pictures(api, folder, root, progress=notify, cancel=cancellation,
                                           progress_state=lambda event: events.put(('progress', event)))
        state['retaking'] = True
        retake_button.configure(state='disabled')
        start_worker(operation, 'GNS3 사진을 다시 저장합니다. 저장 창이 닫힐 때까지 GNS3 창을 조작하지 마세요.', pausable=False)

    # Tools open in their own window from the 도구 button, grouped by what they work on.
    TOOLS = [('수집 준비', [('edit', '기대값 편집', edit_criteria,
                             '장비별 자동 확인 항목을 끄거나 값을 바꾸고 새 항목을 추가합니다. 저장하면 다음 수집부터 적용됩니다.'),
                            ('scan', '프로젝트 다시 확인', scan,
                             '프로젝트 폴더를 다시 읽어 수집할 프로젝트 목록을 새로 만듭니다.')]),
             ('기존 결과', [('reassess', '기존 결과 다시 정리', reassess_saved,
                             '이미 수집한 결과 폴더를 지금 기대값으로 다시 판정합니다. 장비 명령은 실행하지 않습니다.'),
                            ('regenerate', '보고서 다시 만들기', regenerate,
                             '수집한 결과 폴더로 보고서만 다시 만듭니다. 판정은 바꾸지 않습니다.'),
                            ('share', '공개 검토용 사본 만들기', share,
                             '암호와 키를 가린 사본을 따로 만듭니다. 원본 결과 폴더는 그대로 둡니다.')]),
             ('기록·문의', [('logs', '기록 폴더 열기', open_log_folder,
                             '프로그램 실행 기록과 오류 보고 사본이 있는 폴더를 엽니다.'),
                            ('feedback', '피드백 보내기', lambda: report_error(feedback=True),
                             '불편한 점이나 의견을 개발자에게 보냅니다.')])]
    tool_window = {'window': None}
    tool_buttons = {}

    def run_tool(command):
        if tool_window['window'] is not None:
            tool_window['window'].destroy()
        command()

    def open_tools():
        if tool_window['window'] is not None and tool_window['window'].winfo_exists():
            tool_window['window'].lift()
            return
        top = tk.Toplevel(window)
        top.title('TopoLog · 도구')
        top.transient(window)
        top.resizable(False, False)
        top.configure(background=COLORS['panel'])
        frame = ttk.Frame(top, style='Panel.TFrame', padding=(20, 16))
        frame.pack(fill='both', expand=True)
        row = 0
        made = []
        for heading, items in TOOLS:
            ttk.Label(frame, text=heading, style='Value.TLabel').grid(row=row, column=0, columnspan=2, sticky='w', pady=(0 if row == 0 else 12, 4))
            row += 1
            for key, label, command, note in items:
                button = ttk.Button(frame, text=label, command=lambda c=command: run_tool(c),
                                    style='Soft.TButton' if key in ('edit', 'regenerate') else 'TButton')
                button.grid(row=row, column=0, sticky='ew', pady=2)
                ttk.Label(frame, text=note, style='Lead.TLabel', wraplength=430, justify='left').grid(row=row, column=1, sticky='w', padx=(12, 0))
                if state['running'] and key not in ('logs', 'feedback'):
                    button.state(['disabled'])
                tool_buttons[key] = button
                made.append(button)
                row += 1
        fit(made)
        ttk.Button(frame, text='닫기', command=top.destroy).grid(row=row, column=1, sticky='e', pady=(14, 0))
        top.bind('<Destroy>', lambda event: tool_window.update(window=None) if event.widget is top else None)
        tool_window['window'] = top
        top._topolog_tools = tool_buttons
        return top

    class ToolAction:
        """actions[key] for a tool: invoking it runs the tool like its button in the tools window."""
        def __init__(self, key, command):
            self.key, self.command = key, command

        def invoke(self):
            if self.key in ('logs', 'feedback') or not state['running']:
                return self.command()

        def cget(self, option):
            if option == 'state':
                return 'disabled' if state['running'] and self.key not in ('logs', 'feedback') else 'normal'
            raise KeyError(option)

        def configure(self, **options):
            pass

    for heading, items in TOOLS:
        for key, label, command, note in items:
            actions[key] = ToolAction(key, command)
    tools_button = ttk.Button(nav, text='도구', style='Soft.TButton', command=open_tools)
    tools_button.pack(side='right', padx=(0, 10))
    actions['tools'] = tools_button

    def step_clicked(step):
        if not state['running'] and step != state['step']:
            go(step)
    # Picking from an earlier result changes the ticks of the project list, so it sits above that list.
    earlier = ttk.Button(pick_bar, style='Compact.TButton', text='이전 결과에서 고르기', command=lambda: choose_projects(True))
    earlier.pack(side='right', padx=(0, 8))
    actions['retry'] = earlier
    controls.append(earlier)
    fit([start_button, pause_button, stop])
    fit([folder_button, result_button, titles_button, retake_button])
    fit([back, forward, log_button, tools_button])
    actions.update(run=start_button, pause=pause_button, next=forward, back=back)
    controls.extend([start_button, change])

    def close():
        if state['running']:
            state['closing'] = True
            request_stop()
        else:
            window.destroy()

    def bring_to_front():
        # A background window only flashes in the taskbar; raise it for the prompt.
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

    def project_row(event):
        pid = event.get('project_id') or event.get('project_done')
        return pid if pid and tree.exists(pid) else None

    activity = {}

    def track_activity(text):
        """Keep the latest message per device ('[R1] 수집: show ip route'); drop it once the device is done."""
        done = re.match(r'^(.+?): (완료|일부 완료|실패|대상 아님|권한 확인 필요|지원되지 않는 장비|중지)', text)
        if done:
            activity.get('devices', {}).pop(done[1], None)
            return
        current = re.match(r'^\[(.+?)\] (.+)$', text)
        if current:
            label = current[2]
            for prefix in ('장비: ', '수집: '):
                if label.startswith(prefix):
                    label = label[len(prefix):]
            if label == current[1]:
                label = '연결 중'
            activity.setdefault('devices', {})[current[1]] = label

    def describe_activity():
        devices = activity.get('devices') or {}
        if not devices:
            return ''
        shown = ['· {}  {}'.format(name, label.rstrip('.')) for name, label in list(devices.items())[:3]]
        more = len(devices) - len(shown)
        return '\n'.join(shown) + ('\n· 외 {}대'.format(more) if more > 0 else '')

    def handle_progress(value):
        tracker.update(value)
        if 'project_index' in value and value['project_index'] != activity.get('project'):
            activity.clear()                     # devices of the previous project are no longer being worked on
            activity['project'] = value['project_index']
        row = project_row(value)
        if row and 'project_done' in value:
            label = STATUS_LABELS.get(value.get('project_status'), value.get('project_status') or '')
            photo = PHOTO_LABELS.get(value.get('photo'), '—') if capture.get() else '사용 안 함'
            tree.item(row, values=(label, photo))
        elif row and value.get('task'):
            tree.item(row, values=('진행 중 · ' + ('GNS3 사진 저장 중' if '조작' in value['task'] else value['task'].split(' · ')[0]), tree.item(row, 'values')[1] if tree.item(row, 'values') else '—'))
            tree.see(row)
        if 'gate_paused' in value:
            if value['gate_paused']:
                status.set('일시중지됨 · 계속을 누르면 이어서 수집합니다. 중지도 바로 받을 수 있습니다.')
                append('일시중지됨 · ' + str(value.get('pause_where', '')))
            else:
                append('계속 진행')
        task = value.get('task') or ''
        notice_text.set(task if '사진' in task and '조작' in task else '')

    def marked_count(result):
        return sum(1 for p in result.get('projects', []) if ((p.get('topology_images') or {}).get('annotated') or {}).get('status') == 'generated')

    def finish(kind, value):
        tracker.finish(kind == 'done' and not value[2])
        state['running'] = False
        gate.resume()
        for widget in controls:
            widget.configure(state='normal')
        for button in tool_buttons.values():
            if button.winfo_exists():
                button.state(['!disabled'])
        sync_password()
        stop.configure(state='disabled')
        pause_button.configure(state='disabled', text='일시중지')
        back.configure(state='normal')
        notice_text.set('')
        if kind == 'done':
            state['result'], result, cancelled = value
            projects_done = [p for p in result.get('projects', []) if p.get('status') not in ('pending', 'not_run', 'excluded')]
            errors = sum(1 for p in projects_done if p.get('status') in ('error', 'cleanup_failed'))
            photos = [(p.get('topology_images') or {}).get('native', {}).get('status') for p in projects_done]
            text = '처리한 프로젝트 {}개'.format(len(projects_done))
            if errors:
                text += ' · 오류·정리 오류 {}개(보고서에 원인 기록)'.format(errors)
            if capture.get() and photos:
                text += ' · GNS3 사진 저장 {}개, 저장 못 함 {}개'.format(photos.count('captured'), len(photos) - photos.count('captured'))
                marked = sum(1 for p in projects_done if ((p.get('topology_images') or {}).get('annotated') or {}).get('status') == 'generated')
                if marked:
                    text += ' · 자동 주석 {}개'.format(marked)
            status.set(('중지됨 · 그때까지의 결과를 저장했습니다.' if cancelled else '완료 · 보고서와 ZIP을 저장했습니다.'))
            outcome_text.set(text + ' · 결과: ' + str(state['result']))
            append('결과: {} · {}'.format(state['result'], text))
            for p in result.get('projects', []):
                pid = p.get('project_id')
                if pid and tree.exists(pid):
                    photo = PHOTO_LABELS.get((p.get('topology_images') or {}).get('native', {}).get('status'), '—') if capture.get() else '사용 안 함'
                    tree.item(pid, values=(STATUS_LABELS.get(p.get('status'), p.get('status', '')), photo))
        elif kind == 'cancelled':
            status.set('프로젝트를 시작하기 전에 중지했습니다.')
            append('시작 전에 중지')
        else:
            status.set('실행 오류 · 상세 기록에서 원인을 확인하세요.')
            append('실행 오류: ' + str(value))
            state['last_error'] = str(value)
            if not state['closing']:
                # Only opens the window; nothing is sent until the person agrees and presses send.
                report_error(str(value))
        result_button.configure(state='normal' if state['result'] else 'disabled')
        titles_button.configure(state='normal' if state['result'] else 'disabled')
        for pid in selectable():
            if tree.exists(pid):
                tree.item(pid, image=window._topolog_check_images[1 if picked(pid) else 0])
        from .engine import failed_pictures
        missing = failed_pictures(value[1]) if kind == 'done' else []
        retake_button.configure(state='normal' if missing and state['result'] else 'disabled')
        retaken = state.pop('retaking', False)
        if kind == 'done' and not value[2] and not state['closing'] and not retaken:
            messagebox.showinfo('수집 완료', '수집이 끝났습니다.\n\n' + outcome_text.get().split(' · 결과: ')[0], parent=window)
        if kind == 'done' and not value[2] and not state['closing'] and missing and not retaken:
            names = ', '.join(p.get('name', '') for p in missing[:8]) + (' 외' if len(missing) > 8 else '')
            if messagebox.askyesno('GNS3 사진 다시 저장', 'GNS3 사진 {}개를 저장하지 못했습니다: {}\n\n지금 다시 저장할까요? 해당 프로젝트를 다시 열고 '
                                   '이번 실행에서 켰던 장비를 잠시 켠 뒤 사진만 저장합니다. 수집한 설정과 출력은 바뀌지 않습니다.\n\n'
                                   '아니요를 누르면 나중에 \'사진 다시 저장\' 버튼으로 할 수 있습니다.'.format(len(missing), names), parent=window):
                retake()
                return
        if kind == 'done' and not value[2] and not state['closing']:
            # Titles are chosen after the pictures exist: open the title editor first, the report after it closes.
            if marked_count(value[1]) and edit_titles(after=lambda: state['closing'] or open_result()):
                append('사진 제목 정리 창을 열었습니다. 닫으면 보고서가 열립니다.')
            else:
                open_result()

    def poll():
        try:
            while True:
                kind, value = events.get_nowait()
                if kind == 'progress':
                    handle_progress(value)
                    continue
                if kind == 'password':
                    tracker.update({'paused': True, 'stage': '장비 암호 입력 대기'})
                    try:
                        if not state['closing']:
                            bring_to_front()
                            value['password'] = simpledialog.askstring('장비 권한', value['name'] + '의 enable 암호를 입력하세요. 취소하면 이 장비를 미수집으로 남깁니다.',
                                                                       show='*', parent=window)
                    finally:
                        release_front()
                        tracker.update({'paused': False, 'stage': '수집 재개'})
                        value['ready'].set()
                    continue
                if kind == 'log':
                    track_activity(value)
                    append(value)
                    continue
                finish(kind, value)
                if state['closing']:
                    window.destroy()
                    return
        except queue.Empty:
            pass
        if state.get('has_run'):
            view = tracker.snapshot()
            percent.set(view['percent'])
            percent_text.set(str(view['percent']) + '%')
            progress_text.set(view['text'])
            if view.get('project_name'):
                project_text.set('{}/{} · {}'.format(view.get('project_index'), view.get('project_total'), view['project_name'])
                                 if view.get('project_total') else view['project_name'])
            task = view.get('task') or view.get('stage') or '—'
            if '사진' in task and '조작' in task:
                # The full warning is already in the yellow notice; here only what is running.
                stage = (view.get('stage') or '').split(' · ')[0]
                task = stage if 'GNS3 사진' in stage else (stage + ' · GNS3 사진 저장 중' if stage else 'GNS3 사진 저장 중')
            task_text.set(task)
            steps_text.set(project_steps(view.get('stage') or '') if state['running'] else '')
            activity_text.set(describe_activity() if state['running'] else '')
            time_text.set('경과 {} · 예상 남은 시간 {}'.format(view['elapsed'], view['remaining']))
            if notice_text.get():
                notice.pack(anchor='w', fill='x', pady=(8, 0), before=tree_frame)
            else:
                notice.pack_forget()
        window.after(100, poll)

    from . import motw
    unblocked = motw.describe(motw.unblock())
    if unblocked:
        append(unblocked)
    if defaults.get('migrated_auto_start'):
        append('이전 설정의 GNS3 자동 실행 끄기는 더 이상 쓰지 않습니다. GNS3와 연동 VM은 필요할 때 자동으로 실행됩니다.')
    sync_password()
    go(0)
    window.protocol('WM_DELETE_WINDOW', close)
    window.after(100, poll)
    window._topolog = {'step_labels': step_labels, 'steps_text': steps_text, 'activity_text': activity_text, 'settings_column': settings_column, 'path_labels': path_labels, 'projects': projects, 'output': output, 'rules': rules, 'status': status, 'actions': actions,
                       'state': state, 'stop': stop, 'result': result_button, 'events': events, 'cancel': cancellation,
                       'edit': edit_criteria, 'close': close, 'default_password': default_password, 'allow_enable': allow_enable,
                       'capture': capture, 'retake': retake, 'detail_scope': detail_scope, 'bring_to_front': bring_to_front, 'tracker': tracker,
                       'progress_bar': progress_bar, 'progress_text': progress_text, 'percent': percent, 'tree': tree,
                       'go': go, 'pages': pages, 'gate': gate, 'notice': notice_text, 'outcome': outcome_text,
                       'summary': summary_values, 'show_log': show_log, 'folder_message': folder_message,
                       'pick_text': pick_text, 'report_error': report_error, 'pick_all': pick_all, 'pick_none': pick_none, 'toggle_project': toggle_project}
    if run_loop:
        window.mainloop()
    return window
