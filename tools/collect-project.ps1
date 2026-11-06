#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ProbePath,
    [Parameter(Mandatory=$true)][string]$OutputDirectory
)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'gns3-api.ps1')
$probe=Get-Content -LiteralPath $ProbePath -Raw -Encoding UTF8 | ConvertFrom-Json
if(-not $probe.target_project_found -or -not $probe.nodes_read){throw 'A successful project probe is required.'}
$projectId=[string]$probe.target_project.project_id
$parsedId=[guid]::Empty
if(-not [guid]::TryParse($projectId,[ref]$parsedId)){throw 'Invalid project ID.'}
$connection=New-TopoLogGns3Connection -BaseUri ([uri]$probe.base_uri)
$outDir=[IO.Path]::GetFullPath($OutputDirectory)
if(Test-Path -LiteralPath $outDir){throw 'Choose a new snapshot directory; previous evidence is not overwritten.'}
[IO.Directory]::CreateDirectory((Join-Path $outDir 'configs'))|Out-Null
$utf8=[Text.UTF8Encoding]::new($false)
$started=[DateTimeOffset]::Now.ToString('o')
$requests=[Collections.Generic.List[object]]::new()
$configs=[Collections.Generic.List[object]]::new()
$prefix='/v2/projects/'+$projectId

function Read-ProjectJson([string]$Endpoint) {
    $data=Invoke-RestMethod -Uri ($connection.BaseUri+$Endpoint) -Headers $connection.Headers -Method Get -TimeoutSec 15 -ErrorAction Stop
    $requests.Add([pscustomobject]@{endpoint=$Endpoint; collected_at=[DateTimeOffset]::Now.ToString('o')})
    return $data
}

$project=Read-ProjectJson $prefix
if($project.project_id -ne $projectId -or $project.name -ne $probe.target_project_name -or $project.status -ne 'opened'){throw 'The project changed or is not open. Run the probe again.'}
$nodes=@(Read-ProjectJson ($prefix+'/nodes'))
$links=@(Read-ProjectJson ($prefix+'/links'))
$drawings=@(Read-ProjectJson ($prefix+'/drawings'))
if(-not $nodes.Count){throw 'The open project contains no nodes.'}
$topology=[ordered]@{schema='topolog-api-topology-v1'; name=$project.name; project_id=$projectId; topology=[ordered]@{nodes=$nodes;links=$links;drawings=$drawings}}
[IO.File]::WriteAllText((Join-Path $outDir 'topology.json'),($topology|ConvertTo-Json -Depth 50),$utf8)

foreach($node in $nodes) {
    $record=[ordered]@{node_id=$node.node_id;node_name=$node.name;kind='saved_startup_config';status='not_collected';reason=$null;endpoint=$null;file=$null;sha256=$null;collected_at=$null}
    $configPath=[string]$node.properties.startup_config
    if($node.node_type -ne 'dynamips'){$record.reason='This prototype collects saved startup files for Dynamips only.'}
    elseif(-not $configPath){$record.reason='The node does not expose a saved startup configuration path.'}
    else {
        $parts=@($configPath.Replace('\','/').Split('/'))
        $nodeGuid=[guid]::Empty
        if(-not [guid]::TryParse([string]$node.node_id,[ref]$nodeGuid) -or
           $parts -contains '..' -or $parts -contains '.' -or $parts -contains '' -or $configPath -match '[:\x00-\x1f]' -or
           [IO.Path]::GetExtension($configPath) -ne '.cfg') {
            $record.reason='The server returned an unsupported configuration path or node ID.'
        } else {
            $encoded=($parts|ForEach-Object{[uri]::EscapeDataString($_)}) -join '/'
            $record.endpoint=$prefix+'/nodes/'+$node.node_id+'/files/'+$encoded
            $relative='configs/'+$node.node_id+'_startup.cfg'
            $destination=Join-Path $outDir $relative
            try {
                $null=Invoke-WebRequest -Uri ($connection.BaseUri+$record.endpoint) -Headers $connection.Headers -Method Get -TimeoutSec 15 -UseBasicParsing -OutFile $destination -ErrorAction Stop
                if((Get-Item -LiteralPath $destination).Length -eq 0){throw 'The saved configuration file is empty.'}
                $record.status='collected';$record.file=$relative
                $record.sha256=(Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
                $record.collected_at=[DateTimeOffset]::Now.ToString('o')
            } catch {
                $record.reason='The saved startup file could not be read from the GNS3 API.'
                if(Test-Path -LiteralPath $destination){Remove-Item -LiteralPath $destination -Force}
            }
        }
    }
    $configs.Add([pscustomobject]$record)
}
$snapshot=[ordered]@{
    schema='topolog-project-snapshot-v1'; complete=$true; project_id=$projectId; project_name=$project.name
    started_at=$started; finished_at=[DateTimeOffset]::Now.ToString('o'); source='GNS3 controller API v2 GET'
    topology_file='topology.json';topology_sha256=(Get-FileHash -LiteralPath (Join-Path $outDir 'topology.json') -Algorithm SHA256).Hash.ToLowerInvariant()
    requests=@($requests.ToArray());configurations=@($configs.ToArray())
    note='API topology snapshot, not an importable .gns3 file. Saved startup files are not running configurations. GET requests are sequential, not an atomic snapshot.'
}
[IO.File]::WriteAllText((Join-Path $outDir 'snapshot.json'),($snapshot|ConvertTo-Json -Depth 12),$utf8)
Write-Host ('Project snapshot: '+$outDir)
Write-Host ('Saved startup files: '+@($configs|Where-Object status -eq 'collected').Count+'/'+$nodes.Count)
