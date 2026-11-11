"""Error reports and feedback sent to the developer, only after the person reads them and agrees.

Nothing is sent automatically. build() collects the error, the program version,
the Windows version and the run log, hides known password/key formats and the
user's home folder, and returns the exact text that will be sent. send() posts
that text to the developer's Sentry project. The DSN below can only add new
reports; it cannot read or delete reports (Sentry designs it to be public).
A copy of every report is written next to the run logs first, so a report that
could not be sent can still be passed on by hand.
"""
from datetime import datetime, timezone
import json
import os
import platform
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

DSN = 'https://aa693189fef0f78db462019747e91fa6@o4512410646806528.ingest.us.sentry.io/4512410670792704'
LOG_LIMIT = 200_000          # characters of the run log kept in one report


def endpoint(dsn=DSN):
    """(envelope URL, public key) of a Sentry DSN."""
    parts = urllib.parse.urlsplit(dsn)
    project = parts.path.strip('/')
    if parts.scheme != 'https' or not parts.username or not project.isdigit():
        raise ValueError('Sentry DSN 형식이 아닙니다.')
    return '{}://{}/api/{}/envelope/'.format(parts.scheme, parts.hostname, project), parts.username


def mask(text):
    """Known password/key formats and the user's home folder hidden."""
    from .sharing import redact
    text = redact(str(text or ''))
    home = str(Path.home())
    if home and len(home) > 3:
        text = text.replace(home, '%USERPROFILE%').replace(home.replace('\\', '/'), '%USERPROFILE%')
    return text


def build(error, log_lines=(), context=None, kind='error'):
    """The report exactly as it will be sent: {'event': ..., 'log': str, 'preview': str}."""
    from .resources import version
    log = mask('\n'.join(log_lines))[-LOG_LIMIT:]
    message = mask(error)
    extra = {k: mask(v) for k, v in (context or {}).items()}
    event = {'event_id': uuid.uuid4().hex, 'timestamp': datetime.now(timezone.utc).isoformat(),
             'platform': 'python', 'level': 'info' if kind == 'feedback' else 'error', 'logger': 'topolog',
             'release': 'topolog@' + version('dev'),
             'message': {'formatted': message[:8000]},
             'tags': {'os': platform.system() + ' ' + platform.release(), 'build': context.get('build', '') if context else '', 'kind': kind},
             'contexts': {'os': {'name': platform.system(), 'version': platform.version()},
                          'runtime': {'name': 'Python', 'version': platform.python_version()}},
             'extra': extra}
    preview = '\n'.join(['[피드백]' if kind == 'feedback' else '[오류]', message, '', '[프로그램]', event['release'] + ' · ' + event['tags']['os'] + ' · Python ' + platform.python_version()] +
                        (['', '[상황]'] + ['{}: {}'.format(k, v) for k, v in extra.items()] if extra else []) +
                        ['', '[실행 기록]', log or '(없음)'])
    return {'event': event, 'log': log, 'preview': preview}


def keep(report, folder):
    """Write the report next to the run logs; returns the file path."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ('error-report-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.txt')
    path.write_text(report['preview'], encoding='utf-8')
    return path


def envelope(report):
    event = json.dumps(report['event'], ensure_ascii=False).encode('utf-8')
    log = report['log'].encode('utf-8')
    header = json.dumps({'event_id': report['event']['event_id'], 'sent_at': report['event']['timestamp']}).encode()
    lines = [header,
             json.dumps({'type': 'event', 'length': len(event)}).encode(), event,
             json.dumps({'type': 'attachment', 'length': len(log), 'filename': 'topolog-run.log',
                         'content_type': 'text/plain'}).encode(), log]
    return b'\n'.join(lines) + b'\n'


def send(report, dsn=DSN, timeout=20, opener=urllib.request.urlopen):
    """Post the report; returns the event id. Raises OSError/ValueError on failure."""
    url, key = endpoint(dsn)
    from .resources import version
    request = urllib.request.Request(url, data=envelope(report), method='POST', headers={
        'Content-Type': 'application/x-sentry-envelope',
        'X-Sentry-Auth': 'Sentry sentry_version=7, sentry_client=topolog/{}, sentry_key={}'.format(version('dev'), key)})
    with opener(request, timeout=timeout) as response:
        if response.status != 200:
            raise OSError('Sentry 응답 {}'.format(response.status))
    return report['event']['event_id']
