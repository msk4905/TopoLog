#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$ProjectName = '20260827-1',
    [string]$ProjectFile = '',
    [ValidateSet('Native','PrintWindow','Screen')][string]$Method = 'Native',
    [string]$OutputDirectory = '',
    [long]$RestoreWindowHandle = 0,
    [int]$RestoreWindowProcessId = 0
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
# Window the user had in front, restored after GNS3 was raised for this capture.
$foregroundBefore=[TopoLogWindowCapture]::ForegroundHandle()
$metadata['foreground_before']=$foregroundBefore
$beforeName=[TopoLogWindowCapture]::ProcessNameOf([TopoLogWindowCapture]::WindowProcessId($foregroundBefore))
if($beforeName -in @('gns3','vmware','vmplayer','vmware-vmx','VirtualBox','VirtualBoxVM') -and $RestoreWindowHandle -ne 0 -and
   [TopoLogWindowCapture]::WindowProcessId($RestoreWindowHandle) -eq $RestoreWindowProcessId) {
    $foregroundBefore=$RestoreWindowHandle
    $metadata['focus_target_reused']=$true
}
$metadata['focus_target']=$foregroundBefore
$focusTarget=$null;$startedMinimized=$false
try {
    $windows=@([TopoLogWindowCapture]::List())
    $candidates=@($windows | Where-Object {[TopoLogWindowCapture]::TitleMatches($_.Title,$ProjectName)})
    if($candidates.Count -eq 1){$focusTarget=$candidates[0];$startedMinimized=[bool]$candidates[0].Minimized}
    if($candidates.Count -eq 0 -and $ProjectFile) {
        $idle=@($windows | Where-Object {$_.Title.Trim() -eq 'GNS3'})
        if($idle.Count -ne 1) {throw 'Keep one idle GNS3 window open for batch screenshots. An unrelated open project will not be replaced.'}
        $focusTarget=$idle[0];$startedMinimized=[bool]$idle[0].Minimized
        # A freshly started GNS3 shows its modal startup chooser; cancel only that dialog.
        $startup=@([TopoLogWindowCapture]::FindStartupProjectDialogs($idle[0].Handle,$idle[0].ProcessId))
        if($startup.Count -eq 1) {
            [TopoLogWindowCapture]::CloseWindow($startup[0].Handle)
            $metadata['startup_dialog']='closed'
            $clock=[Diagnostics.Stopwatch]::StartNew()
            while($clock.Elapsed.TotalSeconds -lt 5 -and @([TopoLogWindowCapture]::FindStartupProjectDialogs($idle[0].Handle,$idle[0].ProcessId)).Count -gt 0){Start-Sleep -Milliseconds 150}
            if(@([TopoLogWindowCapture]::FindStartupProjectDialogs($idle[0].Handle,$idle[0].ProcessId)).Count -gt 0){throw 'The GNS3 startup Project dialog did not close.'}
        } elseif($startup.Count -gt 1) {throw 'More than one GNS3 Project dialog is open.'}
        $metadata['open_project_record']='open-project.json'
        for($openAttempt=1;;$openAttempt++) {
            try {
                & (Join-Path $PSScriptRoot 'open-gns3-project.ps1') -WindowHandle $idle[0].Handle -Gns3ProcessId $idle[0].ProcessId -ProjectFile $ProjectFile -ProjectName $ProjectName -RecordDirectory $OutputDirectory
                break
            } catch {
                # One more attempt only when GNS3 simply stayed idle: no message box,
                # no other GNS3 window and no other project title. The first record is kept.
                $openRecord=Join-Path $OutputDirectory 'open-project.json'
                $retry=$false
                if($openAttempt -eq 1 -and (Test-Path -LiteralPath $openRecord)) {
                    $first=Get-Content -LiteralPath $openRecord -Raw -Encoding UTF8 | ConvertFrom-Json
                    $stillIdle=@([TopoLogWindowCapture]::List() | Where-Object {$_.Handle -eq $idle[0].Handle -and $_.Title.Trim() -eq 'GNS3'}).Count -eq 1
                    $others=@([TopoLogWindowCapture]::ListProcessWindows($idle[0].ProcessId) | Where-Object {$_.Handle -ne $idle[0].Handle}).Count
                    if($first.phase -eq 'wait_project_loaded' -and $stillIdle -and $others -eq 0) {
                        Move-Item -LiteralPath $openRecord -Destination (Join-Path $OutputDirectory 'open-project-attempt1.json')
                        $metadata['open_retry']='first open stayed idle without a GNS3 message; retried once'
                        $retry=$true
                    }
                }
                if(-not $retry){throw}
                Start-Sleep -Seconds 2
            }
        }
        $windows=@([TopoLogWindowCapture]::List())
        $candidates=@($windows | Where-Object {[TopoLogWindowCapture]::TitleMatches($_.Title,$ProjectName)})
    }
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
        & (Join-Path $PSScriptRoot 'export-gns3-screenshot.ps1') -WindowHandle $window.Handle -Gns3ProcessId $window.ProcessId -Destination $path -RecordPath $nativeRecord -ProjectName $ProjectName
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
    # Which program was in front when it failed: helps tell a user switch from a GNS3 delay.
    try {
        $front=[TopoLogWindowCapture]::ForegroundHandle()
        $metadata['foreground_at_failure']=[TopoLogWindowCapture]::ProcessNameOf([TopoLogWindowCapture]::WindowProcessId($front))
    } catch {}
    Write-Warning $metadata.error
} finally {
    if($focusTarget) {
        try {$metadata['focus_restore']=[TopoLogWindowCapture]::RestoreForeground($foregroundBefore,[int]$focusTarget.ProcessId,[long]$focusTarget.Handle,$startedMinimized)}
        catch {$metadata['focus_restore']='failed: '+$_.Exception.GetBaseException().Message}
    }
    [IO.File]::WriteAllText((Join-Path $OutputDirectory 'capture.json'),($metadata|ConvertTo-Json -Depth 5),[Text.UTF8Encoding]::new($false))
    Write-Host ('Capture record: '+(Join-Path $OutputDirectory 'capture.json'))
}
if($metadata.capture_status -ne 'captured') { exit 1 }
