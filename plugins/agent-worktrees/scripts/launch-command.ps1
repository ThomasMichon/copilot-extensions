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
# The fast path below runs default-setup.ps1 IN THIS HOST PROCESS, so it is
# only behavior-preserving when the requested invocation is actually
# equivalent to this host: the requested executable resolves to the SAME
# binary currently hosting this process -- not merely the same basename.
# An explicit, alternate `pwsh`/`powershell` install (a pinned version, a
# different architecture) must still get exactly that binary, never
# silently run under whatever happens to host launch-command.ps1. The host
# options before `-File` must also be EXACTLY the fixed `-NoProfile
# -NoLogo` pair this wrapper's own host was itself started with (not
# merely a subset -- this process already has no profile loaded, so a
# requested child that OMITS -NoProfile, signaling the caller actually
# wants one loaded, must not be silently coerced into this already-
# profile-less host; any other unrecognized option, e.g.
# -ExecutionPolicy/-WindowStyle/-Mta/-Sta, would otherwise be silently
# dropped instead of applied). AND the resolved script is genuinely THIS
# plugin's own canonical default-setup.ps1 -- not merely a same-named
# file. A repo's authoritative launch template can point at its OWN
# custom script that merely happens to share the 'default-setup.ps1'
# basename; dot-sourcing isn't generally equivalent to `pwsh -File` for
# arbitrary script content (e.g. $MyInvocation.InvocationName and
# top-level `return` semantics differ), so only this plugin's own,
# known-compatible script qualifies. Anything else falls through to the
# original spawn-and-wait, which honors the requested executable/
# options/script exactly as before.
$ownDefaultSetupScript = Join-Path $PSScriptRoot 'default-setup.ps1'
# This whole optimization is Windows-specific (copilot-extensions#5579): on
# POSIX, Python's own launch layer already uses a real os.execvp (no extra
# process at all), so there is nothing to collapse here, and binary-path
# identity resolution below (Process.MainModule.FileName vs Get-Command)
# has been observed to disagree on Linux even for the SAME nominal `pwsh`
# (e.g. a symlink resolving differently), which would silently -- and
# harmlessly, since it only ever degrades to the pre-existing
# spawn-and-wait -- skip the optimization there anyway. Gate on Windows
# explicitly rather than relying on that incidental mismatch. `$IsWindows`
# does not exist in Windows PowerShell 5.1 (Desktop edition, always
# Windows), only in PowerShell 7+ (Core, cross-platform).
$onWindows = ($PSVersionTable.PSEdition -eq 'Desktop') -or $IsWindows
$currentHostExecutablePath = $null
if ($onWindows) {
    try {
        $currentHostExecutablePath = [Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
    } catch { }
    $requestedExecutablePath = $null
    try {
        if ([IO.Path]::IsPathRooted($executable)) {
            $requestedExecutablePath = [IO.Path]::GetFullPath($executable)
        } else {
            $resolvedCommand = Get-Command $executable -CommandType Application -ErrorAction SilentlyContinue |
                Select-Object -First 1
            if ($resolvedCommand) { $requestedExecutablePath = $resolvedCommand.Source }
        }
    } catch { }
    $isCurrentHostExecutable = (
        $currentHostExecutablePath -and $requestedExecutablePath -and
        [string]::Equals($requestedExecutablePath, $currentHostExecutablePath, [StringComparison]::OrdinalIgnoreCase)
    )
    if ($isCurrentHostExecutable) {
        for ($index = 0; $index -lt ($remainingArgs.Count - 1); $index++) {
            if (
                [string]::Equals(
                    $remainingArgs[$index],
                    '-File',
                    [StringComparison]::OrdinalIgnoreCase
                )
            ) {
                $requestedScript = $remainingArgs[$index + 1]
                $isOwnScript = $false
                try {
                    $isOwnScript = [string]::Equals(
                        [IO.Path]::GetFullPath($requestedScript),
                        [IO.Path]::GetFullPath($ownDefaultSetupScript),
                        [StringComparison]::OrdinalIgnoreCase
                    )
                } catch { }
                if ($isOwnScript) {
                    $precedingHostOptions = if ($index -gt 0) { $remainingArgs[0..($index - 1)] } else { @() }
                    $hasNoProfile = @($precedingHostOptions | Where-Object {
                        [string]::Equals($_, '-NoProfile', [StringComparison]::OrdinalIgnoreCase)
                    }).Count -eq 1
                    $hasNoLogo = @($precedingHostOptions | Where-Object {
                        [string]::Equals($_, '-NoLogo', [StringComparison]::OrdinalIgnoreCase)
                    }).Count -eq 1
                    $exactlyInertOptions = $hasNoProfile -and $hasNoLogo -and $precedingHostOptions.Count -eq 2
                    if ($exactlyInertOptions) {
                        $usesDefaultSetup = $true
                    }
                }
                break
            }
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
