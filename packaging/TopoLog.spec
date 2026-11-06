# PyInstaller build description for the TopoLog Windows program (one folder).
# Build with tools/build-exe.ps1; it pins PyInstaller and writes outside source.
import os
import ssl
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

source = Path(SPECPATH).resolve().parent
tools = source / 'tools'
package = tools / 'topolog'
capture_tools = ['capture-gns3.ps1', 'open-gns3-project.ps1', 'export-gns3-screenshot.ps1', 'invoke-gns3-action.ps1',
                 'dialog-filename.ps1', 'project-paths.ps1', 'TopoLogWindowCapture.cs', 'annotate-topology.ps1']
datas = [(str(source / 'VERSION'), '.')]
# Some Python distributions load an additional CA policy file in SSLContext.
# Keep that policy intact in the frozen program; do not disable TLS validation.
ca_policy = Path(ssl.__file__).with_name('codex-rejected-cas.pem')
if ca_policy.is_file():
    datas.append((str(ca_policy), '.'))
datas += [(str(tools / name), 'tools') for name in capture_tools]
datas += [(str(path), 'topolog/docx_template') for path in sorted((package / 'docx_template').glob('*.xml'))]
datas += [(str(path), 'topolog/assets') for path in sorted((package / 'assets').glob('*.png'))]
for _, target in datas:
    assert not any(part in ('origin', 'work', 'exports', 'handoff', 'release') for part in Path(target).parts)

analysis = Analysis([str(source / 'packaging' / 'topolog_app.py')], pathex=[str(tools)], datas=datas,
                    hiddenimports=collect_submodules('topolog'),
                    excludes=['pytest', 'numpy', 'PIL', 'IPython', 'matplotlib'], noarchive=False)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='TopoLog', console=False,
          icon=None, version=None, upx=False)
collect = COLLECT(exe, analysis.binaries, analysis.datas, upx=False, name='TopoLog')
