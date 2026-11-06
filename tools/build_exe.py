"""Build the one-folder Windows program with a pinned PyInstaller.

Usage (Windows, any Python 3.10+ with tkinter):  python tools\\build_exe.py
A private virtual environment is created under work\\, the source tree is not
modified, and the result goes to exports\\TopoLog-v<version>-windows (+ .zip).
An existing result is never deleted: with --replace it is moved to
work\\superseded-exe-<version>-<time> before the new build is written.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone

PYINSTALLER = '6.11.1'
SOURCE = Path(__file__).resolve().parents[1]
WORKSPACE = SOURCE.parent
REQUIRED = ['TopoLog.exe', '_internal/VERSION', '_internal/tools/capture-gns3.ps1', '_internal/tools/TopoLogWindowCapture.cs',
            '_internal/tools/dialog-filename.ps1', '_internal/tools/annotate-topology.ps1', '_internal/topolog/assets/example-gns3-native.png',
            '_internal/topolog/docx_template/styles.xml']
PRIVATE = ('origin', 'handoff', 'exports', 'release', 'work')
NOTES = '''TopoLog {version} Windows 프로그램

실행: TopoLog 폴더의 TopoLog.exe를 실행합니다. Python을 따로 설치할 필요가 없습니다.
설치 과정은 없습니다. 폴더를 원하는 위치에 두고 실행하면 되고, 지울 때는 폴더를 삭제합니다.
GNS3, GNS3 VM(VMware 등), 장비 이미지는 이 PC에 설치된 것을 그대로 사용합니다.
설정은 %LOCALAPPDATA%\\TopoLog\\settings.json에 폴더와 선택 항목만 저장하며 enable 암호는 저장하지 않습니다.
실행 기록은 %LOCALAPPDATA%\\TopoLog\\logs에 남습니다.
실제 GNS3 사진 저장은 함께 들어 있는 Windows PowerShell 스크립트를 사용합니다.
인터넷에서 받은 ZIP이라면 풀기 전에 ZIP 파일 속성에서 차단 해제가 필요할 수 있습니다.
이 프로그램은 코드 서명이 되어 있지 않습니다.
'''


def run(command, **kwargs):
    print('>', ' '.join(str(c) for c in command), flush=True)
    subprocess.run([str(c) for c in command], check=True, **kwargs)


def main():
    # ensurepip must also work when the host's default temp folder is restricted.
    temp = WORKSPACE / 'work' / 'exe-temp'
    temp.mkdir(parents=True, exist_ok=True)
    os.environ.update(TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp))
    version = (SOURCE / 'VERSION').read_text(encoding='utf-8').strip()
    target = WORKSPACE / 'exports' / ('TopoLog-v' + version + '-windows')
    archive = Path(str(target) + '.zip')
    if target.exists() or archive.exists():
        if '--replace' not in sys.argv[1:]:
            raise SystemExit('Output already exists; keep it and remove or rename it first, or pass --replace: ' + str(target))
        kept = WORKSPACE / 'work' / ('superseded-exe-' + version + '-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
        kept.mkdir(parents=True)
        # Same-volume rename only: shutil.move would fall back to copy+delete and,
        # with a running TopoLog.exe locking its DLLs, leave a half-deleted folder.
        for item in (target, archive):
            if item.exists():
                try:
                    os.rename(item, kept / item.name)
                except OSError as exc:
                    raise SystemExit('Could not move the previous output (close every running TopoLog.exe first): '
                                     + str(item) + ' (' + str(exc) + ')')
        print('Previous output moved to', kept, flush=True)
    import tkinter  # noqa: F401  the program needs it in the build interpreter
    build = WORKSPACE / 'work' / ('exe-build-' + version)
    if build.exists():
        os.rename(build, build.with_name(build.name + '-previous-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f')))
    build.mkdir(parents=True)
    run([sys.executable, '-m', 'venv', build / 'venv'])
    python = build / 'venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    run([python, '-m', 'pip', 'install', '--disable-pip-version-check', '--no-input', 'pyinstaller==' + PYINSTALLER])
    run([python, '-m', 'PyInstaller', '--noconfirm', '--clean', '--distpath', build / 'dist', '--workpath', build / 'build',
         SOURCE / 'packaging' / 'TopoLog.spec'], cwd=build)   # PyInstaller refuses to run from the System32 folder
    program = build / 'dist' / 'TopoLog'
    executable = 'TopoLog.exe' if os.name == 'nt' else 'TopoLog'
    for name in REQUIRED:
        name = executable if name == 'TopoLog.exe' else name
        if not (program / name).exists():
            raise SystemExit('Missing in build: ' + name)
    for path in program.rglob('*'):
        relative = path.relative_to(program).parts
        if any(p in PRIVATE for p in relative) or path.name in ('settings.json', '.topolog-workspace.json'):
            raise SystemExit('Private file in build: ' + str(path))
    target.mkdir(parents=True)
    shutil.copytree(program, target / 'TopoLog')
    (target / '사용 안내.txt').write_text(NOTES.format(version=version), encoding='utf-8-sig')
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(target.rglob('*')):
            if path.is_file():
                bundle.write(path, path.relative_to(target).as_posix())
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    built_python = subprocess.run([str(python), '-c', 'import sys;print(sys.version)'], capture_output=True, text=True).stdout.strip()
    record = {'version': version, 'built_at': datetime.now(timezone.utc).isoformat(), 'python': built_python,
              'pyinstaller': PYINSTALLER, 'signed': False, 'program': str(target / 'TopoLog' / executable),
              'zip': archive.name, 'zip_sha256': digest(archive), 'exe_sha256': digest(target / 'TopoLog' / executable),
              'files': sum(1 for p in target.rglob('*') if p.is_file())}
    (WORKSPACE / 'work' / ('exe-build-' + version + '.json')).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
