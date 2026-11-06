#requires -Version 5.1
[CmdletBinding()]
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$BatchArguments)
$ErrorActionPreference='Stop'
$entry=Join-Path $PSScriptRoot 'batch.py'
. (Join-Path $PSScriptRoot 'python-launcher.ps1')
# With no remaining arguments the parameter is $null and @($null).Count is 1,
# so drop empty values before deciding to open the window.
$arguments=@($BatchArguments | Where-Object {$_})
if(-not $arguments.Count){$arguments=@('--gui')}
$probeCode="import sys; print('TOPOLOG_PYTHON_OK' if sys.version_info >= (3,10) else 'OLD')"
if($arguments -contains '--gui'){$probeCode="import sys, tkinter; print('TOPOLOG_PYTHON_OK' if sys.version_info >= (3,10) else 'OLD')"}
foreach($candidate in @(Get-TopoLogPythonCandidates -SourceRoot (Split-Path -Parent $PSScriptRoot))) {
    $prefix=@($candidate.Arguments)
    try {
        $probe=& $candidate.Executable @prefix -c $probeCode 2>$null
        if($LASTEXITCODE -ne 0 -or $probe -notcontains 'TOPOLOG_PYTHON_OK'){continue}
    } catch {continue}
    & $candidate.Executable @prefix $entry @arguments
    exit $LASTEXITCODE
}
Write-Host 'Python 3.10 or later is required. Install Python with tkinter and enable the Python launcher/PATH.'
exit 1
