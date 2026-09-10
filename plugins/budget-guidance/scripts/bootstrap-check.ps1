<#
    Session-start runtime reconcile -- generic, self-locating; shipped
    byte-identical across agent-* runtime plugins. Invoked (via hooks.json) from
    the plugin's own scripts/ dir. Derives the plugin from its own location and
    the install dir from plugin.json's name (~/.<name>), then re-runs the
    installer in the BACKGROUND only when the deployed runtime version drifts
    from the payload -- so a `copilot plugin update` is picked up automatically.
    Reconciles the TOOL, never machine state/config. PS5.1+.

    OPT-IN GATE (fanned out from the agent-bridge reference implementation,
    see tools/check-bootstrap-sync.py FAMILIES): the background reconcile
    spawn below requires an explicit, checked-in, PER-PLUGIN opt-in --
    ``<project>/.copilot-extensions/config.yaml`` carrying a top-level
    ``background_reconcile_<plugin-name>: true`` line (plain regex match, no
    yaml parser -- this hook has no python/venv yet). No opt-in -> no
    background spawn; deliberate behavior change from silent auto-heal.
#>
$ErrorActionPreference = 'SilentlyContinue'
$script:SessionStartJsonEmitted = $false
function Write-SessionStartJson {
    if (-not $script:SessionStartJsonEmitted) {
        [Console]::Out.Write('{}')
        $script:SessionStartJsonEmitted = $true
    }
}
function Exit-SessionStart {
    Write-SessionStartJson
    exit 0
}
$PluginDir = Split-Path -Parent $PSScriptRoot
try {
    $name = (Get-Content (Join-Path $PluginDir 'plugin.json') -Raw | ConvertFrom-Json).name
    if (-not $name) { Exit-SessionStart }
    $InstallDir = Join-Path $env:USERPROFILE ".$name"
    $Manifest = Join-Path $InstallDir 'deploy-manifest.json'
    if (-not (Test-Path $Manifest)) {
        # Not provisioned yet -- do the cheap FIRST install ('stamp') so the
        # binstub is on PATH this session; the self-provisioning binstub then
        # builds the venv on first use (#1393). Fires only when the installer
        # (init.ps1 or install.ps1) declares a 'stamp' action; else a safe no-op.
        $stampInst = @("$PluginDir\scripts\init.ps1", "$PluginDir\scripts\install.ps1") |
            Where-Object { (Test-Path $_) -and (Select-String -Path $_ -Pattern "'stamp'" -Quiet) } |
            Select-Object -First 1
        if ($stampInst) {
            $pw = Get-Command pwsh -ErrorAction SilentlyContinue
            $exe = if ($pw) { $pw.Source } else { 'powershell.exe' }
            & $exe -NoProfile -ExecutionPolicy Bypass -File $stampInst stamp *> $null
        }
        Exit-SessionStart
    }
    $deployed = "" + (Get-Content $Manifest -Raw | ConvertFrom-Json).source.version
    $current = $deployed
    $pyproj = Join-Path $PluginDir 'pyproject.toml'
    if (Test-Path $pyproj) {
        $vl = Select-String -Path $pyproj -Pattern '^\s*version\s*=' | Select-Object -First 1
        if ($vl) { $current = ($vl.Line -replace '.*=\s*"([^"]+)".*', '$1') }
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
    if ($provisioned -and $deployed -eq $current) { Exit-SessionStart }

    # OPT-IN GATE: drift exists, so we'd normally reconcile -- but only when
    # this project has explicitly opted THIS plugin in (per-plugin, not a
    # blanket flag). COPILOT_PROJECT_DIR is the session's project checkout,
    # injected by the CLI at session start; fall back to cwd if unset.
    $ProjectDir = $env:COPILOT_PROJECT_DIR
    if (-not $ProjectDir) { $ProjectDir = (Get-Location).Path }
    $optInFile = Join-Path $ProjectDir '.copilot-extensions\config.yaml'
    $optInKey = "background_reconcile_$name"
    $optedIn = $false
    if (Test-Path -LiteralPath $optInFile -PathType Leaf) {
        $optInPattern = '^\s*' + [regex]::Escape($optInKey) + ':\s*true\s*$'
        $optedIn = [bool](Select-String -LiteralPath $optInFile -Pattern $optInPattern -Quiet -ErrorAction SilentlyContinue)
    }
    if (-not $optedIn) {
        [Console]::Error.WriteLine("[$name] runtime $deployed -> $current; background reconcile SKIPPED (no opt-in -- add '$optInKey`: true' to $optInFile to enable)")
        Exit-SessionStart
    }

    $init = Join-Path $PluginDir 'scripts\init.ps1'
    if (Test-Path $init) {
        $reCmd = "& `"$init`""
    } else {
        $inst = Join-Path $PluginDir 'scripts\install.ps1'
        if (-not (Test-Path $inst)) { Exit-SessionStart }
        $reCmd = "& `"$inst`" install"
    }
    [Console]::Error.WriteLine("[$name] runtime $deployed -> $current; reconciling in background...")
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
Exit-SessionStart