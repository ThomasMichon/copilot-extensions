# Native first-touch helpers, packaged independently of the Python installer engine.
function Get-InstallWatchdogBudget {
    param([string]$Payload, [string]$Name)

    $manifest = Get-Content -LiteralPath (Join-Path $Payload 'plugin.json') -Raw |
        ConvertFrom-Json
    if ($manifest -isnot [pscustomobject]) { throw "$Name`: installer manifest must be an object" }
    $variable = (($Name -replace '[^A-Za-z0-9]+', '_').ToUpperInvariant()) +
        '_INSTALL_DEADLINE_SEC'
    $raw = [Environment]::GetEnvironmentVariable($variable)
    if (-not $raw) { $raw = $env:COPILOT_PLUGIN_INSTALL_DEADLINE_SEC }
    $build = 480
    $cutover = 300
    foreach ($field in @('installerDeadlineSeconds', 'cutoverDeadlineSeconds')) {
        if ($field -eq 'installerDeadlineSeconds' -and $raw) { continue }
        $property = $manifest.PSObject.Properties[$field]
        if ($property) {
            if (($property.Value -isnot [int] -and $property.Value -isnot [long]) -or
                $property.Value -le 0 -or $property.Value -gt [int]::MaxValue) {
                throw "$Name`: $field must be a positive 32-bit integer"
            }
            if ($field -eq 'installerDeadlineSeconds') { $build = $property.Value }
            else { $cutover = $property.Value }
        }
    }
    if ($raw) {
        $parsed = 0L
        if (-not [long]::TryParse($raw, [ref]$parsed) -or
            $parsed -lt [int]::MinValue -or $parsed -gt [int]::MaxValue) {
            throw "$Name`: invalid installer deadline; expected 32-bit integer seconds"
        }
        $build = $parsed
    }
    return [pscustomobject]@{ BuildSec = [long]$build; CutoverSec = [long]$cutover }
}

function Enter-InstallPhase {
    param(
        [ValidateSet('cutover')][string]$Name,
        [long]$BudgetSec = 0
    )

    if (-not $env:COPILOT_PLUGIN_INSTALL_PHASE_FILE) { return }
    if ($BudgetSec -eq 0) {
        $BudgetSec = [long]$env:COPILOT_PLUGIN_INSTALL_CUTOVER_BUDGET_SEC
    }
    if ($BudgetSec -le 0 -or $BudgetSec -gt [int]::MaxValue) {
        throw 'Install phase requires a positive 32-bit budget.'
    }
    $path = $env:COPILOT_PLUGIN_INSTALL_PHASE_FILE
    $temporary = "$path.$PID.tmp"
    $phase = [ordered]@{
        phase = $Name
        extend_until = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() + $BudgetSec
    }
    [IO.File]::WriteAllText(
        $temporary, ($phase | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false)
    )
    if ([IO.File]::Exists($path)) {
        [IO.File]::Replace($temporary, $path, [System.Management.Automation.Language.NullString]::Value)
    }
    else { [IO.File]::Move($temporary, $path) }
}

function Open-InstallPhaseReceipt {
    param([string]$Path)

    return [IO.File]::Open(
        $Path, [IO.FileMode]::Open, [IO.FileAccess]::Read,
        ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)
    )
}

function Get-InstallWatchdogDeadline {
    param(
        [long]$BuildSec, [long]$CutoverSec, [long]$StartedAt, [string]$PhaseFile
    )

    $deadline = $StartedAt + $BuildSec
    $phase = 'build'
    if ([IO.File]::Exists($PhaseFile)) {
        try {
            $reader = [IO.StreamReader]::new((Open-InstallPhaseReceipt -Path $PhaseFile))
            try { $receipt = $reader.ReadToEnd() | ConvertFrom-Json }
            finally { $reader.Dispose() }
            if ($receipt.phase -cne 'cutover' -or
                ($receipt.extend_until -isnot [long] -and $receipt.extend_until -isnot [int]) -or
                $receipt.extend_until -le 0) {
                throw 'Invalid install-phase receipt.'
            }
            $phase = 'cutover'
            $deadline = [Math]::Min(
                $StartedAt + $BuildSec + $CutoverSec,
                [Math]::Max($deadline, [long]$receipt.extend_until)
            )
        } catch {
            $phase = 'invalid'
        }
    }
    return [pscustomobject]@{ Deadline = [long]$deadline; Phase = $phase }
}

function Stop-InstallWatchdogTree {
    param([Diagnostics.Process]$Child, [long]$ExpectedStartTimeTicks)

    if ($Child.HasExited) { return $false }
    # Retain the native process object through PID-based tree termination.
    [void]$Child.Handle
    if ($Child.StartTime.ToUniversalTime().Ticks -ne $ExpectedStartTimeTicks) {
        throw 'Watchdog target identity mismatch; refusing tree termination.'
    }
    if ($Child.HasExited) { return $false }
    try { & taskkill.exe /PID $Child.Id /T /F 2>&1 | Out-Null } catch {
        [Console]::Error.WriteLine("Watchdog tree termination failed: $_")
    }
    if (-not $Child.HasExited) {
        # The retained Process instance, not an unbound PID, owns this fallback.
        try { $Child.Kill() } catch [InvalidOperationException] {
            if (-not $Child.HasExited) { throw }
        }
    }
    return $true
}

function Wait-InstallWatchdog {
    param(
        [Diagnostics.Process]$Child, $Budget, [long]$StartedAt,
        [string]$PhaseFile, [string]$Name, [string]$Stage, [string]$LogPath
    )

    if ($Budget.BuildSec -le 0) {
        $Child.WaitForExit()
        return $Child.ExitCode
    }
    if ($Child.HasExited) { return $Child.ExitCode }
    [void]$Child.Handle
    $expectedStartTimeTicks = $Child.StartTime.ToUniversalTime().Ticks
    $invalidReported = $false
    while (-not $Child.HasExited) {
        $state = Get-InstallWatchdogDeadline -BuildSec $Budget.BuildSec `
            -CutoverSec $Budget.CutoverSec -StartedAt $StartedAt -PhaseFile $PhaseFile
        if ($state.Phase -eq 'invalid' -and -not $invalidReported) {
            [Console]::Error.WriteLine('Invalid install-phase receipt; retaining the build deadline.')
            $invalidReported = $true
        }
        $remaining = $state.Deadline - [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        if ($remaining -le 0) {
            $termination = 'requested'
            try {
                if (-not (Stop-InstallWatchdogTree -Child $Child `
                    -ExpectedStartTimeTicks $expectedStartTimeTicks)) {
                    return $Child.ExitCode
                }
            } catch {
                [Console]::Error.WriteLine("Watchdog tree termination refused or failed: $_")
                $termination = 'failed'
            }
            $message = (
                '[{0}] WATCHDOG-KILL {1}: phase={2}, build={3}s, cutover={4}s ' +
                '(child pid {5}); tree termination={7}. Stage: {6}'
            ) -f ([DateTimeOffset]::UtcNow.ToString('s') + 'Z'), $Name, $state.Phase,
                $Budget.BuildSec, $Budget.CutoverSec, $Child.Id, $Stage, $termination
            [Console]::Error.WriteLine($message)
            try {
                [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($LogPath))
                Add-Content -LiteralPath $LogPath -Value $message
            } catch {
                [Console]::Error.WriteLine("Watchdog log write failed: $_")
            }
            return 124
        }
        [void]$Child.WaitForExit([int]([Math]::Min(5, $remaining) * 1000))
    }
    return $Child.ExitCode
}
