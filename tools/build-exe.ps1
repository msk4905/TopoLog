#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Python='',
    [string]$OutputDirectory='',
    [string]$PyInstallerVersion='6.11.1'
)
# Build the one-folder Windows program with a pinned PyInstaller in a private
# virtual environment under work\. The source tree is not modified; the
# result goes to exports\ and an existing result is never replaced.
$ErrorActionPreference='Stop'
$source=Split-Path -Parent $PSScriptRoot
$workspace=Split-Path -Parent $source
$version=(Get-Content -LiteralPath (Join-Path $source 'VERSION') -Raw -Encoding UTF8).Trim()
if($version -notmatch '^\d+\.\d+\.\d+([.-][A-Za-z0-9.-]+)?$'){throw 'Invalid version.'}
if(-not $OutputDirectory){$OutputDirectory=Join-Path $workspace ('exports\TopoLog-v'+$version+'-windows')}
$OutputDirectory=[IO.Path]::GetFullPath($OutputDirectory)
if(Test-Path -LiteralPath $OutputDirectory){throw ('Output already exists; keep it and choose another folder: '+$OutputDirectory)}
if(-not $Python){
    . (Join-Path $PSScriptRoot 'python-launcher.ps1')
    $Python=@(Get-TopoLogPythonCandidates -SourceRoot $source)[0]
}
if(-not $Python){throw 'Python 3.10+ with tkinter is needed only to build the program.'}
$build=Join-Path $workspace ('work\exe-build-'+$version)
if(Test-Path -LiteralPath $build){Remove-Item -LiteralPath $build -Recurse -Force}
New-Item -ItemType Directory -Path $build | Out-Null
& $Python -c "import sys, tkinter; assert sys.version_info >= (3, 10); print(sys.version)"
if($LASTEXITCODE -ne 0){throw 'The build Python needs tkinter and version 3.10 or newer.'}
& $Python -m venv (Join-Path $build 'venv')
$venvPython=Join-Path $build 'venv\Scripts\python.exe'
& $venvPython -m pip install --disable-pip-version-check --no-input ('pyinstaller=='+$PyInstallerVersion)
if($LASTEXITCODE -ne 0){throw 'PyInstaller install failed.'}
& $venvPython -m PyInstaller --noconfirm --clean --distpath (Join-Path $build 'dist') --workpath (Join-Path $build 'build') (Join-Path $source 'packaging\TopoLog.spec')
if($LASTEXITCODE -ne 0){throw 'PyInstaller build failed.'}
$program=Join-Path $build 'dist\TopoLog'
foreach($required in @('TopoLog.exe','_internal\VERSION','_internal\tools\capture-gns3.ps1','_internal\tools\TopoLogWindowCapture.cs','_internal\topolog\assets\example-gns3-native.png','_internal\topolog\docx_template\styles.xml')){
    if(-not (Test-Path -LiteralPath (Join-Path $program $required))){throw ('Missing in build: '+$required)}
}
$private=@(Get-ChildItem -LiteralPath $program -Recurse -File | Where-Object {$_.FullName -match '\\(origin|handoff|exports|release)\\|\.topolog-workspace\.json$|settings\.json$'})
if($private.Count){throw ('Private file in build: '+$private[0].FullName)}
New-Item -ItemType Directory -Path $OutputDirectory | Out-Null
Copy-Item -LiteralPath $program -Destination (Join-Path $OutputDirectory 'TopoLog') -Recurse
$notes=@(
 'TopoLog '+$version+' Windows 프로그램',
 '',
 '실행: TopoLog 폴더의 TopoLog.exe를 실행합니다. Python을 따로 설치할 필요가 없습니다.',
 'GNS3, GNS3 VM(VMware 등), 장비 이미지는 이 PC에 설치된 것을 그대로 사용합니다.',
 '설정은 %LOCALAPPDATA%\TopoLog\settings.json에 폴더·선택 항목만 저장하며 enable 암호는 저장하지 않습니다.',
 '실행 기록은 %LOCALAPPDATA%\TopoLog\logs에 남습니다.',
 '실제 GNS3 사진 저장은 Windows PowerShell 스크립트를 사용합니다. 인터넷에서 받은 ZIP이라면 풀기 전에 ZIP 파일 속성에서 차단 해제가 필요할 수 있습니다.',
 '이 프로그램은 코드 서명이 되어 있지 않습니다.'
)
[IO.File]::WriteAllLines((Join-Path $OutputDirectory '사용 안내.txt'),$notes,[Text.UTF8Encoding]::new($true))
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip=$OutputDirectory+'.zip'
if(Test-Path -LiteralPath $zip){throw 'ZIP already exists.'}
[IO.Compression.ZipFile]::CreateFromDirectory($OutputDirectory,$zip,[IO.Compression.CompressionLevel]::Optimal,$false)
$hash=(Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
$exeHash=(Get-FileHash -LiteralPath (Join-Path $OutputDirectory 'TopoLog\TopoLog.exe') -Algorithm SHA256).Hash.ToLowerInvariant()
$record=[ordered]@{version=$version;built_at=[DateTimeOffset]::Now.ToString('o');python=(& $venvPython -c "import sys;print(sys.version)");pyinstaller=$PyInstallerVersion;
    signed=$false;zip=[IO.Path]::GetFileName($zip);zip_sha256=$hash;exe_sha256=$exeHash}
[IO.File]::WriteAllText((Join-Path $workspace ('work\exe-build-'+$version+'.json')),($record|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
Write-Host ('Program: '+(Join-Path $OutputDirectory 'TopoLog\TopoLog.exe'))
Write-Host ('ZIP: '+$zip+'  sha256 '+$hash)
