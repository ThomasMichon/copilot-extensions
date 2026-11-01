param(
    [ValidateSet('caller','execute','writer','leaf')][string]$Role = 'caller',
    [Parameter(Mandatory)][string]$Root,
    [Parameter(Mandatory)][string]$Executable,
    [string]$Helper,
    [ValidateSet('caller-loss','helper-loss','cancel')][string]$Scenario = 'cancel'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2

function Publish-Fixture {
    param([string]$Name, $Value)
    $target = Join-Path $Root ($Name + '.json')
    $pending = Join-Path $Root ($Name + '.pending')
    [IO.File]::WriteAllText($pending, ($Value | ConvertTo-Json -Depth 8 -Compress))
    [IO.File]::Move($pending, $target)
    [IO.File]::WriteAllText((Join-Path $Root ($Name + '.closed')), 'closed')
}

function Get-FixtureIdentity {
    param([Diagnostics.Process]$Process)
    [void]$Process.Handle
    return @{
        pid=$Process.Id
        birth=('windows:' + $Process.StartTime.ToUniversalTime().ToFileTimeUtc().ToString(
            [Globalization.CultureInfo]::InvariantCulture
        ))
    }
}

if ($Role -in @('writer','leaf')) {
    if ($Role -eq 'writer') {
        $literalRoot = "'" + $Root.Replace("'", "''") + "'"
        $literalFixture = "'" + $PSCommandPath.Replace("'", "''") + "'"
        $literalExecutable = "'" + $Executable.Replace("'", "''") + "'"
        $body = "& $literalFixture -Role leaf -Root $literalRoot -Executable $literalExecutable"
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($body))
        $start = New-Object Diagnostics.ProcessStartInfo
        $start.FileName = $Executable
        $start.Arguments = '-NoLogo -NoProfile -NonInteractive -EncodedCommand ' + $encoded
        $start.UseShellExecute = $false
        $start.CreateNoWindow = $true
        $child = [Diagnostics.Process]::Start($start)
        $child.Dispose()
    }
    $current = [Diagnostics.Process]::GetCurrentProcess()
    try { Publish-Fixture $Role (Get-FixtureIdentity $current) }
    finally { $current.Dispose() }
    Start-Sleep -Seconds 40
    throw 'Owned writer survived its required retirement.'
}

if ($Role -eq 'execute') {
    . $Helper
    try {
        $code = Invoke-NativeOwnedProcess -FilePath $Executable -Arguments @(
            '-NoLogo','-NoProfile','-NonInteractive','-File',$PSCommandPath,
            '-Role','writer','-Root',$Root,'-Executable',$Executable
        ) -WorkingDirectory $Root -LogPath (Join-Path $Root 'writers.log') -TimeoutMilliseconds 30000
        Publish-Fixture 'outcome' @{status='returned';code=$code}
    } catch {
        $settled = $_.Exception.Data['CleanupConfirmed']
        Publish-Fixture 'outcome' @{
            status='refused';cleanup_confirmed=($settled -is [bool] -and $settled -ceq $true)
        }
    }
    return
}

. (Join-Path (Split-Path $Helper) 'daemon_instances.ps1')
$gate = [string]::Intern('copilot-daemon-instance-shell-v1')
$runner = [PowerShell]::Create()
$null = $runner.AddScript({
    param($Fixture,$Area,$Shell,$Adapter)
    & $Fixture -Role execute -Root $Area -Executable $Shell -Helper $Adapter
}).AddArgument($PSCommandPath).AddArgument($Root).AddArgument($Executable).AddArgument($Helper)
try {
    $async = $runner.BeginInvoke()
    $clock = [Diagnostics.Stopwatch]::StartNew()
    while (-not ([IO.File]::Exists((Join-Path $Root 'writer.closed')) -and
        [IO.File]::Exists((Join-Path $Root 'leaf.closed')))) {
        if ($async.IsCompleted -or $clock.Elapsed.TotalSeconds -ge 15) {
            throw ('Owned writer startup failed: ' + ($runner.Streams.Error | Out-String))
        }
        Start-Sleep -Milliseconds 10
    }
    $state = [AppDomain]::CurrentDomain.GetData($gate)
    $current = [Diagnostics.Process]::GetCurrentProcess()
    try {
        $identities = @{
            parent=(Get-FixtureIdentity $current)
            helper=(Get-FixtureIdentity $state.worker)
            launcher=(Get-FixtureIdentity $state.process)
            writer=([IO.File]::ReadAllText((Join-Path $Root 'writer.json')) | ConvertFrom-Json)
            leaf=([IO.File]::ReadAllText((Join-Path $Root 'leaf.json')) | ConvertFrom-Json)
        }
        Publish-Fixture 'ready' $identities
    } finally { $current.Dispose() }
    $clock.Restart()
    while (-not [IO.File]::Exists((Join-Path $Root 'release'))) {
        if ($clock.Elapsed.TotalSeconds -ge 10) { throw 'Fixture owner did not release its pins.' }
        Start-Sleep -Milliseconds 10
    }
    if ($Scenario -eq 'cancel') { $runner.Stop() }
    $clock.Restart()
    while (-not $async.IsCompleted) {
        if ($clock.Elapsed.TotalSeconds -ge 5) { throw 'Owned request did not settle after owner action.' }
        Start-Sleep -Milliseconds 10
    }
    if ($Scenario -eq 'cancel') {
        Publish-Fixture 'completed' @{
            cancelled=$true;broken=$state.broken;helper_job_closed=$state.job.IsClosed
        }
    } else {
        $null = $runner.EndInvoke($async)
        if (-not [IO.File]::Exists((Join-Path $Root 'outcome.closed'))) {
            throw 'Lost-helper refusal has no closed outcome.'
        }
        $outcome = [IO.File]::ReadAllText((Join-Path $Root 'outcome.json')) | ConvertFrom-Json
        Publish-Fixture 'completed' $outcome
    }
} finally {
    try { $runner.Stop() }
    finally { $runner.Dispose() }
}
