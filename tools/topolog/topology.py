"""Distinct native GNS3 screenshots and explicitly reconstructed SVG diagrams."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
from xml.sax.saxutils import escape
from .motw import PROCESS_POLICY


def reconstruct(topology, destination):
    nodes=topology.get('nodes',[])
    points={n['node_id']:(float(n.get('x',i*170)),float(n.get('y',0))) for i,n in enumerate(nodes)}
    xs=[p[0] for p in points.values()] or [0];ys=[p[1] for p in points.values()] or [0]
    left,top=min(xs)-100,min(ys)-100;width,height=max(400,max(xs)-left+100),max(260,max(ys)-top+100)
    svg=['<svg xmlns="http://www.w3.org/2000/svg" viewBox="{} {} {} {}">'.format(left,top,width,height),
         '<rect x="{}" y="{}" width="{}" height="{}" fill="#f4f7fa"/>'.format(left,top,width,height)]
    for link in topology.get('links',[]):
        ends=link.get('nodes',[])
        if len(ends)==2 and all(e.get('node_id') in points for e in ends):
            a,b=[points[e['node_id']] for e in ends]
            svg.append('<path d="M {} {} L {} {}" stroke="#718b9d" stroke-width="3"/>'.format(*a,*b))
    for node in nodes:
        x,y=points[node['node_id']]
        color='#668897' if node.get('node_type')=='ethernet_switch' else '#176b78'
        svg.append('<g><rect x="{}" y="{}" width="96" height="52" rx="12" fill="{}"/><text x="{}" y="{}" text-anchor="middle" font-family="sans-serif" font-size="14" fill="#fff">{}</text></g>'.format(x-48,y-26,color,x,y+5,escape(node.get('name','node'))))
    svg.append('</svg>');Path(destination).write_text(''.join(svg),encoding='utf-8')


HINTS=[('accessibility was not ready','GNS3 프로젝트를 연 직후 화면 정보(접근성)가 준비되지 않아 사진 메뉴를 찾지 못했습니다. 잠시 재시도한 뒤에도 같아 중단했습니다.'),
       ('did not finish opening','GNS3 화면에서 프로젝트 열기가 제한 시간 안에 끝나지 않았습니다. GNS3 창에 다른 메시지가 떠 있지 않은지 확인하세요.'),
       ('project file dialog did not open','GNS3의 Open project 동작을 실행했지만 파일 창이 열리지 않았습니다. 도구 모음 버튼과 File 메뉴로 다시 시도한 뒤에도 같아 중단했습니다. 창 크기를 줄여 버튼이 숨었거나 GNS3가 응답하지 않았을 수 있으며, open-project.json의 attempts에 시도별 창 크기와 결과가 있습니다.'),
       ('closed or changed','사진 저장 중 GNS3 창이 닫히거나 다른 프로젝트로 바뀌었습니다. 사진 저장 중에는 GNS3 창을 조작하지 말아 주세요.'),
       ('now shows another project','사진 저장 중 GNS3 창이 다른 프로젝트로 바뀌었습니다. 사진 저장 중에는 GNS3 창을 조작하지 말아 주세요.'),
       ('filename changed before saving','저장 창의 파일 이름이 저장 직전에 바뀌어 저장을 취소했습니다. 사진 저장 중에는 저장 창을 조작하지 말아 주세요.'),
       ('did not retain the requested path','저장 창에 파일 경로를 입력하지 못했습니다.'),
       ('Save dialog did not appear','GNS3 사진 저장 창이 나타나지 않았습니다.'),
       ('did not finish saving','GNS3가 사진 파일 저장을 끝내지 못했습니다.')]


def explain(detail,metadata=None):
    """Plain reason for the report; the original message stays in 'detail' and capture.json."""
    for key,text in HINTS:
        if key in (detail or ''):
            return text
    return detail or 'GNS3 자체 캡처를 완료하지 못했습니다.'


def capture_native(name, project_file, destination, cancel=None):
    destination=Path(destination)
    result={'status':'unavailable','kind':'gns3_native_topology_screenshot'}
    if os.name!='nt':return {**result,'reason':'GNS3 GUI 스크린샷은 Windows 환경에서 지원합니다.','retryable':False}
    try:
        if cancel and cancel.is_set():raise InterruptedError('Screenshot cancelled')
        from .security import safe_argument
        try:name=safe_argument(name)
        except ValueError:
            # A leading '-' or quote could be bound by PowerShell as another parameter.
            return {**result,'reason':'프로젝트 이름에 캡처 스크립트로 전달할 수 없는 문자(앞의 -, 따옴표, 제어 문자)가 있어 GNS3 자체 캡처를 건너뛰었습니다.','retryable':False}
        from .resources import tool_directory
        script=tool_directory()/'capture-gns3.ps1'
        from . import winfocus
        restore_target = winfocus.capture_target()
        restore_args = (['-RestoreWindowHandle', str(restore_target['handle']),
                         '-RestoreWindowProcessId', str(restore_target['pid'])] if restore_target else [])
        completed=subprocess.run(['powershell.exe','-NoLogo','-NoProfile','-NonInteractive',*PROCESS_POLICY,'-File',str(script),
                                  '-ProjectName',name,'-ProjectFile',str(Path(project_file).resolve()),
                                  '-Method','Native','-OutputDirectory',str(destination.resolve())] + restore_args,
                                  stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=240,
                                  creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        record=destination/'capture.json'
        if not record.is_file():return {**result,'reason':'GNS3 자체 캡처 실패. GUI 또는 PowerShell 실행 권한을 확인하세요.'}
        metadata=json.loads(record.read_text(encoding='utf-8-sig'));image=destination/'gns3-topology.png'
        if completed.returncode!=0:
            detail=metadata.get('error') or ''
            return {**result,'reason':explain(detail,metadata),'detail':detail,
                    **{k:metadata[k] for k in ('foreground_at_failure','focus_restore','open_retry') if metadata.get(k)}}
        if metadata.get('capture_status')!='captured' or not image.is_file() or image.read_bytes()[:8]!=b'\x89PNG\r\n\x1a\n':
            return {**result,'reason':'유효한 GNS3 자체 PNG를 확인하지 못했습니다.'}
        return {'status':'captured','kind':result['kind'],'file':'capture/gns3-topology.png',
                'sha256':hashlib.sha256(image.read_bytes()).hexdigest(),'review':'unreviewed'}
    except InterruptedError:raise
    except (OSError,ValueError,subprocess.TimeoutExpired):
        return {**result,'reason':'GNS3 자체 캡처를 완료하지 못했습니다. capture 기록을 확인하세요.'}
