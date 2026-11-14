"""Self-contained, readable HTML reports from structured collection results."""
from collections import Counter
import html
import json
from pathlib import PurePosixPath
from urllib.parse import quote
from .features import LABELS

STATUS = {'policy_limited':'방화벽 설정 참고','passed':'출력 정리','failed':'수집 실패','inconclusive':'수집 내용 확인',
          'unverified':'원문 참조','collected':'수집 완료','completed':'처리 완료',
          'unsupported':'미지원','requires_privilege':'권한 필요','cancelled':'중지',
          'not_run':'미실행','not_applicable':'기본 구성','partial':'일부 수집','cleanup_failed':'복구 오류','error':'실행 오류','pending':'대기','excluded':'확인 제외'}


def esc(value):
    from .findings import wording
    return html.escape(wording(str(value)), quote=True)


def badge(status):
    kind = 'info' if status=='policy_limited' else 'ok' if status in ('passed','collected','completed') else ('bad' if status in ('failed','error','cleanup_failed') else 'wait')
    return '<span class="badge '+kind+'">'+esc(STATUS.get(status,status or '확인 필요'))+'</span>'


def link(path, label, download=False):
    from .security import relative_name
    try:path = relative_name(str(path))
    except ValueError:return esc(label)
    parts = PurePosixPath(path)
    if parts.is_absolute() or '..' in parts.parts or any(':' in p for p in parts.parts):
        return esc(label)
    return '<a '+('download ' if download else '')+'href="./'+quote(path, safe='/')+'">'+esc(label)+'</a>'


def running_config(node):
    """Evidence file of the device's collected running configuration, or None."""
    item = next((c for c in reversed(node.get('commands', [])) if c.get('command') in ('show running-config', 'show run')
                 and c.get('status') == 'collected' and c.get('file')), None)
    return str(item['file']) if item else None


def folder_link(path, label):
    """Link that opens a folder of the result in the browser's file view (no download attribute)."""
    path=str(path).strip('/')
    parts=PurePosixPath(path)
    if not path or parts.is_absolute() or '..' in parts.parts or any(':' in p for p in parts.parts):
        return esc(label)
    return '<a class="folder" href="./'+quote(path, safe='/')+'/">'+esc(label)+'</a>'


def expectation(rule):
    """What was checked, as a sentence, with the command whose output it was read from."""
    text = _expectation(rule or {})
    command = (rule or {}).get('command')
    return text + (' (' + command + ')' if command and command not in text else '')


def _expectation(rule):
    kind = rule.get('kind')
    get = lambda key, default='': str(rule.get(key) if rule.get(key) is not None else default)
    if kind=='interface_address': return get('interface')+' 인터페이스 주소 '+get('address')+' 확인'
    if kind=='interface_up': return get('interface')+' 인터페이스 상태 up/up 확인'
    if kind=='ipv6_address': return get('interface')+' 인터페이스 IPv6 주소 '+get('address')+' 확인'
    if kind=='ospf_neighbors': return 'OSPF 이웃 FULL: '+', '.join(rule.get('neighbors',[]))
    if kind=='route': return '경로 '+get('prefix')+' · '+get('protocol')+(' · next hop '+rule['next_hop'] if rule.get('next_hop') else '')
    if kind=='static_route': return '정적 경로 '+get('prefix')+(' → '+get('via') if rule.get('via') else '')+' 확인'
    if kind in ('route_covered','route6_covered'):
        where=' ('+get('origin')+')' if rule.get('origin') else ''
        return ('IPv6 ' if kind=='route6_covered' else '')+'목적지 '+get('address')+where+'로 가는 경로 확인'
    if kind=='ping':
        source=' · 출발 주소 '+get('source') if rule.get('source') else ''
        where=get('destination')+' ' if rule.get('destination') else ''
        return where+get('target')+'로 ping 응답 확인'+source
    if kind=='eigrp_neighbor': return 'EIGRP 이웃 '+get('address')+(' ('+get('peer')+')' if rule.get('peer') else '')+' 확인'
    if kind=='isis_neighbor': return 'IS-IS 이웃 '+get('peer')+' Up 확인'
    if kind=='ipv6_neighbor':
        protocol='OSPFv3' if 'ospf' in get('command') else 'IPv6 EIGRP' if 'eigrp' in get('command') else 'IPv6'
        return protocol+' 이웃 '+get('peer')+(' · '+get('interface') if rule.get('interface') else '')+' 확인'
    if kind=='bgp_peer': return 'BGP 이웃 '+get('address')+' 연결(Established) 확인'
    if kind=='redistribution': return get('source')+' 경로를 '+get('into')+'에 재분배하는지 확인'
    if kind=='ssh_enabled': return 'SSH 활성 상태 확인'
    if kind=='isakmp_sa': return '피어 '+get('peer')+'와 ISAKMP(IKE 1단계) SA 확인'
    if kind=='ipsec_sa': return '피어 '+get('peer')+'와 IPsec(2단계) SA 확인'
    if kind=='nat_interfaces': return 'NAT inside '+', '.join(rule.get('inside',[]))+' · outside '+', '.join(rule.get('outside',[]))+' 지정 확인'
    if kind=='vtp_mode': return 'VTP 모드 '+get('mode')+' 확인'
    if kind=='stp_root': return 'VLAN '+get('vlan')+(' STP Root Bridge 확인' if rule.get('root') else ' STP Root Priority '+get('priority')+' 확인')
    if kind=='vpcs_ip': return 'VPCS 주소 '+get('address')+(' · 게이트웨이 '+get('gateway') if rule.get('gateway') else '')+' 확인'
    if kind=='vlan_active': return 'VLAN '+get('vlan')+' 활성'
    if kind=='dot1q_vlan': return get('interface')+' 하위 인터페이스 802.1Q VLAN '+get('vlan')+' 확인'
    if kind=='ppp_link': return get('interface')+' PPP 캡슐화·LCP Open 확인'
    if kind=='ospf_area_type':
        return 'OSPF area '+get('area')+' '+('NSSA' if rule.get('type')=='nssa' else 'stub')+(' (no-summary)' if rule.get('no_summary') else '')+' 확인'
    if kind=='trunk': return get('interface')+' · trunking'
    if kind=='hsrp': return get('interface')+' · HSRP '+get('group')+' · '+get('virtual_ip')
    if kind=='dhcp_pool': return 'DHCP 풀 '+get('pool')+' · '+get('network')
    if kind=='static_nat': return '정적 NAT '+get('local')+' → '+get('global')
    if kind=='acl_binding': return get('interface')+' · ACL '+get('acl')+' · '+{'in':'수신','out':'송신'}.get(rule.get('direction'),'')
    if kind=='stp_instance': return 'VLAN '+get('vlan')+' · STP 인스턴스 활성'
    if kind=='contains': return get('command')+' 출력에 '+', '.join(rule.get('expected',[]))+' 포함 확인'
    if kind=='asa_packet':
        return 'ASA packet-tracer {} {} {} → {}:{} · 예상 {}'.format(get('interface'), get('protocol'), get('source'),
                                                              get('destination'), get('destination_port'),
                                                              {'allow':'허용','drop':'차단'}.get(rule.get('expected_action'), get('expected_action')))
    # Kinds without a sentence still read as text: their fields, never a JSON literal.
    shown={k:v for k,v in rule.items() if k not in INTERNAL_RULE_KEYS+('kind','command')} if rule else {}
    if not shown:
        return get('command')+' 출력 확인' if rule.get('command') else '수집 내용'
    return ' · '.join('{} {}'.format(k, ', '.join(map(str,v)) if isinstance(v,list) else v) for k,v in shown.items())


INTERNAL_RULE_KEYS=('origin_node','origin_interface','conditional','policy_context','diagnostic_flow')
SCOPE_NOTES=('selective_redistribution','origin_inactive')


def observation(value):
    if value is None: return '관측값 없음'
    if value == []: return '일치하는 항목 없음'
    if isinstance(value,list): return '\n'.join(observation(x) for x in value)
    if isinstance(value,dict):
        if 'success_percent' in value: return ('ping 성공' if value['success_percent'] >= 40 else ('응답 없음' if value['success_percent']==0 else '확인 필요'))
        return '\n'.join(str(k)+': '+observation(v) for k,v in value.items())
    return str(value)


def ping_observation(check):
    value=check.get('observed') or {}
    label='ping 성공' if check.get('status')=='passed' else ('응답 없음' if value.get('success_percent')==0 else '확인 필요')
    reason=check.get('reason') or ''
    if reason.startswith(label):
        return reason                      # the reason already opens with the same words
    return label+(' · '+reason if reason else '')


CSS = '''
.topology-tabs{display:flex;flex-wrap:wrap;gap:6px;margin:16px 0}.tab-radio{position:absolute;opacity:0;width:1px;height:1px}.tab-label{order:1;cursor:pointer;padding:10px 16px;border:1px solid #dce4ed;border-radius:8px;background:#eef3f7}.tab-panel{order:2;display:none;width:100%;padding:16px;border:1px solid #dce4ed;border-radius:8px}.tab-radio:checked+.tab-label{background:#176b78;color:white}.tab-radio:checked+.tab-label+.tab-panel{display:block}.tab-radio:focus-visible+.tab-label{outline:3px solid #81c9df}.topology-image{display:block;width:100%;max-height:560px;object-fit:contain;background:#f4f7fa}
:root{color-scheme:light;--ink:#192b40;--muted:#5b6b7d;--border:#dce4ed;--bg:#f3f6fa;--accent:#216b72}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 system-ui,-apple-system,"Segoe UI","Malgun Gothic",sans-serif}
a{color:#176a78;text-underline-offset:3px;overflow-wrap:anywhere}a:hover{color:#0a414d}
header{background:#15313f;color:#fff;padding:36px max(24px,calc((100vw - 1240px)/2))}
header .brand{font-size:14px;letter-spacing:.12em;font-weight:700;color:#a9dcda}h1{font-size:32px;line-height:1.3;margin:8px 0 12px}header p{margin:0;color:#cfdee5}
main{max-width:1288px;padding:28px 24px 64px;margin:auto}h2{font-size:23px;margin:30px 0 14px}h3{font-size:19px;margin:0}
.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:20px}.metric,.panel,.project{background:white;border:1px solid var(--border);border-radius:12px}
.metric{padding:18px 22px}.metric strong{display:block;font-size:32px;line-height:1.3}.metric span,.muted{color:var(--muted)}
.note{background:#e9f2f3;border-left:4px solid var(--accent);padding:14px 18px;margin:18px 0;border-radius:4px}
.downloads{display:flex;flex-wrap:wrap;gap:12px}.downloads a{background:white;border:1px solid var(--border);border-radius:7px;padding:8px 14px;text-decoration:none}
.info{color:#36566e;background:#eaf1f6}.badge{display:inline-block;font-size:13px;font-weight:650;line-height:1.5;padding:3px 10px;border-radius:20px;white-space:nowrap}.ok{color:#166443;background:#e4f3eb}.wait{color:#85540c;background:#fff1d8}.bad{color:#a52b3d;background:#fde9ed}
.panel{overflow:hidden}.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;text-align:left}th{background:#f0f4f8;font-size:14px;color:#506277}th,td{padding:13px 16px;vertical-align:top;border-bottom:1px solid var(--border)}tbody tr:last-child td{border-bottom:0}
.project{margin:20px 0;overflow:hidden}.project>summary{padding:20px 22px;font-size:21px;font-weight:700;cursor:pointer}.project>summary .badge{margin-left:12px}
.project-body{padding:0 22px 22px}.chips{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 18px}.chip{background:#eef3f7;border:1px solid var(--border);border-radius:6px;padding:3px 10px;font-size:14px}
.node{margin:14px 0;border:1px solid var(--border);border-radius:8px;overflow:hidden}.node>summary{padding:14px 16px;background:#f8fafc;cursor:pointer;display:list-item}.node>summary strong{font-size:17px;margin-right:10px}.node>summary .muted{font-size:14px;margin-left:10px}
.node-body{padding:12px 16px 16px}.checks{table-layout:fixed}.checks th:nth-child(1){width:34%}.checks th:nth-child(2){width:33%}.checks th:nth-child(3){width:15%}.checks th:nth-child(4){width:18%}
.checks td{overflow-wrap:anywhere;word-break:normal;white-space:normal}.value{white-space:pre-wrap;overflow-wrap:anywhere}.small{font-size:13px;color:var(--muted);margin-top:7px;line-height:1.6}.error{color:#96364a;white-space:pre-wrap;overflow-wrap:anywhere}
details.raw{margin-top:14px}details.raw summary{cursor:pointer;color:#476477;font-size:14px}pre{font:13px/1.6 ui-monospace,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f4f8;padding:14px;border-radius:6px}ul.files{columns:2;padding:0 24px}ul.files li{padding:4px;break-inside:avoid;font-size:14px}
footer{color:var(--muted);font-size:13px;margin-top:30px}@media(max-width:760px){header{padding:28px 18px}h1{font-size:26px}main{padding:18px 12px}.metrics{grid-template-columns:repeat(2,1fr);gap:10px}.metric{padding:14px}.project-body{padding:0 10px 12px}th,td{padding:10px}.checks{min-width:720px}ul.files{columns:1}}
@media print{body{background:white;font-size:11pt}header{background:white;color:#192b40;padding:10px}header p{color:#5b6b7d}main{padding:0}.downloads{display:none}.project,.node{break-inside:avoid}.metrics{grid-template-columns:repeat(4,1fr)}}
'''


def render(report, files):
    # Switch records from runs before dev.19 carry no port evidence; they stay
    # hidden as before instead of being shown as excluded.
    projects=[{**p,'nodes':[n for n in p.get('nodes',[]) if not (n.get('node_type') in ('ethernet_switch','ethernet_hub','cloud','nat','frame_relay_switch','atm_switch') and n.get('collection')=='not_applicable')]} for p in report.get('projects',[])]
    nodes=[n for p in projects for n in p.get('nodes',[])]
    title='TopoLog · 실습 수집 보고서'
    out=['<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="topolog-report-format" content="structured-html-v1"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; script-src &#39;none&#39;; style-src &#39;unsafe-inline&#39;; img-src &#39;self&#39; data: file:; connect-src &#39;none&#39;; object-src &#39;none&#39;; frame-src &#39;none&#39;; base-uri &#39;self&#39; file:; form-action &#39;none&#39;"><title>'+title+'</title><style>'+CSS+'</style></head><body>',
         '<header><div class="brand">TOPOLOG / COLLECTION REPORT</div><h1>실습 수집 보고서</h1><p>프로젝트별 설정과 수집 출력을 기능별로 정리합니다.</p></header><main>',
         '<div class="metrics">']
    for number,label in ((len(projects),'프로젝트'),(len(nodes),'전체 장비'),
                         (sum(n.get('collection')=='collected' for n in nodes),'수집 완료 장비'),
                         (sum(p.get('topology_images',{}).get('native',{}).get('status')=='captured' for p in projects),'GNS3 토폴로지 사진')):
        out.append('<div class="metric"><strong>'+str(number)+'</strong><span>'+label+'</span></div>')
    out+=['</div><p class="muted">수집 시작 '+esc(report.get('started_at','기록 없음'))+' · 종료 '+esc(report.get('finished_at','기록 없음'))+
          ' · 실행 버전 '+esc(report.get('runtime',{}).get('version','이전 실행 · 버전 기록 없음'))+'</p>']
    if report.get('assessment'):
        out.append('<div class="note">기존 수집 자료 정리 · '+esc(report['assessment']['assessed_at'])+'<br>장비를 새로 실행한 결과가 아닙니다.</div>')
    from .naming import report_files
    names=report_files(report)
    from .resultdir import prefix, project_dir, node_dir, node_dir_of
    # Records (evidence, JSON, Markdown) sit one folder down; the report, Word file and pictures stay at the top.
    inner=prefix(report)
    word=names['word'] if names['word'] in files else 'report.docx'
    document=inner+(names['document'] if inner+names['document'] in files else 'report-document.md')
    record=inner+(names['record'] if inner+names['record'] in files else 'report.md')
    entries=[(word,'Word 보고서'),(document,'보고서(Markdown)')] if word in files else []
    if names['annotated_word'] in files:entries.insert(1,(names['annotated_word'],'Word 보고서(주석본)'))
    from .gallery import FOLDER as PICTURES
    folders=[(PICTURES,'토폴로지 사진 폴더')] if any(f.startswith(PICTURES+'/') for f in files) else []
    if inner:folders.append((inner,'수집 기록 폴더'))
    if folders:out.append('<nav class="downloads folders">'+''.join(folder_link(p,'📁 '+l) for p,l in folders)+'</nav>')
    out.append('<nav class="downloads">'+''.join(link(p,l,True) for p,l in entries+[(record,'Markdown 수집 기록'),(inner+'summary.json','전체 결과 JSON'),('ai_prompt.md','AI 편집 프롬프트'),(inner+'manifest.json','파일 무결성 목록')])+'</nav>')
    from .findings import collection_rows as rows
    findings=rows(report)
    if report.get('selection',{}).get('project_ids') is not None:out.append('<p class="note">선택 범위의 새 수집 결과입니다. 선택한 프로젝트의 자료를 정리했습니다.</p>')
    if findings:
        out.append('<h2>수집 메모</h2><div class="panel scroll"><table><tr><th>프로젝트</th><th>장비</th><th>구분</th><th>대상</th><th>확인할 내용</th><th>근거 파일</th></tr>')
        for row in findings:out.append('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in row)+'</tr>')
        out.append('</table></div>')
    out.append('<h2>프로젝트 요약</h2><div class="panel scroll"><table><thead><tr><th>프로젝트</th><th>처리 상태</th><th>장비</th></tr></thead><tbody>')
    for index,p in enumerate(projects):
        out.append('<tr><td><a href="#project-'+str(index)+'">'+esc(p.get('name',p.get('file','프로젝트')))+'</a></td><td>'+badge(p.get('status'))+'</td><td>'+str(len(p.get('nodes',[])))+'대</td></tr>')
    out.append('</tbody></table></div><h2>프로젝트별 상세</h2>')
    for index,p in enumerate(projects):
        out.append('<details class="project" id="project-'+str(index)+'" open><summary>'+esc(p.get('name',p.get('file','프로젝트')))+badge(p.get('status'))+'</summary><div class="project-body">')
        base=inner+project_dir(p)
        if project_dir(p) and any(f.startswith(base+'/') for f in files):
            out.append('<p class="small">'+folder_link(base,'📁 이 프로젝트의 수집 원문 폴더')+'</p>')
        images=p.get('topology_images',{})
        out.append('<div class="topology-tabs" role="group" aria-label="토폴로지 사진">')
        tabs=[('native','실제 GNS3 스크린샷',True),('reconstructed','재구성 토폴로지',False)]
        if (images.get('annotated') or {}).get('status')=='generated':tabs.insert(1,('annotated','자동 주석 사진',False))
        for key,label,selected in tabs:
            ident='topology-'+str(index)+'-'+key;item=images.get(key,{})
            out.append('<input class="tab-radio" type="radio" name="topology-'+str(index)+'" id="'+ident+'"'+(' checked' if selected else '')+'><label class="tab-label" for="'+ident+'">'+label+'</label><div class="tab-panel">')
            file=item.get('file');path=base+'/'+str(file or '')
            from .gallery import planned
            collected_copy=planned(report).get((p.get('project_id'),key))
            if file and collected_copy and collected_copy in set(files or []):path=collected_copy
            from .security import relative_name
            try:relative_name(path)
            except ValueError:file=None
            if file and item.get('status') in ('captured','generated') and '..' not in PurePosixPath(path).parts and ':' not in path:
                out.append('<img class="topology-image" alt="'+label+'" src="./'+quote(path,safe='/')+'"><p>'+link(path,'사진 내려받기',True)+'</p>')
            else:out.append('<p class="muted">'+esc(item.get('reason','이 실행에는 이미지가 없습니다. 새 수집에서 생성합니다.'))+'</p>')
            retry=item.get('retry') if key=='native' else None
            if retry:
                out.append('<p class="small">'+esc('첫 시도에서 저장하지 못해 수집이 끝난 뒤 다시 저장했습니다. 첫 시도 원인: '+(retry.get('first_reason') or '기록 없음'))+'</p>'
                           if item.get('status')=='captured' else
                           '<p class="small">'+esc('수집이 끝난 뒤 다시 시도했지만 저장하지 못했습니다.'+(' '+retry['reason'] if retry.get('reason') else ''))+'</p>')
            note={'native':'GNS3 자체 Screenshot 기능의 이미지입니다. 내용은 직접 확인하세요.',
                  'annotated':'GNS3 사진의 사본에 수집한 설정의 서브넷·cost와 제목을 TopoLog가 덧붙인 그림입니다. 원래 사진은 첫 번째 탭입니다.'}
            out.append('<p class="small">'+note.get(key,'API 좌표와 연결로 구성한 그림이며 실제 스크린샷과 구분합니다.')+'</p></div>')
        out.append('</div>')
        features=sorted({f for n in p.get('nodes',[]) for f in n.get('analysis',{}).get('features',[])})
        if features:out.append('<div class="chips">'+''.join('<span class="chip">'+esc(LABELS.get(f,f))+'</span>' for f in features)+'</div>')
        if p.get('gns3_project_name'):out.append('<p class="muted">'+esc('GNS3에 등록된 프로젝트 이름: '+p['gns3_project_name'])+'</p>')
        copy=p.get('gns3_copy') or {}
        if copy:
            other=('GNS3 기본 프로젝트 폴더의 '+copy['other']) if copy.get('other') else '같은 폴더 안의 다른 프로젝트 파일'
            out.append('<p class="note">'+esc('같은 GNS3 프로젝트 ID를 쓰는 프로젝트('+other+')가 있어, 원본 파일은 그대로 두고 임시 사본(새 ID)으로 열어 수집했습니다. '
                       +('사본은 수집이 끝난 뒤 지웠습니다.' if copy.get('removed') else '사본을 지우지 못했을 수 있습니다: '+str(copy.get('path',''))))+'</p>')
        for reason in [p.get('reason'),p.get('recovery_hint')]+p.get('cleanup_errors',[]):
            if reason:out.append('<p class="error">'+esc(reason)+'</p>')
        for node in p.get('nodes',[]):
            checks=node.get('checks',[]);counts=Counter(c['status'] for c in checks)
            here=base+'/'+node_dir(node)+'/'
            expanded = ' open' if node is p['nodes'][0] else ''
            switch=node.get('node_type') in ('ethernet_switch','ethernet_hub','cloud','nat','frame_relay_switch','atm_switch')
            detail=('기본 동작 스위치' if node.get('switch_profile')=='default' else 'GNS3 내장 스위치 · 포트 설정 있음' if node.get('switch_profile')=='configured' else ('GNS3 내장 노드 · '+str(node.get('platform') or '')) if node.get('switch_profile')=='builtin' else 'GNS3 내장 스위치') if switch else node.get('platform','unknown').upper()
            out.append('<details class="node"'+expanded+'><summary><strong>'+esc(node.get('name','장비'))+'</strong>'+badge(node.get('collection'))+' <span class="muted">'+esc(detail)+'</span></summary><div class="node-body">')
            if switch:
                if node.get('reason'):out.append('<p class="small">'+esc(node['reason'])+'</p>')
                if node.get('evidence_file'):
                    path=here+str(node['evidence_file'])
                    out.append('<p class="small">포트 설정(API): '+link(path,'보기')+' · '+link(path,'다운로드',True)+'</p>')
            for reason in ([] if switch else [node.get('reason')])+[node.get('recovery_hint')]+node.get('cleanup_errors',[]):
                if reason:out.append('<p class="error">'+esc(reason)+'</p>')
            startup=node.get('startup_configuration',{})
            if startup:
                path=here+str(startup.get('file','startup.cfg'))
                running=running_config(node)
                out.append('<p class="small">저장 설정: '+(link(path,'startup.cfg 보기') if startup.get('status')=='collected' else '미수집')+
                           ' · 출처 '+esc(startup.get('source','—'))+
                           ' · 실행 중 설정: '+(link(here+running,'running-config 보기') if running else '미수집')+'</p>')
            if node.get('privilege'):
                out.append('<p class="small">EXEC 권한 전환: '+('성공' if node['privilege'].get('elevated') else '실패')+
                           ' · 원래 권한 복원 '+esc(node['privilege'].get('restoration','변경 없음'))+'</p>')
            if checks:
                out.append('<div class="scroll"><table class="checks"><thead><tr><th>설정·확인 내용</th><th>수집 출력 요약</th><th>근거</th></tr></thead><tbody>')
                for check in checks:
                    rule=check.get('criterion',{});expected=expectation(rule) if rule else LABELS.get(check.get('feature'), '수집 내용')
                    actual='관련 설정·원문 참조' if check.get('status')=='unverified' else ping_observation(check) if isinstance(check.get('observed'),dict) and 'success_percent' in check['observed'] else observation(check['observed']) if 'observed' in check else check.get('reason','관측값은 근거 파일을 확인하세요.')
                    if check.get('classification') in SCOPE_NOTES and 'success_percent' not in (check.get('observed') if isinstance(check.get('observed'),dict) else {}):
                        actual+=' · '+check.get('reason','')
                    if check.get('status')=='excluded':
                        actual=check.get('reason','사용자가 확인에서 제외했습니다.')
                    if check.get('origin')=='user':
                        expected+=' · 사용자 지정'
                    evidence=check.get('evidence');path=here+str(evidence)
                    extra=''.join('<br>'+link(base+'/'+node_dir_of(p,e['node_id'])+'/'+str(e['file']),e.get('label','추가 근거')) for e in check.get('additional_evidence',[]) if e.get('node_id') and e.get('file'))
                    out.append('<tr><td>'+esc(expected)+''+'</td><td class="value">'+esc(actual)+'</td><td>'+(link(path,'보기')+' · '+link(path,'다운로드',True) if evidence else '—')+extra+'</td></tr>')
                out.append('</tbody></table></div>')
            out.append('<details class="raw"><summary>원본 명령과 상세 기록</summary><ul>')
            for command in node.get('commands',[]):
                path=here+str(command.get('file',''))
                out.append('<li>'+link(path,command['command'])+' · '+link(path,'다운로드',True)+' '+badge(command.get('status'))+'</li>')
            record=here+'collection.json'
            out.append('</ul>'+link(record,'장비 상세 JSON')+'</details></div></details>')
        if not p.get('nodes'):out.append('<p class="muted">수집된 장비가 없습니다. 처리 상태와 오류 기록을 확인하세요.</p>')
        for missing in p.get('missing_rule_nodes',[]):out.append('<p class="error">기준 대상 장비 없음: '+esc(missing)+'</p>')
        out.append('</div></details>')
    out.append('<details class="raw panel"><summary style="padding:16px">전체 파일 다운로드 · '+str(len(files))+'개</summary><ul class="files">'+''.join('<li>'+link(p,p,True)+'</li>' for p in files)+'</ul></details>')
    for missing in report.get('unmatched_rule_projects',[]):out.append('<p class="error">기준 대상 프로젝트 없음: '+esc(missing)+'</p>')
    out.append('<footer>TopoLog · API 구성 정보는 실제 토폴로지 스크린샷과 구분합니다. 원문과 생성 보고서는 별도 파일로 보존됩니다.</footer></main></body></html>')
    return ''.join(out)
