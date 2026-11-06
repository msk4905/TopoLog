"""Explicit project selection and fresh retries; never merge collection times."""
import json
from pathlib import Path

def retry_ids(folder):
    from .resultdir import summary_file
    data=json.loads(summary_file(folder).read_text(encoding='utf-8'))
    if data.get('schema')!='topolog-batch-v2':raise ValueError('TopoLog 결과 폴더가 아닙니다.')
    return {p['project_id'] for p in data.get('projects',[]) if p.get('project_id') and
            (p.get('status')!='completed' or p.get('verdict')!='passed' or (p.get('topology_images',{}).get('native',{}).get('status')=='unavailable' and '사용하지 않은' not in p.get('topology_images',{}).get('native',{}).get('reason','')))}

def select(entries, ids):
    if ids is None:return entries
    ids=set(ids)
    if not ids:raise ValueError('선택한 프로젝트가 없습니다.')
    available={p.get('project_id') for p in entries}
    if ids-available:raise ValueError('현재 입력 폴더에 없는 프로젝트가 선택되었습니다: '+', '.join(sorted(ids-available)))
    return [p for p in entries if p.get('project_id') in ids]

def choose(parent,entries,initial=None):
    import tkinter as tk
    from tkinter import ttk
    result=[]
    dialog=tk.Toplevel(parent);dialog.title('수집할 프로젝트 선택');dialog.geometry('620x390');dialog.transient(parent)
    ttk.Label(dialog,text='선택한 프로젝트의 모든 장비를 새로 수집합니다. 기존 결과는 유지합니다.').pack(padx=12,pady=10)
    listing=tk.Listbox(dialog,selectmode='extended',exportselection=False)
    listing.pack(fill='both',expand=True,padx=12)
    valid=[p for p in entries if p.get('status')=='pending']
    for i,p in enumerate(valid):
        listing.insert('end',p.get('name',p['file']))
        if initial is None or p['project_id'] in initial:listing.selection_set(i)
    def done():
        result.extend(valid[i]['project_id'] for i in listing.curselection());dialog.destroy()
    bar=ttk.Frame(dialog);bar.pack(pady=10)
    ttk.Button(bar,text='전체 선택',command=lambda:listing.selection_set(0,'end')).pack(side='left',padx=5)
    ttk.Button(bar,text='선택 적용',command=done).pack(side='left',padx=5)
    ttk.Button(bar,text='취소',command=dialog.destroy).pack(side='left')
    dialog.grab_set();parent.wait_window(dialog)
    return set(result) if result else None
