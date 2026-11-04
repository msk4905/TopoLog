"""Refresh presentation in place while keeping raw evidence and checks unchanged."""
import hashlib
import json
import os
from pathlib import Path
import shutil
from contextlib import contextmanager
import uuid
import zipfile
from .reporting import render


@contextmanager
def stage_directory(parent):
    # One mkdir attempt: Windows tempfile retries can loop on sandbox ACL denial.
    stage=parent/('.topolog-view-'+uuid.uuid4().hex)
    stage.mkdir()
    try:yield stage
    finally:shutil.rmtree(stage)


def refresh_report(folder):
    folder=Path(folder).resolve(strict=True)
    report=json.loads((folder/'summary.json').read_text(encoding='utf-8'))
    if report.get('schema')!='topolog-batch-v2' or not report.get('finished_at'):
        raise ValueError('완료된 TopoLog 일괄 결과 폴더를 선택하세요.')
    from . import portfolio
    try:portfolio.write(report,folder)
    except Exception as exc:(folder/'report-document-error.txt').write_text('{}: {}'.format(type(exc).__name__,exc),encoding='utf-8')
    files=sorted(p for p in folder.rglob('*') if p.is_file())
    if any(p.is_symlink() for p in files):raise ValueError('Linked evidence files are not supported')
    names=sorted({p.relative_to(folder).as_posix() for p in files}|{'index.html','manifest.json'})
    content=render(report,names).encode('utf-8')
    manifest={'files':[]}
    for name in names:
        if name=='manifest.json':continue
        data=content if name=='index.html' else (folder/name).read_bytes()
        manifest['files'].append({'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    manifest_bytes=json.dumps(manifest,ensure_ascii=False,indent=2).encode('utf-8')
    archive=Path(str(folder)+'.zip')
    targets=[folder/'index.html',folder/'manifest.json',archive]
    with stage_directory(folder.parent) as stage:
        for i,target in enumerate(targets):
            if target.exists():shutil.copyfile(target,stage/('old'+str(i)))
        (stage/'new0').write_bytes(content);(stage/'new1').write_bytes(manifest_bytes)
        with zipfile.ZipFile(stage/'new2','w',zipfile.ZIP_DEFLATED) as z:
            for name in names:
                if name=='index.html':z.writestr(name,content)
                elif name=='manifest.json':z.writestr(name,manifest_bytes)
                else:z.write(folder/name,name)
        with zipfile.ZipFile(stage/'new2') as z:
            if z.testzip():raise OSError('Presentation ZIP integrity check failed')
        changed=[]
        try:
            for i,target in enumerate(targets):
                os.replace(stage/('new'+str(i)),target);changed.append(i)
        except BaseException:
            for i in reversed(changed):
                backup=stage/('old'+str(i))
                if backup.exists():os.replace(backup,targets[i])
                else:targets[i].unlink(missing_ok=True)
            raise
    return folder/'index.html'
