"""Return the screen to the window the user was using after TopoLog launched GNS3/VM.

Only a window that TopoLog's own action brought forward is replaced, and only
by the window that was in front just before that action. If the user has
switched to something else meanwhile, nothing is changed. Windows may refuse a
foreground change; that is recorded, never forced.
"""
import os
import threading

_capture_context = threading.local()


def remember_target(previous):
    """Keep a non-GNS3 target for this collection thread, not across runs."""
    _capture_context.target = previous if previous and previous.get('exe') and previous.get('exe') not in PROGRAMS else None


def capture_target():
    current = snapshot()
    if current and current.get('exe') and current.get('exe') not in PROGRAMS:
        _capture_context.target = current
    return getattr(_capture_context, 'target', None)

PROGRAMS = {'gns3.exe', 'vmware.exe', 'vmplayer.exe', 'vmware-vmx.exe', 'virtualbox.exe', 'virtualboxvm.exe'}


def _user32():
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    return ctypes, wintypes, user32


def _process_name(pid):
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ''
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return os.path.basename(buffer.value).lower()
        return ''
    finally:
        kernel32.CloseHandle(handle)


def gns3_window_titles():
    """Titles of visible top-level windows owned by gns3.exe; None when this cannot be checked."""
    if os.name != 'nt':
        return None
    try:
        ctypes, wintypes, user32 = _user32()
        titles = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def visit(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if _process_name(pid.value) == 'gns3.exe':
                    buffer = ctypes.create_unicode_buffer(512)
                    user32.GetWindowTextW(hwnd, buffer, 512)
                    if buffer.value.strip():
                        titles.append(buffer.value.strip())
            return True
        user32.EnumWindows(callback_type(visit), 0)
        return titles
    except Exception:
        return None


VMWARE_PROGRAMS = ('vmware.exe', 'vmplayer.exe')
GNS3_PROGRAMS = ('gns3.exe', 'gns3server.exe')


def program_windows(names):
    """Handles of visible top-level windows owned by the named programs; None when this cannot be checked."""
    if os.name != 'nt':
        return None
    try:
        ctypes, wintypes, user32 = _user32()
        found = set()
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def visit(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if _process_name(pid.value) in names:
                    found.add(int(hwnd))
            return True
        user32.EnumWindows(callback_type(visit), 0)
        return found
    except Exception:
        return None


def minimize(handles):
    """Minimize the given windows without activating anything; returns how many were minimized."""
    if os.name != 'nt' or not handles:
        return 0
    try:
        ctypes, wintypes, user32 = _user32()
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        done = 0
        for handle in handles:
            if user32.IsWindow(handle) and not user32.IsIconic(handle):
                user32.ShowWindow(handle, 7)   # SW_SHOWMINNOACTIVE: minimized, focus stays where it is
                done += 1
        return done
    except Exception:
        return 0


def snapshot():
    """{'handle','pid','exe'} of the current foreground window, or None off Windows."""
    if os.name != 'nt':
        return None
    try:
        ctypes, wintypes, user32 = _user32()
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return {'handle': int(hwnd), 'pid': int(pid.value), 'exe': _process_name(int(pid.value))}
    except (OSError, AttributeError, ValueError):
        return None


def decide(previous, current, programs=PROGRAMS, previous_exists=True):
    """Pure decision used by restore(); kept separate for tests."""
    if not previous:
        return 'no_previous'
    if not previous_exists:
        return 'previous_closed'
    if current and current['handle'] == previous['handle']:
        return 'unchanged'
    if previous.get('exe') in programs:
        return 'previous_was_gns3_or_vm'
    if current and current.get('exe') not in programs:
        return 'user_switched'
    return 'restore'


def restore(previous, programs=PROGRAMS):
    if os.name != 'nt':
        return 'not_windows'
    try:
        ctypes, wintypes, user32 = _user32()
        exists = bool(previous) and bool(user32.IsWindow(previous['handle']))
        action = decide(previous, snapshot(), programs, exists)
        if action != 'restore':
            return action
        if user32.IsIconic(previous['handle']):
            return 'previous_minimized'
        user32.SetForegroundWindow(previous['handle'])
        now = snapshot()
        if now and now['handle'] != previous['handle'] and now.get('exe') in programs:
            # Temporarily share the foreground input queue; detach on every path.
            # No synthetic input, permanent topmost flag or retry loop is used.
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            own_thread = kernel32.GetCurrentThreadId()
            pid = wintypes.DWORD()
            foreground_thread = user32.GetWindowThreadProcessId(now['handle'], ctypes.byref(pid))
            attached = bool(own_thread != foreground_thread and user32.AttachThreadInput(own_thread, foreground_thread, True))
            try:
                current = snapshot()
                if current and current['handle'] == now['handle']:
                    user32.BringWindowToTop(previous['handle'])
                    user32.SetForegroundWindow(previous['handle'])
            finally:
                if attached:
                    user32.AttachThreadInput(own_thread, foreground_thread, False)
            now = snapshot()
        return 'restored' if now and now['handle'] == previous['handle'] else 'os_refused'
    except (OSError, AttributeError, ValueError):
        return 'failed'
