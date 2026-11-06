#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$NodeName = 'R1',
    [string]$ProbePath = '',
    [string]$OutputDirectory = '',
    [ValidateRange(5,60)][int]$CommandTimeoutSeconds = 20
)

# Technical prototype. Only the three fixed show commands below are sent.
# No enable, login, configuration, terminal-length, start or stop commands.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'project-paths.ps1')
$projectPaths=Get-TopoLogPaths -SourceRoot (Split-Path -Parent $PSScriptRoot)
$root = $projectPaths.DataRoot
if (-not $ProbePath) { $ProbePath = Join-Path $root 'work\gns3-probe.json' }
$probe = Get-Content -LiteralPath $ProbePath -Raw -Encoding UTF8 | ConvertFrom-Json
$probeProject = [string]$probe.target_project_name
if (-not $probeProject) { throw 'The probe does not identify a project.' }
# Refresh via read-only API requests so a previous console port is not reused.
& (Join-Path $PSScriptRoot 'probe-gns3.ps1') -ProjectName $probeProject -OutputPath $ProbePath
$probe = Get-Content -LiteralPath $ProbePath -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $probe.nodes_read -or -not $probe.target_project_found) { throw 'Run probe-gns3.ps1 successfully first.' }
if (((Get-Date) - [DateTimeOffset]::Parse($probe.collected_at).LocalDateTime).TotalMinutes -gt 15) {
    throw 'The probe is older than 15 minutes. Run probe-gns3.ps1 again first.'
}
$matches = @($probe.nodes | Where-Object { $_.name -eq $NodeName })
if ($matches.Count -ne 1) { throw 'The requested node is missing or ambiguous.' }
$node = $matches[0]
if ($node.node_type -ne 'dynamips' -or $node.console_type -ne 'telnet' -or $node.status -ne 'started') {
    throw 'This first prototype requires a running Dynamips node with a Telnet console.'
}
$consoleHost = [string]$node.console_host
if ($consoleHost -in @('', '0.0.0.0', '::')) { $consoleHost = ([uri]$probe.base_uri).Host }
$consolePort = [int]$node.console_port
if ($consolePort -lt 1 -or $consolePort -gt 65535) { throw 'Invalid console port.' }

if (-not ('TopoLogTelnetCodec' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Text;
public class TopoLogTelnetChunk {
    public byte[] Data;
    public byte[] Reply;
}
// Incremental Telnet decoder: preserves state when IAC sequences are split.
// Protocol reference: https://www.rfc-editor.org/rfc/rfc854.html
public class TopoLogTelnetCodec {
    private int state, verb;
    private readonly bool[] remoteEnabled = new bool[256];
    private readonly bool[] localEnabled = new bool[256];
    public TopoLogTelnetChunk Feed(byte[] bytes) {
        var data = new List<byte>();
        var reply = new List<byte>();
        foreach (byte b in bytes) {
            if (state == 0) { if (b == 255) state = 1; else data.Add(b); }
            else if (state == 1) {
                if (b == 255) { data.Add(b); state = 0; }
                else if (b >= 251 && b <= 254) { verb = b; state = 2; }
                else if (b == 250) state = 3;
                else state = 0;
            } else if (state == 2) {
                if (verb == 251) {
                    bool accept = b == 1 || b == 3;
                    if (!accept || !remoteEnabled[b]) reply.AddRange(new byte[] {255, (byte)(accept ? 253 : 254), b});
                    remoteEnabled[b] = accept;
                } else if (verb == 253) {
                    bool accept = b == 3;
                    if (!accept || !localEnabled[b]) reply.AddRange(new byte[] {255, (byte)(accept ? 251 : 252), b});
                    localEnabled[b] = accept;
                } else if (verb == 252) remoteEnabled[b] = false;
                else if (verb == 254) {
                    if (localEnabled[b]) reply.AddRange(new byte[] {255, 252, b});
                    localEnabled[b] = false;
                }
                state = 0;
            } else if (state == 3) { if (b == 255) state = 4; }
            else if (state == 4) { state = b == 240 ? 0 : 3; }
        }
        return new TopoLogTelnetChunk {Data = data.ToArray(), Reply = reply.ToArray()};
    }
    public static string Visible(string text) {
        text = System.Text.RegularExpressions.Regex.Replace(text, @"\x1B\[[0-?]*[ -/]*[@-~]", "");
        text = System.Text.RegularExpressions.Regex.Replace(text, @"\x1B\][^\x07]*(?:\x07|\x1B\\)", "");
        var result = new StringBuilder();
        foreach (char c in text.Replace("\r\n", "\n").Replace("\r\0", "\r")) {
            if (c == '\b') { if (result.Length > 0 && result[result.Length - 1] != '\n') result.Length--; }
            else if (c != '\0' && c != '\r' && c != '\x07') result.Append(c);
        }
        return result.ToString();
    }
}
'@
}

$stamp = (Get-Date -Format 'yyyyMMdd_HHmmss') + '_' + [guid]::NewGuid().ToString('N').Substring(0,8)
$safeNodeName = $NodeName -replace '[^A-Za-z0-9_-]', '_'
$outDir = Join-Path $root ('work\console_' + $safeNodeName + '_' + $stamp)
if($OutputDirectory) { $outDir=[IO.Path]::GetFullPath($OutputDirectory) }
if(Test-Path -LiteralPath $outDir) { throw 'Choose a new output directory; existing console evidence is not overwritten.' }
[IO.Directory]::CreateDirectory($outDir) | Out-Null
$utf8 = [Text.UTF8Encoding]::new($false)
$script:wire = [Collections.Generic.List[byte]]::new()
$script:sessionText = [Text.StringBuilder]::new()
$script:codec = [TopoLogTelnetCodec]::new()
$commands = @('show ip interface brief', 'show ip ospf neighbor', 'show ip route')
$results = [Collections.Generic.List[object]]::new()
$errorMessage = $null
$client = $null
$script:stream = $null
$startedAt = [DateTimeOffset]::Now.ToString('o')

function Send-ConsoleText([string]$Text) {
    $bytes = [Text.Encoding]::ASCII.GetBytes($Text)
    $script:stream.Write($bytes, 0, $bytes.Length)
    $script:stream.Flush()
}
function Read-ConsolePrompt {
    $text = [Text.StringBuilder]::new()
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $lastDataAt = 0
    $pages = 0
    $buffer = New-Object byte[] 8192
    while ($clock.Elapsed.TotalSeconds -lt $CommandTimeoutSeconds) {
        if ($script:stream.DataAvailable) {
            $count = $script:stream.Read($buffer, 0, $buffer.Length)
            if ($count -eq 0) { throw 'The console closed the connection.' }
            $received = New-Object byte[] $count
            [Array]::Copy($buffer, $received, $count)
            $script:wire.AddRange($received)
            if ($script:wire.Count -gt 4MB) { throw 'Console receive limit exceeded.' }
            $chunk = $script:codec.Feed($received)
            if ($chunk.Reply.Length) { $script:stream.Write($chunk.Reply,0,$chunk.Reply.Length) }
            $decoded = [Text.Encoding]::ASCII.GetString($chunk.Data)
            [void]$text.Append($decoded)
            [void]$script:sessionText.Append($decoded)
            $lastDataAt = $clock.ElapsedMilliseconds
            $raw = $text.ToString()
            if ($raw -match '(?im)(?:username|password|login):\s*$') {
                throw 'Console authentication is required. No credentials were sent.'
            }
            $newPages = [regex]::Matches($raw, '--More--').Count
            if ($newPages -gt $pages) { Send-ConsoleText ' '; $pages = $newPages }
        } else {
            $visible = [TopoLogTelnetCodec]::Visible($text.ToString())
            $prompt = [regex]::Match($visible, '(?m)^([A-Za-z0-9_.:/-]+(?:\([^\r\n]*\))?[>#])[ \t]*\z')
            if ($prompt.Success -and ($clock.ElapsedMilliseconds - $lastDataAt) -ge 200) {
                return [pscustomobject]@{ text = $text.ToString(); visible = $visible; prompt = $prompt.Groups[1].Value; pages = $pages }
            }
            if ($client.Client.Poll(0, [Net.Sockets.SelectMode]::SelectRead) -and $client.Available -eq 0) {
                throw 'The console closed the connection before a prompt was received.'
            }
            Start-Sleep -Milliseconds 30
        }
    }
    throw 'Timed out waiting for a complete CLI prompt. Partial output was retained in session files.'
}

try {
    Write-Host ('Connecting to ' + $NodeName + ' at ' + $consoleHost + ':' + $consolePort)
    $client = [Net.Sockets.TcpClient]::new()
    $pending = $client.BeginConnect($consoleHost, $consolePort, $null, $null)
    try {
        if (-not $pending.AsyncWaitHandle.WaitOne(6000)) { throw 'Console TCP connection timed out.' }
        $client.EndConnect($pending)
    } finally { $pending.AsyncWaitHandle.Close() }
    $script:stream = $client.GetStream()
    $script:stream.WriteTimeout = 3000
    Send-ConsoleText "`r`n"
    $initial = Read-ConsolePrompt
    if ($initial.prompt -match '\(') { throw 'The console is in a configuration mode. Return it to EXEC mode and retry.' }
    [IO.File]::WriteAllText((Join-Path $outDir 'initial.log'), $initial.text, $utf8)
    Write-Host ('CLI prompt: ' + $initial.prompt)
    $index = 0
    foreach ($command in $commands) {
        $index++
        $commandStarted = [DateTimeOffset]::Now.ToString('o')
        Send-ConsoleText ($command + "`r`n")
        $response = Read-ConsolePrompt
        if ($response.prompt -ne $initial.prompt) { throw 'The CLI prompt changed unexpectedly. Stopped before sending more commands.' }
        $stem = ('{0:D2}_' -f $index) + ($command -replace '[^A-Za-z0-9_-]', '_')
        $rawFile = $stem + '.raw.log'
        $textFile = $stem + '.txt'
        [IO.File]::WriteAllText((Join-Path $outDir $rawFile), $response.text, $utf8)
        [IO.File]::WriteAllText((Join-Path $outDir $textFile), $response.visible, $utf8)
        $cliError = ($response.visible -match '(?im)^\s*%\s*(Invalid input|Ambiguous command|Incomplete command|Authorization failed|Unrecognized command|Error)')
        $results.Add([pscustomobject]@{
            command = $command; started_at = $commandStarted; finished_at = [DateTimeOffset]::Now.ToString('o')
            collection_status = 'collected'; cli_error_detected = $cliError; verification_status = 'unreviewed'
            raw_text_file = $rawFile; readable_text_file = $textFile; pages_advanced = $response.pages
        })
        Write-Host ('Collected: ' + $command + ' (CLI error detected: ' + $cliError + ')')
    }
} catch {
    $errorMessage = $_.Exception.Message
    Write-Warning $errorMessage
} finally {
    if ($script:stream) { $script:stream.Dispose() }
    if ($client) { $client.Close() }
    [IO.File]::WriteAllBytes((Join-Path $outDir 'session.rx.bin'), $script:wire.ToArray())
    [IO.File]::WriteAllText((Join-Path $outDir 'session.raw.log'), $script:sessionText.ToString(), $utf8)
    $manifest = [ordered]@{
        project_name = $probe.target_project.name; project_id = $probe.target_project.project_id
        node_name = $node.name; node_id = $node.node_id; console_host = $consoleHost; console_port = $consolePort
        probe_collected_at = $probe.collected_at; collection_started_at = $startedAt; finished_at = [DateTimeOffset]::Now.ToString('o')
        collection_complete = ($null -eq $errorMessage -and $results.Count -eq $commands.Count)
        verification_status = 'unreviewed'; error = $errorMessage; commands = @($results.ToArray())
        raw_received_file = 'session.rx.bin'; raw_decoded_text_file = 'session.raw.log'
        note = 'Collected command output is evidence for review, not an automatic success verdict. Images are not generated by this prototype.'
    }
    [IO.File]::WriteAllText((Join-Path $outDir 'collection.json'), ($manifest | ConvertTo-Json -Depth 8), $utf8)
    Write-Host ('Saved: ' + $outDir)
}
if ($errorMessage) { exit 1 }
