#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][long]$WindowHandle,
    [Parameter(Mandatory=$true)][int]$Gns3ProcessId,
    [Parameter(Mandatory=$true)][string]$Destination,
    [Parameter(Mandatory=$true)][string]$RecordPath,
    [string]$ProjectName=''
)

# Invoke GNS3's own QAction and automate only its owned Screenshot save dialog.
# This does not capture/crop desktop pixels or modify GNS3's installed files.
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'dialog-filename.ps1')
$record=[ordered]@{ method='GNS3 native Screenshot action';status='failed';phase='load_accessibility';action_name=$null;dialog_title=$null;dialog_handle=$null;dialog_process_id=$null;dialog_class=$null;dialog_reused=$false;filename_control=$null;available_actions=@();error=$null;finished_at=$null }
$ownedDialog=$null
$worker=$null
$menuWorker=$null

function Save-ExportCheckpoint {
    [IO.File]::WriteAllText([IO.Path]::GetFullPath($RecordPath),($record|ConvertTo-Json -Depth 6),[Text.UTF8Encoding]::new($false))
}
function Get-UiName($Element) {
    try { return ([string]$Element.Current.Name).Replace('&','').Trim() } catch { return '' }
}
function Get-ProcessRoots {
    $condition=[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ProcessIdProperty,$Gns3ProcessId)
    return [System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,$condition)
}
function Find-ScreenshotDialogs {
    # Native owned dialogs can be nested in the accessibility tree instead of
    # appearing as direct children of its desktop root.
    return [TopoLogWindowCapture]::FindScreenshotDialogs($WindowHandle,$Gns3ProcessId)
}
function Invoke-UiElement($Element) {
    $pattern=$null
    if($Element.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$pattern)) { $pattern.Invoke();return }
    $pattern=$null
    if($Element.TryGetCurrentPattern([System.Windows.Automation.LegacyIAccessiblePattern]::Pattern,[ref]$pattern)) { $pattern.DoDefaultAction();return }
    throw ('The control does not expose an invoke action: '+(Get-UiName $Element))
}
function Assert-TargetWindow {
    # A user click can switch or close the GNS3 project; never type into another window.
    $match=@([TopoLogWindowCapture]::List() | Where-Object {$_.Handle -eq $WindowHandle -and $_.ProcessId -eq $Gns3ProcessId})
    if($match.Count -ne 1){throw 'The GNS3 window closed or changed during the screenshot.'}
    if($ProjectName -and -not [TopoLogWindowCapture]::TitleMatches($match[0].Title,$ProjectName)){throw ('The GNS3 window now shows another project: '+$match[0].Title)}
}
$script:offscreenActions=0
$savedPlacement=$null
function Find-ScreenshotAction($Root) {
    $controls=$Root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
    foreach($element in $controls) {
        try {
            $type=$element.Current.ControlType
            if($type -ne [System.Windows.Automation.ControlType]::Button -and $type -ne [System.Windows.Automation.ControlType]::MenuItem) {continue}
            $name=Get-UiName $element
            $help=[string]$element.Current.HelpText
            $id=[string]$element.Current.AutomationId
            if($name -match '^(Take a screenshot|Screenshot)(\.\.\.|…)?$' -or $help -match '^Take a screenshot' -or $id -eq 'uiScreenshotAction') {
                # A toolbar button pushed out of a narrowed window stays in the tree but cannot be invoked.
                if([bool]$element.Current.IsOffscreen) { $script:offscreenActions++; continue }
                return $element
            }
        } catch { }
    }
    return $null
}

try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $record.phase='locate_action'
    $Destination=[IO.Path]::GetFullPath($Destination)
    if(Test-Path -LiteralPath $Destination) {throw 'Screenshot destination already exists.'}
    $existingDialogs=@(Find-ScreenshotDialogs)
    if($existingDialogs.Count -gt 1){throw 'Multiple owned Screenshot dialogs are open.'}
    if($existingDialogs.Count -eq 1) {
        $ownedDialog=$existingDialogs[0]
        $record.dialog_reused=$true
        $record.action_name='Take a screenshot (resume existing owned dialog)'
    } else {
    # Right after a project opens, the Qt accessibility tree is rebuilt and UIA
    # calls can fail with a generic COM error. Retry briefly; the window identity
    # is re-checked every time so a different window is never used.
    $action=$null;$record['locate_attempts']=0;$record['locate_errors']=@()
    for($attempt=1;$attempt -le 8 -and -not $action;$attempt++) {
        $record['locate_attempts']=$attempt
        Assert-TargetWindow
        try {
            $rootElement=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$WindowHandle)
            if($rootElement.Current.ProcessId -ne $Gns3ProcessId) {throw 'The target GNS3 window process changed.'}
            $action=Find-ScreenshotAction $rootElement
            if(-not $action -and $attempt -lt 8){Start-Sleep -Milliseconds 300}
        } catch {
            $failure=$_.Exception.GetBaseException()
            $message=$failure.Message
            $transient=($failure.GetType().Name -in @('COMException','ElementNotAvailableException','InvalidOperationException')) -or ($message -match '인식할 수 없는 오류|Unspecified error|0x80004005')
            if($message -match 'process changed' -or -not $transient){throw}
            if($record['locate_errors'] -notcontains $message){$record['locate_errors']+=$message}
            # Errors while walking a partly hidden toolbar should not end the capture: fall through to the
            # widened-window and Tools-menu paths below.
            if($attempt -lt 8){Start-Sleep -Milliseconds 600}
        }
    }
    if(-not $action) {
        # A narrow GNS3 window hides whole toolbars, the screenshot button included. Maximize it for the
        # capture only; the exact previous placement is put back in finally.
        Assert-TargetWindow
        $width=[TopoLogWindowCapture]::WindowWidth($WindowHandle)
        $record['window_width']=$width
        $savedPlacement=[TopoLogWindowCapture]::SavePlacement($WindowHandle)
        if([TopoLogWindowCapture]::Widen($WindowHandle,1200)) {
            $record['widened_window']=$true;$record['window_placement_before']=$savedPlacement
            # Let Qt finish laying out the toolbars before reading them.
            Start-Sleep -Milliseconds 400
            for($widenAttempt=1;$widenAttempt -le 8 -and -not $action;$widenAttempt++) {
                try {
                    $rootElement=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$WindowHandle)
                    $action=Find-ScreenshotAction $rootElement
                } catch { $record['widen_error']=$_.Exception.GetBaseException().Message }
                if(-not $action){Start-Sleep -Milliseconds 250}
            }
            if($action){$record['action_source']='toolbar after widening the window'}
        }
    }
    if(-not $action) {
        $rootElement=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$WindowHandle)
        $diagnostic=$rootElement.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
        $record.available_actions=@($diagnostic | Where-Object {$_.Current.ControlType -in @([System.Windows.Automation.ControlType]::Button,[System.Windows.Automation.ControlType]::MenuItem)} | ForEach-Object {[pscustomobject]@{name=(Get-UiName $_);automation_id=$_.Current.AutomationId}})
        $menuItems=$rootElement.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::MenuItem))
        # Screenshot belongs to Tools, not File. A narrow/rearranged toolbar
        # may hide its button; invoke the semantic menu without screen coordinates.
        $toolsMenus=@($menuItems | Where-Object {(Get-UiName $_) -eq 'Tools'})
        if($toolsMenus.Count -eq 1) {
            Assert-TargetWindow
            $record['action_source']='Tools menu'
            $record.phase='open_tools_menu';Save-ExportCheckpoint
            $menuRequest=Join-Path ([IO.Path]::GetDirectoryName($RecordPath)) 'tools-action-request.json'
            $menuResult=Join-Path ([IO.Path]::GetDirectoryName($RecordPath)) 'tools-action-result.json'
            [IO.File]::WriteAllText($menuRequest,([ordered]@{process_id=$Gns3ProcessId;runtime_id=($toolsMenus[0].GetRuntimeId() -join ',');result_path=$menuResult;expand=$true}|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
            $menuShell='powershell.exe';if($PSVersionTable.PSEdition -eq 'Core'){$menuShell='pwsh.exe'}
            $menuScript=Join-Path $PSScriptRoot 'invoke-gns3-action.ps1'
            $menuWorker=Start-Process -FilePath (Join-Path $PSHOME $menuShell) -WindowStyle Hidden -PassThru -ArgumentList @('-NoLogo','-NoProfile','-ExecutionPolicy','RemoteSigned','-File',('"'+$menuScript+'"'),'-RequestPath',('"'+$menuRequest+'"'))
            for($menuAttempt=1;$menuAttempt -le 12 -and -not $action;$menuAttempt++) {
                Assert-TargetWindow
                try {
                    $rootElement=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$WindowHandle)
                    $action=Find-ScreenshotAction $rootElement
                    if(-not $action) {foreach($processRoot in (Get-ProcessRoots)) {$action=Find-ScreenshotAction $processRoot;if($action){break}}}
                } catch {
                    $record['menu_error']=$_.Exception.GetBaseException().Message
                }
                if(-not $action){Start-Sleep -Milliseconds 250}
            }
        }
    }
    $record['offscreen_screenshot_actions']=$script:offscreenActions
    if(-not $action) {throw 'GNS3 Take a screenshot action was not exposed through Windows accessibility.'}
    Assert-TargetWindow
    if(-not $record.Contains('action_source')){$record['action_source']='accessible screenshot action'}
    $record.action_name=Get-UiName $action
    $record.phase='invoke_action';Save-ExportCheckpoint
    $workingDirectory=[IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($RecordPath))
    $shell='powershell.exe';if($PSVersionTable.PSEdition -eq 'Core'){$shell='pwsh.exe'}
    $workerScript=Join-Path $PSScriptRoot 'invoke-gns3-action.ps1'
    # Right after the window is widened the toolbar is still being laid out and Invoke can fail with a
    # generic COM error. A failed invoke that opened no dialog is retried on a freshly located button;
    # a visible dialog always ends the loop, so the action is never run twice.
    $record['invoke_attempts']=@()
    for($invokeAttempt=1;$invokeAttempt -le 3 -and -not $ownedDialog;$invokeAttempt++) {
        if($invokeAttempt -gt 1) {
            Start-Sleep -Milliseconds 700
            Assert-TargetWindow
            $dialogs=@(Find-ScreenshotDialogs)
            if($dialogs.Count -eq 1) {$ownedDialog=$dialogs[0];break}
            $rootElement=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$WindowHandle)
            $fresh=Find-ScreenshotAction $rootElement
            if(-not $fresh) {foreach($processRoot in (Get-ProcessRoots)) {$fresh=Find-ScreenshotAction $processRoot;if($fresh){break}}}
            if(-not $fresh) {throw $lastInvokeError}
            $action=$fresh
        }
        $suffix='';if($invokeAttempt -gt 1){$suffix='-'+$invokeAttempt}
        $workerRequest=Join-Path $workingDirectory ('native-action-request'+$suffix+'.json')
        $workerResult=Join-Path $workingDirectory ('native-action-result'+$suffix+'.json')
        $actionRequest=[ordered]@{process_id=$Gns3ProcessId;runtime_id=($action.GetRuntimeId() -join ',');result_path=$workerResult}
        [IO.File]::WriteAllText($workerRequest,($actionRequest|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
        $worker=Start-Process -FilePath (Join-Path $PSHOME $shell) -WindowStyle Hidden -PassThru -ArgumentList @('-NoLogo','-NoProfile','-ExecutionPolicy','RemoteSigned','-File',('"'+$workerScript+'"'),'-RequestPath',('"'+$workerRequest+'"'))
        $deadline=[Diagnostics.Stopwatch]::StartNew()
        $failedAt=$null;$lastInvokeError=$null
        while($deadline.Elapsed.TotalSeconds -lt 10) {
            $dialogs=@(Find-ScreenshotDialogs)
            if($dialogs.Count -eq 1) {$ownedDialog=$dialogs[0];break}
            if($dialogs.Count -gt 1) {throw 'Multiple Screenshot save dialogs are visible.'}
            if(-not $failedAt -and $worker.HasExited -and (Test-Path -LiteralPath $workerResult)) {
                $actionResult=Get-Content -LiteralPath $workerResult -Raw -Encoding UTF8 | ConvertFrom-Json
                if($actionResult.status -eq 'failed'){$failedAt=$deadline.Elapsed.TotalSeconds;$lastInvokeError=[string]$actionResult.error}
            }
            # After a reported failure keep watching briefly: the action may still have opened its dialog.
            if($failedAt -and $deadline.Elapsed.TotalSeconds -gt $failedAt+1) {break}
            Start-Sleep -Milliseconds 100
        }
        $outcome='dialog';if(-not $ownedDialog){$outcome='no dialog'}
        $record['invoke_attempts']+=[ordered]@{attempt=$invokeAttempt;error=$lastInvokeError;result=$outcome}
        if(-not $ownedDialog -and -not $lastInvokeError) {throw 'The GNS3 Screenshot save dialog did not appear.'}
        $transient=$lastInvokeError -match '인식할 수 없는 오류|Unspecified error|0x80004005|no longer available'
        if(-not $ownedDialog -and -not $transient) {throw $lastInvokeError}
    }
    if(-not $ownedDialog) {throw $lastInvokeError}
    }
    $record['requested_path']=$Destination
    $record.phase='fill_filename';Save-ExportCheckpoint
    $record.dialog_title=$ownedDialog.Title
    $record.dialog_handle=$ownedDialog.Handle
    $record.dialog_process_id=$ownedDialog.ProcessId
    $record.dialog_class=$ownedDialog.ClassName
    [void][TopoLogWindowCapture]::PrepareWindow($ownedDialog.Handle,$true)
    Start-Sleep -Milliseconds 200
    if($ownedDialog.ClassName -eq '#32770') {
        # Standard Windows common-save dialog control IDs are language-neutral.
        $editHandle=[TopoLogWindowCapture]::FindFilenameControl($ownedDialog.Handle)
        Set-TopoLogDialogFilename -DialogHandle $ownedDialog.Handle -EditHandle $editHandle -Filename $Destination
        $record['filename_verified']=$true;Save-ExportCheckpoint
        $record.filename_control=[ordered]@{method='Win32 standard dialog control';control_id=[TopoLogWindowCapture]::ControlId($editHandle);handle=$editHandle}
        $saveHandle=[TopoLogWindowCapture]::FindDialogControl($ownedDialog.Handle,1,'Button')
        # Re-read just before Save: a user edit after verification must not be saved elsewhere.
        if(-not (Test-TopoLogFilenameText ([TopoLogWindowCapture]::ReadDialogFilename($editHandle)) $Destination)){throw 'The screenshot filename changed before saving; the dialog was cancelled.'}
        $record.phase='save_image'
        [TopoLogWindowCapture]::ClickDialogButton($saveHandle)
    } else {
    $dialogElement=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$ownedDialog.Handle)
    $edits=@($dialogElement.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Edit)))
    $filename=@($edits | Where-Object { $_.Current.AutomationId -in @('1001','fileNameEdit') -or (Get-UiName $_) -match '^(File name|Filename|파일 이름)\s*(\([A-Za-z]\))?\s*[:：]?$' })
    if($filename.Count -ne 1) {throw 'Could not uniquely identify the screenshot filename field.'}
    $value=$null
    if(-not $filename[0].TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$value)) {throw 'The screenshot filename field cannot be edited through accessibility.'}
    $record.filename_control=[ordered]@{name=(Get-UiName $filename[0]);automation_id=$filename[0].Current.AutomationId}
    $value.SetValue($Destination)
    $buttons=@($dialogElement.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Button)))
    $save=@($buttons | Where-Object {$_.Current.AutomationId -eq '1'})
    if($save.Count -ne 1) {$save=@($buttons | Where-Object {(Get-UiName $_) -match '^(Save|저장)(\s*\([A-Za-z]\))?$'})}
    if($save.Count -ne 1) {throw 'Could not uniquely identify the screenshot Save button.'}
    $record.phase='save_image'
    Invoke-UiElement $save[0]
    }
    $deadline=[Diagnostics.Stopwatch]::StartNew()
    $lastSize=-1;$stable=0
    while($deadline.Elapsed.TotalSeconds -lt 12) {
        if(Test-Path -LiteralPath $Destination) {
            $size=(Get-Item -LiteralPath $Destination).Length
            if($size -gt 0 -and $size -eq $lastSize) {$stable++}else{$stable=0}
            if($stable -ge 2){break}
            $lastSize=$size
        }
        Start-Sleep -Milliseconds 150
    }
    if(-not(Test-Path -LiteralPath $Destination) -or $stable -lt 2) {throw 'GNS3 did not finish saving the requested screenshot.'}
    $record.status='saved'
    $record.phase='finished'
    Write-Host ('GNS3 native topology screenshot saved: '+$Destination)
} catch {
    $record.error=$_.Exception.GetBaseException().Message
    if(-not $record.error){$record.error='GNS3 native export failed during '+$record.phase+' ('+$_.Exception.GetType().Name+')'}
    if($ownedDialog -and [string]$ownedDialog.ClassName -eq '#32770' -and $record.phase -eq 'fill_filename') {
        try {$record['dialog_controls']=@([TopoLogWindowCapture]::DescribeDialogControls([long]$ownedDialog.Handle))} catch {}
    }
    if($ownedDialog) {
        try {
            if($ownedDialog.ClassName -eq '#32770') {
                $cancelHandle=[TopoLogWindowCapture]::FindDialogControl($ownedDialog.Handle,2,'Button')
                [TopoLogWindowCapture]::ClickDialogButton($cancelHandle)
            } else {
                $dialogElement=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$ownedDialog.Handle)
                $cancel=$dialogElement.FindFirst([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::AutomationIdProperty,'2'))
                if($cancel) {Invoke-UiElement $cancel}
            }
        } catch { }
    }
    Write-Warning $record.error
} finally {
    if($menuWorker -and $record.status -ne 'saved') {
        try {
            foreach($toolsMenu in $toolsMenus) {
                $collapse=$null
                if($toolsMenu.TryGetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern,[ref]$collapse)){$collapse.Collapse()}
            }
        } catch {}
    }
    if($record.Contains('widened_window')) {
        try {$record['window_placement_restored']=[TopoLogWindowCapture]::RestorePlacement($WindowHandle,[string]$record['window_placement_before'])} catch {$record['window_placement_restored']=$false}
    }
    if($menuWorker){if(-not $menuWorker.HasExited -and -not $menuWorker.WaitForExit(500)){$menuWorker.Kill()};$menuWorker.Dispose()}
    if($worker) {
        if(-not $worker.HasExited -and -not $worker.WaitForExit(1000)) {$worker.Kill()}
        $worker.Dispose()
    }
    $record.finished_at=[DateTimeOffset]::Now.ToString('o')
    [IO.File]::WriteAllText([IO.Path]::GetFullPath($RecordPath),($record|ConvertTo-Json -Depth 6),[Text.UTF8Encoding]::new($false))
}
if($record.status -ne 'saved') {exit 1}
