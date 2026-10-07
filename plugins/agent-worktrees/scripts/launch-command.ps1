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
# The fast path below runs default-setup.ps1 IN THIS HOST PROCESS, so it is
# only behavior-preserving when the requested invocation is actually
# equivalent to this host: same engine (an explicit `powershell.exe`
# request must still get real Windows PowerShell, never silently run under
# whatever engine happens to host launch-command.ps1), and no host option
# before `-File` beyond the fixed, inert `-NoProfile -NoLogo` pair this
# wrapper already assumes -- any other option (e.g. -ExecutionPolicy,
# -WindowStyle, -Mta/-Sta) would otherwise be silently dropped instead of
# applied. Anything else falls through to the original spawn-and-wait,
# which honors the requested engine/options exactly as before.
$currentHostEngine = if ($PSVersionTable.PSEdition -eq 'Core') { 'pwsh' } else { 'powershell' }
if ([string]::Equals($executableName, $currentHostEngine, [StringComparison]::OrdinalIgnoreCase)) {
    for ($index = 0; $index -lt ($remainingArgs.Count - 1); $index++) {
        if (
            [string]::Equals(
                $remainingArgs[$index],
                '-File',
                [StringComparison]::OrdinalIgnoreCase
            ) -and
            [IO.Path]::GetFileName($remainingArgs[$index + 1]) -eq 'default-setup.ps1'
        ) {
            $precedingHostOptions = if ($index -gt 0) { $remainingArgs[0..($index - 1)] } else { @() }
            $onlyKnownInertOptions = @($precedingHostOptions | Where-Object {
                -not [string]::Equals($_, '-NoProfile', [StringComparison]::OrdinalIgnoreCase) -and
                -not [string]::Equals($_, '-NoLogo', [StringComparison]::OrdinalIgnoreCase)
            }).Count -eq 0
            if ($onlyKnownInertOptions) {
                $usesDefaultSetup = $true
            }
            break
        }
    }
}
if ($usesDefaultSetup) {
    # Collapse the common session-launch path (copilot-extensions#5579):
    # dot-source default-setup.ps1 directly in THIS process instead of
    # relaunching it in a brand-new pwsh.exe. Same interpreter, same working
    # directory, same environment -- this script has nothing left to do
    # afterward anyway, so default-setup.ps1's own top-level `exit` calls
    # terminating this host process is exactly the desired final behavior.
    # Windows has no real exec(), but dot-sourcing a script achieves the
    # equivalent (no new process, no new console) without changing what
    # default-setup.ps1 itself does. `$index` is the `-File` token found by
    # the detection loop above; the script path follows it directly, and
    # everything after that path is the script's own argument list (pwsh's
    # `-File` consumes the rest of argv as script args, never further host
    # flags).
    $defaultSetupScript = $remainingArgs[$index + 1]
    $defaultSetupArgs = @()
    if ($remainingArgs.Count -gt ($index + 2)) {
        $defaultSetupArgs = $remainingArgs[($index + 2)..($remainingArgs.Count - 1)]
    }

    # A plain array splat (`@defaultSetupArgs`) binds PowerShell parameters
    # POSITIONALLY ONLY (see about_Splatting) -- it does NOT re-parse a
    # '-Machine' element as a named-parameter token the way a native exe's
    # reconstructed command line, or a literal `-Machine value` typed in
    # source, does. That silently mis-binds default-setup.ps1's own named
    # parameters (confirmed: `-Machine testbox` ends up with $Machine ==
    # '-Machine'). Re-parse the forwarded tokens the same way the PowerShell
    # parser would: build a textual command line, quoting every token except
    # genuine single-dash flag names (so they're recognized as parameter
    # syntax, exactly like typing them literally), then compile and
    # dot-source that as a script block -- still zero new processes, and
    # with no coupling to default-setup.ps1's current parameter names.
    function ConvertTo-ForwardableToken([string]$Token) {
        if ($Token -match '^-[A-Za-z][\w-]*$') { return $Token }
        return "'" + ($Token -replace "'", "''") + "'"
    }
    $forwardedTokens = @(ConvertTo-ForwardableToken $defaultSetupScript)
    $forwardedTokens += $defaultSetupArgs | ForEach-Object { ConvertTo-ForwardableToken $_ }
    $forwardedCommandLine = ". " + ($forwardedTokens -join ' ')
    . ([scriptblock]::Create($forwardedCommandLine))
    exit $LASTEXITCODE
}

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

& $executable @remainingArgs
exit $LASTEXITCODE
