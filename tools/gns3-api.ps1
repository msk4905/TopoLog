#requires -Version 5.1

function New-TopoLogGns3Connection {
    [CmdletBinding()]
    param([uri]$BaseUri)
    $settings=@{}
    $configPath=Join-Path ([Environment]::GetFolderPath('ApplicationData')) 'GNS3\2.2\gns3_server.ini'
    if(Test-Path -LiteralPath $configPath) {
        $section=''
        foreach($line in (Get-Content -LiteralPath $configPath -Encoding UTF8)) {
            if($line -match '^\s*\[([^\]]+)\]'){$section=$matches[1]}
            elseif($section -eq 'Server' -and $line -match '^\s*([^=;#]+?)\s*=\s*(.*)$'){$settings[$matches[1].Trim()]=$matches[2].Trim()}
        }
    }
    $configuredUri=$null
    if($settings['host'] -and $settings['port']) {
        $serverHost=$settings['host']
        if($serverHost -in @('0.0.0.0','::')){$serverHost='127.0.0.1'}
        $scheme='http'; if($settings['protocol']){$scheme=$settings['protocol']}
        $configuredUri=[UriBuilder]::new($scheme,$serverHost,[int]$settings['port']).Uri
    }
    if(-not $BaseUri){if($configuredUri){$BaseUri=$configuredUri}else{$BaseUri=[uri]'http://127.0.0.1:3080'}}
    if($BaseUri.Scheme -notin @('http','https') -or $BaseUri.UserInfo -or $BaseUri.Query -or $BaseUri.Fragment){throw 'Use an HTTP(S) server URL without embedded credentials, query or fragment.'}
    $headers=@{}
    $configured=($settings['auth'] -match '^(?i:true|yes|1)$')
    $used=$false
    if($configured -and $configuredUri -and $BaseUri.Scheme -eq $configuredUri.Scheme -and
       $BaseUri.Host -eq $configuredUri.Host -and $BaseUri.Port -eq $configuredUri.Port -and $settings['user']) {
        $pair=$settings['user']+':'+$settings['password']
        $headers['Authorization']='Basic '+[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($pair))
        $pair=$null; $used=$true
    }
    $settings.Clear()
    # Headers are memory-only. Never serialize this connection object.
    [pscustomobject]@{BaseUri=$BaseUri.AbsoluteUri.TrimEnd('/'); Headers=$headers; ConfigPath=$configPath; AuthenticationConfigured=$configured; AuthenticationUsed=$used}
}
