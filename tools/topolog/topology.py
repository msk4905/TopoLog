"""Distinct native GNS3 screenshots and explicitly reconstructed SVG diagrams."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
from xml.sax.saxutils import escape


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


def capture_native(name, project_file, destination, cancel=None):
    destination=Path(destination)
    result={'status':'unavailable','kind':'gns3_native_topology_screenshot'}
    if os.name!='nt':return {**result,'reason':'GNS3 GUI 스크린샷은 Windows 환경에서 지원합니다.'}
    try:
        if cancel and cancel.is_set():raise InterruptedError('Screenshot cancelled')
        script=Path(__file__).resolve().parents[1]/'capture-gns3.ps1'
        completed=subprocess.run(['powershell.exe','-NoLogo','-NoProfile','-NonInteractive','-File',str(script),
                                  '-ProjectName',name,'-ProjectFile',str(Path(project_file).resolve()),
                                  '-Method','Native','-OutputDirectory',str(destination.resolve())],
                                  stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=150,
                                  creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        record=destination/'capture.json'
        if not record.is_file():return {**result,'reason':'GNS3 자체 캡처 실패. GUI 또는 PowerShell 실행 권한을 확인하세요.'}
        metadata=json.loads(record.read_text(encoding='utf-8-sig'));image=destination/'gns3-topology.png'
        if completed.returncode!=0:return {**result,'reason':metadata.get('error') or 'GNS3 자체 캡처를 완료하지 못했습니다.'}
        if metadata.get('capture_status')!='captured' or not image.is_file() or image.read_bytes()[:8]!=b'\x89PNG\r\n\x1a\n':
            return {**result,'reason':'유효한 GNS3 자체 PNG를 확인하지 못했습니다.'}
        return {'status':'captured','kind':result['kind'],'file':'capture/gns3-topology.png',
                'sha256':hashlib.sha256(image.read_bytes()).hexdigest(),'review':'unreviewed'}
    except InterruptedError:raise
    except (OSError,ValueError,subprocess.TimeoutExpired):
        return {**result,'reason':'GNS3 자체 캡처를 완료하지 못했습니다. capture 기록을 확인하세요.'}
