param(
    [Parameter(Mandatory)][string]$RequestPath,
    [switch]$Worker,
    [switch]$LibraryOnly
)
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion -lt [version]'7.2') { throw 'Windows elevation requires PowerShell 7.2 or newer.' }
if (-not ('AgentConduct.ElevationNative' -as [type])) {
    Add-Type -Path (Join-Path $PSScriptRoot '..\assets\ElevationNative.cs')
}

function Write-ElevationTerminal {
    param($Request, [string]$State, [int]$Code, [bool]$Elevated)
    $value = @{ schema = 1; runId = $Request.runId; state = $State; exitCode = $Code; elevated = $Elevated }
    [AgentConduct.ElevationNative]::Append($Request.logPath, 'AGENT_ELEVATION_END ' + ($value | ConvertTo-Json -Compress))
}

function Read-ElevationTerminal {
    param([string]$LogPath, [string]$RunId)
    $line = Get-Content -LiteralPath $LogPath -Tail 1 -Encoding utf8
    if (-not $line -or -not $line.StartsWith('AGENT_ELEVATION_END ')) { return $null }
    try { $value = $line.Substring(20) | ConvertFrom-Json -ErrorAction Stop }
    catch [System.ArgumentException] { return $null }
    if ($value.schema -ne 1 -or $value.runId -cne $RunId) { throw 'Foreign or stale elevation terminal marker.' }
    if ($value.state -notin @('success', 'failed', 'canceled', 'probe') -or
        $value.exitCode -isnot [long] -and $value.exitCode -isnot [int] -or
        $value.elevated -isnot [bool]) { throw 'Malformed elevation terminal marker.' }
    if ($value.state -eq 'success' -and ($value.exitCode -ne 0 -or -not $value.elevated)) {
        throw 'Elevation terminal marker does not prove administrator success.'
    }
    if (($value.state -eq 'probe' -and $value.exitCode -ne 0) -or
        ($value.state -in @('failed', 'canceled') -and $value.exitCode -eq 0) -or
        ($value.state -eq 'canceled' -and ($value.exitCode -ne 1223 -or $value.elevated))) {
        throw 'Elevation terminal state contradicts its exit result.'
    }
    return $value
}

function Get-ElevationTaskCompletion {
    param($Task, [string]$LogPath, [string]$RunId)
    $instances = $Task.GetInstances(0).Count
    $state = $Task.State
    $lastResult = $Task.LastTaskResult
    $stopped = $instances -eq 0 -and $state -notin @(2, 4) -and $lastResult -notin @(267009, 267011)
    if ($stopped) {
        $terminal = Read-ElevationTerminal $LogPath $RunId
        if (-not $terminal) { throw "Interactive task exited without its matching terminal marker (result $lastResult)." }
        return $terminal
    }
    return $null
}

if ($LibraryOnly) { return }
$request = Get-Content -LiteralPath $RequestPath -Raw | ConvertFrom-Json
$startedPath = Join-Path (Split-Path -Parent $RequestPath) 'worker-started.json'
$code = 1
$admin = $false
$scriptRead = $null
try {
    if ($Worker -or $request.interactiveProbe) {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = [Security.Principal.WindowsPrincipal]::new($identity)
        $admin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        if (-not $request.interactiveProbe -and -not $admin) { throw 'RunAs worker did not receive an administrator token.' }
        if (-not $request.interactiveProbe -and $identity.User.Value -cne $request.ownerSid) {
            throw 'Alternate-account elevation is unsupported; the approved operation requires its original user identity.'
        }
        if ([Diagnostics.Process]::GetCurrentProcess().SessionId -ne $request.sessionId) { throw 'Worker reached the wrong desktop session.' }
        $scriptRead = [IO.File]::Open($request.scriptPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        if ((Get-FileHash -InputStream $scriptRead -Algorithm SHA256).Hash -cne $request.scriptHash) {
            throw 'Approved script changed before execution.'
        }
        $now = [DateTimeOffset]::UtcNow
        if ($now -ge [DateTimeOffset]::Parse($request.admissionDeadline)) {
            throw 'Consent admission deadline expired before payload execution.'
        }
        $remaining = [Math]::Floor(([DateTimeOffset]::Parse($request.deadline) - $now).TotalSeconds)
        if ($remaining -lt 1) { throw 'Elevation request deadline expired before payload admission.' }
        $payloadTimeout = [int][Math]::Min($request.timeoutSeconds, $remaining)
        [AgentConduct.ElevationNative]::OwnWorker()
        @{ runId = $request.runId; elevated = $admin; sessionId = $request.sessionId; pid = $PID
            desktopIdentity = [AgentConduct.ElevationNative]::TryDesktopIdentity($request.logPath) } |
            ConvertTo-Json -Compress | Set-Content -LiteralPath $startedPath -Encoding utf8NoBOM
        $code = [AgentConduct.ElevationNative]::RunPayload(
            $request.shellPath, $request.scriptPath, [string[]]$request.scriptArguments,
            $request.workingDirectory, $request.logPath, $payloadTimeout
        )
        $state = if ($code -ne 0) { 'failed' } elseif ($request.interactiveProbe) { 'probe' } else { 'success' }
        Write-ElevationTerminal $request $state $code $admin
    } else {
        $escaped = $RequestPath.Replace("'", "''")
        $script = $PSCommandPath.Replace("'", "''")
        $command = "& '$script' -RequestPath '$escaped' -Worker; exit `$LASTEXITCODE"
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
        $arguments = "--headless `"$($request.shellPath)`" -NoLogo -NoProfile -NonInteractive -EncodedCommand $encoded"
        # headless-guard: allow the explicitly approved UAC dialog, not a console window.
        $process = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\conhost.exe') `
            -Verb RunAs -ArgumentList $arguments -PassThru -Wait
        $code = $process.ExitCode
        if (-not (Read-ElevationTerminal $request.logPath $request.runId)) {
            $admin = $false
            if (Test-Path -LiteralPath $startedPath) {
                $started = Get-Content -LiteralPath $startedPath -Raw | ConvertFrom-Json
                if ($started.runId -cne $request.runId) { throw 'Foreign worker-started receipt.' }
                $admin = [bool]$started.elevated
            }
            if ($code -eq 0) { $code = 1 }
            Write-ElevationTerminal $request 'failed' $code $admin
        }
    }
} catch {
    [AgentConduct.ElevationNative]::Append($request.logPath, $_.Exception.ToString())
    $errorCause = $_.Exception
    while ($errorCause -and $errorCause -isnot [ComponentModel.Win32Exception]) { $errorCause = $errorCause.InnerException }
    $code = if ($errorCause) { $errorCause.NativeErrorCode } else { 1 }
    Write-ElevationTerminal $request $(if ($code -eq 1223 -and -not $admin) { 'canceled' } else { 'failed' }) $code $admin
} finally {
    if ($null -ne $scriptRead) { $scriptRead.Dispose() }
}
exit $code
