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
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$worker=$null;$dialog=$null;$saved=$false
$record=[ordered]@{status='failed';method='GNS3 Open project action on idle window';phase='check_window';dialog_class=$null;dialog_handle=$null;filename_control_id=$null;dialog_controls=@();error=$null;finished_at=$null}
try {
    if(-not [IO.File]::Exists($ProjectFile)){throw 'The selected GNS3 project file was not found.'}
    $current=@([TopoLogWindowCapture]::List() | Where-Object {$_.Handle -eq $WindowHandle -and $_.ProcessId -eq $Gns3ProcessId -and $_.Title.Trim() -eq 'GNS3'})
    if($current.Count -ne 1){throw 'The GNS3 window is no longer idle.'}
    [TopoLogWindowCapture]::PrepareWindow($WindowHandle,$true)|Out-Null
    $root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$WindowHandle)
    $controls=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
    $action=$null
    foreach($control in $controls) {
        try {
            $name=([string]$control.Current.Name).Replace('&','').Trim()
            $help=[string]$control.Current.HelpText
            if($control.Current.ControlType -in @([System.Windows.Automation.ControlType]::Button,[System.Windows.Automation.ControlType]::MenuItem) -and
               ($name -match '^Open project(?:\.{3}|…)?$' -or $help -match '^Open (?:a )?project(?:\b|$)')){$action=$control;break}
        } catch {}
    }
    if(-not $action){throw 'The GNS3 Open project action is not exposed through accessibility.'}
    $record.phase='invoke_action'
    $request=Join-Path $RecordDirectory 'open-action-request.json'
    $result=Join-Path $RecordDirectory 'open-action-result.json'
    [IO.File]::WriteAllText($request,([ordered]@{process_id=$Gns3ProcessId;runtime_id=($action.GetRuntimeId() -join ',');result_path=$result}|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
    $start=[Diagnostics.ProcessStartInfo]::new()
    $start.FileName=Join-Path $PSHOME 'powershell.exe'
    $start.Arguments='-NoLogo -NoProfile -NonInteractive -File "'+(Join-Path $PSScriptRoot 'invoke-gns3-action.ps1')+'" -RequestPath "'+$request+'"'
    $start.UseShellExecute=$false;$start.CreateNoWindow=$true
    $worker=[Diagnostics.Process]::Start($start)
    $clock=[Diagnostics.Stopwatch]::StartNew()
    while($clock.Elapsed.TotalSeconds -lt 12) {
        $dialogs=@([TopoLogWindowCapture]::FindProjectOpenDialogs($WindowHandle,$Gns3ProcessId))
        if($dialogs.Count -eq 1){$dialog=$dialogs[0];break}
        if($dialogs.Count -gt 1){throw 'More than one GNS3 project file dialog was found.'}
        Start-Sleep -Milliseconds 150
    }
    if(-not $dialog){throw 'The GNS3 project file dialog did not open.'}
    $handle=[long]$dialog.Handle
    $record.phase='fill_filename';$record.dialog_class=$dialog.ClassName;$record.dialog_handle=$handle
    if($dialog.ClassName -eq '#32770') {
        $edit=[TopoLogWindowCapture]::FindFilenameControl($handle)
        $record.filename_control_id=[TopoLogWindowCapture]::ControlId($edit)
        [TopoLogWindowCapture]::SetDialogFilename($edit,[IO.Path]::GetFullPath($ProjectFile))
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
    $clock.Restart()
    # Large projects can take longer than 25 s for the GUI to load every item.
    while($clock.Elapsed.TotalSeconds -lt 60) {
        $matches=@([TopoLogWindowCapture]::List() | Where-Object {$_.Handle -eq $WindowHandle -and [TopoLogWindowCapture]::TitleMatches($_.Title,$ProjectName)})
        if($matches.Count -eq 1){$saved=$true;$record.status='opened';$record.phase='finished';break}
        Start-Sleep -Milliseconds 200
    }
    if(-not $saved){throw 'The GNS3 project did not finish opening for the screenshot.'}
    Start-Sleep -Milliseconds 700
} catch {
    $record.error=$_.Exception.GetBaseException().Message
    if($dialog -and -not $saved -and [string]$dialog.ClassName -eq '#32770') {
        try {$record.dialog_controls=@([TopoLogWindowCapture]::DescribeDialogControls([long]$dialog.Handle))} catch {}
    }
    throw
} finally {
    if($worker){if(-not $worker.HasExited -and -not $worker.WaitForExit(500)){$worker.Kill()};$worker.Dispose()}
    if($dialog -and -not $saved) {
        try {[TopoLogWindowCapture]::ClickDialogButton([TopoLogWindowCapture]::FindDialogControl([long]$dialog.Handle,2,'Button'))} catch {}
    }
    $record.finished_at=[DateTimeOffset]::Now.ToString('o')
    [IO.File]::WriteAllText((Join-Path $RecordDirectory 'open-project.json'),($record|ConvertTo-Json -Depth 4),[Text.UTF8Encoding]::new($false))
}
