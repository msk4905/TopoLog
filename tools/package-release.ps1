#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidatePattern('^\d+\.\d+\.\d+([.-][A-Za-z0-9.-]+)?$')][string]$Version='',
    [string]$OutputDirectory=''
)

$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem
$root=Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'project-paths.ps1')
$projectPaths=Get-TopoLogPaths -SourceRoot $root
if(-not $Version){$Version=(Get-Content -LiteralPath (Join-Path $root 'VERSION') -Raw -Encoding UTF8).Trim()}
if($Version -notmatch '^\d+\.\d+\.\d+([.-][A-Za-z0-9.-]+)?$'){throw 'Invalid version.'}
$release=[IO.Path]::GetFullPath((Join-Path $projectPaths.ReleaseDirectory ('TopoLog-v'+$Version)))
if($OutputDirectory){$release=[IO.Path]::GetFullPath($OutputDirectory)}
if(-not $release.StartsWith(([IO.Path]::GetFullPath($projectPaths.DataRoot).TrimEnd('\')+'\'),[StringComparison]::OrdinalIgnoreCase)){throw 'Release directory is outside the workspace.'}
if(Test-Path -LiteralPath $release){throw 'A release directory already exists. Preserve it and choose a new version or move the previous generated release first.'}
$stage=Join-Path $release 'TopoLog'
[IO.Directory]::CreateDirectory($stage)|Out-Null
$files=@()
foreach($name in @('README.md','VERSION','CHANGELOG.md','RELEASE_NOTES.md','VALIDATION.md','BATCH.md','TopoLog-Batch.cmd','TopoLog-Report.cmd','TopoLog.cmd','.gitignore')){
    $files+=Get-Item -LiteralPath (Join-Path $root $name)
}
foreach($folder in @('tools','tests','docs','examples','packaging')){
    $files+=Get-ChildItem -LiteralPath (Join-Path $root $folder) -Recurse -Force -File | Where-Object {$_.FullName -notmatch '\\(__pycache__|node_modules)\\'}
}
foreach($file in $files){
    $relative=$file.FullName.Substring($root.Length+1)
    if($relative -match '(^|\\)(origin|work|exports|demo-site|\.git|\.openai)(\\|$)' -or $file.Name -like '.env*'){throw ('Unexpected private/local file: '+$relative)}
    $destination=Join-Path $stage $relative
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination))|Out-Null
    Copy-Item -LiteralPath $file.FullName -Destination $destination
}
$sourceZip=Join-Path $release ('TopoLog-v'+$Version+'-source.zip')
$demoZip=Join-Path $release ('TopoLog-v'+$Version+'-demo.zip')
[IO.Compression.ZipFile]::CreateFromDirectory($stage,$sourceZip,[IO.Compression.CompressionLevel]::Optimal,$true)
[IO.Compression.ZipFile]::CreateFromDirectory((Join-Path $root 'docs'),$demoZip,[IO.Compression.CompressionLevel]::Optimal,$false)
Copy-Item -LiteralPath (Join-Path $root 'RELEASE_NOTES.md') -Destination (Join-Path $release 'RELEASE_NOTES.md')
$hashes=@($sourceZip,$demoZip)|ForEach-Object{((Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant())+'  '+[IO.Path]::GetFileName($_)}
[IO.File]::WriteAllText((Join-Path $release 'SHA256SUMS.txt'),($hashes -join "`n")+"`n",[Text.UTF8Encoding]::new($false))
Write-Host ('Upload directory: '+$stage)
Write-Host ('Source ZIP: '+$sourceZip)
Write-Host ('Demo ZIP: '+$demoZip)
