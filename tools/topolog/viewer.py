"""Open local reports with a browser, independently of HTML file associations."""
import os
from pathlib import Path
import re
import subprocess
import webbrowser
import json
import tempfile
import html


def report_view(path):
    """Upgrade old presentation and integrity metadata; raw evidence is unchanged."""
    path = Path(path).resolve(strict=True)
    from .resultdir import summary_file
    summary = summary_file(path.parent)
    with path.open(encoding='utf-8', errors='replace') as stream:
        legacy = '<h1>TopoLog</h1><pre>' in stream.read(4096)
    if not legacy or not summary.is_file():
        return path
    try:
        from .presentation import refresh_report
        return refresh_report(path.parent)
    except (OSError, ValueError):
        # A read-only archive can still be displayed from an isolated cache.
        pass
    try:
        data = json.loads(summary.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        return path
    if data.get('schema') != 'topolog-batch-v2':
        return path
    from .reporting import render
    files = sorted(p.relative_to(path.parent).as_posix() for p in path.parent.rglob('*') if p.is_file())
    content = render(data, files)
    base = html.escape(path.parent.as_uri() + '/', quote=True)
    content = content.replace('<head>', '<head><base href="' + base + '">', 1)
    target = Path(tempfile.mkdtemp(prefix='TopoLog-report-')) / 'index.html'
    target.write_text(content, encoding='utf-8')
    return target


def windows_browsers():
    candidates = []
    try:
        import winreg
        user = r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, user) as key:
            progid = winreg.QueryValueEx(key, 'ProgId')[0]
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + r'\shell\open\command') as key:
            command = winreg.QueryValueEx(key, None)[0]
        match = re.match(r'^\s*(?:"([^"]+)"|(\S+))', command)
        if match:
            path = Path(os.path.expandvars(match[1] or match[2]))
            if path.name.lower() in ('chrome.exe','msedge.exe','firefox.exe','brave.exe','opera.exe','vivaldi.exe'):
                candidates.append(path)
    except (ImportError, OSError, TypeError):
        pass
    for root in (os.environ.get('ProgramFiles'), os.environ.get('ProgramFiles(x86)'), os.environ.get('LOCALAPPDATA')):
        if root:
            candidates.extend(Path(root) / name for name in ('Google/Chrome/Application/chrome.exe',
                              'Microsoft/Edge/Application/msedge.exe','Mozilla Firefox/firefox.exe'))
    return [p for p in dict.fromkeys(candidates) if p.is_file()]


def open_report(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise OSError('Report is not a file')
    path = report_view(path)
    url = path.as_uri()
    if os.name == 'nt':
        for browser in windows_browsers():
            try:
                subprocess.Popen([str(browser), url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return browser.name
            except OSError:
                continue
    if webbrowser.open(url):
        return 'system browser'
    raise OSError('No browser accepted the report')


def latest_report(output):
    try:
        from .naming import result_folders
        candidates = [p for p in result_folders(output) if p.is_dir() and (p / 'index.html').is_file()]
        return max(candidates, key=lambda p:(p/'index.html').stat().st_mtime) if candidates else None
    except OSError:
        return None
