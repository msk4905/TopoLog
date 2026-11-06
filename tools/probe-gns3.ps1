#requires -Version 5.1
[CmdletBinding()]
param(
    [uri]$BaseUri,
    [string]$ProjectName = '20260827-1',
    [string]$OutputPath = '',
    [switch]$InspectOnly
)

# Read-only diagnostic: GET requests only. Does not open/start/stop projects or
# nodes or connect to consoles. Saved server authentication is used locally and
# is never included in the output. Device configurations are not read.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'project-paths.ps1')
$projectPaths=Get-TopoLogPaths -SourceRoot (Split-Path -Parent $PSScriptRoot)
$settings = @{}
$configPath = Join-Path ([Environment]::GetFolderPath('ApplicationData')) 'GNS3\2.2\gns3_server.ini'
if (Test-Path -LiteralPath $configPath) {
    $section = ''
    foreach ($line in (Get-Content -LiteralPath $configPath -Encoding UTF8)) {
        if ($line -match '^\s*\[([^\]]+)\]') { $section = $matches[1] }
        elseif ($section -eq 'Server' -and $line -match '^\s*([^=;#]+?)\s*=\s*(.*)$') {
            $settings[$matches[1].Trim()] = $matches[2].Trim()
        }
    }
}
$configuredUri = $null
if ($settings['host'] -and $settings['port']) {
    $serverHost = $settings['host']
    if ($serverHost -in @('0.0.0.0', '::')) { $serverHost = '127.0.0.1' }
    $scheme = 'http'
    if ($settings['protocol']) { $scheme = $settings['protocol'] }
    $configuredUri = [UriBuilder]::new($scheme, $serverHost, [int]$settings['port']).Uri
}
if (-not $BaseUri) {
    if ($configuredUri) { $BaseUri = $configuredUri }
    else { $BaseUri = [uri]'http://127.0.0.1:3080' }
}
if ($BaseUri.Scheme -notin @('http', 'https') -or $BaseUri.UserInfo) {
    throw 'Use an HTTP(S) server URL without embedded credentials.'
}
if ($BaseUri.Query -or $BaseUri.Fragment) {
    throw 'Use a server URL without a query or fragment.'
}
if (-not $OutputPath) {
    $filename = 'work\gns3-probe.json'
    if ($InspectOnly) { $filename = 'work\gns3-probe-config.json' }
    $OutputPath = Join-Path $projectPaths.DataRoot $filename
}
$base = $BaseUri.AbsoluteUri.TrimEnd('/')
$headers = @{}
$authenticationUsed = $false
$authenticationConfigured = ($settings['auth'] -match '^(?i:true|yes|1)$')
# Never send saved credentials to an unrelated override URL.
if ($authenticationConfigured -and $configuredUri -and
    $BaseUri.Scheme -eq $configuredUri.Scheme -and
    $BaseUri.Host -eq $configuredUri.Host -and
    $BaseUri.Port -eq $configuredUri.Port -and $settings['user']) {
    $pair = $settings['user'] + ':' + $settings['password']
    $headers['Authorization'] = 'Basic ' + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($pair))
    $pair = $null
    $authenticationUsed = $true
}
$settings.Clear()
if ($InspectOnly) {
    $inspection = [ordered]@{
        base_uri = $base
        config_source = $configPath
        authentication_configured = $authenticationConfigured
        authentication_prepared = $authenticationUsed
        requests_sent = 0
    }
    $absoluteOutput = [IO.Path]::GetFullPath($OutputPath)
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($absoluteOutput)) | Out-Null
    [IO.File]::WriteAllText($absoluteOutput, ($inspection | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    Write-Host ('Saved configuration-only inspection: ' + $absoluteOutput)
    Write-Host ('Server: ' + $base)
    exit 0
}
$requests = New-Object 'System.Collections.Generic.List[object]'

function Read-Gns3Endpoint([string]$Endpoint) {
    try {
        $data = Invoke-RestMethod -Uri ($base + $Endpoint) -Method Get -Headers $headers -TimeoutSec 8 -ErrorAction Stop
        $requests.Add([pscustomobject]@{ endpoint = $Endpoint; success = $true; http_status = 200; error = $null })
        return [pscustomobject]@{ success = $true; data = $data }
    } catch {
        $status = $null
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
            $status = [int]$_.Exception.Response.StatusCode
        }
        $requests.Add([pscustomobject]@{
            endpoint = $Endpoint
            success = $false
            http_status = $status
            error = $_.Exception.Message
        })
        return [pscustomobject]@{ success = $false; data = $null }
    }
}

$versionResult = Read-Gns3Endpoint '/v2/version'
$projectResult = Read-Gns3Endpoint '/v2/projects'
$projects = @()
$target = $null
$nodes = @()
$nodesRead = $false
if ($projectResult.success) {
    $projects = @($projectResult.data | Where-Object { $null -ne $_ } | ForEach-Object {
        [pscustomobject]@{ name = $_.name; project_id = $_.project_id; status = $_.status }
    })
    $matches = @($projects | Where-Object { $_.name -eq $ProjectName })
    if ($matches.Count -eq 1) {
        $target = $matches[0]
        $nodeResult = Read-Gns3Endpoint ('/v2/projects/' + [uri]::EscapeDataString([string]$target.project_id) + '/nodes')
        $nodesRead = $nodeResult.success
        if ($nodesRead) {
            $nodes = @($nodeResult.data | Where-Object { $null -ne $_ } | ForEach-Object {
                [pscustomobject]@{
                    name = $_.name
                    node_id = $_.node_id
                    node_type = $_.node_type
                    status = $_.status
                    compute_id = $_.compute_id
                    console_type = $_.console_type
                    console_host = $_.console_host
                    console_port = $_.console
                }
            })
        }
    }
}
$version = $null
if ($versionResult.success) { $version = $versionResult.data.version }
$report = [ordered]@{
    collected_at = (Get-Date).ToString('o')
    base_uri = $base
    config_source = $configPath
    authentication_used = $authenticationUsed
    api_family_tested = 'v2'
    target_project_name = $ProjectName
    server_version = $version
    projects_read = $projectResult.success
    projects = @($projects)
    target_project_found = ($null -ne $target)
    target_project = $target
    nodes_read = $nodesRead
    nodes = @($nodes)
    requests = @($requests.ToArray())
    console_collection_tested = $false
}
$absoluteOutput = [IO.Path]::GetFullPath($OutputPath)
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($absoluteOutput)) | Out-Null
[IO.File]::WriteAllText($absoluteOutput, ($report | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
Write-Host ('Saved: ' + $absoluteOutput)
Write-Host ('Projects readable: ' + $projectResult.success)
Write-Host ('Target found: ' + ($null -ne $target))
Write-Host ('Nodes readable: ' + $nodesRead)
if ($nodes.Count) { $nodes | Format-Table name, node_type, status, console_type, console_host, console_port -AutoSize }
foreach ($failure in @($requests.ToArray() | Where-Object { -not $_.success })) {
    Write-Warning ($failure.endpoint + ': ' + $failure.error)
}
if (-not $projectResult.success -or -not $target -or -not $nodesRead) { exit 1 }
