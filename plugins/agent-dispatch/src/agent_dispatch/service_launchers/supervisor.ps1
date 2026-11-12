# Slot-local Dispatch service execution. Stable entrypoints only select and forward.
param(
    [Parameter(Mandatory)][string]$InstallDir,
    [Parameter(Mandatory)][string]$RuntimePython,
    [Parameter(Mandatory)][string]$RuntimeSlot,
    [Parameter(Mandatory)][string]$EnvFile,
    [switch]$DescribeLaunch
)
$ErrorActionPreference = 'Stop'
$_root = $InstallDir
$_py = $RuntimePython
$_slot = $RuntimeSlot
$_profilePath = [IO.Path]::GetFullPath($EnvFile)
$_profileHasher = [Security.Cryptography.SHA256]::Create()
try {
    $_profileGeneration = ([BitConverter]::ToString($_profileHasher.ComputeHash(
        [IO.File]::ReadAllBytes($_profilePath)))).Replace('-', '').ToLowerInvariant()
} finally { $_profileHasher.Dispose() }
$_cacheId = [Guid]::NewGuid().ToString('N')
$_cache = [IO.Path]::GetFullPath((Join-Path $_root "cache\python\unsealed-$_cacheId"))
if (-not $_cache.StartsWith('\\?\')) {
    $_cache = if ($_cache.StartsWith('\\')) { '\\?\UNC\' + $_cache.Substring(2) } else { '\\?\' + $_cache }
}
# Reuse the sealed installed-<digest> bytecode cache when install evidence is valid.
$_mintedCache = $_cache
try {
    $_selectedCache = & $_py -I -B -X ('pycache_prefix=' + $_cache) -c "import json,os,sys; from agent_procutil.python_cache import cache_arguments as c; p=c(sys.argv[1],runtime=sys.executable)[1].split('=',1)[1]; print(json.dumps(p if os.path.basename(p).startswith('installed-') else ''))" $_root 2>$null
    if ($LASTEXITCODE -eq 0 -and @($_selectedCache).Count -eq 1) {
        $_selectedCache = [string]($_selectedCache | ConvertFrom-Json)
        if ($_selectedCache) { $_cache = $_selectedCache }
    }
} catch {}
$env:PYTHONUTF8 = '1'
$env:AGENT_DISPATCH_INSTALL_DIR = $InstallDir
Set-Location -LiteralPath $InstallDir
$envFile = $EnvFile
$labels = ''
$interval = '30'
$maxConcurrent = '1'
$maxAttempts = '3'
$labelMaxAttempts = ''
$embodyBackend = ''
$cliLabels = ''
$headlessLabels = ''
$headlessAgent = ''
$extra = ''
$mode = ''
$sMachine = ''
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        $t = $line.Trim()
        if ($t -eq '' -or $t.StartsWith('#')) { continue }
        $kv = $t -split '=', 2
        if ($kv.Count -ne 2) { continue }
        $k = $kv[0].Trim(); $v = $kv[1].Trim()
        switch ($k) {
            'AGENT_DISPATCH_SUPERVISE_LABELS'         { $labels = $v }
            'AGENT_DISPATCH_SUPERVISE_INTERVAL'       { if ($v) { $interval = $v } }
            'AGENT_DISPATCH_SUPERVISE_MAX_CONCURRENT' { if ($v) { $maxConcurrent = $v } }
            'AGENT_DISPATCH_SUPERVISE_MAX_ATTEMPTS'   { if ($v) { $maxAttempts = $v } }
            'AGENT_DISPATCH_SUPERVISE_LABEL_MAX_ATTEMPTS' { $labelMaxAttempts = $v }
            'AGENT_DISPATCH_SUPERVISE_EMBODY_BACKEND'  { $embodyBackend = $v }
            'AGENT_DISPATCH_SUPERVISE_CLI_LABELS'      { $cliLabels = $v }
            'AGENT_DISPATCH_SUPERVISE_HEADLESS_LABELS' { $headlessLabels = $v }
            'AGENT_DISPATCH_SUPERVISE_HEADLESS_AGENT'  { $headlessAgent = $v }
            'AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS'     { $extra = $v }
            'AGENT_DISPATCH_SUPERVISE_MODE'           { $mode = $v }
            'AGENT_DISPATCH_SUPERVISE_MACHINE'        { $sMachine = $v }
        }
    }
}
if ($mode -eq 'serve') {
    # MODE=serve (opt-in): run the single MASTER registrar daemon instead of the
    # classic direct embody loop. The daemon reconciles declared pools (discovered
    # pointers) + this host's legacy env profiles (--legacy-env: supervisor.env +
    # supervisors/*.env), each in its own subprocess. It is self-gating (only
    # labeled declarations/profiles run), so it needs no label opt-in here.
    $argsList = @('supervise', 'serve', '--legacy-env', '--interval', $interval)
    # Explicit machine scope (recommended for a service context, where CWD-based
    # identity resolution can fail and leave the daemon unable to scope
    # machine-pinned declarations -- the downstream tracker). Falls back to the host
    # node name at runtime when unset.
    if ($sMachine) { $argsList += @('--machine', $sMachine) }
    if ($extra) { $argsList += ($extra -split '\s+') }
} else {
$argsList = @('supervise', '--all-repos', '--interval', $interval,
    '--max-concurrent', $maxConcurrent, '--max-attempts', $maxAttempts)
$haveLabel = $false
foreach ($l in ($labels -split '[\s,]+')) {
    if ($l) { $argsList += @('--label', $l); $haveLabel = $true }
}
if (-not $haveLabel) {
    Write-Error "agent-dispatch-supervisor: refusing to run with no opt-in label. A label-less supervisor would embody EVERY queued task. Set AGENT_DISPATCH_SUPERVISE_LABELS in $envFile."
    exit 78  # EX_CONFIG
}
foreach ($lm in ($labelMaxAttempts -split '[\s,]+')) {
    if ($lm) { $argsList += @('--label-max-attempts', $lm) }
}
if ($embodyBackend) { $argsList += @('--embody-backend', $embodyBackend) }
foreach ($cl in ($cliLabels -split '[\s,]+')) {
    if ($cl) { $argsList += @('--cli-label', $cl) }
}
foreach ($hl in ($headlessLabels -split '[\s,]+')) {
    if ($hl) { $argsList += @('--headless-label', $hl) }
}
if ($headlessAgent) { $argsList += @('--headless-agent', $headlessAgent) }
if ($extra) { $argsList += ($extra -split '\s+') }
}
# A busy/locked log must NEVER block the supervisor launch -- prefer the canonical
# supervise-service.log, else a VERSION- and pid-aware fallback (see serve-service).
function Resolve-WritableLog([string]$primary, [string]$slot) {
    $ver = if ($slot) { Split-Path -Leaf $slot } else { 'unknown' }
    $alt = ($primary -replace '\.log$', "-$ver-$PID.log")
    foreach ($cand in @($primary, $alt)) {
        try { $fs = [System.IO.File]::Open($cand, 'Append', 'Write', 'ReadWrite'); $fs.Close(); return $cand } catch { }
    }
    return $alt
}
$logFile = Resolve-WritableLog (Join-Path $InstallDir 'supervise-service.log') $_slot
try {
    if ((Test-Path $logFile) -and ((Get-Item $logFile).Length -gt 1MB)) {
        Move-Item -Force $logFile "$logFile.1"
    }
} catch { }
if ($DescribeLaunch) {
    $description = @{
        version = 1
        executable = $_py
        arguments = @('-X', ('pycache_prefix=' + $_cache), '-m', 'agent_dispatch') + $argsList
        log_path = $logFile
    } | ConvertTo-Json -Compress -Depth 4
    [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($description))
    exit 0
}
$_profile = ':none:'
$_observe = '0'
$_envFull = [IO.Path]::GetFullPath($EnvFile)
if ($_envFull -eq (Join-Path $InstallDir 'supervisor.env')) { $_observe = '1' }
elseif ((Split-Path $_envFull) -eq (Join-Path $InstallDir 'supervisors')) {
    $_profile = [IO.Path]::GetFileNameWithoutExtension($_envFull)
    if ($_profile -match '^[A-Za-z0-9_-]+$') { $_observe = '1' } else { $_profile = ':none:' }
}
& $_py -I -B -c "import sys; from pathlib import Path; from agent_dispatch.installation_policy import windows_policy; p=windows_policy(Path(sys.argv[1]), observe_supervisor=sys.argv[3]=='1', supervisor_profile=None if sys.argv[2]==':none:' else sys.argv[2]); raise SystemExit(0 if p['start_supervisor'] else 3)" $InstallDir $_profile $_observe
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
try {
    "[$(Get-Date -Format o)] agent-dispatch supervisor launch (env=$envFile labels=$labels interval=$interval log=$logFile)" |
        Out-File -FilePath $logFile -Append -Encoding utf8
} catch { }
$ErrorActionPreference = 'Continue'
$_encodedArguments = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(
    (ConvertTo-Json -InputObject @($argsList) -Compress)))
$_ensure = 'import base64,json,sys; from pathlib import Path; from agent_dispatch.supervisor_lane_status import ensure; ensure(Path(sys.argv[1]),Path(sys.argv[2]),json.loads(base64.b64decode(sys.argv[3])),generation=sys.argv[4])'
$_admissionLog = Resolve-WritableLog (Join-Path $InstallDir 'supervise-admission.log') $_slot
try {
    & $_py -I -B -X ('pycache_prefix=' + $_cache) -c $_ensure $InstallDir $_profilePath $_encodedArguments $_profileGeneration 2>&1 | Out-File -FilePath $_admissionLog -Append -Encoding utf8
} catch {
    # A log failure does not select a different admission or lifetime owner.
    & $_py -I -B -X ('pycache_prefix=' + $_cache) -c $_ensure $InstallDir $_profilePath $_encodedArguments $_profileGeneration *> $null
}
if ($_cache -eq $_mintedCache) { try { [IO.Directory]::Delete($_mintedCache, $true) } catch {} }
exit $LASTEXITCODE
