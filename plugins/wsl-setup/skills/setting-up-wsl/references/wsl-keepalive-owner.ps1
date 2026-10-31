param(
    [Parameter(Mandatory)][string]$Distro,
    [string]$Service
)

$ErrorActionPreference = 'Stop'
$helper = Join-Path $PSScriptRoot 'daemon_instances.ps1'
if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) {
    $helper = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..\..\..\libs\agent-procutil\src\agent_procutil\daemon_instances.ps1'))
}
if (-not (Test-Path -LiteralPath $helper -PathType Leaf) -or
    ((Get-Item -LiteralPath $helper).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'The common daemon instance binding is unavailable.'
}
if ($env:COPILOT_EXTENSIONS_DAEMON_TEST_CONTEXT) {
    throw 'Ephemeral WSL keepalive requires an independently admitted in-distro lifetime.'
}
if ($Service -and $Service -cnotmatch '^[A-Za-z0-9_.@:-]+$') {
    throw 'A systemd unit name is required.'
}
. $helper
$instance = Enter-DaemonInstance -Service 'wsl-keepalive' -RuntimeRoot $PSScriptRoot -Source $PSCommandPath -Purpose production

function Quote-KeepaliveArgument([string]$Value) {
    $escaped = [regex]::Replace($Value, '(\\*)"', {
        param($match)
        $match.Groups[1].Value + $match.Groups[1].Value + '\"'
    })
    '"' + [regex]::Replace($escaped, '(\\+)$', '$1$1') + '"'
}

try {
    if ((Read-DaemonInstanceRecord $instance.record_path).purpose -ne 'production') {
        throw 'Ephemeral WSL keepalive requires an independently admitted in-distro lifetime.'
    }
    $inner = if ($Service) { "systemctl start '$Service'; exec sleep infinity" } else { 'exec sleep infinity' }
    $arguments = @('-d', $Distro, '-u', 'root', '--exec', '/bin/sh', '-c', $inner)
    $childInstance = New-DaemonInstanceReservation -Service 'wsl-keepalive-transport' `
        -RuntimeRoot $PSScriptRoot -Source $PSCommandPath
    $process = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\wsl.exe') `
        -ArgumentList (($arguments | ForEach-Object { Quote-KeepaliveArgument $_ }) -join ' ') `
        -NoNewWindow -PassThru
    $null = Set-DaemonInstanceProcess $childInstance $process -Admitted
    $process.WaitForExit()
    Exit-DaemonInstance -Receipt $childInstance -Process $process
    exit $process.ExitCode
} finally {
    Exit-DaemonInstance -Receipt $instance
}
