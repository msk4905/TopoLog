#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$ProjectName='20260827-1',
    [string]$NodeName='R1',
    [ValidateSet('Native','PrintWindow','Screen')][string]$CaptureMethod='Native',
    [switch]$OpenReport
)

$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'project-paths.ps1')
$projectPaths=Get-TopoLogPaths -SourceRoot (Split-Path -Parent $PSScriptRoot)
$root=$projectPaths.DataRoot
$stamp=(Get-Date -Format 'yyyyMMdd_HHmmss')+'_'+[guid]::NewGuid().ToString('N').Substring(0,8)
$runDirectory=Join-Path $root ('work\run_'+$stamp)
[IO.Directory]::CreateDirectory($runDirectory)|Out-Null
$run=[ordered]@{
    started_at=[DateTimeOffset]::Now.ToString('o'); finished_at=$null
    project_name=$ProjectName; node_name=$NodeName; stage='server_probe'; status='running'
    capture_record='capture/capture.json'; collection_record='console/collection.json'
    project_record='project/snapshot.json'
    export_record='export.json'; export_zip=$null; preview=$null; error=$null
    visual_review='pending'
}
try {
    Write-Host '[1/5] Checking the GNS3 project and nodes...'
    $probePath=Join-Path $runDirectory 'probe.json'
    & (Join-Path $PSScriptRoot 'probe-gns3.ps1') -ProjectName $ProjectName -OutputPath $probePath
    $probe=Get-Content -LiteralPath $probePath -Raw -Encoding UTF8 | ConvertFrom-Json
    if(-not $probe.projects_read -or -not $probe.target_project_found -or -not $probe.nodes_read) { throw 'The GNS3 project could not be queried. Check the probe record.' }

    $run.stage='project_snapshot'
    Write-Host '[2/5] Reading the current project and saved startup files...'
    $snapshotDirectory=Join-Path $runDirectory 'project'
    & (Join-Path $PSScriptRoot 'collect-project.ps1') -ProbePath $probePath -OutputDirectory $snapshotDirectory

    $run.stage='topology_screenshot'
    Write-Host ('[3/5] Exporting GNS3 topology screenshot ('+$CaptureMethod+')...')
    $captureDirectory=Join-Path $runDirectory 'capture'
    & (Join-Path $PSScriptRoot 'capture-gns3.ps1') -ProjectName $ProjectName -Method $CaptureMethod -OutputDirectory $captureDirectory
    $capturePath=Join-Path $captureDirectory 'capture.json'
    $capture=Get-Content -LiteralPath $capturePath -Raw -Encoding UTF8 | ConvertFrom-Json
    if($capture.capture_status -ne 'captured') { throw ('Automatic window capture did not succeed: '+$capture.error) }

    $run.stage='console_collection'
    Write-Host '[4/5] Collecting fixed show commands...'
    $consoleDirectory=Join-Path $runDirectory 'console'
    & (Join-Path $PSScriptRoot 'collect-console.ps1') -NodeName $NodeName -ProbePath $probePath -OutputDirectory $consoleDirectory
    $collection=Get-Content -LiteralPath (Join-Path $consoleDirectory 'collection.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if(-not $collection.collection_complete) { throw ('Console collection did not finish: '+$collection.error) }

    $run.stage='evidence_export'
    Write-Host '[5/5] Building the report and evidence ZIP...'
    $resultPath=Join-Path $runDirectory 'export.json'
    & (Join-Path $PSScriptRoot 'export-evidence.ps1') -CollectionDirectory $consoleDirectory -ProjectSnapshotDirectory $snapshotDirectory `
        -TopologyImagePath (Join-Path $captureDirectory $capture.image_file) `
        -TopologyImageSource 'GNS3 automatic window capture' -TopologyCaptureMetadataPath $capturePath -ResultPath $resultPath
    $result=Get-Content -LiteralPath $resultPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if(-not (Test-Path -LiteralPath $result.export_zip)) { throw 'The export ZIP was not created.' }
    $run.export_zip=$result.export_zip
    $run.preview=$result.html_preview
    $run.stage='finished'
    $run.status='completed_pending_visual_review'
    Write-Host 'Collection and export finished. Review the captured image in the report.'
    Write-Host ('Report: '+$result.html_preview)
    Write-Host ('ZIP: '+$result.export_zip)
    if($OpenReport) { Invoke-Item -LiteralPath $result.html_preview }
} catch {
    $run.status='failed'
    $run.error=$_.Exception.Message
    Write-Warning $run.error
} finally {
    $run.finished_at=[DateTimeOffset]::Now.ToString('o')
    [IO.File]::WriteAllText((Join-Path $runDirectory 'run.json'),($run|ConvertTo-Json -Depth 6),[Text.UTF8Encoding]::new($false))
    Write-Host ('Run record: '+(Join-Path $runDirectory 'run.json'))
}
if($run.status -eq 'failed') { exit 1 }
