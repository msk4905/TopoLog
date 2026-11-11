"""Remove the downloaded-file mark from TopoLog's own PowerShell scripts.

Windows marks every file extracted from a ZIP that came from the internet
(the Zone.Identifier stream, shown as "차단" in the file's properties). Under
the RemoteSigned execution policy PowerShell then refuses those unsigned
scripts, and TopoLog saves no GNS3 picture. Unblocking the ZIP before
extracting avoids it; this does the same afterwards, for the program's own
script files only (the names below, in the program's own tools folder). It
removes nothing else, changes no stored execution policy and needs no administrator
rights. The program file TopoLog.exe itself is not touched.

The PowerShell processes TopoLog starts run with PROCESS_POLICY, so a PC left at
Windows' default (Restricted) still runs these local scripts.
"""
import os
from pathlib import Path

# The files TopoLog hands to PowerShell (the same list packaging/TopoLog.spec bundles).
SCRIPTS = ('capture-gns3.ps1', 'open-gns3-project.ps1', 'export-gns3-screenshot.ps1', 'invoke-gns3-action.ps1',
           'dialog-filename.ps1', 'project-paths.ps1', 'TopoLogWindowCapture.cs', 'annotate-topology.ps1')
STREAM = ':Zone.Identifier'
# Execution policy for the PowerShell processes TopoLog itself starts (Process scope only):
# the PC's own setting is not changed, it is not Bypass (internet-marked unsigned scripts
# still stop), and a Group Policy setting still takes precedence.
PROCESS_POLICY = ('-ExecutionPolicy', 'RemoteSigned')


def _windows():
    return os.name == 'nt'


def unblock(folder=None):
    """{'unblocked': [...], 'failed': [(name, reason)]} for the bundled scripts; empty off Windows."""
    result = {'unblocked': [], 'failed': []}
    if not _windows():
        return result
    if folder is None:
        from .resources import tool_directory
        folder = tool_directory()
    folder = Path(folder)
    for name in SCRIPTS:
        path = folder / name
        if not path.is_file() or path.is_symlink():
            continue
        try:
            os.remove(str(path) + STREAM)
        except FileNotFoundError:
            continue                      # not marked
        except OSError as exc:
            result['failed'].append((name, type(exc).__name__))
            continue
        result['unblocked'].append(name)
    return result


def describe(result):
    """One log line, or '' when nothing was marked."""
    parts = []
    if result['unblocked']:
        parts.append('다운로드 표시(차단)를 해제한 TopoLog 스크립트 {}개: {}'.format(len(result['unblocked']), ', '.join(result['unblocked'])))
    if result['failed']:
        parts.append('차단 해제 실패: ' + ', '.join('{} ({})'.format(n, r) for n, r in result['failed']) +
                     ' · 파일 속성에서 직접 차단 해제하세요.')
    return ' · '.join(parts)
