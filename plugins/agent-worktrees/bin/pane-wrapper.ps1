#Requires -Version 7.0
# pane-wrapper.ps1 -- Windows counterpart of pane-wrapper.sh. Wraps the psmux
# pane command so the child's real exit code is observable (the launcher cannot
# see it -- the child runs inside the pane), records a durable `pane_exited`
# activity mark, and shows a crash diagnostic before the pane closes.
#
# Behavior (mirrors pane-wrapper.sh):
#   exit 0, runtime >= threshold : exit 0 silently (normal session end)
#   exit 130 (Ctrl+C)            : exit 0 silently (intentional interrupt)
#   exit 0, runtime < threshold  : pause with diagnostic (startup crash)
#   any other non-zero exit      : pause with diagnostic (error/crash)
#
# Always exits 0 so the pane isn't trapped. Uses $args with NO param() block so
# `--allow-all` and other `--`-prefixed passthrough args are never treated as
# parameters to this wrapper (the same re-tokenization hazard documented in the
# #102 note in launch-session.ps1). An optional leading `-AwWt <id>` carries the
# worktree id for the activity mark and is consumed here (never forwarded).
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}

$minRuntime = if ($env:WORKTREE_PANE_MIN_RUNTIME) { [int]$env:WORKTREE_PANE_MIN_RUNTIME } else { 3 }
$waitTimeout = if ($env:WORKTREE_PANE_WAIT_TIMEOUT) { [int]$env:WORKTREE_PANE_WAIT_TIMEOUT } else { 60 }

$rest = @($args)
$awWt = ''
if ($rest.Count -ge 2 -and $rest[0] -eq '-AwWt') {
    $awWt = [string]$rest[1]
    $rest = if ($rest.Count -gt 2) { @($rest[2..($rest.Count - 1)]) } else { @() }
}

if ($rest.Count -eq 0) { exit 0 }

$start = Get-Date
& $rest[0] @($rest[1..($rest.Count - 1)])
$exitCode = $LASTEXITCODE
if ($null -eq $exitCode) { $exitCode = 0 }
$runtime = [int]((Get-Date) - $start).TotalSeconds

# Durable pane-exit mark (Tier-A): the only place the psmux pane's real exit code
# is observable. Best-effort, detached, fail-silent -- must never delay pane
# teardown. Correlates to the launch flow via WORKTREE_LAUNCH_ID (inherited from
# the mux server env).
try {
    $awArgs = @('activity-log', 'pane_exited', '--source', 'launcher',
                '--field', "exit_code=$exitCode", '--field', "runtime=$runtime")
    if ($awWt) { $awArgs += @('--worktree-id', $awWt) }
    if ($env:WORKTREE_LAUNCH_ID) { $awArgs += @('--launch-id', $env:WORKTREE_LAUNCH_ID) }
    Start-Process -FilePath 'agent-worktrees' -ArgumentList $awArgs `
        -WindowStyle Hidden -ErrorAction Stop | Out-Null
} catch {}

# Intentional interrupt -- exit silently so post-exit finalization runs.
if ($exitCode -eq 130) { exit 0 }
# Normal exit after running long enough -- nothing to report.
if ($exitCode -eq 0 -and $runtime -ge $minRuntime) { exit 0 }

# Something worth showing the user -- crash, error, or suspiciously fast exit.
Write-Host ''
Write-Host '------------------------------------------------------------'
if ($exitCode -eq 0) {
    Write-Host "  Session exited immediately (runtime: ${runtime}s)"
    Write-Host '  This usually means a startup error occurred.'
} elseif ($exitCode -ge 128) {
    Write-Host "  Session terminated abnormally (exit code $exitCode)"
} else {
    Write-Host "  Session exited with code $exitCode"
}
Write-Host ''
if ($env:WORKTREE_SETUP_LOG -and (Test-Path $env:WORKTREE_SETUP_LOG)) {
    Write-Host "  Setup log: $env:WORKTREE_SETUP_LOG"
    Write-Host ''
}
Write-Host "  Press any key to close, or wait ${waitTimeout}s..."
Write-Host '------------------------------------------------------------'
# Timed wait for a keypress (bash uses `read -t`); poll KeyAvailable so the
# timeout is honored. No console (KeyAvailable throws) -> just sleep the timeout.
try {
    $deadline = (Get-Date).AddSeconds($waitTimeout)
    while ((Get-Date) -lt $deadline) {
        if ([Console]::KeyAvailable) { [Console]::ReadKey($true) | Out-Null; break }
        Start-Sleep -Milliseconds 150
    }
} catch {
    Start-Sleep -Seconds $waitTimeout
}
exit 0
