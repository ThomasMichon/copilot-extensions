#Requires -Version 7.0
# agent-worktrees:native-seed-boundary-v1
# Dot-sourced into an existing lifetime owner, never launched as another waiter.

function Get-AwNativeSeedRuntime {
    param([string]$Root)
    if (-not [IO.Path]::IsPathRooted($Root)) { return $null }
    try {
        $version = [IO.File]::ReadAllText((Join-Path $Root 'current-version')).Trim()
        if ($version -notmatch '^[A-Za-z0-9][A-Za-z0-9._+-]{0,255}$') { return $null }
        $slot = Join-Path (Join-Path $Root 'versions') $version
        $python = Join-Path $slot 'Scripts\python.exe'
        $bootstrap = Join-Path $slot '.runtime-bootstrap\runtime_launcher.py'
        foreach ($file in @($python, $bootstrap, (Join-Path $slot '.install-complete.json'))) {
            if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { return $null }
        }
        return @{ Python = $python; Bootstrap = $bootstrap }
    } catch { return $null }
}

function Invoke-AwNativeSeedPhase {
    param([string]$Root, [string[]]$Arguments)
    $runtime = Get-AwNativeSeedRuntime $Root
    if (-not $runtime) { return $null }
    $process = $null
    try {
        $info = [Diagnostics.ProcessStartInfo]::new($runtime.Python)
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        $info.RedirectStandardInput = $true
        $info.RedirectStandardOutput = $true
        $info.RedirectStandardError = $true
        $info.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
        $info.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
        foreach ($argument in (@(
            '-I', '-S', '-B', '-X', 'utf8', $runtime.Bootstrap,
            '-m', 'agent_worktrees.launch_seed_native',
            '--contract-version', '1', '--root', $Root
        ) + $Arguments)) { $info.ArgumentList.Add([string]$argument) }
        $process = [Diagnostics.Process]::Start($info)
        $process.StandardInput.Close()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(5000)) {
            $process.Kill($true)
            $null = $process.WaitForExit(1000)
            return $null
        }
        if (-not [Threading.Tasks.Task]::WaitAll(
            [Threading.Tasks.Task[]]@($stdout, $stderr), 1000
        )) { return $null }
        if ($process.ExitCode -ne 0) { return $null }
        return ConvertFrom-Json -InputObject $stdout.GetAwaiter().GetResult() -ErrorAction Stop
    } catch {
        if ($process) {
            try { if (-not $process.HasExited) { $process.Kill($true) } } catch {}
        }
        return $null
    }
    finally {
        if ($process) { $process.Dispose() }
    }
}

function Invoke-AwNativeSeedBackend {
    param(
        [string]$Root,
        [string]$Record,
        [string]$SeedId,
        [string[]]$Command,
        [string]$LaunchAttemptId,
        [string]$LaunchResolutionId,
        [string]$LaunchParentId
    )
    $prepareArgs = @('--phase', 'prepare', '--record', $Record)
    if ($SeedId) { $prepareArgs += @('--seed-id', $SeedId) }
    foreach ($pair in @(
        @('--launch-attempt-id', $LaunchAttemptId),
        @('--launch-resolution-id', $LaunchResolutionId),
        @('--launch-parent-id', $LaunchParentId)
    )) {
        if ($pair[1]) { $prepareArgs += @($pair[0], $pair[1]) }
    }
    $prepared = Invoke-AwNativeSeedPhase $Root ($prepareArgs + @('--') + $Command)
    if (
        -not $prepared -or $prepared.version -ne 1 -or
        @($prepared.argv).Count -lt 1 -or
        -not $prepared.attempt_id -or -not $prepared.resolution_id -or -not $prepared.operation_id
    ) {
        [Console]::Error.WriteLine('Native backend preparation is unconfirmed; no backend started.')
        return 3
    }
    $callbackArgs = @(
        '--record', $Record, '--launch-attempt-id', [string]$prepared.attempt_id,
        '--launch-resolution-id', [string]$prepared.resolution_id,
        '--operation-id', [string]$prepared.operation_id
    )
    if ($prepared.parent_id) { $callbackArgs += @('--launch-parent-id', [string]$prepared.parent_id) }
    $info = [Diagnostics.ProcessStartInfo]::new([string]$prepared.argv[0])
    $info.UseShellExecute = $false
    try { $info.CreateNoWindow = [Console]::IsInputRedirected }
    catch { $info.CreateNoWindow = $true }
    $info.RedirectStandardOutput = [Console]::IsOutputRedirected
    $info.RedirectStandardError = [Console]::IsErrorRedirected
    if ($info.CreateNoWindow -and -not $info.RedirectStandardOutput -and -not $info.RedirectStandardError) {
        $info.RedirectStandardOutput = $true
    }
    foreach ($argument in @($prepared.argv | Select-Object -Skip 1)) {
        $info.ArgumentList.Add([string]$argument)
    }
    if ($prepared.environment) {
        $info.Environment.Clear()
        foreach ($property in $prepared.environment.PSObject.Properties) {
            $info.Environment[$property.Name] = [string]$property.Value
        }
    }
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $info
    $receiptUncertain = $false
    $outputCopies = [Collections.Generic.List[Threading.Tasks.Task]]::new()
    try {
        try {
            if (-not $process.Start()) {
                [Console]::Error.WriteLine('Native backend start is unconfirmed; the handoff is retained.')
                return 3
            }
        } catch [ComponentModel.Win32Exception] {
            $null = Invoke-AwNativeSeedPhase $Root (@('--phase', 'spawn-failed') + $callbackArgs)
            [Console]::Error.WriteLine('Native backend could not start; inspect its retained launch outcome.')
            return 3
        }
        # Explicit output handles also preserve the original stdin handle in
        # consoleless children. Never pipe a real terminal's interactive streams.
        if ($info.RedirectStandardOutput) {
            $outputCopies.Add($process.StandardOutput.BaseStream.CopyToAsync([Console]::OpenStandardOutput()))
        }
        if ($info.RedirectStandardError) {
            $outputCopies.Add($process.StandardError.BaseStream.CopyToAsync([Console]::OpenStandardError()))
        }
        try {
            $birth = $process.StartTime.ToUniversalTime().ToFileTimeUtc().ToString(
                [Globalization.CultureInfo]::InvariantCulture
            )
            $spawned = Invoke-AwNativeSeedPhase $Root (
                @('--phase', 'spawned', '--pid', [string]$process.Id, '--process-start-time', $birth) +
                $callbackArgs
            )
            $receiptUncertain = -not ($spawned -and $spawned.recorded -eq $true)
        } catch { $receiptUncertain = $true }
        # Keep the actual child handle until exit; detaching the mux is not exit.
        $process.WaitForExit()
        $code = $process.ExitCode
        if ($outputCopies.Count) {
            $null = [Threading.Tasks.Task]::WaitAll($outputCopies.ToArray(), 1000)
        }
        $returnedArgs = @('--phase', 'returned', '--exit-code', [string]$code) + $callbackArgs
        if ($receiptUncertain) { $returnedArgs += '--receipt-uncertain' }
        $null = Invoke-AwNativeSeedPhase $Root $returnedArgs
        if ($code -eq 0 -and $receiptUncertain) { return 3 }
        return $code
    } finally { $process.Dispose() }
}
