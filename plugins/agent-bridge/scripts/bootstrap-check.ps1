<#
    agent-bridge session-start runtime reconcile (reference implementation).

    Invoked via hooks.json at session start. Derives the install dir from
    plugin.json's name (~/.<name>), and if the deployed runtime version drifts
    from the plugin payload, re-runs the installer in the BACKGROUND so a
    `copilot plugin update` is picked up automatically. Reconciles the TOOL,
    never machine state/config. PS5.1+.

    NOTE ON SHARING: this file is NOT byte-identical across all agent-* plugins.
    Three deploy-model families exist (see tools/check-bootstrap-sync.py):
    versioned-venv/PSScriptRoot (the common set), versioned-venv/manifest-path
    (agent-ssh, agent-machines), and lib-copy (agent-worktrees). This copy is the
    reference for the observability + venv-or-.venv behavior described below.

    OBSERVABILITY (#167): the background reconcile is otherwise silent -- a failed
    cutover would leave no trace. So this hook records every reconcile ATTEMPT to
    ~/.<name>/reconcile-status.json and redirects the installer's output to
    ~/.<name>/reconcile.log (stdout) / reconcile.err.log (stderr). Check those to
    see whether the last auto-reconcile succeeded.
#>
$ErrorActionPreference = 'SilentlyContinue'
$PluginDir = Split-Path -Parent $PSScriptRoot
try {
    $name = (Get-Content (Join-Path $PluginDir 'plugin.json') -Raw | ConvertFrom-Json).name
    if (-not $name) { exit 0 }
    $InstallDir = Join-Path $env:USERPROFILE ".$name"
    $Manifest = Join-Path $InstallDir 'deploy-manifest.json'
    if (-not (Test-Path $Manifest)) { exit 0 }
    $deployed = "" + (Get-Content $Manifest -Raw | ConvertFrom-Json).source.version
    $current = $deployed
    $pyproj = Join-Path $PluginDir 'pyproject.toml'
    if (Test-Path $pyproj) {
        $vl = Select-String -Path $pyproj -Pattern '^\s*version\s*=' | Select-Object -First 1
        if ($vl) { $current = ($vl.Line -replace '.*=\s*"([^"]+)".*', '$1') }
    }
    # The immutable-versioned layout points a stable link at the active slot;
    # its name is '.venv' for most plugins but 'venv' for a few (agent-bridge).
    # Accept EITHER so the early-exit actually fires -- otherwise this hook
    # re-launches the installer on every session start (churn), and under a
    # version drift it repeatedly attempts a swap.
    $venvPresent = (Test-Path (Join-Path $InstallDir '.venv')) -or (Test-Path (Join-Path $InstallDir 'venv'))
    if ($venvPresent -and $deployed -eq $current) { exit 0 }
    $init = Join-Path $PluginDir 'scripts\init.ps1'
    if (Test-Path $init) {
        $targs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $init)
    } else {
        $inst = Join-Path $PluginDir 'scripts\install.ps1'
        if (-not (Test-Path $inst)) { exit 0 }
        $targs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $inst, 'install')
    }
    Write-Host "[$name] runtime $deployed -> $current; reconciling in background (log: $InstallDir\reconcile.log)..." -ForegroundColor DarkGray
    $pw = Get-Command pwsh -ErrorAction SilentlyContinue
    $exe = if ($pw) { $pw.Source } else { 'powershell.exe' }

    # Observability (#167): capture the otherwise-silent background reconcile so a
    # failed auto-update is diagnosable. -RedirectStandard* truncates each file,
    # so reconcile.log always holds the MOST RECENT reconcile's output.
    $reconcileLog = Join-Path $InstallDir 'reconcile.log'
    $reconcileErr = Join-Path $InstallDir 'reconcile.err.log'
    $statusFile = Join-Path $InstallDir 'reconcile-status.json'
    $proc = Start-Process -FilePath $exe -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $reconcileLog -RedirectStandardError $reconcileErr `
        -ArgumentList $targs
    $now = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    $launchedPid = if ($proc) { $proc.Id } else { 0 }
    $status = [ordered]@{
        at           = $now
        from         = $deployed
        to           = $current
        launched_pid = $launchedPid
        log          = $reconcileLog
        err_log      = $reconcileErr
    } | ConvertTo-Json -Compress
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($statusFile, $status, $utf8NoBom)
} catch { }
exit 0