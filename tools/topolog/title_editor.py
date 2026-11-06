"""Window for choosing the title written on each annotated GNS3 picture.

Shows, per project of one result folder, the saved GNS3 picture, the detected
features and the captions taken from the collected configs, so the title can be
chosen without opening the project in GNS3. Two starting points are offered and
both can be edited: the candidate built from the collected facts, or the
project name. An empty field means no title. Saving rewrites only the annotated
copies and the reports; the GNS3 pictures and the collected records stay as they are.
"""
import os
from pathlib import Path
import threading
import tkinter as tk
from tkinter import ttk

from . import annotate

CHOICE_LABELS = (('candidate', '제목 후보'), ('project', '프로젝트 이름'))
# Router names and port labels come from GNS3 itself (its own show-interface-labels option), so only these are offered.
KIND_LABELS = (('subnet', 'IP 대역(서브넷)'), ('cost', 'OSPF cost'))


def _preview(path, width=540, height=380):
    if not path or not Path(path).is_file():
        return None
    try:
        image = tk.PhotoImage(file=str(path))
    except (tk.TclError, OSError):
        return None
    factor = max(1, -(-image.width() // width), -(-image.height() // height))
    return image.subsample(factor, factor) if factor > 1 else image


def open_editor(parent, folder, rebuild=None, on_done=None):
    from .resultdir import top
    folder = top(Path(folder))
    rows = annotate.overview(folder)
    rebuild = rebuild or annotate.rebuild
    window = tk.Toplevel(parent)
    window.title('GNS3 사진 주석 제목 정리')
    window.geometry('1020x660')
    window.minsize(860, 560)
    drafts = []
    for row in rows:
        start = row['text'] if row['text'] is not None else (row['candidate'] if row['choice'] == 'candidate' else row['name'])
        drafts.append({'choice': row['choice'] if row['choice'] in dict(CHOICE_LABELS) else 'candidate',
                       'texts': {'candidate': row['candidate'], 'project': row['name'], row['choice']: start},
                       'show': dict(row.get('show') or {kind: True for kind, _ in KIND_LABELS})})
    editable = [i for i, r in enumerate(rows) if r['photo'] and not r['user_text']]

    body = ttk.Frame(window, padding=14)
    body.pack(fill='both', expand=True)
    ttk.Label(body, text='프로젝트를 고르고 사진에 넣을 제목을 정하세요. 제목 후보나 프로젝트 이름 중 하나로 시작해 고칠 수 있고, '
                         '칸을 비우면 제목 없이 만듭니다.', wraplength=960).pack(anchor='w', pady=(0, 4))
    ttk.Label(body, text='결과 폴더: ' + str(folder.resolve()), foreground='#555555', wraplength=960).pack(anchor='w', pady=(0, 10))
    columns = ttk.Frame(body)
    columns.pack(fill='both', expand=True)
    listing = tk.Listbox(columns, width=30, exportselection=False, activestyle='none')
    listing.pack(side='left', fill='y')
    for row in rows:
        mark = '' if row['photo'] else '  · 사진 없음'
        if row['photo'] and row['user_text']:
            mark = '  · 직접 입력한 글자 있음'
        listing.insert('end', row['name'] + mark)
    detail = ttk.Frame(columns, padding=(14, 0, 0, 0))
    detail.pack(side='left', fill='both', expand=True)
    picture = tk.Label(detail, background='#ffffff', borderwidth=1, relief='solid')
    picture.pack(anchor='w')
    facts = tk.StringVar()
    ttk.Label(detail, textvariable=facts, wraplength=560, justify='left').pack(anchor='w', pady=(8, 8))
    choice = tk.StringVar(value='candidate')
    radios = ttk.Frame(detail)
    radios.pack(anchor='w')
    buttons = []
    for value, label in CHOICE_LABELS:
        button = ttk.Radiobutton(radios, text=label, value=value, variable=choice)
        button.pack(side='left', padx=(0, 14))
        buttons.append(button)
    text = tk.StringVar()
    entry = ttk.Entry(detail, textvariable=text, width=70)
    entry.pack(anchor='w', pady=(6, 0), fill='x')
    kinds_frame = ttk.Frame(detail)
    kinds_frame.pack(anchor='w', pady=(10, 0))
    ttk.Label(kinds_frame, text='사진에 넣을 주석:').pack(side='left', padx=(0, 8))
    kind_vars, kind_buttons = {}, []
    for kind, label in KIND_LABELS:
        kind_vars[kind] = tk.BooleanVar(value=True)
        button = ttk.Checkbutton(kinds_frame, text=label, variable=kind_vars[kind])
        button.pack(side='left', padx=(0, 12))
        kind_buttons.append(button)
    same_button = ttk.Button(kinds_frame, text='이 선택을 모든 프로젝트에 적용')
    same_button.pack(side='left', padx=(8, 0))
    note = tk.StringVar()
    ttk.Label(detail, textvariable=note, foreground='#7a5a00', wraplength=560).pack(anchor='w', pady=(6, 0))
    # Buttons sit on their own row at the bottom and the message wraps above them, so a long message
    # (a full folder path) can never push the buttons out of the window.
    footer = ttk.Frame(body)
    footer.pack(side='bottom', fill='x', pady=(8, 0), before=columns)
    status = tk.StringVar(value='')
    ttk.Label(body, textvariable=status, wraplength=960, justify='left').pack(side='bottom', fill='x', pady=(10, 0), before=footer)
    save_button = ttk.Button(footer, text='저장하고 주석 사진·주석본 보고서 만들기')
    save_button.pack(side='left')

    def pictures_folder():
        from .gallery import FOLDER, KINDS
        return folder / FOLDER / KINDS['annotated'][0]

    def open_pictures():
        target = pictures_folder()
        if not target.is_dir():
            status.set('아직 주석 사진이 없습니다. 저장하면 ' + str(target) + '에 만들어집니다.')
            return
        try:
            os.startfile(str(target))                       # Windows Explorer
        except (AttributeError, OSError) as exc:
            status.set('폴더를 열지 못했습니다: ' + str(target) + ' (' + str(exc) + ')')
    folder_button = ttk.Button(footer, text='주석 사진 폴더 열기', command=open_pictures)
    folder_button.pack(side='left', padx=(8, 0))
    close_button = ttk.Button(footer, text='닫기', command=window.destroy)
    close_button.pack(side='right')
    state = {'index': None, 'images': [], 'busy': False, 'shown': 'candidate'}

    def remember():
        index = state['index']
        if index is not None:
            drafts[index]['texts'][state['shown']] = text.get()
            drafts[index]['choice'] = choice.get()
            drafts[index]['show'] = {kind: var.get() for kind, var in kind_vars.items()}

    def show(index):
        remember()
        state['index'] = index
        row, draft = rows[index], drafts[index]
        image = _preview(row['annotated'] or row['photo'])
        state['images'] = [image] if image else []
        picture.configure(image=image or '', text='' if image else '사진 없음', width=0 if image else 60, height=0 if image else 12)
        lines = ['탐지한 기능: ' + (', '.join(row['features']) or '없음')]
        if row['captions']:
            lines.append('수집한 값: ' + ' / '.join(' · '.join(c) for c in row['captions']))
        lines.append('라우터 이름과 포트 이름은 GNS3가 그린 그대로 둡니다(포트 이름은 GNS3의 Show interface labels로 켜고 끕니다).')
        facts.set('\n'.join(lines))
        usable = index in editable
        for kind, var in kind_vars.items():
            var.set(bool(draft['show'].get(kind, True)))
        for widget in buttons + [entry] + kind_buttons + [same_button]:
            widget.configure(state='normal' if usable else 'disabled')
        if not row['photo']:
            note.set('GNS3 사진이 없어 주석 그림을 만들 수 없습니다.')
        elif row['user_text']:
            note.set('GNS3 프로젝트에 직접 입력한 글자가 있어 자동 주석을 넣지 않습니다: ' + ', '.join(row['user_text'][:3]))
        else:
            note.set('미리보기는 마지막으로 저장한 그림입니다.' if row['annotated'] else '')
        choice.set(draft['choice'])
        state['shown'] = draft['choice']
        text.set(draft['texts'].get(draft['choice'], ''))

    def switch(*_):
        index = state['index']
        if index is None:
            return
        drafts[index]['texts'][state['shown']] = text.get()
        drafts[index]['choice'] = state['shown'] = choice.get()
        text.set(drafts[index]['texts'].get(choice.get(), ''))
    for button in buttons:
        button.configure(command=switch)

    def selected(_event=None):
        picked = listing.curselection()
        if picked and picked[0] != state['index']:
            show(picked[0])
    listing.bind('<<ListboxSelect>>', selected)

    def titles():
        remember()
        return {rows[i]['project_id']: {'choice': drafts[i]['choice'], 'text': drafts[i]['texts'].get(drafts[i]['choice'], ''),
                                        'show': dict(drafts[i]['show'])}
                for i in editable}

    def same_for_all():
        remember()
        picked = {kind: var.get() for kind, var in kind_vars.items()}
        for i in editable:
            drafts[i]['show'] = dict(picked)
        status.set('주석 선택을 모든 프로젝트에 적용했습니다. 저장해야 그림에 반영됩니다.')
    same_button.configure(command=same_for_all)

    def save():
        if state['busy']:
            return
        state['busy'] = True
        save_button.configure(state='disabled')
        close_button.configure(state='disabled')
        status.set('주석 그림과 보고서를 다시 만드는 중입니다.')
        chosen = titles()
        outcome = {}

        def work():
            try:
                outcome['report'] = rebuild(folder, chosen, progress=lambda message: None)
            except Exception as exc:
                outcome['error'] = '{}: {}'.format(type(exc).__name__, exc)
        worker = threading.Thread(target=work, daemon=True)
        worker.start()

        def wait():
            if worker.is_alive():
                window.after(100, wait)
                return
            state['busy'] = False
            save_button.configure(state='normal')
            close_button.configure(state='normal')
            if 'error' in outcome:
                status.set('다시 만들지 못했습니다: ' + outcome['error'])
                return
            status.set('저장했습니다. 주석 사진: ' + str(pictures_folder()) + ' · 주석본 Word 보고서와 HTML 보고서를 새로 만들었습니다. '
                       '수집 때 만든 Word 보고서는 그대로입니다.')
            fresh = {r['project_id']: r for r in annotate.overview(folder)}
            for i, row in enumerate(rows):
                row.update(annotated=fresh.get(row['project_id'], {}).get('annotated'))
            if state['index'] is not None:
                index, state['index'] = state['index'], None
                show(index)
            if on_done:
                on_done(outcome.get('report'))
        window.after(100, wait)
    save_button.configure(command=save)
    if rows:
        first = editable[0] if editable else 0
        listing.selection_set(first)
        show(first)
    else:
        status.set('이 결과 폴더에는 프로젝트가 없습니다.')
        save_button.configure(state='disabled')
    window._topolog = {'rows': rows, 'drafts': drafts, 'listing': listing, 'choice': choice, 'text': text,
                       'save': save_button, 'status': status, 'show': show, 'titles': titles, 'note': note,
                       'editable': editable, 'switch': switch, 'state': state,
                       'kinds': kind_vars, 'same_for_all': same_for_all, 'open_pictures': open_pictures, 'buttons': (save_button, folder_button, close_button), 'footer': footer}
    return window
