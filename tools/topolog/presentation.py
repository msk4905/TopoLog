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


def refresh_report(folder, detail_scope=None, original_documents=True):
    """Rebuild the presentation. original_documents=False (after choosing picture titles) leaves the
    collection-time Word/Markdown documents untouched and rewrites only the annotated Word report,
    the picture folder, the HTML report, the manifest and the ZIP."""
    from .resultdir import top
    folder=top(Path(folder).resolve(strict=True))
    from .security import ensure_tree,validate_report_paths
    ensure_tree(folder)
    from .resultdir import records,prefix
    data=records(folder)
    report=json.loads((data/'summary.json').read_text(encoding='utf-8'))
    if report.get('schema')!='topolog-batch-v2' or not report.get('finished_at'):
        raise ValueError('완료된 TopoLog 일괄 결과 폴더를 선택하세요.')
    validate_report_paths(report,folder)
    if detail_scope is not None:
        from .report_options import save as save_options
        save_options(data,detail_scope)
    from . import portfolio
    from .engine import write_collection_record,write_ai_prompt
    from . import gallery
    gallery.build(report,folder)
    if original_documents:
        write_collection_record(folder,report)
        write_ai_prompt(folder,report)
    try:portfolio.write(report,folder,original=original_documents)
    except Exception as exc:(data/'report-document-error.txt').write_text('{}: {}'.format(type(exc).__name__,exc),encoding='utf-8')
    files=sorted(p for p in folder.rglob('*') if p.is_file())
    if any(p.is_symlink() for p in files):raise ValueError('Linked evidence files are not supported')
    manifest_name=prefix(report)+'manifest.json'
    names=sorted({p.relative_to(folder).as_posix() for p in files}|{'index.html',manifest_name})
    content=render(report,names).encode('utf-8')
    manifest={'files':[]}
    for name in names:
        if name==manifest_name:continue
        body=content if name=='index.html' else (folder/name).read_bytes()
        manifest['files'].append({'path':name,'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()})
    manifest_bytes=json.dumps(manifest,ensure_ascii=False,indent=2).encode('utf-8')
    archive=Path(str(folder)+'.zip')
    targets=[folder/'index.html',folder/manifest_name,archive]
    with stage_directory(folder.parent) as stage:
        for i,target in enumerate(targets):
            if target.exists():shutil.copyfile(target,stage/('old'+str(i)))
        (stage/'new0').write_bytes(content);(stage/'new1').write_bytes(manifest_bytes)
        with zipfile.ZipFile(stage/'new2','w',zipfile.ZIP_DEFLATED) as z:
            for name in names:
                if name=='index.html':z.writestr(name,content)
                elif name==manifest_name:z.writestr(name,manifest_bytes)
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
