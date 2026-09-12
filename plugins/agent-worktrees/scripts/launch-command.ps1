$ErrorActionPreference = 'Stop'

$recovery = $false
$offset = 0
if ($args.Count -gt $offset -and $args[$offset] -eq '--recovery') {
    $recovery = $true
    $offset++
}
if ($args.Count -gt $offset -and $args[$offset] -eq '--') {
    $offset++
}
if ($args.Count -le $offset) {
    [Console]::Error.WriteLine('ERROR: launch-command.ps1 requires a command.')
    exit 2
}

$machineSettingsHelper = Join-Path $PSScriptRoot 'reconcile-machine-settings.ps1'
if (Test-Path -LiteralPath $machineSettingsHelper) {
    . $machineSettingsHelper -Recovery:$recovery
}

$executable = [string]$args[$offset]
[string[]]$remainingArgs = @()
if ($args.Count -gt ($offset + 1)) {
    $remainingArgs = $args[($offset + 1)..($args.Count - 1)]
}

$usesDefaultSetup = $false
$executableName = [IO.Path]::GetFileNameWithoutExtension($executable)
if ($executableName -in @('pwsh', 'powershell')) {
    for ($index = 0; $index -lt ($remainingArgs.Count - 1); $index++) {
        if (
            [string]::Equals(
                $remainingArgs[$index],
                '-File',
                [StringComparison]::OrdinalIgnoreCase
            ) -and
            [IO.Path]::GetFileName($remainingArgs[$index + 1]) -eq 'default-setup.ps1'
        ) {
            $usesDefaultSetup = $true
            break
        }
    }
}
if (-not $usesDefaultSetup) {
    Remove-Item Env:AGENT_WORKTREES_MACHINE_SETTINGS_RECONCILED -ErrorAction SilentlyContinue
    # Stage 3 (copilot_invoked): the config-driven launch-template and legacy
    # tools/setup/setup.ps1 paths never reach default-setup.ps1's own precise
    # exec-point emitter, so this wrapper -- the one seam EVERY resolved
    # command passes through -- emits a coarser "attempted" mark for them
    # instead (best-effort/detached; distinct from the confirmed event
    # default-setup.ps1 emits for its own path).
    try {
        $awPy = $null
        # Honor a contextual/cell launch's validated runtime root before the
        # legacy per-user fallback (same precedence as default-setup.ps1 /
        # launch-session.ps1).
        $runtimeRoot = if ($env:AGENT_WORKTREES_LAUNCH_RUNTIME_ROOT) {
            $env:AGENT_WORKTREES_LAUNCH_RUNTIME_ROOT
        } else {
            Join-Path $env:USERPROFILE '.agent-worktrees'
        }
        $resolver = Join-Path $runtimeRoot 'bin\resolve-runtime.ps1'
        if (Test-Path -LiteralPath $resolver) { . $resolver; $awPy = $AwPy }
        if ($awPy -and (Test-Path -LiteralPath $awPy)) {
            $wtId = & $awPy -I -m agent_worktrees get worktree-id 2>$null
            if ($wtId) {
                Start-Process -FilePath 'conhost.exe' -ArgumentList (@('--headless', "`"$awPy`"",
                    '-I', '-m', 'agent_worktrees', 'activity-log', 'copilot_invocation_attempted',
                    '--worktree-id', $wtId, '--source', 'launcher')) `
                    -WindowStyle Hidden -ErrorAction Stop | Out-Null
            }
        }
    } catch { }
}

& $executable @remainingArgs
exit $LASTEXITCODE
