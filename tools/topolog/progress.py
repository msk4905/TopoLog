"""Concise user-facing progress, with technical details retained in result files."""
def message(text):
    if text.startswith('CLI pagination:'):return None
    for prefix,label in [('Project: ','프로젝트: '),('Node: ','장비: '),('Command: ','수집: '),('Reachability: ','도달성 확인(ping): ')]:
        if text.startswith(prefix):return label+text[len(prefix):]
    if text.startswith('Node result: '):
        parts=text[len('Node result: '):].split(' / ')
        labels={'collected':'완료','partial':'일부 완료','failed':'실패','not_applicable':'대상 아님','requires_privilege':'권한 확인 필요','unsupported':'지원되지 않는 장비','cancelled':'중지'}
        if len(parts)>2 and parts[2].startswith('기본 동작 스위치'):return parts[0]+': 완료 (기본 동작 스위치)'
        return parts[0]+': '+labels.get(parts[1],parts[1]) if len(parts)>1 else text
    if text.startswith('Waiting for console readiness:'):return '장비 콘솔 준비를 기다리는 중입니다.'
    if text.startswith('Waiting for CLI prompt'):return '장비 응답을 기다리는 중입니다.'
    if text.startswith('Waiting for GNS3 compute/VM:'):return 'GNS3 서버와 VM 연결을 기다리는 중입니다.'
    if text.startswith('Starting local GNS3 backend:'):return 'GNS3 로컬 서버를 시작합니다.'
    if text.startswith('Node startup failed:'):return '장비를 시작하지 못했습니다. 보고서에서 오류 내용을 확인하세요.'
    if text.startswith('Collection failed:'):return '장비 출력을 수집하지 못했습니다. 보고서에서 오류 내용을 확인하세요.'
    if text.startswith('Preflight: '):return text[len('Preflight: '):]
    if text.startswith('Port conflict:'):return '장비 포트가 이미 사용 중이라 시작하지 못했습니다. 포트·프로젝트는 변경하지 않았고, 사용 주체 확인 결과를 보고서에 기록했습니다.'
    if text.startswith('Recovery:'):return 'GNS3 서버를 저장 후 다시 시작하고 재시도하세요. 상세 복구 안내는 보고서에 기록했습니다.'
    if text.startswith('Project incomplete:'):return '프로젝트를 완료하지 못했습니다. 결과 보고서에서 원인을 확인하세요.'
    if text=='Requesting privileged EXEC (one attempt)':return '필요한 권한으로 전환합니다.'
    return text

def wrap(callback):
    def notify(text):
        translated=message(text)
        if translated:callback(translated)
    return notify
