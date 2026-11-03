#requires -Version 5.1
$ErrorActionPreference='Stop'
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'tools\project-paths.ps1')
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('topolog-paths-'+[guid]::NewGuid().ToString('N'))
$source=Join-Path $fixture 'source'
[IO.Directory]::CreateDirectory($source)|Out-Null

function Assert-DataRoot([string]$Expected) {
    $actual=Get-TopoLogPaths -SourceRoot $source
    if($actual.SourceRoot -ne $source -or $actual.DataRoot -ne $Expected){throw 'Incorrect source/data root resolution.'}
    foreach($pair in @(@('WorkDirectory','work'),@('ExportDirectory','exports'),@('ReleaseDirectory','release'))) {
        if($actual.($pair[0]) -ne (Join-Path $Expected $pair[1])){throw ('Incorrect path: '+$pair[0])}
    }
}

try {
    Assert-DataRoot $source
    [IO.Directory]::CreateDirectory((Join-Path $fixture 'origin'))|Out-Null
    Assert-DataRoot $source
    [IO.Directory]::CreateDirectory((Join-Path $source 'origin'))|Out-Null
    Assert-DataRoot $source
    [IO.File]::WriteAllText((Join-Path $source '.topolog-workspace.json'),'{"data_directory":".."}')
    Assert-DataRoot $fixture
    Write-Host 'Path resolution passed: standalone, origin presence ignored, explicit output directory.'
} finally {
    # Only the exact newly created temporary fixture may be removed.
    $tempPrefix=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')+'\'
    $resolved=[IO.Path]::GetFullPath($fixture)
    if(-not $resolved.StartsWith($tempPrefix,[StringComparison]::OrdinalIgnoreCase) -or
       [IO.Path]::GetFileName($resolved) -notmatch '^topolog-paths-[a-f0-9]{32}$'){throw 'Unsafe fixture cleanup path.'}
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
