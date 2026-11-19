"""Read-only command alternatives and evidence selection, without model guesses.

Attempts remain separate records. A newer failed sample never silently reuses an
older successful sample; VLAN alternatives represent the same observation only.
"""
import re

from .transport import CLI_ERROR

VLAN_COMMANDS = ('show vlan brief', 'show vlan-switch brief')
UNSUPPORTED = re.compile(
    r'(?im)^\s*(?:%\s*(?:Invalid|Ambiguous|Incomplete|Unrecognized|Unknown|'
    r'Unsupported|Not supported|Command[^\r\n]*not supported)|'
    r'Unknown command|Invalid command|Command not supported|Syntax error)')


def alternatives(command, platform):
    if platform == 'ios' and command in VLAN_COMMANDS:
        return (command,) + tuple(c for c in VLAN_COMMANDS if c != command)
    return (command,)


def response_state(item):
    if not item or item.get('status') != 'collected':
        return 'not_collected'
    text = item.get('text', '')
    if CLI_ERROR.search(text) or item.get('cli_error'):
        return 'unsupported' if UNSUPPORTED.search(text) else 'cli_error'
    # Import locally so profiles can use this module as well.
    from .profiles import body
    content = body(text, item['command']).strip()
    if not content:
        return 'no_output'
    if item['command'] in VLAN_COMMANDS and not re.search(
            r'(?im)^\s*(?:VLAN\s+Name\s+Status\b|\d+\s+\S+\s+(?:active|act/\S+|suspend\S*|shutdown)\b)', content):
        return 'unrecognized_output'
    return 'output'


def latest(evidence, command, vlan_equivalent=False):
    commands = VLAN_COMMANDS if vlan_equivalent and command in VLAN_COMMANDS else (command,)
    return next((item for item in reversed(evidence) if item.get('command') in commands), None)


def usable(item):
    return response_state(item) == 'output'


def unavailable(item, command):
    state = response_state(item)
    reasons = {'not_collected': '필요한 명령 증거를 수집하지 못했습니다.',
               'unsupported': '장비가 명령 구문을 거부했거나 지원하지 않습니다.',
               'cli_error': '장비가 명령 실행 오류를 반환했습니다.',
               'no_output': '명령 응답에 해석할 출력이 없습니다.',
               'unrecognized_output': '수집한 명령 출력의 형식을 해석하지 못했습니다.'}
    result = {'status': 'inconclusive', 'command': item['command'] if item else command,
              'response_state': state, 'reason': reasons.get(state, '정상 명령 증거가 없습니다.')}
    if item and item.get('file'):
        result['evidence'] = item['file']
    if item and item.get('reason'):
        result['reason'] += ' ' + str(item['reason'])
    return result
