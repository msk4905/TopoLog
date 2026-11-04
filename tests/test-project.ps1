#requires -Version 5.1
# Synthetic API responses; no live server or development reference files used.
param([string]$PythonExecutable='')
$ErrorActionPreference='Stop'
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('topolog-project-'+[guid]::NewGuid().ToString('N'))
$tools=Join-Path $fixture 'tools'
[IO.Directory]::CreateDirectory($tools)|Out-Null
$sourceTools=Join-Path (Split-Path -Parent $PSScriptRoot) 'tools'
foreach($file in @('collect-project.ps1','gns3-api.ps1','project-paths.ps1','export-evidence.ps1')){Copy-Item -LiteralPath (Join-Path $sourceTools $file) -Destination $tools}
$utf8=[Text.UTF8Encoding]::new($false)
$projectId='00000000-0000-0000-0000-000000000001'
$nodeId='00000000-0000-0000-0000-000000000002'
$missingId='00000000-0000-0000-0000-000000000003'
$badPathId='00000000-0000-0000-0000-000000000004'
$unsupportedId='00000000-0000-0000-0000-000000000005'
$base='http://127.0.0.1:39999'
$global:TopoLogProjectTestState=@{Prefix=$base+'/v2/projects/'+$projectId;FailTopology=$false;Requests=[Collections.Generic.List[string]]::new()}
function Invoke-RestMethod {
    param($Uri,$Headers,$Method,$TimeoutSec,$ErrorAction)
    if($Method -ne 'Get' -or $Headers.ContainsKey('Authorization')){throw 'Unexpected method or credentials sent to test server.'}
    $global:TopoLogProjectTestState.Requests.Add([string]$Uri)
    if([string]$Uri -eq $global:TopoLogProjectTestState.Prefix){return [pscustomobject]@{name='Synthetic';project_id=$projectId;status='opened'}}
    if([string]$Uri -eq ($global:TopoLogProjectTestState.Prefix+'/nodes')) {
            return @(
                [pscustomobject]@{node_id=$nodeId;name='R1';node_type='dynamips';x=0;y=0;width=60;height=40;properties=@{startup_config='configs/i1_startup-config.cfg'}},
                [pscustomobject]@{node_id=$missingId;name='R2';node_type='dynamips';x=200;y=0;width=60;height=40;properties=@{startup_config='configs/missing.cfg'}},
                [pscustomobject]@{node_id=$badPathId;name='R3';node_type='dynamips';x=0;y=200;width=60;height=40;properties=@{startup_config='../outside.cfg'}},
                [pscustomobject]@{node_id=$unsupportedId;name='PC1';node_type='vpcs';x=200;y=200;width=60;height=40;properties=@{}}
            )
    }
    if([string]$Uri -eq ($global:TopoLogProjectTestState.Prefix+'/links')){if($global:TopoLogProjectTestState.FailTopology){throw 'Synthetic topology failure'};return @()}
    if([string]$Uri -eq ($global:TopoLogProjectTestState.Prefix+'/drawings')){return @()}
    throw ('Unexpected endpoint: '+$Uri)
}
function Invoke-WebRequest {
    param($Uri,$Headers,$Method,$TimeoutSec,[switch]$UseBasicParsing,$OutFile,$ErrorAction)
    if($Method -ne 'Get' -or $Headers.ContainsKey('Authorization')){throw 'Unexpected method or credentials.'}
    $global:TopoLogProjectTestState.Requests.Add([string]$Uri)
    if($Uri -eq ($global:TopoLogProjectTestState.Prefix+'/nodes/'+$nodeId+'/files/configs/i1_startup-config.cfg')){
        [IO.File]::WriteAllText($OutFile,"! Synthetic configuration`nhostname R1`n",$utf8)
    }elseif($Uri -eq ($global:TopoLogProjectTestState.Prefix+'/nodes/'+$missingId+'/files/configs/missing.cfg')){throw 'Synthetic 404'}
    else{throw ('Unexpected config endpoint: '+$Uri)}
}
try {
    $probePath=Join-Path $fixture 'probe.json'
    $probe=@{target_project_found=$true;nodes_read=$true;target_project_name='Synthetic';base_uri=$base;target_project=@{project_id=$projectId}}
    [IO.File]::WriteAllText($probePath,($probe|ConvertTo-Json),$utf8)
    $snapshotDirectory=Join-Path $fixture 'project'
    & (Join-Path $tools 'collect-project.ps1') -ProbePath $probePath -OutputDirectory $snapshotDirectory
    $snapshot=Get-Content (Join-Path $snapshotDirectory 'snapshot.json') -Raw -Encoding UTF8|ConvertFrom-Json
    if(@($snapshot.configurations|Where-Object status -eq 'collected').Count -ne 1 -or
       @($snapshot.configurations|Where-Object status -eq 'not_collected').Count -ne 3){throw 'Incorrect configuration collection statuses.'}
    if(@($global:TopoLogProjectTestState.Requests|Where-Object {$_ -match '\.\.|outside'}).Count){throw 'Unsafe configuration path was requested.'}

    $console=Join-Path $fixture 'console'
    [IO.Directory]::CreateDirectory($console)|Out-Null
    $now=[DateTimeOffset]::Now.ToString('o')
    [IO.File]::WriteAllText((Join-Path $console 'show.txt'),"Synthetic output only`nR1#`n",$utf8)
    $collection=@{project_name='Synthetic';project_id=$projectId;node_name='R1';node_id=$nodeId;collection_complete=$true;collection_started_at=$now;finished_at=$now;commands=@(@{command='show ip route';readable_text_file='show.txt';raw_text_file='show.txt';cli_error_detected=$false;started_at=$now;collection_status='collected'})}
    [IO.File]::WriteAllText((Join-Path $console 'collection.json'),($collection|ConvertTo-Json -Depth 6),$utf8)
    $resultPath=Join-Path $fixture 'result.json'
    & (Join-Path $tools 'export-evidence.ps1') -CollectionDirectory $console -ProjectSnapshotDirectory $snapshotDirectory -ResultPath $resultPath
    $result=Get-Content $resultPath -Raw -Encoding UTF8|ConvertFrom-Json
    $manifest=Get-Content (Join-Path $result.export_directory 'manifest.json') -Raw -Encoding UTF8|ConvertFrom-Json
    if($manifest.project_source -ne 'GNS3 controller API v2 GET' -or @($manifest.config_sources|Where-Object status -eq 'not_collected').Count -ne 3){throw 'Report lost provenance or missing-config status.'}
    $report=Get-Content (Join-Path $result.export_directory 'report.md') -Raw -Encoding UTF8
    if($report -match 'origin/|GNS3_Analysis_With_History' -or $report -notmatch 'R2: not_collected'){throw 'Report depends on reference input or hides missing configurations.'}
    if($PythonExecutable) {
        & $PythonExecutable (Join-Path $PSScriptRoot 'test-export.py') $result.export_directory
        if($LASTEXITCODE -ne 0){throw 'Export integrity check failed.'}
    }

    $topologyPath=Join-Path $snapshotDirectory 'topology.json'
    [IO.File]::AppendAllText($topologyPath,' ',$utf8)
    $rejected=$false
    try{& (Join-Path $tools 'export-evidence.ps1') -CollectionDirectory $console -ProjectSnapshotDirectory $snapshotDirectory}catch{if($_.Exception.Message -notmatch 'hash mismatch'){throw};$rejected=$true}
    if(-not $rejected){throw 'Tampered topology accepted.'}
    $global:TopoLogProjectTestState.FailTopology=$true
    $rejected=$false
    try{& (Join-Path $tools 'collect-project.ps1') -ProbePath $probePath -OutputDirectory (Join-Path $fixture 'failed')}catch{if($_.Exception.Message -notmatch 'Synthetic topology failure'){throw};$rejected=$true}
    if(-not $rejected -or (Test-Path (Join-Path $fixture 'failed\snapshot.json'))){throw 'Incomplete topology incorrectly recorded as complete.'}
    Write-Host 'PASS: isolated collection/export without origin, missing configs, path rejection, tampered snapshot and API failure.'
} finally {
    Remove-Variable -Name TopoLogProjectTestState -Scope Global -ErrorAction SilentlyContinue
    $tempPrefix=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')+'\'
    $resolved=[IO.Path]::GetFullPath($fixture)
    if(-not $resolved.StartsWith($tempPrefix,[StringComparison]::OrdinalIgnoreCase) -or [IO.Path]::GetFileName($resolved) -notmatch '^topolog-project-[a-f0-9]{32}$'){throw 'Unsafe fixture cleanup path.'}
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
