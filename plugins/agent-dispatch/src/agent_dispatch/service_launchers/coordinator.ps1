# Slot-local Dispatch service execution. Stable entrypoints only select and forward.
param(
    [Parameter(Mandatory)][string]$InstallDir,
    [Parameter(Mandatory)][string]$RuntimePython,
    [Parameter(Mandatory)][string]$RuntimeSlot
)
$ErrorActionPreference = 'Stop'
$_root = $InstallDir
$_py = $RuntimePython
$_slot = $RuntimeSlot
$_cacheArguments = @('-B')
try {
    $_selectedCache = & $_py -I -B -X utf8 -c "import json,sys; from agent_procutil.python_cache import POLICY_VERSION,policy_arguments; print(json.dumps(policy_arguments(sys.argv[1],runtime=sys.executable) if POLICY_VERSION==2 else ['-B']))" $_root 2>$null
    if ($LASTEXITCODE -eq 0 -and @($_selectedCache).Count -eq 1) {
        $_candidate = ConvertFrom-Json -InputObject ([string]$_selectedCache)
        if ($_candidate.Count -eq 2 -and $_candidate[0] -ceq '-X' -and
            $_candidate[1] -is [string] -and $_candidate[1].StartsWith('pycache_prefix=') -and
            $_candidate[1].Length -gt 15 -and $_candidate[1] -notmatch "[`r`n]" -and
            $_candidate[1].IndexOf([char]0) -lt 0) { $_cacheArguments = $_candidate }
    }
} catch {}
$env:PYTHONUTF8 = '1'
$env:AGENT_DISPATCH_INSTALL_DIR = $InstallDir
Set-Location -LiteralPath $InstallDir
$envFile = Join-Path $InstallDir 'service.env'
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        $t = $line.Trim()
        if ($t -eq '' -or $t.StartsWith('#')) { continue }
        $kv = $t -split '=', 2
        if ($kv.Count -eq 2) {
            [Environment]::SetEnvironmentVariable($kv[0].Trim(), [Environment]::ExpandEnvironmentVariables($kv[1].Trim()), 'Process')
        }
    }
}
# A busy/locked log must NEVER block the coordinator launch. Prefer the canonical
# serve-service.log; if it cannot be opened for append (a stale or concurrent
# process still holds the handle), fall back to a VERSION- and pid-aware file so
# startup always has a writable log. (Previously a locked log threw under
# -ErrorActionPreference Stop and killed the launch before serve ever ran.)
function Resolve-WritableLog([string]$primary, [string]$slot) {
    $ver = if ($slot) { Split-Path -Leaf $slot } else { 'unknown' }
    $alt = ($primary -replace '\.log$', "-$ver-$PID.log")
    foreach ($cand in @($primary, $alt)) {
        try { $fs = [System.IO.File]::Open($cand, 'Append', 'Write', 'ReadWrite'); $fs.Close(); return $cand } catch { }
    }
    return $alt
}
$logFile = Resolve-WritableLog (Join-Path $InstallDir 'serve-service.log') $_slot
try {
    if ((Test-Path $logFile) -and ((Get-Item $logFile).Length -gt 1MB)) {
        Move-Item -Force $logFile "$logFile.1"
    }
} catch { }
$pinned = if ($env:AGENT_DISPATCH_HOST) { $env:AGENT_DISPATCH_HOST } else { 'auto (resolved by serve)' }
$portShown = if ($env:AGENT_DISPATCH_PORT) { $env:AGENT_DISPATCH_PORT } else { 'default' }
# Banner write is best-effort -- a logging hiccup must not be fatal (wrapped so a
# late lock on the resolved path still can't kill startup).
try {
    "[$(Get-Date -Format o)] agent-dispatch coordinator launch (host=$pinned port=$portShown log=$logFile)" |
        Out-File -FilePath $logFile -Append -Encoding utf8
} catch { }
# Tee every stream (stdout/stderr/warning/info) to the log while still writing
# through, so the retry lines from serve's bind-host resolution are captured.
# serve logs via uvicorn to STDERR; under $ErrorActionPreference = 'Stop'`
# PowerShell wraps a native command's stderr as a terminating NativeCommandError
# and would kill the long-lived coordinator on its very first log line. Drop to
# 'Continue' for the serve invocation so stderr is captured, never fatal.
$ErrorActionPreference = 'Continue'
& $_py -I -B -c "import sys; from pathlib import Path; from agent_dispatch.installation_policy import windows_policy; p=windows_policy(Path(sys.argv[1]), observe_supervisor=False); raise SystemExit(0 if p['start_coordinator'] else 3)" $InstallDir
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
try {
    & $_py @_cacheArguments -m agent_dispatch serve 2>&1 | Out-File -FilePath $logFile -Append -Encoding utf8
} catch {
    # Teeing to the log failed (e.g. it got locked after resolution) -- keep the
    # coordinator alive without the tee rather than let logging kill the service.
    & $_py @_cacheArguments -m agent_dispatch serve *> $null
}
