param(
    [Parameter(Mandatory)][string]$Root,
    [Parameter(Mandatory)][string]$Helper,
    [ValidateSet('expiry','closed','caller-loss','helper-loss','production')][string]$Scenario
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
. $Helper
$parameters = @{
    Service='fixture-shell'; RuntimeRoot=$Root; Source=$PSCommandPath
    RegistryRoot=(Join-Path $Root 'registry'); Purpose='unattended-test'
    TtlSeconds=4; MaxLifetimeSeconds=8
}
if ($Scenario -eq 'production') {
    $parameters.Purpose = 'production'
    $parameters.TtlSeconds = 0
    $parameters.MaxLifetimeSeconds = 0
} elseif ($Scenario -eq 'closed') {
    $parameters.TtlSeconds = 1
    $parameters.MaxLifetimeSeconds = 4
}
$first = Enter-DaemonInstance @parameters
$second = Enter-DaemonInstance @parameters
if ($first.instance_id -cne $second.instance_id -or $first.guard_token -ceq $second.guard_token) {
    throw 'Nested guards did not retain the original shell instance.'
}
if ($Scenario -in @('closed','production')) {
    Exit-DaemonInstance $second
    if ((Read-DaemonInstanceRecord $first.record_path).state -cne 'running') {
        throw 'Nested close ended the original admission early.'
    }
    Exit-DaemonInstance $first
    Exit-DaemonInstance $first
}

$child = $null
if ($Scenario -ne 'production') {
    function Quote-FixtureArgument([string]$Value) {
        '"' + (($Value -replace '(\\*)"', '$1$1\"') -replace '(\\+)$', '$1$1') + '"'
    }
    $source = Join-Path $Root 'child.py'
    $proof = Join-Path $Root 'child.json'
    $archive = Join-Path (Split-Path $Helper -Parent) 'daemon_instance_runtime.pyz'
    [IO.File]::WriteAllText($source, @'
import json,os,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from agent_procutil._daemon_instance_identity import birth_identity
proof=Path(sys.argv[2])
pending=proof.with_suffix('.pending')
pending.write_text(json.dumps({'pid':os.getpid(),'birth':birth_identity(os.getpid())}),encoding='ascii')
pending.replace(proof)
time.sleep(30)
'@)
    $python = @(Get-Command python -CommandType Application -ErrorAction Stop)[0]
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = $python.Path
    $start.Arguments = '-I -S -B ' + ((@($source, $archive, $proof) | ForEach-Object {
        Quote-FixtureArgument $_
    }) -join ' ')
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $launcher = [Diagnostics.Process]::Start($start)
    try {
        $wait = [Diagnostics.Stopwatch]::StartNew()
        while (-not [IO.File]::Exists($proof)) {
            if ($launcher.HasExited -or $wait.Elapsed.TotalSeconds -ge 5) {
                throw 'Fixture child did not publish its actual interpreter identity.'
            }
            Start-Sleep -Milliseconds 10
        }
        $child = [IO.File]::ReadAllText($proof) | ConvertFrom-Json
    } finally { $launcher.Dispose() }
}
$state = [AppDomain]::CurrentDomain.GetData('copilot-daemon-instance-shell-v1')
$record = Read-DaemonInstanceRecord $first.record_path
$current = [Diagnostics.Process]::GetCurrentProcess()
try {
    $outcome = @{
        parent=@{pid=$PID; birth=(Get-DaemonInstanceProcessBirth $current)}
        helper=@{pid=$state.worker.Id; birth=(Get-DaemonInstanceProcessBirth $state.worker)}
        child=$child; instance_id=$first.instance_id; record_path=$first.record_path
        state=$record.state; deadline=$record.deadline; hard_deadline=$record.hard_deadline
        job_scopes=$env:COPILOT_EXTENSIONS_OWNED_JOB_SCOPES
    }
} finally { $current.Dispose() }
$ready = Join-Path $Root 'ready.json'
[IO.File]::WriteAllText(($ready + '.pending'), ($outcome | ConvertTo-Json -Depth 5 -Compress))
[IO.File]::Move(($ready + '.pending'), $ready)
$clock = [Diagnostics.Stopwatch]::StartNew()
while ($clock.Elapsed.TotalSeconds -lt 15) { Start-Sleep -Milliseconds 20 }
throw 'Fixture observation deadline expired before its owner retired the original processes.'
