"""Check that GNS3 (and its linked GNS3 VM) is ready before a batch starts.

Only the GNS3 GUI program is ever launched; servers, VMs, projects and nodes
of other work are never stopped or modified. The GNS3 VM is started by GNS3
itself according to the user's own GNS3 VM setting.
"""
import os
from pathlib import Path
import subprocess
import time


def gui_running():
    """True/False on Windows; None when it cannot be determined."""
    if os.name != 'nt':
        return None
    try:
        # Bytes, not text: tasklist prints in the console code page (e.g. CP949
        # "정보: ..." when nothing matches), which fails to decode under
        # PYTHONUTF8=1 and left stdout as None. The image name itself is ASCII.
        result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq gns3.exe', '/FO', 'CSV', '/NH'],
                                capture_output=True, timeout=15,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = result.stdout or b''
    if result.returncode != 0 and not output:
        return None
    return b'"gns3.exe"' in output.lower()


def gui_executable(server_candidates=None):
    """The installed gns3.exe next to a discovered gns3server.exe."""
    if server_candidates is None:
        from . import backend
        _, server_candidates = backend.discover()
    candidates = [Path(server).parent / 'gns3.exe' for server in server_candidates]
    for root in (os.environ.get('ProgramFiles'), os.environ.get('ProgramFiles(x86)'), os.environ.get('LOCALAPPDATA')):
        if root:
            candidates += [Path(root) / 'GNS3' / 'gns3.exe', Path(root) / 'Programs' / 'GNS3' / 'gns3.exe']
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def launch_gui(executable):
    flags = getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
    # Started with no project argument: the GUI starts its own local server
    # (--local) and, if the user enabled it in GNS3, the linked GNS3 VM.
    return subprocess.Popen([str(executable)], cwd=str(Path(executable).parent), stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)


def _wait(condition, timeout, cancel, progress, message, interval=1.0):
    deadline = time.monotonic() + timeout
    next_notice = 0
    while True:
        if cancel and cancel.is_set():
            raise InterruptedError('GNS3 preparation cancelled')
        value = condition()
        if value:
            return value
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        if time.monotonic() >= next_notice:
            progress(message.format(max(0, int(remaining))))
            next_notice = time.monotonic() + 10
        if cancel:
            cancel.wait(min(interval, remaining))
        else:
            time.sleep(min(interval, remaining))


def vm_status(api):
    """(enabled, connected) for the linked GNS3 VM; enabled is None if unknown."""
    enabled = None
    try:
        settings = api.call('/v2/gns3vm')
        if isinstance(settings, dict) and 'enable' in settings:
            enabled = bool(settings['enable'])
    except Exception:
        pass
    try:
        connected = any(c.get('compute_id') == 'vm' and c.get('connected') is True for c in api.call('/v2/computes'))
    except Exception:
        connected = False
    return enabled, connected


def vm_last_error(api):
    """GNS3's own recorded reason for a failed GNS3 VM start, if any."""
    try:
        for compute in api.call('/v2/computes'):
            if compute.get('compute_id') == 'vm' and compute.get('last_error'):
                return str(compute['last_error'])[:500]
    except Exception:
        pass
    return None


def prepare(api_factory, launch, progress=None, cancel=None, gui_timeout=None, vm_timeout=None, needs_vm=True,
            is_running=gui_running, find_executable=gui_executable, start=launch_gui, record=None, window_titles=None):
    """Wrapper that returns the screen to the user's window after a launch, on any outcome."""
    record = {} if record is None else record
    state = {}
    from . import winfocus
    winfocus.remember_target(winfocus.snapshot())
    try:
        return _prepare(api_factory, launch, progress, cancel, gui_timeout, vm_timeout, needs_vm,
                        is_running, find_executable, start, record, state, window_titles)
    finally:
        if 'before' in state:
            from . import winfocus
            record['focus_restore'] = winfocus.restore(state['before'])


STABLE_CHECKS = 5


def _launch_settled(reachable, window_titles, state):
    """One polling step: the API object once the window exists and the server answered STABLE_CHECKS times in a row."""
    titles = window_titles()
    state['window_titles'] = titles
    api = reachable()
    if not api or titles == []:            # None: windows cannot be listed here, rely on the server checks
        state['stable'] = 0
        return None
    state['stable'] = state.get('stable', 0) + 1
    return api if state['stable'] >= STABLE_CHECKS else None


def _prepare(api_factory, launch, progress, cancel, gui_timeout, vm_timeout, needs_vm,
             is_running, find_executable, start, record, state, window_titles=None):
    """Make GNS3 ready. Returns a record of what was found and done.

    launch: True to start GNS3 when it is not running; False to only report.
    needs_vm: whether the selected projects have GNS3 VM nodes. If so and the VM
    does not connect within vm_timeout, the run stops with a restart instruction.
    """
    progress = progress or (lambda message: None)
    if window_titles is None:
        from .winfocus import gns3_window_titles as window_titles
    record.update({'gui_running': is_running(), 'launched': False})
    if record['gui_running'] is None:
        # Not Windows or process list unavailable: keep the previous behaviour.
        return record
    if record['gui_running'] is False and launch:
        executable = find_executable()
        if not executable:
            raise ValueError('GNS3가 실행되어 있지 않고 설치된 gns3.exe를 찾지 못했습니다. GNS3를 직접 실행한 뒤 다시 시도하세요.')
        progress('Preflight: GNS3를 실행합니다. 연동 GNS3 VM은 GNS3 설정에 따라 함께 시작됩니다.')
        from . import winfocus
        state['before'] = winfocus.snapshot()
        start(executable)
        record.update(launched=True, executable=str(executable))
    elif record['gui_running'] is False:
        progress('Preflight: GNS3가 실행되어 있지 않습니다. 실제 GNS3 사진은 저장되지 않습니다.')
        return record

    if vm_timeout is None:vm_timeout=90 if record['launched'] else 60
    if gui_timeout is None:gui_timeout=vm_timeout

    def reachable():
        try:
            api = api_factory()
        except Exception as exc:
            record['controller'] = 'initialization_failed'
            record['controller_error_type'] = type(exc).__name__
            raise RuntimeError('GNS3 서버 연결 초기화 실패 (' + type(exc).__name__ +
                               '). 프로그램 파일과 상세 기록을 확인하세요.') from exc
        try:
            api.call('/v2/version')
            return api
        except Exception as exc:
            from urllib.error import HTTPError
            if isinstance(exc, HTTPError) and exc.code in (401, 403):
                raise ValueError('GNS3 서버 인증 확인이 필요합니다 (HTTP ' + str(exc.code) + ').') from exc
            record['controller_error_type'] = type(exc).__name__
            return None
    api = _wait(reachable, gui_timeout if record['launched'] else 5, cancel, progress,
                'Preflight: GNS3 서버 준비를 기다리는 중입니다 ({}초 남음).')
    if not api:
        record['controller'] = 'unreachable'
        if record['launched']:
            raise TimeoutError('GNS3를 실행했지만 서버가 준비되지 않았습니다. GNS3 창의 오류를 확인한 뒤 다시 시도하세요.')
        return record
    if record['launched']:
        # A freshly started GNS3 answers the API before its main window exists and can restart its
        # local server once during start-up (seen as 'connection refused' right after the first answer).
        # Continue only when the window is up and the server stayed reachable for a few seconds.
        settled = _wait(lambda: _launch_settled(reachable, window_titles, state), gui_timeout, cancel, progress,
                        'Preflight: GNS3 창과 서버가 안정될 때까지 기다리는 중입니다 ({}초 남음).', interval=1.0)
        record['launch_settle'] = {'window': state.get('window_titles', []), 'stable_checks': state.get('stable', 0),
                                   'settled': bool(settled)}
        if not settled:
            raise TimeoutError('GNS3를 실행했지만 GNS3 창이나 서버가 안정되지 않았습니다. GNS3 창의 오류를 확인한 뒤 다시 시도하세요.')
        api = settled
    record['controller'] = 'ready'
    enabled, connected = vm_status(api)
    record['gns3_vm'] = {'enabled': enabled, 'connected': connected}
    if enabled and not connected:
        # Short on purpose: a VM that GNS3 did not start is restarted by the user.
        if vm_timeout is None:
            vm_timeout = 90 if record['launched'] else 60
        ok = _wait(lambda: vm_status(api)[1], vm_timeout, cancel, progress,
                   'Preflight: GNS3 VM 연결을 기다리는 중입니다 ({}초 남음).', interval=2.0)
        record['gns3_vm']['connected'] = bool(ok)
        if not ok:
            error = vm_last_error(api)
            record['gns3_vm']['last_error'] = error
            message = ('GNS3 VM이 자동으로 켜지지 않았습니다' + (' (GNS3 기록: ' + error + ')' if error else '') +
                       '. VMware에서 GNS3 VM을 켠 뒤 다시 실행하세요.')
            if needs_vm:
                raise ValueError(message)
            progress('Preflight: ' + message + ' 이번 프로젝트는 VM 장비가 없어 계속 진행합니다.')
    if record['launched']:
        # The fresh GUI shows its startup "Project" chooser; the screenshot step
        # closes it (cancel) before using the idle window.
        record['startup_dialog'] = 'closed by screenshot step when present'
    return record


def projects_need_vm(root, entries):
    """True if any selected project places a node on the GNS3 VM compute."""
    import json
    for entry in entries:
        if entry.get('status') == 'excluded':
            continue
        try:
            data = json.loads((Path(root) / entry['file']).read_text(encoding='utf-8-sig'))
        except (OSError, ValueError, KeyError):
            return True
        if any(n.get('compute_id') == 'vm' for n in data.get('topology', {}).get('nodes', [])):
            return True
    return False
