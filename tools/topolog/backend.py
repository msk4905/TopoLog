"""Discover a local GNS3 backend without treating a remote VM as local."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess


def launch_environment(executable):
    """Expose installed helper binaries to a directly launched Windows server.

    GNS3's GUI normally supplies this environment. Keep it process-local so
    collecting a lab never edits the user's PATH or GNS3 installation.
    """
    environment = os.environ.copy()
    root = Path(executable).resolve().parent
    helpers = []
    # Same helper folders that the GNS3 2.2 Windows GUI prepends before it
    # starts its local server (gns3/main.py frozen_dirs), plus QEMU.
    for relative, binary in (('dynamips', 'dynamips.exe'),
                             ('ubridge', 'ubridge.exe'), ('vpcs', 'vpcs.exe'),
                             ('traceng', 'traceng.exe'), ('qemu', 'qemu-system-x86_64.exe'),
                             ('.', 'dynamips.exe')):
        directory = root / relative
        if (directory / binary).is_file() and str(directory) not in helpers:
            helpers.append(str(directory))
    if helpers:
        environment['PATH'] = os.pathsep.join(helpers + [environment.get('PATH', '')])
    return environment


def discover():
    addresses = {'127.0.0.1', 'localhost', '::1'}
    executables = []
    try:
        addresses.update(item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None))
    except OSError:
        pass
    command = shutil.which('gns3server')
    if command:
        executables.append(command)
    if os.name == 'nt':
        # Fixed read-only OS metadata query. No user text is interpolated into it.
        script = r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$addresses=@()
$executables=@()
try {
    $addresses=@([Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() | ForEach-Object {
        $_.GetIPProperties().UnicastAddresses | ForEach-Object {$_.Address.IPAddressToString}
    })
} catch { }
try {
    $shell=New-Object -ComObject WScript.Shell
    foreach($root in @([Environment]::GetFolderPath('CommonPrograms'),[Environment]::GetFolderPath('Programs'))) {
        foreach($relative in @('GNS3\GNS3.lnk','GNS3.lnk')) {
            $link=Join-Path $root $relative
            if(Test-Path -LiteralPath $link) {
                $target=$shell.CreateShortcut($link).TargetPath
                if($target) {
                    $server=Join-Path (Split-Path -Parent $target) 'gns3server.exe'
                    if(Test-Path -LiteralPath $server){$executables+=$server}
                }
            }
        }
    }
} catch { }
@{addresses=@($addresses);executables=@($executables)} | ConvertTo-Json -Compress
"""
        try:
            result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script],
                                    capture_output=True, text=True, encoding='utf-8', timeout=20,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            data = json.loads(result.stdout)
            addresses.update(data.get('addresses', []))
            executables.extend(data.get('executables', []))
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        for root in (os.environ.get('ProgramFiles'), os.environ.get('ProgramFiles(x86)'), os.environ.get('LOCALAPPDATA')):
            if root:
                for relative in ('GNS3/gns3server.exe', 'Programs/GNS3/gns3server.exe'):
                    executables.append(str(Path(root) / relative))
    return addresses, [Path(value).resolve() for value in dict.fromkeys(executables) if Path(value).is_file()]


def failure(api, exc):
    from urllib.error import HTTPError
    if isinstance(exc, HTTPError) and exc.code in (401, 403):
        return 'GNS3 authentication/permission failed at {} (HTTP {}). Check the saved server authentication.'.format(api.base, exc.code)
    reason = getattr(exc, 'reason', exc)
    return 'Cannot reach GNS3 at {}: {}. Check the backend process and server binding.'.format(api.base, reason)
