#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$ProjectName = '20260827-1',
    [ValidateSet('Native','PrintWindow','Screen')][string]$Method = 'Native',
    [string]$OutputDirectory = ''
)

$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Drawing
if(-not ('TopoLogWindowCapture' -as [type])) {
    $references=@('System','System.Core','System.Drawing')
    if($PSVersionTable.PSEdition -eq 'Core') {
        $references=@(Get-ChildItem -LiteralPath (Join-Path $PSHOME 'ref') -Filter '*.dll' | ForEach-Object FullName)+
            @([Drawing.Bitmap].Assembly.Location)+
            @(Get-ChildItem -LiteralPath $PSHOME -Filter 'System.Private.Windows*.dll' | ForEach-Object FullName)
    }
    Add-Type -Path (Join-Path $PSScriptRoot 'TopoLogWindowCapture.cs') -ReferencedAssemblies ($references | Select-Object -Unique)
}
. (Join-Path $PSScriptRoot 'project-paths.ps1')
$projectPaths=Get-TopoLogPaths -SourceRoot (Split-Path -Parent $PSScriptRoot)
$root=$projectPaths.DataRoot
if(-not $OutputDirectory) {
    $stamp=(Get-Date -Format 'yyyyMMdd_HHmmss')+'_'+[guid]::NewGuid().ToString('N').Substring(0,8)
    $OutputDirectory=Join-Path $root ('work\capture_'+$stamp)
}
$OutputDirectory=[IO.Path]::GetFullPath($OutputDirectory)
if(Test-Path -LiteralPath $OutputDirectory) { throw 'Choose a new output directory; existing capture evidence is not overwritten.' }
[IO.Directory]::CreateDirectory($OutputDirectory)|Out-Null
$metadata=[ordered]@{
    schema='topolog-capture-v2'; project_name=$ProjectName;capture_kind='window_client_capture'
    capture_status='failed'; review_status='unreviewed'; method=$Method
    captured_at=$null; image_file=$null; image_sha256=$null; width=$null; height=$null
    window_title=$null; window_handle=$null; process_name=$null; process_id=$null
    was_minimized=$null; window_restored_from_minimized=$false
    region='GNS3 window client area'; error=$null
    note='A window screenshot is not a verdict on network operation. The viewport may not show every node; review the image.'
}
try {
    $windows=@([TopoLogWindowCapture]::List())
    $candidates=@($windows | Where-Object {[TopoLogWindowCapture]::TitleMatches($_.Title,$ProjectName)})
    if($candidates.Count -ne 1) {
        $windows | Select-Object Title,ProcessName,Minimized | Format-Table -AutoSize
        throw ('Expected exactly one GNS3 window for project '+$ProjectName+'; found '+$candidates.Count+'.')
    }
    $window=$candidates[0]
    $metadata.window_title=$window.Title
    $metadata.window_handle=$window.Handle
    $metadata.process_name=$window.ProcessName
    $metadata.process_id=$window.ProcessId
    $metadata.was_minimized=$window.Minimized
    if($window.Minimized) { Write-Host 'Restoring the minimized GNS3 window before capture...' }
    $imageName='gns3-window.png'
    if($Method -eq 'Native') {$imageName='gns3-topology.png'}
    $path=Join-Path $OutputDirectory $imageName
    if($Method -eq 'Native') {
        $metadata.window_restored_from_minimized=[TopoLogWindowCapture]::PrepareWindow($window.Handle,$true)
        $metadata.capture_kind='gns3_native_topology_screenshot'
        $metadata.region='Complete topology scene exported by GNS3 Screenshot action'
        $metadata.note='GNS3 exported the topology scene using its own Screenshot action. Image content requires visual review and is not a verdict on network operation.'
        $metadata['native_export_record']='native-export.json'
        $nativeRecord=Join-Path $OutputDirectory 'native-export.json'
        & (Join-Path $PSScriptRoot 'export-gns3-screenshot.ps1') -WindowHandle $window.Handle -Gns3ProcessId $window.ProcessId -Destination $path -RecordPath $nativeRecord
        $native=Get-Content -LiteralPath $nativeRecord -Raw -Encoding UTF8 | ConvertFrom-Json
        if($native.status -ne 'saved') {throw ('GNS3 native screenshot export failed: '+$native.error)}
        $image=[Drawing.Bitmap]::new($path)
        try {
            if(-not [TopoLogWindowCapture]::HasVisualContent($image)) {throw 'GNS3 exported an apparently blank topology image.'}
            $dimensions=@($image.Width,$image.Height)
        } finally {$image.Dispose()}
    } else {
        $dimensions=[TopoLogWindowCapture]::Capture($window.Handle,$path,$Method)
        $metadata.window_restored_from_minimized=($dimensions[2] -eq 1)
    }
    $metadata.capture_status='captured'
    $metadata.captured_at=[DateTimeOffset]::Now.ToString('o')
    $metadata.image_file=$imageName
    $metadata.image_sha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    $metadata.width=$dimensions[0];$metadata.height=$dimensions[1]
    Write-Host ('Captured GNS3 image: '+$window.Title+' ('+$Method+')')
    Write-Host ('Image: '+$path)
} catch {
    $metadata.error=$_.Exception.GetBaseException().Message
    Write-Warning $metadata.error
} finally {
    [IO.File]::WriteAllText((Join-Path $OutputDirectory 'capture.json'),($metadata|ConvertTo-Json -Depth 5),[Text.UTF8Encoding]::new($false))
    Write-Host ('Capture record: '+(Join-Path $OutputDirectory 'capture.json'))
}
if($metadata.capture_status -ne 'captured') { exit 1 }
