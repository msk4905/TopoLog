"""Self-contained, readable HTML reports from structured collection results."""
from collections import Counter
import html
import json
from pathlib import PurePosixPath
from urllib.parse import quote
from .features import LABELS

STATUS = {'passed':'기준 충족','failed':'기준 불일치','inconclusive':'확인 필요',
          'unverified':'미검증','collected':'수집 완료','completed':'처리 완료',
          'unsupported':'미지원','requires_privilege':'권한 필요','cancelled':'중지',
          'not_run':'미실행','not_applicable':'판정 대상 아님','partial':'일부 수집','cleanup_failed':'복구 오류','error':'실행 오류','pending':'대기'}


def esc(value):
    return html.escape(str(value), quote=True)


def badge(status):
    kind = 'ok' if status in ('passed','collected','completed') else ('bad' if status in ('failed','error','cleanup_failed') else 'wait')
    return '<span class="badge '+kind+'">'+esc(STATUS.get(status,status or '확인 필요'))+'</span>'


def link(path, label, download=False):
    path = str(path).replace('\\','/')
    parts = PurePosixPath(path)
    if parts.is_absolute() or '..' in parts.parts or any(':' in p for p in parts.parts):
        return esc(label)
    return '<a '+('download ' if download else '')+'href="./'+quote(path, safe='/')+'">'+esc(label)+'</a>'


def expectation(rule):
    kind = rule.get('kind')
    if kind=='interface_address': return rule.get('interface','')+' · '+rule.get('address','')
    if kind=='ospf_neighbors': return 'OSPF 이웃 FULL: '+', '.join(rule.get('neighbors',[]))
    if kind=='route': return '경로 '+rule.get('prefix','')+' · '+rule.get('protocol','')+(' · next hop '+rule['next_hop'] if rule.get('next_hop') else '')
    if kind=='ping': return 'Ping '+rule.get('target','')+' · 성공률 '+str(rule.get('min_percent',100))+'% 이상'
    if kind=='vlan_active': return 'VLAN '+str(rule.get('vlan',''))+' 활성'
    if kind=='trunk': return rule.get('interface','')+' · trunking'
    if kind=='hsrp': return rule.get('interface','')+' · HSRP '+str(rule.get('group',''))+' · '+rule.get('virtual_ip','')
    if kind=='dhcp_pool': return 'DHCP 풀 '+rule.get('pool','')+' · '+rule.get('network','')
    if kind=='static_nat': return '정적 NAT '+rule.get('local','')+' → '+rule.get('global','')
    if kind=='acl_binding': return rule.get('interface','')+' · ACL '+rule.get('acl','')+' · '+{'in':'수신','out':'송신'}.get(rule.get('direction'),'')
    if kind=='stp_instance': return 'VLAN '+str(rule.get('vlan',''))+' · STP 인스턴스 활성'
    if kind=='contains': return rule.get('command','')+' · '+', '.join(rule.get('expected',[]))
    return json.dumps(rule,ensure_ascii=False) if rule else '검증 범위 확인'


def observation(value):
    if value is None: return '관측값 없음'
    if value == []: return '일치하는 항목 없음'
    if isinstance(value,list): return '\n'.join(observation(x) for x in value)
    if isinstance(value,dict): return '\n'.join(str(k)+': '+observation(v) for k,v in value.items())
    return str(value)


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
.badge{display:inline-block;font-size:13px;font-weight:650;line-height:1.5;padding:3px 10px;border-radius:20px;white-space:nowrap}.ok{color:#166443;background:#e4f3eb}.wait{color:#85540c;background:#fff1d8}.bad{color:#a52b3d;background:#fde9ed}
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
    statuses=Counter(c['status'] for n in nodes for c in n.get('checks',[]))
    unverified=sum(statuses[k] for k in ('unverified','inconclusive'))
    title='TopoLog · 실습 수집 보고서'
    out=['<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="topolog-report-format" content="structured-html-v1"><meta name="viewport" content="width=device-width, initial-scale=1"><title>'+title+'</title><style>'+CSS+'</style></head><body>',
         '<header><div class="brand">TOPOLOG / COLLECTION REPORT</div><h1>실습 수집 보고서</h1><p>프로젝트별 구성과 검증 근거를 한곳에서 확인합니다.</p></header><main>',
         '<div class="metrics">']
    for number,label in ((len(projects),'프로젝트'),(len(nodes),'전체 장비'),(statuses['passed'],'충족한 검증 조건'),(unverified,'미검증·판정 불가 조건')):
        out.append('<div class="metric"><strong>'+str(number)+'</strong><span>'+label+'</span></div>')
    out+=['</div><div class="note">전체 판정 '+badge(report.get('verdict'))+
          ' <span class="muted">실패 조건 '+str(statuses['failed'])+'개 · 수집 완료 '+str(sum(n.get('collection')=='collected' for n in nodes))+'대</span><br>기준 충족은 검사한 조건의 충족을 의미합니다. 미지원·미검증 항목은 아래에서 별도로 확인하세요.</div>',
          '<p class="muted">수집 시작 '+esc(report.get('started_at','기록 없음'))+' · 종료 '+esc(report.get('finished_at','기록 없음'))+
          ' · 실행 버전 '+esc(report.get('runtime',{}).get('version','이전 실행 · 버전 기록 없음'))+'</p>']
    if report.get('assessment'):
        out.append('<div class="note">기존 증거 재판정 · '+esc(report['assessment']['assessed_at'])+'<br>장비를 새로 실행한 결과가 아닙니다.</div>')
    entries=[('report.docx','Word 보고서'),('report-document.md','보고서(Markdown)')] if 'report.docx' in files else []
    out.append('<nav class="downloads">'+''.join(link(p,l,True) for p,l in entries+[('report.md','Markdown 검증 기록'),('summary.json','전체 결과 JSON'),('ai_prompt.md','AI 편집 프롬프트'),('manifest.json','파일 무결성 목록')])+'</nav>')
    out.append('<h2>프로젝트 요약</h2><div class="panel scroll"><table><thead><tr><th>프로젝트</th><th>처리 상태</th><th>판정</th><th>장비</th></tr></thead><tbody>')
    for index,p in enumerate(projects):
        out.append('<tr><td><a href="#project-'+str(index)+'">'+esc(p.get('name',p.get('file','프로젝트')))+'</a></td><td>'+badge(p.get('status'))+'</td><td>'+badge(p.get('verdict','inconclusive'))+'</td><td>'+str(len(p.get('nodes',[])))+'대</td></tr>')
    out.append('</tbody></table></div><h2>프로젝트별 상세</h2>')
    for index,p in enumerate(projects):
        out.append('<details class="project" id="project-'+str(index)+'" open><summary>'+esc(p.get('name',p.get('file','프로젝트')))+badge(p.get('verdict','inconclusive'))+'</summary><div class="project-body">')
        images=p.get('topology_images',{})
        out.append('<div class="topology-tabs" role="group" aria-label="토폴로지 사진">')
        for key,label,selected in [('native','실제 GNS3 스크린샷',True),('reconstructed','재구성 토폴로지',False)]:
            ident='topology-'+str(index)+'-'+key;item=images.get(key,{})
            out.append('<input class="tab-radio" type="radio" name="topology-'+str(index)+'" id="'+ident+'"'+(' checked' if selected else '')+'><label class="tab-label" for="'+ident+'">'+label+'</label><div class="tab-panel">')
            file=item.get('file');path=str(p.get('project_id',''))+'/'+str(file or '')
            if file and item.get('status') in ('captured','generated') and '..' not in PurePosixPath(path).parts and ':' not in path:
                out.append('<img class="topology-image" alt="'+label+'" src="./'+quote(path,safe='/')+'"><p>'+link(path,'사진 내려받기',True)+'</p>')
            else:out.append('<p class="muted">'+esc(item.get('reason','이 실행에는 이미지가 없습니다. 새 수집에서 생성합니다.'))+'</p>')
            out.append('<p class="small">'+('GNS3 자체 Screenshot 기능의 이미지입니다. 내용은 직접 확인하세요.' if key=='native' else 'API 좌표와 연결로 구성한 그림이며 실제 스크린샷과 구분합니다.')+'</p></div>')
        out.append('</div>')
        features=sorted({f for n in p.get('nodes',[]) for f in n.get('analysis',{}).get('features',[])})
        if features:out.append('<div class="chips">'+''.join('<span class="chip">'+esc(LABELS.get(f,f))+'</span>' for f in features)+'</div>')
        for reason in [p.get('reason'),p.get('recovery_hint')]+p.get('cleanup_errors',[]):
            if reason:out.append('<p class="error">'+esc(reason)+'</p>')
        for node in p.get('nodes',[]):
            checks=node.get('checks',[]);counts=Counter(c['status'] for c in checks)
            expanded = ' open' if node is p['nodes'][0] else ''
            switch=node.get('node_type') in ('ethernet_switch','ethernet_hub','cloud','nat','frame_relay_switch','atm_switch')
            detail=('기본 동작 스위치' if node.get('switch_profile')=='default' else 'GNS3 내장 스위치 · 포트 설정 있음' if node.get('switch_profile')=='configured' else ('GNS3 내장 노드 · '+str(node.get('platform') or '')) if node.get('switch_profile')=='builtin' else 'GNS3 내장 스위치') if switch else node.get('platform','unknown').upper()+' · 충족 '+str(counts['passed'])+' · 불일치 '+str(counts['failed'])+' · 미확인 '+str(counts['unverified']+counts['inconclusive'])
            out.append('<details class="node"'+expanded+'><summary><strong>'+esc(node.get('name','장비'))+'</strong>'+badge(node.get('collection'))+' <span class="muted">'+esc(detail)+'</span></summary><div class="node-body">')
            if switch:
                if node.get('reason'):out.append('<p class="small">'+esc(node['reason'])+'</p>')
                if node.get('evidence_file'):
                    path=str(p.get('project_id',''))+'/'+str(node.get('node_id',''))+'/'+str(node['evidence_file'])
                    out.append('<p class="small">포트 설정(API): '+link(path,'보기')+' · '+link(path,'다운로드',True)+'</p>')
            for reason in ([] if switch else [node.get('reason')])+[node.get('recovery_hint')]+node.get('cleanup_errors',[]):
                if reason:out.append('<p class="error">'+esc(reason)+'</p>')
            startup=node.get('startup_configuration',{})
            if startup:
                path=str(p.get('project_id',''))+'/'+str(node.get('node_id',''))+'/'+str(startup.get('file','startup.cfg'))
                out.append('<p class="small">저장 설정: '+(link(path,'startup.cfg 보기') if startup.get('status')=='collected' else '미수집')+
                           ' · 출처 '+esc(startup.get('source','—'))+'</p>')
            if node.get('privilege'):
                out.append('<p class="small">EXEC 권한 전환: '+('성공' if node['privilege'].get('elevated') else '실패')+
                           ' · 원래 권한 복원 '+esc(node['privilege'].get('restoration','변경 없음'))+'</p>')
            if checks:
                out.append('<div class="scroll"><table class="checks"><thead><tr><th>기대 조건</th><th>관측값·미확인 사유</th><th>판정</th><th>근거</th></tr></thead><tbody>')
                for check in checks:
                    rule=check.get('criterion',{});expected=expectation(rule) if rule else LABELS.get(check.get('feature'), '검증 범위 확인')
                    actual=observation(check['observed']) if 'observed' in check else check.get('reason','관측값은 근거 파일을 확인하세요.')
                    evidence=check.get('evidence');path=str(p.get('project_id',''))+'/'+str(node.get('node_id',''))+'/'+str(evidence)
                    out.append('<tr><td>'+esc(expected)+('<div class="small">자동 도출 · '+esc(check['basis'])+'</div>' if check.get('basis') else '')+'</td><td class="value">'+esc(actual)+'</td><td>'+badge(check['status'])+'</td><td>'+(link(path,'보기')+' · '+link(path,'다운로드',True) if evidence else '—')+'</td></tr>')
                out.append('</tbody></table></div>')
            out.append('<details class="raw"><summary>원본 명령과 상세 기록</summary><ul>')
            for command in node.get('commands',[]):
                path=str(p.get('project_id',''))+'/'+str(node.get('node_id',''))+'/'+str(command.get('file',''))
                out.append('<li>'+link(path,command['command'])+' · '+link(path,'다운로드',True)+' '+badge(command.get('status'))+'</li>')
            record=str(p.get('project_id',''))+'/'+str(node.get('node_id',''))+'/collection.json'
            out.append('</ul>'+link(record,'장비 상세 JSON')+'</details></div></details>')
        if not p.get('nodes'):out.append('<p class="muted">수집된 장비가 없습니다. 처리 상태와 오류 기록을 확인하세요.</p>')
        for missing in p.get('missing_rule_nodes',[]):out.append('<p class="error">기준 대상 장비 없음: '+esc(missing)+'</p>')
        out.append('</div></details>')
    out.append('<details class="raw panel"><summary style="padding:16px">전체 파일 다운로드 · '+str(len(files))+'개</summary><ul class="files">'+''.join('<li>'+link(p,p,True)+'</li>' for p in files)+'</ul></details>')
    for missing in report.get('unmatched_rule_projects',[]):out.append('<p class="error">기준 대상 프로젝트 없음: '+esc(missing)+'</p>')
    out.append('<footer>TopoLog · API 구성 정보는 실제 토폴로지 스크린샷과 구분합니다. 원문과 생성 보고서는 별도 파일로 보존됩니다.</footer></main></body></html>')
    return ''.join(out)
