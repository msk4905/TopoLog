#requires -Version 5.1
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$RequestPath)

# Separate worker because a provider may keep Invoke() blocked while its modal
# save dialog is open. The parent handles that dialog concurrently.
$ErrorActionPreference='Stop'
$request=Get-Content -LiteralPath $RequestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$result=[ordered]@{status='failed';error=$null;finished_at=$null}
function Save-Result {
    [IO.File]::WriteAllText([string]$request.result_path,($result|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
}
try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $condition=[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ProcessIdProperty,[int]$request.process_id)
    $roots=[System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,$condition)
    $target=$null
    foreach($root in $roots) {
        $elements=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
        foreach($element in $elements) {
            try {
                if(($element.GetRuntimeId() -join ',') -eq $request.runtime_id) {$target=$element;break}
            } catch { }
        }
        if($target){break}
    }
    if(-not $target){throw 'The selected GNS3 screenshot action is no longer available.'}
    $result.status='invoking';Save-Result
    $pattern=$null
    if($target.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$pattern)) {$pattern.Invoke()}
    else {
        $pattern=$null
        if(-not $target.TryGetCurrentPattern([System.Windows.Automation.LegacyIAccessiblePattern]::Pattern,[ref]$pattern)){throw 'The selected GNS3 action cannot be invoked.'}
        $pattern.DoDefaultAction()
    }
    $result.status='invoked'
} catch {
    $result.status='failed';$result.error=$_.Exception.GetBaseException().Message
} finally {
    $result.finished_at=[DateTimeOffset]::Now.ToString('o');Save-Result
}
if($result.status -eq 'failed'){exit 1}
