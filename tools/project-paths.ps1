#requires -Version 5.1

function Get-TopoLogPaths {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][string]$SourceRoot)

    $source=[IO.Path]::GetFullPath($SourceRoot).TrimEnd('\')
    $data=$source
    # Optional local output placement, independent of development references.
    $settingsFile=Join-Path $source '.topolog-workspace.json'
    if(Test-Path -LiteralPath $settingsFile -PathType Leaf) {
        $settings=Get-Content -LiteralPath $settingsFile -Raw -Encoding UTF8 | ConvertFrom-Json
        if(-not $settings.data_directory){throw 'Workspace settings require data_directory.'}
        if([IO.Path]::IsPathRooted($settings.data_directory)){$data=[IO.Path]::GetFullPath($settings.data_directory)}
        else{$data=[IO.Path]::GetFullPath((Join-Path $source $settings.data_directory))}
    }
    return [pscustomobject]@{
        SourceRoot=$source
        DataRoot=$data
        WorkDirectory=(Join-Path $data 'work')
        ExportDirectory=(Join-Path $data 'exports')
        ReleaseDirectory=(Join-Path $data 'release')
    }
}
