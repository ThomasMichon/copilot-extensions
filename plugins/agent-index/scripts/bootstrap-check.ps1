<#
    Session-start runtime reconcile -- generic, self-locating; shipped
    byte-identical across agent-* runtime plugins. Invoked (via hooks.json) from
    the plugin's own scripts/ dir. Derives the plugin from its own location and
    the legacy install dir from plugin.json's name (~/.<name>), then re-runs the
    installer in the BACKGROUND only when the deployed runtime version drifts
    from the payload. An explicit validated installation context redirects
    manifest inspection only; namespaced writes remain blocked until a
    context-aware installer is operative. PS5.1+.
#>
$ErrorActionPreference = 'SilentlyContinue'
$PluginDir = Split-Path -Parent $PSScriptRoot
try {
    $name = (Get-Content (Join-Path $PluginDir 'plugin.json') -Raw | ConvertFrom-Json).name
    if (-not $name) { exit 0 }
    function Test-LegacyMutationAllowed {
        $probe = Join-Path $PSScriptRoot 'installation-context\legacy-entrypoint-probe.ps1'
        if (-not (Test-Path -LiteralPath $probe -PathType Leaf)) {
            Write-Host "[$name] legacy mutation probe is unavailable; skipping reconcile." -ForegroundColor DarkGray
            return $false
        }
        $hostExe = (Get-Process -Id $PID).Path
        if (-not $hostExe) { return $false }
        $global:LASTEXITCODE = 1
        try {
            & $hostExe -NoProfile -ExecutionPolicy Bypass -File $probe `
                -PayloadRoot $PluginDir -LegacyRoot (Join-Path $env:USERPROFILE ".$name") |
                Out-Null
        } catch {
            return $false
        }
        return $LASTEXITCODE -eq 0
    }
    $contextSelected = $false
    $InstallDir = Join-Path $env:USERPROFILE ".$name"
    if ($env:COPILOT_EXTENSIONS_CONTEXT) {
        $resolver = Join-Path $PSScriptRoot 'installation-context\installation-context.ps1'
        if (-not (Test-Path $resolver)) {
            Write-Host "[$name] installation context is selected but its validator is unavailable; skipping reconcile." -ForegroundColor DarkGray
            exit 0
        }
        $durableHome = $env:COPILOT_EXTENSIONS_CONTEXT
        1..5 | ForEach-Object { $durableHome = Split-Path -Parent $durableHome }
        $hostExe = (Get-Process -Id $PID).Path
        $validatedJson = & $hostExe -NoProfile -ExecutionPolicy Bypass -File $resolver validate `
            -Context $env:COPILOT_EXTENSIONS_CONTEXT -DurableHome $durableHome
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[$name] installation context is invalid; skipping reconcile without legacy fallback." -ForegroundColor DarkGray
            exit 0
        }
        try { $contextPlugin = ($validatedJson | ConvertFrom-Json).pluginId } catch {
            Write-Host "[$name] installation context is invalid; skipping reconcile without legacy fallback." -ForegroundColor DarkGray
            exit 0
        }
        if (-not $contextPlugin) {
            Write-Host "[$name] installation context is invalid; skipping reconcile without legacy fallback." -ForegroundColor DarkGray
            exit 0
        }
        if ($contextPlugin -ceq $name) {
            $contextJson = & $hostExe -NoProfile -ExecutionPolicy Bypass -File $resolver resolve `
                -Context $env:COPILOT_EXTENSIONS_CONTEXT -PluginId $name `
                -PayloadRoot $PluginDir -DurableHome $durableHome
            if ($LASTEXITCODE -ne 0) {
                Write-Host "[$name] installation context is invalid; skipping reconcile without legacy fallback." -ForegroundColor DarkGray
                exit 0
            }
            $resolvedContext = $contextJson | ConvertFrom-Json
            if (-not $resolvedContext.pluginRoot) {
                Write-Host "[$name] installation context returned no plugin root; skipping reconcile without legacy fallback." -ForegroundColor DarkGray
                exit 0
            }
            $InstallDir = $resolvedContext.pluginRoot
            $contextSelected = $true
        }
    }
    $Manifest = Join-Path $InstallDir 'deploy-manifest.json'
    if (-not (Test-Path $Manifest)) {
        if ($contextSelected) {
            Write-Host "[$name] selected context has no deploy manifest; namespaced install remains non-operative." -ForegroundColor DarkGray
            exit 0
        }
        # Not provisioned yet -- do the cheap FIRST install ('stamp') so the
        # binstub is on PATH this session; the self-provisioning binstub then
        # builds the venv on first use (#1393). Fires only when the installer
        # (init.ps1 or install.ps1) declares a 'stamp' action; else a safe no-op.
        $stampInst = @("$PluginDir\scripts\init.ps1", "$PluginDir\scripts\install.ps1") |
            Where-Object { (Test-Path $_) -and (Select-String -Path $_ -Pattern "'stamp'" -Quiet) } |
            Select-Object -First 1
        if ($stampInst) {
            if (-not (Test-LegacyMutationAllowed)) { exit 0 }
            $pw = Get-Command pwsh -ErrorAction SilentlyContinue
            $exe = if ($pw) { $pw.Source } else { 'powershell.exe' }
            & $exe -NoProfile -ExecutionPolicy Bypass -File $stampInst stamp *> $null
        }
        exit 0
    }
    $deployed = "" + (Get-Content $Manifest -Raw | ConvertFrom-Json).source.version
    $current = $deployed
    $pyproj = Join-Path $PluginDir 'pyproject.toml'
    if (Test-Path $pyproj) {
        $vl = Select-String -Path $pyproj -Pattern '^\s*version\s*=' | Select-Object -First 1
        if ($vl) { $current = ($vl.Line -replace '.*=\s*"([^"]+)".*', '$1') }
    }
    if ($contextSelected) {
        if ($deployed -eq $current) { exit 0 }
        Write-Host "[$name] selected context runtime $deployed -> $current; context-aware install is not active yet." -ForegroundColor DarkGray
        exit 0
    }
    # "Provisioned" no longer implies a `.venv`: the marker runtime model (#581)
    # publishes the active slot via a `current-version` marker with NO junction on
    # Windows (RedirectionGuard), so a healthy current runtime has no `.venv` there.
    # Treat a marker whose slot python exists as provisioned too -- otherwise a
    # current runtime needlessly background-rebuilds every session.
    $provisioned = Test-Path (Join-Path $InstallDir '.venv')
    if (-not $provisioned) {
        $cvMarker = Join-Path $InstallDir 'current-version'
        if (Test-Path $cvMarker) {
            $cv = ('' + (Get-Content $cvMarker -Raw)).Trim()
            # ...and only when it names the CURRENT payload version: the marker is
            # authoritative for the ACTIVE slot, so a stale/corrupt marker naming an
            # older slot must NOT suppress reconcile and strand the wrong runtime.
            if ($cv -and $cv -eq $current -and ((Test-Path (Join-Path $InstallDir "versions\$cv\Scripts\python.exe")) -or (Test-Path (Join-Path $InstallDir "versions/$cv/bin/python")))) { $provisioned = $true }
        }
    }
    if ($provisioned -and $deployed -eq $current) { exit 0 }
    $init = Join-Path $PluginDir 'scripts\init.ps1'
    if (Test-Path $init) {
        $reCmd = "& `"$init`""
    } else {
        $inst = Join-Path $PluginDir 'scripts\install.ps1'
        if (-not (Test-Path $inst)) { exit 0 }
        $reCmd = "& `"$inst`" install"
    }
    if (-not (Test-LegacyMutationAllowed)) { exit 0 }
    Write-Host "[$name] runtime $deployed -> $current; reconciling in background..." -ForegroundColor DarkGray
    $pw = Get-Command pwsh -ErrorAction SilentlyContinue
    $exe = if ($pw) { $pw.Source } else { 'powershell.exe' }
    # Launch the background reconcile through conhost --headless so Windows
    # Terminal / the DefTerm handoff cannot surface it as a visible window --
    # -WindowStyle Hidden ALONE is ignored by DefTerm (proven pattern; see
    # agent-bridge). The reconcile command is base64-encoded to avoid any arg
    # quoting under conhost; children (uv/python building the venv) inherit the
    # headless console and stay hidden too.
    $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($reCmd))
    Start-Process -FilePath 'conhost.exe' `
        -ArgumentList @('--headless', "`"$exe`"", '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-EncodedCommand', $enc) `
        -WindowStyle Hidden | Out-Null
} catch { }
exit 0