"""Review copies with explicit redaction limits; original evidence is untouched."""
import copy
import hashlib
import json
from pathlib import Path
import re
import shutil

CONTEXT_SECRET=re.compile(r'(?im)^\s*(?:crypto isakmp key\b|(?:tacacs|radius)-server\b[^\n]*\bkey\b|snmp-server (?:host|user)\b|key\s+\S|failover (?:ipsec pre-shared-)?key\b|'
                          r'(?:standby|vrrp|glbp)\b[^\n]*\bauthentication\b|ip (?:nhrp|ospf|rip)\b[^\n]*\b(?:authentication|message-digest-key|key)\b|'
                          r'authentication\b[^\n]*\bstring\b|'
                          r'(?:ip )?ospf message-digest-key\b|wpa-psk\b)[^\n]*$')
SECRET=re.compile(r'(?im)^([^\n]*(?:password|passwd|secret|community|pre-shared-key|private-key|authentication-key|key-string)\b)[^\n]*$')
PRIVATE=re.compile(r'-----BEGIN [^\n]*PRIVATE KEY-----.*?-----END [^\n]*PRIVATE KEY-----',re.S)
def redact(value):
    if isinstance(value,dict):return {k:('[REDACTED]' if re.search(r'password|passwd|secret|token|community|pre.?shared.?key|private.?key|authentication.?key|key.?string|^(?:api[_-]?)?key$',k,re.I) else redact(v)) for k,v in value.items()}
    if isinstance(value,list):return [redact(x) for x in value]
    if isinstance(value,str):
        value=PRIVATE.sub('[REDACTED PRIVATE KEY]',value)
        value=CONTEXT_SECRET.sub('[REDACTED CREDENTIAL LINE]',value)
        return SECRET.sub('[REDACTED CREDENTIAL LINE]',value)
    return value

def safe(base,name):
    from .security import safe_path
    return safe_path(base,name)


def create(source,output):
    from .engine import save,export
    from .resultdir import RECORDS,records,top
    source=top(Path(source).resolve());output=Path(output).resolve()
    if output==source or output.is_relative_to(source):raise ValueError('원본 결과 밖에 새 폴더를 지정하세요.')
    from .security import ensure_tree,validate_report_paths,passive_svg
    ensure_tree(source)
    data=(records(source)/'summary.json').read_bytes();report=json.loads(data)
    validate_report_paths(report,source)
    if report.get('schema')!='topolog-batch-v2':raise ValueError('TopoLog 결과 폴더가 아닙니다.')
    from .report_options import detail_scope
    scope=detail_scope(report,records(source))
    report=redact(copy.deepcopy(report));output.mkdir(parents=True,exist_ok=False)
    inner=output/RECORDS;inner.mkdir();report['records_dir']=RECORDS
    source_data=records(source)
    report['report_options']={**(report.get('report_options') or {}),'detail_scope':scope}
    report['runtime']={'version':report.get('runtime',{}).get('version','unknown')}
    report.pop('assessment',None);report.pop('selection',None)
    report['sharing']={'status':'manual_review_required','source_summary_sha256':hashlib.sha256(data).hexdigest(),
                       'warning':'공개 검토용 사본입니다. 이미지·주소·이름·사용자 정의 비밀 값은 직접 확인해야 합니다. 원본 증거/해시와 다른 편집 자료입니다.'}
    for project in report.get('projects',[]):
        from .resultdir import project_dir,node_dir
        base=safe(source_data,project_dir(project));dest=safe(inner,project_dir(project));dest.mkdir(exist_ok=True)
        for filename in ['topology.json','topology-reconstructed.svg']:
            p=safe(base,filename)
            if p.is_file():
                text=p.read_text(encoding='utf-8');(dest/filename).write_text(json.dumps(redact(json.loads(text)),ensure_ascii=False) if filename.endswith('.json') else passive_svg(redact(text)),encoding='utf-8')
        for item in project.get('topology_images',{}).values():
            name=item.get('file')
            if not name:continue
            p=safe(base,name);d=safe(dest,name)
            if p.is_file() and p.suffix.lower() in ('.png','.svg'):
                d.parent.mkdir(parents=True,exist_ok=True)
                if p.suffix.lower()=='.svg':d.write_text(passive_svg(redact(p.read_text(encoding='utf-8'))),encoding='utf-8')
                else:
                    from .security import png_without_metadata
                    d.write_bytes(png_without_metadata(p.read_bytes()))
                item['sha256']=hashlib.sha256(d.read_bytes()).hexdigest()
            else:item.update(status='unavailable',reason='원본 이미지 파일이 없습니다.')
        for node in project.get('nodes',[]):
            nd=safe(dest,node_dir(node));nd.mkdir(exist_ok=True)
            for cmd in node.get('commands',[]):
                if cmd.get('file'):
                    if 'text' not in cmd:
                        original_command=safe(safe(base,node_dir(node)),cmd['file'])
                        if not original_command.is_file():raise ValueError('원문 증거가 없는 명령은 공개 사본으로 만들 수 없습니다.')
                        cmd['text']=redact(original_command.read_text(encoding='utf-8',errors='replace'))
                    safe(nd,cmd['file']).write_text(cmd['text'],encoding='utf-8')
            startup=node.get('startup_configuration',{})
            original=safe(safe(base,node_dir(node)),startup.get('file','startup.cfg'))
            if startup.get('status')=='collected' and original.is_file():safe(nd,startup.get('file','startup.cfg')).write_text(redact(original.read_text(encoding='utf-8',errors='replace')),encoding='utf-8')
            save(nd/'collection.json',node)
        save(dest/'result.json',project)
    save(inner/'summary.json',report)
    (output/'PUBLIC_REVIEW.md').write_text(report['sharing']['warning']+'\n원본 파일은 변경하지 않았습니다.',encoding='utf-8')
    export(output,report)
    return report
