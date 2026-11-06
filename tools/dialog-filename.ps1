function Test-TopoLogFilenameText([string]$Actual,[string]$Expected) {
    if($Actual.Length -ge 2 -and $Actual.StartsWith('"') -and $Actual.EndsWith('"')) {$Actual=$Actual.Substring(1,$Actual.Length-2)}
    if(-not [IO.Path]::IsPathRooted($Actual)){return $false}
    try {return [string]::Equals([IO.Path]::GetFullPath($Actual),[IO.Path]::GetFullPath($Expected),[StringComparison]::OrdinalIgnoreCase)}
    catch {return $false}
}
# Native common dialogs: avoid UIAutomation while Qt Invoke holds a modal RPC.
function Set-TopoLogDialogFilename([long]$EditHandle,[string]$Filename,[long]$DialogHandle) {
    $lastError='Filename entry did not stabilize.'
    for($attempt=0;$attempt -lt 3;$attempt++) {
        try {
            if($DialogHandle){$EditHandle=[TopoLogWindowCapture]::FindFilenameControl($DialogHandle)}
            if($attempt -eq 0){
                try {[TopoLogWindowCapture]::SetDialogFilename($EditHandle,$Filename)}
                catch {[TopoLogWindowCapture]::ReplaceDialogFilename($EditHandle,$Filename)}
            } else {[TopoLogWindowCapture]::ReplaceDialogFilename($EditHandle,$Filename)}
            $clock=[Diagnostics.Stopwatch]::StartNew();$stable=0
            while($clock.Elapsed.TotalSeconds -lt 2) {
                $actual=[TopoLogWindowCapture]::ReadDialogFilename($EditHandle)
                if(Test-TopoLogFilenameText $actual $Filename){$stable++}else{$stable=0;$lastError='Expected '+$Filename.Length+' characters; received '+$actual.Length+' characters.'}
                if($stable -ge 2){return}
                Start-Sleep -Milliseconds 100
            }
        } catch {$lastError=$_.Exception.GetBaseException().Message}
        Start-Sleep -Milliseconds 150
    }
    throw ('The filename field did not retain the requested path: '+$lastError)
}
