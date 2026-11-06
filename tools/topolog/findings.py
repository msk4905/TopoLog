"""Evidence-scoped findings shared by HTML, Markdown and Word."""

def final_recheck_note(node):
    record=node.get('final_routing',{})
    samples=record.get('rechecks',[])
    last=samples[-1] if samples else {}
    if last.get('status') in ('failed','cancelled','cleanup_failed') or record.get('status')=='recheck_failed':
        label='마지막 재확인 중지' if last.get('status')=='cancelled' else '마지막 재확인 실패'
        return label+'. 기존 수집 자료는 보존했으며 마지막 상태는 확인하지 못했습니다.'
    return ''


def collection_rows(report):
    """Collection problems only; do not grade the user's project behaviour."""
    result=[]
    for p in report.get('projects',[]):
        if p.get('reason'):result.append([p.get('name',''),'','프로젝트 처리','',p['reason'],'result.json'])
        native=p.get('topology_images',{}).get('native',{})
        if native.get('reason'):result.append([p.get('name',''),'','토폴로지 사진','',native['reason'],'capture/capture.json'])
        for detail in p.get('cleanup_errors',[]):result.append([p.get('name',''),'','상태 복원','',detail,'result.json'])
        for n in p.get('nodes',[]):
            note=final_recheck_note(n)
            if note:result.append([p.get('name',''),n.get('name',''),'최종 상태 재확인','',note,'collection.json'])
            if n.get('collection')!='collected' and n.get('reason'):
                result.append([p.get('name',''),n.get('name',''),'장비 수집','',n['reason'],'collection.json'])
            for detail in n.get('cleanup_errors',[]):result.append([p.get('name',''),n.get('name',''),'상태 복원','',detail,'collection.json'])
    return result
def rows(report):
    result=[]
    for p in report.get('projects',[]):
        for n in p.get('nodes',[]):
            for c in n.get('checks',[]):
                state=c.get('status')
                if state not in ('failed','inconclusive','unverified'):continue
                label='관측 불일치' if state=='failed' else ('증거/해석 확인 필요' if state=='inconclusive' else '미검증 범위')
                if c.get('classification')=='policy_limited':label='방화벽 설정 참고'
                if c.get('classification')=='selective_redistribution':label='선별 재분배 범위 참고'
                if c.get('classification')=='origin_inactive':label='출발 인터페이스 비활성 참고'
                rule=c.get('criterion',{});reason=c.get('reason') or str(c.get('observed','근거 파일을 확인하세요.'))
                if rule.get('kind')=='route_covered' and state=='failed':reason+=' · 수집 시점의 경로 부재입니다. 광고/필터/수렴 여부를 대조해야 하며 원인을 단정하지 않습니다.'
                result.append([p.get('name',''),n.get('name',''),label,rule.get('network',rule.get('prefix',c.get('command',rule.get('kind','')))),reason,c.get('evidence','')])
    return result


def wording(value):
    if isinstance(value,str):
        for a,b in [('설정상 정책 제한 가능','방화벽 설정 참고'),('정책 제한 가능','방화벽 설정 참고'),('자동 판정','검증'),('자동 검증','검증'),('자동 기준','검증 기준'),('자동 도출','기준'),('자동으로 도출','도출'),('자동으로 확정','확정')]:value=value.replace(a,b)
        return value
    if isinstance(value,list):return [wording(x) for x in value]
    if isinstance(value,tuple):return tuple(wording(x) for x in value)
    return value
