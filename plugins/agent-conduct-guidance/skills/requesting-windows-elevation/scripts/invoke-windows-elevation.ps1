[CmdletBinding(DefaultParameterSetName = 'Start')]
param(
    [Parameter(Mandatory, ParameterSetName = 'Start')][string]$ScriptPath,
    [Parameter(Mandatory, ParameterSetName = 'Start')][string]$LogPath,
    [Parameter(ParameterSetName = 'Start')][string[]]$ScriptArguments = @(),
    [Parameter(ParameterSetName = 'Start')][string]$WorkingDirectory = (Get-Location).Path,
    [Parameter(ParameterSetName = 'Start')][int]$SessionId = 0,
    [Parameter(ParameterSetName = 'Start')][ValidateRange(1, 3600)][int]$TimeoutSeconds = 600,
    [Parameter(ParameterSetName = 'Start')][ValidateRange(1, 1800)][int]$ConsentTimeoutSeconds = 300,
    [Parameter(ParameterSetName = 'Start')][switch]$ConsentConfirmed,
    [Parameter(ParameterSetName = 'Start')][switch]$InteractiveProbe,
    [Parameter(Mandatory, ParameterSetName = 'Wait')][string]$WaitFor
)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT' -or $PSVersionTable.PSVersion -lt [version]'7.2') {
    throw 'This helper requires Windows and PowerShell 7.2 or newer.'
}
$workerScript = Join-Path $PSScriptRoot 'elevation-worker.ps1'
. $workerScript -RequestPath 'unused' -LibraryOnly
$scheduler = New-Object -ComObject Schedule.Service
$scheduler.Connect()
$folder = $scheduler.GetFolder('\')
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value

if ($PSCmdlet.ParameterSetName -eq 'Start') {
    if (-not $InteractiveProbe -and -not $ConsentConfirmed) { throw 'Obtain explicit operation-specific consent before requesting RunAs.' }
    if (-not $InteractiveProbe -and -not [AgentConduct.ElevationNative]::CanElevateSameAccount()) {
        throw 'This helper requires same-account administrator or linked split-token elevation; alternate-account credentials are unsupported.'
    }
    $ScriptPath = (Resolve-Path -LiteralPath $ScriptPath).Path
    $WorkingDirectory = (Resolve-Path -LiteralPath $WorkingDirectory).Path
    if (-not [IO.File]::Exists($ScriptPath) -or [IO.Path]::GetExtension($ScriptPath) -ne '.ps1') {
        throw 'Provide one existing reviewed PowerShell script.'
    }
    $probePath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\assets\probe.ps1')).Path
    if ($InteractiveProbe -and -not [StringComparer]::OrdinalIgnoreCase.Equals($ScriptPath, $probePath)) {
        throw 'InteractiveProbe permits only the bundled harmless probe, not another operation.'
    }
    $LogPath = [IO.Path]::GetFullPath($LogPath)
    $parent = Split-Path -Parent $LogPath
    if (-not [IO.Directory]::Exists($parent)) { throw 'Prepare a private timestamped scratch directory before specifying the log.' }
    $allowed = @($sid, 'S-1-5-18', 'S-1-5-32-544')
    foreach ($rule in (Get-Acl -LiteralPath $parent).Access) {
        if ($rule.AccessControlType -eq 'Allow' -and
            $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -notin $allowed) {
            throw 'The requested log directory grants access beyond its owner, SYSTEM and Administrators.'
        }
    }
    $sessions = @([AgentConduct.ElevationNative]::InteractiveSessions($sid))
    if ($SessionId -eq 0) {
        if ($sessions.Count -ne 1) { throw "Select one active same-user desktop with -SessionId. Candidates: $($sessions -join ', ')" }
        $SessionId = $sessions[0]
    }
    if ($SessionId -notin $sessions) { throw 'Selected desktop is not an active interactive session of the current user.' }
    $runId = [Guid]::NewGuid().ToString('N')
    $stream = [IO.File]::Open($LogPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    $stream.Dispose()
    $stage = Join-Path $parent "elevation-$runId"
    New-Item -ItemType Directory -Path $stage -ErrorAction Stop | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $stage 'scripts') | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $stage 'assets') | Out-Null
    Copy-Item -LiteralPath $workerScript -Destination (Join-Path $stage 'scripts\elevation-worker.ps1')
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot '..\assets\ElevationNative.cs') -Destination (Join-Path $stage 'assets\ElevationNative.cs')
    $scriptHash = (Get-FileHash -LiteralPath $ScriptPath -Algorithm SHA256).Hash
    $requestedAt = [DateTimeOffset]::UtcNow
    $request = [ordered]@{
        schema = 1; runId = $runId; ownerSid = $sid
        scriptPath = $ScriptPath; scriptHash = $scriptHash
        scriptArguments = $ScriptArguments; workingDirectory = $WorkingDirectory; logPath = $LogPath
        sessionId = $SessionId; shellPath = Join-Path $PSHOME 'pwsh.exe'
        timeoutSeconds = $TimeoutSeconds; interactiveProbe = [bool]$InteractiveProbe
        taskName = "AgentConduct-Elevation-$runId"
        admissionDeadline = $requestedAt.AddSeconds($ConsentTimeoutSeconds).ToString('O')
        deadline = $requestedAt.AddSeconds($ConsentTimeoutSeconds + $TimeoutSeconds + 30).ToString('O')
    }
    $WaitFor = Join-Path $stage 'request.json'
    $request | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $WaitFor -Encoding utf8NoBOM
    [AgentConduct.ElevationNative]::Append($LogPath, 'AGENT_ELEVATION_BEGIN ' + ($request.runId))
    $definition = $scheduler.NewTask(0)
    $definition.RegistrationInfo.Description = 'One operation: interactive consent broker, not a persistent elevation grant.'
    $definition.Principal.UserId = $sid
    $definition.Principal.LogonType = 3
    $definition.Principal.RunLevel = 0
    $definition.Settings.AllowDemandStart = $true
    $definition.Settings.DisallowStartIfOnBatteries = $false
    $definition.Settings.StopIfGoingOnBatteries = $false
    $definition.Settings.ExecutionTimeLimit = "PT$($ConsentTimeoutSeconds + $TimeoutSeconds + 30)S"
    $definition.Settings.MultipleInstances = 2
    $definition.Settings.Hidden = $true
    $action = $definition.Actions.Create(0)
    $action.Path = Join-Path $env:SystemRoot 'System32\conhost.exe'
    $broker = (Join-Path $stage 'scripts\elevation-worker.ps1').Replace("'", "''")
    $encodedRequest = $WaitFor.Replace("'", "''")
    $command = "& '$broker' -RequestPath '$encodedRequest'; exit `$LASTEXITCODE"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    $action.Arguments = "--headless `"$($request.shellPath)`" -NoLogo -NoProfile -NonInteractive -EncodedCommand $encoded"
    $action.WorkingDirectory = $WorkingDirectory
    $descriptor = "D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;$sid)"
    $task = $folder.RegisterTaskDefinition($request.taskName, $definition, 2, $sid, $null, 3, $descriptor)
    Write-Host "Elevation request: $WaitFor"
    Write-Host "Log: $LogPath"
    $running = $task.RunEx($null, 4, $SessionId, $null)
} else {
    $WaitFor = (Resolve-Path -LiteralPath $WaitFor).Path
    $request = Get-Content -LiteralPath $WaitFor -Raw | ConvertFrom-Json
    if ($request.schema -ne 1 -or $request.ownerSid -cne $sid -or
        $request.runId -notmatch '^[0-9a-f]{32}$' -or
        $request.taskName -cne "AgentConduct-Elevation-$($request.runId)") {
        throw 'Cannot resume a foreign or malformed elevation request.'
    }
    $registered = @($folder.GetTasks(1) | Where-Object Name -CEQ $request.taskName)
    if ($registered.Count -eq 0) {
        $terminal = Read-ElevationTerminal $request.logPath $request.runId
        if (-not $terminal) { throw 'Elevation task is missing without its matching terminal result; retain the request and log.' }
        $terminal | ConvertTo-Json -Compress
        exit $terminal.exitCode
    }
    if ($registered.Count -ne 1) { throw 'Ambiguous elevation task identity.' }
    $task = $registered[0]
}

while ($true) {
    $terminal = Get-ElevationTaskCompletion $task $request.logPath $request.runId
    if ($terminal) {
        $principal = $task.Definition.Principal.UserId
        $principalSid = if ($principal.StartsWith('S-1-')) { $principal } else {
            [Security.Principal.NTAccount]::new($principal).Translate([Security.Principal.SecurityIdentifier]).Value
        }
        if ($principalSid -cne $sid -or $task.Definition.Principal.RunLevel -ne 0) {
            throw 'Task ownership changed; retain it for explicit diagnosis.'
        }
        $folder.DeleteTask($request.taskName, 0)
        $terminal | ConvertTo-Json -Compress
        exit $terminal.exitCode
    }
    if ([DateTimeOffset]::UtcNow -ge [DateTimeOffset]::Parse($request.deadline)) {
        throw "Elevation did not reach a confirmed terminal result. Retain task '$($request.taskName)', request '$WaitFor' and log; diagnose, do not retry."
    }
    Start-Sleep -Milliseconds 500
}
