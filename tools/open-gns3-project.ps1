param(
    [Parameter(Mandatory=$true)][long]$WindowHandle,
    [Parameter(Mandatory=$true)][int]$Gns3ProcessId,
    [Parameter(Mandatory=$true)][string]$ProjectFile,
    [Parameter(Mandatory=$true)][string]$ProjectName,
    [Parameter(Mandatory=$true)][string]$RecordDirectory
)
# Attach the already-opened API project to one idle GUI. Never replace a user's
# unrelated project or launch another GUI, server or VM.
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'dialog-filename.ps1')
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$script:workers=@();$script:fileMenus=@();$dialog=$null;$saved=$false
function Assert-IdleWindow {
    $current=@([TopoLogWindowCapture]::List() | Where-Object {$_.Handle -eq $WindowHandle -and $_.ProcessId -eq $Gns3ProcessId -and $_.Title.Trim() -eq 'GNS3'})
    if($current.Count -ne 1){throw 'The GNS3 window is no longer idle.'}
}
function Get-UiName($Element) {
    try { return ([string]$Element.Current.Name).Replace('&','').Trim() } catch { return '' }
}
function Get-WindowShape($Element) {
    $shape=[ordered]@{rect=$null;state=$null}
    try {$r=$Element.Current.BoundingRectangle;$shape.rect=@([int]$r.X,[int]$r.Y,[int]$r.Width,[int]$r.Height)} catch {}
    try {$w=$null;if($Element.TryGetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern,[ref]$w)){$shape.state=[string]$w.Current.WindowVisualState}} catch {}
    return $shape
}
function Find-OpenActions($Root) {
    $found=@()
    $controls=$Root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
    foreach($control in $controls) {
        try {
            $type=$control.Current.ControlType
            if($type -ne [System.Windows.Automation.ControlType]::Button -and $type -ne [System.Windows.Automation.ControlType]::MenuItem){continue}
            $name=Get-UiName $control
            $help=[string]$control.Current.HelpText
            if($name -match '^Open project(?:\.{3}|…)?$' -or $help -match '^Open (?:a )?project(?:\b|$)') {
                $r=$control.Current.BoundingRectangle
                $found+=[pscustomobject]@{element=$control;name=$name;type=$type.ProgrammaticName;offscreen=[bool]$control.Current.IsOffscreen;rect=@([int]$r.X,[int]$r.Y,[int]$r.Width,[int]$r.Height)}
            }
        } catch {}
    }
    return $found
}
function Get-ProcessRoots {
    $condition=[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ProcessIdProperty,$Gns3ProcessId)
    return [System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,$condition)
}
function Start-ActionWorker($Element,[string]$Stem) {
    $request=Join-Path $RecordDirectory ($Stem+'-request.json')
    $result=Join-Path $RecordDirectory ($Stem+'-result.json')
    [IO.File]::WriteAllText($request,([ordered]@{process_id=$Gns3ProcessId;runtime_id=($Element.GetRuntimeId() -join ',');result_path=$result;expand=($Stem -like 'file-menu-*')}|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
    $start=[Diagnostics.ProcessStartInfo]::new()
    $start.FileName=Join-Path $PSHOME 'powershell.exe'
    $start.Arguments='-NoLogo -NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File "'+(Join-Path $PSScriptRoot 'invoke-gns3-action.ps1')+'" -RequestPath "'+$request+'"'
    $start.UseShellExecute=$false;$start.CreateNoWindow=$true
    $process=[Diagnostics.Process]::Start($start)
    $script:workers+=$process
    return @{process=$process;result=$result}
}
function Read-WorkerResult($Job) {
    try {return (Get-Content -LiteralPath $Job.result -Raw -Encoding UTF8 | ConvertFrom-Json)} catch {return $null}
}
function Invoke-OpenAction($Element,[string]$Stem) {
    # The worker may stay blocked inside Invoke while the modal file dialog is open; the dialog is watched here.
    $job=Start-ActionWorker $Element $Stem
    $clock=[Diagnostics.Stopwatch]::StartNew()
    while($clock.Elapsed.TotalSeconds -lt 12) {
        $dialogs=@([TopoLogWindowCapture]::FindProjectOpenDialogs($WindowHandle,$Gns3ProcessId))
        if($dialogs.Count -gt 1){throw 'More than one GNS3 project file dialog was found.'}
        if($dialogs.Count -eq 1){$script:dialog=$dialogs[0];return 'dialog'}
        $state=Read-WorkerResult $job
        if($state -and $state.status -eq 'failed') {
            # A late dialog is still accepted before the next attempt.
            Start-Sleep -Milliseconds 400
            $dialogs=@([TopoLogWindowCapture]::FindProjectOpenDialogs($WindowHandle,$Gns3ProcessId))
            if($dialogs.Count -eq 1){$script:dialog=$dialogs[0];return 'dialog'}
            return ('failed: '+[string]$state.error)
        }
        Start-Sleep -Milliseconds 150
    }
    return 'timeout'
}
function Invoke-ThroughFileMenu([int]$Attempt) {
    $root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$WindowHandle)
    $files=@($root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::MenuItem)) | Where-Object {(Get-UiName $_) -eq 'File'})
    if($files.Count -ne 1){return 'not_found: File menu'}
    $script:fileMenus=$files
    $menuJob=Start-ActionWorker $files[0] ('file-menu-'+$Attempt)
    $item=$null
    for($poll=1;$poll -le 10 -and -not $item;$poll++) {
        Start-Sleep -Milliseconds 200
        Assert-IdleWindow
        foreach($processRoot in (Get-ProcessRoots)) {
            $visible=@(Find-OpenActions $processRoot | Where-Object {-not $_.offscreen -and $_.type -eq 'ControlType.MenuItem'})
            if($visible.Count -gt 0){$item=$visible[0].element;break}
        }
    }
    if(-not $item){return 'not_found: Open project in File menu'}
    return (Invoke-OpenAction $item ('open-menu-action-'+$Attempt))
}

$record=[ordered]@{status='failed';method='GNS3 Open project action on idle window';phase='check_window';dialog_class=$null;dialog_handle=$null;filename_control_id=$null;dialog_controls=@();error=$null;finished_at=$null}
try {
    if(-not [IO.File]::Exists($ProjectFile)){throw 'The selected GNS3 project file was not found.'}
    $current=@([TopoLogWindowCapture]::List() | Where-Object {$_.Handle -eq $WindowHandle -and $_.ProcessId -eq $Gns3ProcessId -and $_.Title.Trim() -eq 'GNS3'})
    if($current.Count -ne 1){throw 'The GNS3 window is no longer idle.'}
    [TopoLogWindowCapture]::PrepareWindow($WindowHandle,$true)|Out-Null
    $root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$WindowHandle)
    # Window size/state at the start: a narrowed window hides toolbar buttons (offscreen),
    # and invoking a hidden button fails with a generic COM error.
    $record['window_at_start']=Get-WindowShape $root
    $record['attempts']=@()
    $record.phase='invoke_action'
    for($attempt=1;$attempt -le 3 -and -not $dialog;$attempt++) {
        Assert-IdleWindow
        # Right after the previous project closed, UI Automation can fail once with a generic COM error
        # ('Unrecognized error'); that attempt is recorded and the search is repeated.
        try {
            $root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$WindowHandle)
            $candidates=@(Find-OpenActions $root)
        } catch {
            $record['attempts']+=[ordered]@{attempt=$attempt;source='find open action';result=('failed: '+$_.Exception.GetBaseException().Message)}
            Start-Sleep -Milliseconds 800
            continue
        }
        $visible=@($candidates | Where-Object {-not $_.offscreen})
        $note=[ordered]@{attempt=$attempt;window=(Get-WindowShape $root);candidates=@($candidates | ForEach-Object {[ordered]@{name=$_.name;type=$_.type;offscreen=$_.offscreen;rect=$_.rect}});source=$null;result=$null}
        foreach($menu in $script:fileMenus) {try {$collapse=$null;if($menu.TryGetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern,[ref]$collapse)){$collapse.Collapse()}} catch {}}
        # Attempt 2 always goes through the File menu: a visible button that failed once may be stale.
        if($visible.Count -gt 0 -and $attempt -ne 2) {
            $note.source='toolbar or visible menu item'
            $outcome=Invoke-OpenAction $visible[0].element ('open-action-'+$attempt)
        } else {
            $note.source='File menu'
            $outcome=Invoke-ThroughFileMenu $attempt
        }
        $note.result=$outcome
        $record['attempts']+=$note
        if($outcome -eq 'dialog'){break}
        if($outcome -notmatch '^(failed|not_found|timeout)'){break}
        Start-Sleep -Milliseconds 600
    }
    if(-not $dialog){throw 'The GNS3 project file dialog did not open.'}
    $handle=[long]$dialog.Handle
    $record['requested_path']=[IO.Path]::GetFullPath($ProjectFile)
    $record.phase='fill_filename';$record.dialog_class=$dialog.ClassName;$record.dialog_handle=$handle
    if($dialog.ClassName -eq '#32770') {
        $edit=[TopoLogWindowCapture]::FindFilenameControl($handle)
        $record.filename_control_id=[TopoLogWindowCapture]::ControlId($edit)
        Set-TopoLogDialogFilename -DialogHandle $handle -EditHandle $edit -Filename ([IO.Path]::GetFullPath($ProjectFile))
        $record.phase='confirm_open'
        [TopoLogWindowCapture]::ClickDialogButton([TopoLogWindowCapture]::FindDialogControl($handle,1,'Button'))
    } else {
        $element=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$handle)
        $edits=$element.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Edit))
        $fields=@($edits | Where-Object {$_.Current.AutomationId -in @('1001','fileNameEdit')})
        if($fields.Count -ne 1){throw 'Could not uniquely identify the GNS3 project filename field.'}
        $value=$null
        if(-not $fields[0].TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$value)){throw 'The project filename field is not editable.'}
        $value.SetValue([IO.Path]::GetFullPath($ProjectFile))
        $buttons=$element.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Button))
        $open=@($buttons | Where-Object {$_.Current.Name.Replace('&','').Trim() -in @('Open','열기')})
        if($open.Count -ne 1){throw 'Could not uniquely identify the GNS3 project Open button.'}
        $invoke=$null
        if(-not $open[0].TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$invoke)){throw 'The project Open button cannot be invoked.'}
        $record.phase='confirm_open'
        $invoke.Invoke()
    }
    $record.phase='wait_project_loaded'
    $clock=[Diagnostics.Stopwatch]::StartNew()
    # Large projects can take longer than 25 s for the GUI to load every item.
    while($clock.Elapsed.TotalSeconds -lt 60) {
        $matches=@([TopoLogWindowCapture]::List() | Where-Object {$_.Handle -eq $WindowHandle -and [TopoLogWindowCapture]::TitleMatches($_.Title,$ProjectName)})
        if($matches.Count -eq 1){$saved=$true;$record.status='opened';$record.phase='finished';break}
        Start-Sleep -Milliseconds 200
    }
    if(-not $saved){
        # Record what GNS3 showed instead (a message box, another title, a dialog
        # left open) so a GNS3 delay/error is told apart from a user action.
        try {
            $record['window_title_at_timeout']=[TopoLogWindowCapture]::WindowTitle($WindowHandle)
            $record['gns3_windows_at_timeout']=@([TopoLogWindowCapture]::ListProcessWindows($Gns3ProcessId) | Where-Object {$_.Handle -ne $WindowHandle} | ForEach-Object {[ordered]@{title=$_.Title;class=$_.ClassName}})
            $front=[TopoLogWindowCapture]::ForegroundHandle()
            $record['foreground_at_timeout']=[TopoLogWindowCapture]::ProcessNameOf([TopoLogWindowCapture]::WindowProcessId($front))
        } catch {}
        throw 'The GNS3 project did not finish opening for the screenshot.'
    }
    Start-Sleep -Milliseconds 700
} catch {
    $record.error=$_.Exception.GetBaseException().Message
    if($dialog -and -not $saved -and [string]$dialog.ClassName -eq '#32770') {
        try {$record.dialog_controls=@([TopoLogWindowCapture]::DescribeDialogControls([long]$dialog.Handle))} catch {}
    }
    throw
} finally {
    if(-not $dialog) {
        foreach($menu in $script:fileMenus) {try {$collapse=$null;if($menu.TryGetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern,[ref]$collapse)){$collapse.Collapse()}} catch {}}
    }
    foreach($worker in $script:workers){try {if(-not $worker.HasExited -and -not $worker.WaitForExit(500)){$worker.Kill()};$worker.Dispose()} catch {}}
    if($dialog -and -not $saved) {
        try {[TopoLogWindowCapture]::ClickDialogButton([TopoLogWindowCapture]::FindDialogControl([long]$dialog.Handle,2,'Button'))} catch {}
    }
    $record.finished_at=[DateTimeOffset]::Now.ToString('o')
    [IO.File]::WriteAllText((Join-Path $RecordDirectory 'open-project.json'),($record|ConvertTo-Json -Depth 4),[Text.UTF8Encoding]::new($false))
}
