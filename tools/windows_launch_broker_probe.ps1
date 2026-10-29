param([switch]$EphemeralCiConsent)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $EphemeralCiConsent -or $env:GITHUB_ACTIONS -ne 'true' -or
    $env:RUNNER_ENVIRONMENT -ne 'github-hosted' -or -not $env:RUNNER_TEMP) {
    throw 'Synthetic task registration requires explicit ephemeral-CI consent.'
}

$root = Join-Path $env:RUNNER_TEMP ("ce-broker-probe-" + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $root
$python = (Get-Command python -ErrorAction Stop).Source
$pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) { throw 'Actual GUI interpreter unavailable.' }
$probe = Join-Path $root 'probe.py'
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'windows_launch_broker_probe.py') -Destination $probe
$service = New-Object -ComObject 'Schedule.Service'
$service.Connect()
$folder = $service.GetFolder('\')
$name = 'CE-Broker-Probe-' + [guid]::NewGuid().ToString('N')
$task = $null
$instances = [Collections.Generic.List[object]]::new()
$results = [Collections.Generic.List[object]]::new()
$outcome = [ordered]@{
    scope = 'Synthetic activation experiment; not production lifetime or deployment qualification'
    supported = $false
    cleanup_confirmed = $false
    cycles = @()
}
try {
    $definition = $service.NewTask(0)
    $definition.RegistrationInfo.Description = 'Owned finite CI fixture; no credentials or triggers.'
    $definition.Principal.UserId = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $definition.Principal.LogonType = 2 # S4U; no stored password or network credentials.
    $definition.Principal.RunLevel = 0
    $definition.Settings.Enabled = $true
    $definition.Settings.AllowDemandStart = $true
    $definition.Settings.Hidden = $true
    $definition.Settings.DisallowStartIfOnBatteries = $false
    $definition.Settings.StopIfGoingOnBatteries = $false
    $definition.Settings.ExecutionTimeLimit = 'PT1M'
    $action = $definition.Actions.Create(0)
    $action.Path = $pythonw
    $action.Arguments = '"' + $probe + '" broker "' + $root + '" $(Arg0)'
    $action.WorkingDirectory = $root
    # TASK_CREATE refuses an existing name; never update or take over a task.
    $task = $folder.RegisterTaskDefinition(
        $name, $definition, 2, $definition.Principal.UserId, $null, 2, $null
    )
    foreach ($cycle in 1..2) {
        $nonce = [guid]::NewGuid().ToString('N')
        $instance = $task.RunEx($nonce, 0, 0, $null)
        $instances.Add($instance)
        $deadline = [DateTime]::UtcNow.AddSeconds(15)
        do {
            $instance.Refresh()
            if ($instance.State -eq 4 -and $instance.EnginePID -gt 0) { break }
            Start-Sleep -Milliseconds 50
        } while ([DateTime]::UtcNow -lt $deadline)
        if ($instance.State -ne 4 -or $instance.EnginePID -le 0) {
            throw 'Scheduler did not identify a running fixture instance.'
        }
        $engine = [int]$instance.EnginePID
        $instanceGuid = [string]$instance.InstanceGuid
        # Capture both streams without allocating another console window.
        $start = [Diagnostics.ProcessStartInfo]::new()
        $start.FileName = $python
        $start.ArgumentList.Add($probe)
        $start.ArgumentList.Add('inspect')
        $start.ArgumentList.Add($root)
        $start.ArgumentList.Add($nonce)
        $start.UseShellExecute = $false
        $start.CreateNoWindow = $true
        $start.RedirectStandardOutput = $true
        $start.RedirectStandardError = $true
        $process = [Diagnostics.Process]::Start($start)
        try {
            $stdout = $process.StandardOutput.ReadToEndAsync()
            $stderr = $process.StandardError.ReadToEndAsync()
            if (-not $process.WaitForExit(30000)) {
                $process.Kill($true)
                $process.WaitForExit()
                throw 'Native inspection deadline expired.'
            }
            [Threading.Tasks.Task]::WaitAll($stdout, $stderr)
            if ($process.ExitCode -ne 0) { throw ('Native inspection failed: ' + $stderr.Result) }
            $result = $stdout.Result | ConvertFrom-Json
            $results.Add([ordered]@{
                cycle = $cycle
                instance_guid = $instanceGuid
                engine_pid = $engine
                native_pipe_server_pid = $result.pid
                engine_matches_pipe_server = ($engine -eq $result.pid)
                native = $result
            })
        } finally {
            $process.Dispose()
        }
    }
    $outcome.supported = @($results | Where-Object {
        -not $_.engine_matches_pipe_server -or -not $_.native.child_escape_proved
    }).Count -eq 0 -and $results.Count -eq 2
} catch {
    $outcome.failure = $_.Exception.Message
} finally {
    try {
        if ($null -ne $task) {
            foreach ($running in @($task.GetInstances(0))) { $running.Stop() }
            $deadline = [DateTime]::UtcNow.AddSeconds(5)
            while ($task.GetInstances(0).Count -ne 0 -and [DateTime]::UtcNow -lt $deadline) {
                Start-Sleep -Milliseconds 50
            }
            if ($task.GetInstances(0).Count -ne 0) { throw 'Owned task instance exit unconfirmed.' }
            $folder.DeleteTask($name, 0)
        }
        $remaining = @($folder.GetTasks(1) | Where-Object { $_.Name -eq $name })
        $outcome.cleanup_confirmed = $remaining.Count -eq 0 -and $results.Count -eq 2 -and
            @($results | Where-Object { -not $_.native.child_cleanup_confirmed }).Count -eq 0
    } catch {
        $outcome.cleanup_failure = $_.Exception.Message
    }
    $outcome.cycles = @($results.ToArray())
    $outcome | ConvertTo-Json -Depth 10 |
        Set-Content -LiteralPath (Join-Path $root 'result.json') -Encoding utf8
    $outcome | ConvertTo-Json -Depth 10
}
if (-not $outcome.supported -or -not $outcome.cleanup_confirmed) { exit 1 }
$root | & $python (Join-Path $PSScriptRoot 'windows_broker_protocol_acceptance.py') `
    --ephemeral-ci-consent
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
