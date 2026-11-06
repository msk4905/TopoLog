"""Stable collection dates and readable output names, including legacy results."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import re

KST=timezone(timedelta(hours=9))

def report_files(report):
    value=report.get('started_at','')
    try:
        date=datetime.fromisoformat(value.replace('Z','+00:00'))
        if date.tzinfo:date=date.astimezone(KST)
        suffix='('+date.strftime('%Y-%m-%d')+')'
    except (ValueError,TypeError,AttributeError):suffix=''
    return {'record':'report'+suffix+'.md','word':'report'+suffix+'.docx',
            'document':'report-document'+suffix+'.md','annotated_word':'report'+suffix+'_주석본.docx'}

def is_generated_report(name):
    return bool(re.fullmatch(r'(?:report|report-document)(?:\(\d{4}-\d{2}-\d{2}\))?(?:_주석본)?\.(?:md|docx)',name))

def new_output(parent,kind='수집결과'):
    parent=Path(parent)
    name='TopoLog_'+kind+'_'+datetime.now(KST).strftime('%Y-%m-%d_%H-%M-%S')
    candidate=parent/name
    number=1
    while candidate.exists() or Path(str(candidate)+'.zip').exists():
        number+=1;candidate=parent/(name+'_'+str(number).zfill(2))
    return candidate

def result_folders(parent):
    return [p for p in Path(parent).iterdir() if p.is_dir() and p.name.startswith(('batch_','TopoLog_'))]
