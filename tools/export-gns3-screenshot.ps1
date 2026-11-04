#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][long]$WindowHandle,
    [Parameter(Mandatory=$true)][int]$Gns3ProcessId,
    [Parameter(Mandatory=$true)][string]$Destination,
    [Parameter(Mandatory=$true)][string]$RecordPath
)

# Invoke GNS3's own QAction and automate only its owned Screenshot save dialog.
# This does not capture/crop desktop pixels or modify GNS3's installed files.
$ErrorActionPreference='Stop'
$record=[ordered]@{ method='GNS3 native Screenshot action';status='failed';phase='load_accessibility';action_name=$null;dialog_title=$null;dialog_handle=$null;dialog_process_id=$null;dialog_class=$null;dialog_reused=$false;filename_control=$null;available_actions=@();error=$null;finished_at=$null }
$ownedDialog=$null
$worker=$null

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
function Find-ScreenshotAction($Root) {
    $controls=$Root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
    foreach($element in $controls) {
        try {
            $type=$element.Current.ControlType
            if($type -ne [System.Windows.Automation.ControlType]::Button -and $type -ne [System.Windows.Automation.ControlType]::MenuItem) {continue}
            $name=Get-UiName $element
            $help=[string]$element.Current.HelpText
            $id=[string]$element.Current.AutomationId
            if($name -match '^(Take a screenshot|Screenshot)(\.\.\.|…)?$' -or $help -match '^Take a screenshot' -or $id -eq 'uiScreenshotAction') { return $element }
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
    $rootElement=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$WindowHandle)
    if($rootElement.Current.ProcessId -ne $Gns3ProcessId) {throw 'The target GNS3 window process changed.'}
    $action=Find-ScreenshotAction $rootElement
    if(-not $action) {
        $diagnostic=$rootElement.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
        $record.available_actions=@($diagnostic | Where-Object {$_.Current.ControlType -in @([System.Windows.Automation.ControlType]::Button,[System.Windows.Automation.ControlType]::MenuItem)} | ForEach-Object {[pscustomobject]@{name=(Get-UiName $_);automation_id=$_.Current.AutomationId}})
        $menuItems=$rootElement.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::MenuItem))
        $fileMenus=@($menuItems | Where-Object {(Get-UiName $_) -eq 'File'})
        if($fileMenus.Count -eq 1) {
            $expand=$null
            if($fileMenus[0].TryGetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern,[ref]$expand)) {$expand.Expand()}
            else {Invoke-UiElement $fileMenus[0]}
            Start-Sleep -Milliseconds 200
            $action=Find-ScreenshotAction $rootElement
            if(-not $action) {foreach($processRoot in (Get-ProcessRoots)) {$action=Find-ScreenshotAction $processRoot;if($action){break}}}
        }
    }
    if(-not $action) {throw 'GNS3 Take a screenshot action was not exposed through Windows accessibility.'}
    $record.action_name=Get-UiName $action
    $record.phase='invoke_action'
    $workingDirectory=[IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($RecordPath))
    $workerRequest=Join-Path $workingDirectory 'native-action-request.json'
    $workerResult=Join-Path $workingDirectory 'native-action-result.json'
    $actionRequest=[ordered]@{process_id=$Gns3ProcessId;runtime_id=($action.GetRuntimeId() -join ',');result_path=$workerResult}
    [IO.File]::WriteAllText($workerRequest,($actionRequest|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
    $shell='powershell.exe';if($PSVersionTable.PSEdition -eq 'Core'){$shell='pwsh.exe'}
    $workerScript=Join-Path $PSScriptRoot 'invoke-gns3-action.ps1'
    $worker=Start-Process -FilePath (Join-Path $PSHOME $shell) -WindowStyle Hidden -PassThru -ArgumentList @('-NoLogo','-NoProfile','-File',('"'+$workerScript+'"'),'-RequestPath',('"'+$workerRequest+'"'))
    $deadline=[Diagnostics.Stopwatch]::StartNew()
    while($deadline.Elapsed.TotalSeconds -lt 10) {
        $dialogs=@(Find-ScreenshotDialogs)
        if($dialogs.Count -eq 1) {$ownedDialog=$dialogs[0];break}
        if($dialogs.Count -gt 1) {throw 'Multiple Screenshot save dialogs are visible.'}
        if($worker.HasExited -and (Test-Path -LiteralPath $workerResult)) {
            $actionResult=Get-Content -LiteralPath $workerResult -Raw -Encoding UTF8 | ConvertFrom-Json
            if($actionResult.status -eq 'failed'){throw $actionResult.error}
        }
        Start-Sleep -Milliseconds 100
    }
    if(-not $ownedDialog) {throw 'The GNS3 Screenshot save dialog did not appear.'}
    }
    $record.phase='fill_filename'
    $record.dialog_title=$ownedDialog.Title
    $record.dialog_handle=$ownedDialog.Handle
    $record.dialog_process_id=$ownedDialog.ProcessId
    $record.dialog_class=$ownedDialog.ClassName
    [void][TopoLogWindowCapture]::PrepareWindow($ownedDialog.Handle,$true)
    Start-Sleep -Milliseconds 200
    if($ownedDialog.ClassName -eq '#32770') {
        # Standard Windows common-save dialog control IDs are language-neutral.
        $editHandle=[TopoLogWindowCapture]::FindFilenameControl($ownedDialog.Handle)
        [TopoLogWindowCapture]::SetDialogFilename($editHandle,$Destination)
        $record.filename_control=[ordered]@{method='Win32 standard dialog control';control_id=[TopoLogWindowCapture]::ControlId($editHandle);handle=$editHandle}
        $saveHandle=[TopoLogWindowCapture]::FindDialogControl($ownedDialog.Handle,1,'Button')
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
    if($worker) {
        if(-not $worker.HasExited -and -not $worker.WaitForExit(1000)) {$worker.Kill()}
        $worker.Dispose()
    }
    $record.finished_at=[DateTimeOffset]::Now.ToString('o')
    [IO.File]::WriteAllText([IO.Path]::GetFullPath($RecordPath),($record|ConvertTo-Json -Depth 6),[Text.UTF8Encoding]::new($false))
}
if($record.status -ne 'saved') {exit 1}
