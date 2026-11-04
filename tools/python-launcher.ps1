#requires -Version 5.1
function Get-TopoLogPythonCandidates {
    param([Parameter(Mandatory=$true)][string]$SourceRoot)
    $names=[Collections.Generic.List[string]]::new()
    if($env:TOPOLOG_PYTHON){$names.Add($env:TOPOLOG_PYTHON)}
    $localSettings=Join-Path $SourceRoot '.topolog-workspace.json'
    if(Test-Path -LiteralPath $localSettings -PathType Leaf){
        $settings=Get-Content -LiteralPath $localSettings -Raw -Encoding UTF8 | ConvertFrom-Json
        if($settings.python_executable){$names.Add([string]$settings.python_executable)}
    }
    foreach($name in @('py','python','python3')){$names.Add($name)}
    foreach($registryPath in @('HKCU:\Software\Python\PythonCore','HKLM:\Software\Python\PythonCore','HKLM:\Software\WOW6432Node\Python\PythonCore')){
        try {
            foreach($version in @(Get-ChildItem -LiteralPath $registryPath -ErrorAction Stop)){
                $key=Get-Item -LiteralPath (Join-Path $version.PSPath 'InstallPath') -ErrorAction Stop
                $executable=$key.GetValue('ExecutablePath')
                if($executable){$names.Add([string]$executable)}
                elseif($key.GetValue('')){$names.Add((Join-Path ([string]$key.GetValue('')) 'python.exe'))}
            }
        } catch { }
    }
    foreach($folder in @((Join-Path $env:LOCALAPPDATA 'Programs\Python'),$env:ProgramFiles,${env:ProgramFiles(x86)})){
        if(-not $folder -or -not (Test-Path -LiteralPath $folder -PathType Container)){continue}
        foreach($directory in @(Get-ChildItem -LiteralPath $folder -Directory -Filter 'Python*' -ErrorAction SilentlyContinue)){
            $names.Add((Join-Path $directory.FullName 'python.exe'))
        }
    }
    $seen=@{}
    foreach($name in $names){
        $candidate=Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if(-not $candidate -or $seen.ContainsKey($candidate.Source)){continue}
        $seen[$candidate.Source]=$true
        $arguments=@()
        if([IO.Path]::GetFileNameWithoutExtension($candidate.Source) -eq 'py'){$arguments=@('-3')}
        [pscustomobject]@{Executable=$candidate.Source;Arguments=$arguments}
    }
}
