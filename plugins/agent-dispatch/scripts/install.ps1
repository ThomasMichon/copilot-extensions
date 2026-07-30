<#
.SYNOPSIS
    agent-dispatch installer / lifecycle manager. PS5+ compatible.

.DESCRIPTION
    Canonical installer for the agent-dispatch runtime -- the same lifecycle
    shape as the agent-bridge installer (install|update|status|start|stop|
    uninstall), so the agent-worktrees plugin reconciler (runtimeScope:
    machine-gated) and `aperture-labs services agent-dispatch <action>` both
    drive it.

    Creates the runtime at ~/.agent-dispatch/ (venv + package), a
    ~/.local/bin/agent-dispatch.cmd binstub, and -- on its deploy machines --
    an auto-starting Windows Scheduled Task running the FULL coordinator. The
    always-on Windows host owns the coordinator (Phase 2, issue #2818); it binds
    adaptively by WSL networking mode (mirrored -> 127.0.0.1; NAT -> the
    vEthernet(WSL) IP, resolved at startup, never 0.0.0.0/LAN). This reverses the
    #2777 model where WSL owned the coordinator and Windows was a client.

    On a coordinator host it ALSO installs the embody SUPERVISOR
    (Scheduled Task 'agent-dispatch-supervisor'), which runs
    `agent-dispatch supervise --all-repos` so dispatched, LABELED tasks are
    turned into host embody autopilots unattended -- the Windows peer of the
    Linux systemd supervisor unit (cross-platform-parity). It is label-gated
    for safety (a label-less supervisor would embody every queued task): the
    task is enabled only when AGENT_DISPATCH_SUPERVISE_LABELS is set in
    supervisor.env; with none set the task is registered but left DISABLED
    (inert), and the generated launcher hard-refuses a label-less run (#2869).

.PARAMETER Action
    install (default) | update | status | start | stop | uninstall.

.PARAMETER InstallDir
    Override the runtime install directory (default: ~/.agent-dispatch).

.PARAMETER NoService
    Install/update the client (venv + binstub) only; do NOT install/start the
    coordinator Scheduled Task (a deliberately client-only host). Also skips the
    embody supervisor (it needs a local coordinator).

.PARAMETER NoSupervisor
    Install everything EXCEPT the embody supervisor Scheduled Task (the
    coordinator still installs on an eligible host).

.PARAMETER Purge
    On uninstall: also delete config, DB, and the env file.

.PARAMETER Force
    On update: bypass the downgrade guard (deliberate rollback). Env:
    AGENT_DISPATCH_ALLOW_DOWNGRADE=1.
#>
[CmdletBinding()]
param(
    [ValidateSet('install', 'update', 'status', 'start', 'stop', 'uninstall')]
    [string]$Action = 'install',
    [string]$InstallDir,
    [switch]$NoService,
    [switch]$NoSupervisor,
    [switch]$Interactive,
    [switch]$Purge,
    [switch]$Force
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

if ($env:AGENT_DISPATCH_ALLOW_DOWNGRADE -eq '1') { $Force = $true }

# -- Output helpers (PS5-safe) ------------------------------------------

function Write-Ok      { param([string]$Msg) Write-Host "  [OK]   $Msg" -ForegroundColor Green }
function Write-Skip    { param([string]$Msg) Write-Host "  [SKIP] $Msg" -ForegroundColor Cyan }
function Write-Fail    { param([string]$Msg) Write-Host "  [FAIL] $Msg" -ForegroundColor Red }
function Write-Warn    { param([string]$Msg) Write-Host "  [WARN] $Msg" -ForegroundColor Yellow }
function Write-Step    { param([string]$Msg) Write-Host "  ...    $Msg" -ForegroundColor DarkGray }

# -- Paths --------------------------------------------------------------

$PluginDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PkgSrcDir = Join-Path $PluginDir 'src\agent_dispatch'

if (-not $InstallDir) {
    $InstallDir = Join-Path $env:USERPROFILE '.agent-dispatch'
}
$VenvDir  = Join-Path $InstallDir '.venv'
$LocalBin = Join-Path $env:USERPROFILE '.local\bin'

if ($env:OS -eq 'Windows_NT') {
    $VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
} else {
    $VenvPython = Join-Path $VenvDir 'bin/python'
}
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
$TaskName = 'agent-dispatch'
$SupervisorTaskName = 'agent-dispatch-supervisor'
$DefaultPort = 9847

# === install-contract:v3 versioned-venv (agent-dispatch: .venv-as-junction) ===
# Immutable per-version runtime (#581). Build the venv into versions/<version>
# and make the historical `.venv` path a junction (Windows) / symlink (POSIX)
# into it, so the binstubs, the coordinator + supervisor task launchers, and the
# deploy-manifest -- all of which reference `.venv` -- resolve through the link
# unchanged. LinkDir/LinkPython is the stable `.venv` path (runtime-facing, never
# a versions/<v> absolute a `gc` could remove); VenvDir/VenvPython is the
# versions/<v> slot (build + health-gate + the firewall -Program, which needs the
# RESOLVED image path the running daemon reports). Legacy mode: Link == Venv.
# Gated behind AGENT_DISPATCH_VERSIONED=0 (default ON); COPILOT_EXT_NO_VERSIONED=1
# force-disables. scripts/versioned_runtime.py owns the swap + migration + gc.
$LinkDir          = $VenvDir
$LinkPython       = $VenvPython
$VersionedRuntime = $false
$SrcVersion       = $null
if (($env:COPILOT_EXT_NO_VERSIONED -ne '1') -and
    ($env:AGENT_DISPATCH_VERSIONED -notin @('0', 'false', 'no', 'off'))) {
    $pyprojForVer = Join-Path $PluginDir 'pyproject.toml'
    if (Test-Path $pyprojForVer) {
        $vl = Select-String -Path $pyprojForVer -Pattern '^\s*version\s*=' | Select-Object -First 1
        if ($vl) { $SrcVersion = ($vl.Line -replace '.*=\s*"([^"]+)".*', '$1') }
    }
    if ($SrcVersion) {
        $VersionedRuntime = $true
        $VenvDir = Join-Path (Join-Path $InstallDir 'versions') $SrcVersion
        if ($env:OS -eq 'Windows_NT') { $VenvPython = Join-Path $VenvDir 'Scripts\python.exe' }
        else { $VenvPython = Join-Path $VenvDir 'bin/python' }
    }
}
# === end install-contract:v3 versioned-venv ===

# === install-contract:v3 versioned-venv helpers (agent-dispatch) ===
function Test-VenvIsLink {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $false }
    try { return [bool]((Get-Item $Path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) }
    catch { return $false }
}

function Invoke-VersionedActivate {
    <# Swap the stable `.venv` link to this version's freshly-built slot. No-op in
       legacy mode. First migration: the `.venv` path is still a REAL dir the
       running coordinator + supervisor hold open -- Windows can't rename it aside
       while a loaded python.exe locks it, so stop BOTH daemons first (the task
       re-register below restarts them on the new slot). A later version-bump swaps
       only the link (the daemons run from their own immutable slot until Invoke-
       Update cycles them). #>
    if (-not $VersionedRuntime) { return $true }
    if ((Test-Path $LinkDir) -and -not (Test-VenvIsLink $LinkDir)) {
        Write-Step 'Releasing legacy .venv for versioned migration (stopping coordinator + supervisor)...'
        try { Stop-DispatchProcess -Subcommand serve | Out-Null } catch {}
        try { Stop-DispatchProcess -Subcommand supervise | Out-Null } catch {}
    }
    $vr = Join-Path $PSScriptRoot 'versioned_runtime.py'
    $py = if (Test-Path $VenvPython) { $VenvPython } else { $LinkPython }
    & $py $vr --root $InstallDir --link-name '.venv' activate $SrcVersion --replace-nonlink 2>&1 |
        ForEach-Object { Write-Step $_ }
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Failed to activate versioned venv (.venv -> versions/$SrcVersion)"
        return $false
    }
    Write-Ok "Runtime version $SrcVersion active (.venv -> versions/$SrcVersion)"
    return $true
}

function Get-VersionedCurrent {
    if (-not $VersionedRuntime) { return '' }
    $vr = Join-Path $PSScriptRoot 'versioned_runtime.py'
    $py = if (Test-Path $LinkPython) { $LinkPython } elseif (Test-Path $VenvPython) { $VenvPython } else { $null }
    if (-not $py) { return '' }
    $out = & $py $vr --root $InstallDir --link-name '.venv' current 2>$null
    return ("$out").Trim()
}

function Invoke-VersionedGc {
    param([string]$KeepPrev)
    if (-not $VersionedRuntime) { return }
    $vr = Join-Path $PSScriptRoot 'versioned_runtime.py'
    $py = if (Test-Path $LinkPython) { $LinkPython } elseif (Test-Path $VenvPython) { $VenvPython } else { $null }
    if (-not $py) { return }
    $gcArgs = @($vr, '--root', $InstallDir, '--link-name', '.venv', 'gc', '--protect-pids')
    if ($KeepPrev) { $gcArgs += @('--keep', $KeepPrev) }
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $py @gcArgs 2>&1 | ForEach-Object { Write-Step "gc: $_" }
    $ErrorActionPreference = $prevEAP
}
# === end install-contract:v3 versioned-venv helpers ===
function Remove-ConsoleTrampolines {
    <# Strip the uv-regenerated Scripts\<name>.exe console-script trampolines from
       the venv after install. They are unsigned, zero-reputation PEs that Smart
       App Control blocks (CodeIntegrity 3077); nothing launches them (binstubs,
       services, and probes all use "python.exe -m <pkg>"), so remove every
       agent-*.exe. Best-effort -- rename a locked copy aside, then sweep stale
       stashes. Windows-only: POSIX console scripts are the sanctioned launch
       path and must be preserved. #>
    param([Parameter(Mandatory)][string]$VenvDir)
    if ($env:OS -ne 'Windows_NT') { return }
    $scriptsDir = Join-Path $VenvDir 'Scripts'
    if (-not (Test-Path $scriptsDir)) { return }
    Get-ChildItem (Join-Path $scriptsDir 'agent-*.exe') -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            Remove-Item $_.FullName -Force -ErrorAction Stop
        } catch {
            try { Rename-Item $_.FullName "$($_.FullName).old-$(Get-Date -Format yyyyMMddHHmmss)" -ErrorAction Stop } catch {}
        }
    }
    Get-ChildItem (Join-Path $scriptsDir 'agent-*.exe.old-*') -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue }
}
# === end install-contract:v3 strip-trampolines ===

# === install-contract:v3 source-kind -- keep byte-identical across plugins ===
# Vendored under the Copilot CLI installed-plugins dir => marketplace;
# anything else (a git checkout) => local.
function Get-SourceKind {
    param([string]$PluginPath)
    if (($PluginPath -replace '\\', '/') -match '/\.copilot/installed-plugins/') {
        return 'marketplace'
    }
    return 'local'
}
# === end install-contract:v3 source-kind ===

function Get-GitInfo {
    param([string]$Path)
    try {
        $commit = git -C $Path rev-parse --short HEAD 2>$null
        $branch = git -C $Path rev-parse --abbrev-ref HEAD 2>$null
        $dirty = $false
        if (git -C $Path status --porcelain 2>$null) { $dirty = $true }
        return @{
            commit = $(if ($commit) { $commit } else { 'unknown' })
            branch = $(if ($branch) { $branch } else { 'unknown' })
            dirty  = $dirty
        }
    } catch {
        return @{ commit = 'unknown'; branch = 'unknown'; dirty = $false }
    }
}

# -- Version helpers + downgrade guard (parity with agent-bridge #1790) ------

function Get-InstalledVersion {
    if (-not (Test-Path $LinkPython)) { return $null }
    try {
        $v = & $LinkPython -c 'from importlib.metadata import version; print(version("agent-dispatch"))' 2>$null
        if ($LASTEXITCODE -eq 0 -and $v) { return $v.Trim() }
    } catch {}
    return $null
}

function Get-SourceVersion {
    $manifest = Join-Path $PluginDir 'plugin.json'
    if (-not (Test-Path $manifest)) { return $null }
    $m = Select-String -Path $manifest -Pattern '"version"\s*:\s*"([^"]+)"' | Select-Object -First 1
    if ($m) { return ($m.Line -replace '.*"version"\s*:\s*"([^"]+)".*', '$1') }
    return $null
}

# Integer tuple from a version (extract every run of digits). [0,1,0,19] for
# 0.1.0-dev19. Compared element-wise so the devN build stream orders correctly.
function Get-VerTuple {
    param([string]$v)
    $nums = [regex]::Matches($v, '\d+') | ForEach-Object { [int]$_.Value }
    return , @($nums)
}

function Test-VersionLt {
    param([string]$A, [string]$B)
    if ($A -eq $B) { return $false }
    $ta = Get-VerTuple $A; $tb = Get-VerTuple $B
    $n = [Math]::Max($ta.Count, $tb.Count)
    for ($i = 0; $i -lt $n; $i++) {
        $x = if ($i -lt $ta.Count) { $ta[$i] } else { 0 }
        $y = if ($i -lt $tb.Count) { $tb[$i] } else { 0 }
        if ($x -lt $y) { return $true }
        if ($x -gt $y) { return $false }
    }
    return $false
}

function Invoke-DowngradeGuard {
    $installed = Get-InstalledVersion
    if (-not $installed) { return }
    $source = Get-SourceVersion
    if (-not $source) {
        Write-Warn 'Could not read source version from plugin.json -- skipping downgrade guard'
        return
    }
    if (Test-VersionLt -A $source -B $installed) {
        if ($Force) {
            Write-Warn "Downgrade $installed -> $source forced (-Force / AGENT_DISPATCH_ALLOW_DOWNGRADE)"
            return
        }
        Write-Host ''
        Write-Fail "Refusing to downgrade agent-dispatch: installed $installed > source $source"
        Write-Fail 'This checkout is OLDER than the deployed runtime. Use the sanctioned path:'
        Write-Fail '    aperture-labs services agent-dispatch update'
        Write-Fail 'Or override intentionally (deliberate rollback):'
        Write-Fail "    install.ps1 -Action $Action -Force"
        Write-Host ''
        exit 1
    }
}

# -- Runtime install (venv + package + binstub + manifest + verify + pivot) --

function Install-Runtime {
    if (-not (Test-Path $PkgSrcDir)) {
        Write-Fail "Package source not found at $PkgSrcDir"
        exit 1
    }

    $hasWinget = $null -ne (Get-Command winget -ErrorAction SilentlyContinue)

    # Find a Python interpreter (skip Windows Store aliases that aren't real)
    $pythonCmd = $null
    foreach ($candidate in @('python', 'python3', 'py')) {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($found) {
            $prevEAP = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            try {
                $testOut = & $found.Source --version 2>&1
                if ($LASTEXITCODE -eq 0 -and $testOut -match 'Python') { $pythonCmd = $found.Source }
            } catch { }
            $ErrorActionPreference = $prevEAP
            if ($pythonCmd) { break }
        }
    }
    if (-not $pythonCmd) {
        Write-Fail 'Python not found on PATH (need 3.10+)'
        Write-Host '    winget install Python.Python.3.13' -ForegroundColor DarkGray
        exit 1
    }
    Write-Ok "Python: $pythonCmd"

    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        if ($hasWinget) {
            Write-Step 'uv not found -- installing via winget...'
            $prevEAP = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            & winget install --id astral-sh.uv --accept-source-agreements --accept-package-agreements 2>&1 | Out-Null
            $ErrorActionPreference = $prevEAP
            $env:PATH = [System.Environment]::GetEnvironmentVariable('PATH', 'Machine') + ';' + [System.Environment]::GetEnvironmentVariable('PATH', 'User')
            if (Get-Command uv -ErrorAction SilentlyContinue) { Write-Ok 'uv installed' }
        }
    }

    foreach ($dir in @($InstallDir, $LocalBin)) {
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    }
    Write-Ok "Directories: $InstallDir"

    # -- venv (SAC-trusted signed base python preferred; then uv; then venv) --
    if (-not (Test-Path $VenvPython)) {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $signedBase = $null
        if ($env:OS -eq 'Windows_NT' -and (Get-Command py -ErrorAction SilentlyContinue)) {
            foreach ($v in '3.13', '3.12', '3.11') {
                $cand = (& py "-$v" -c "import sys;print(sys.executable)" 2>$null | Out-String).Trim()
                if ($LASTEXITCODE -eq 0 -and $cand -and (Test-Path $cand)) {
                    try { if ((Get-AuthenticodeSignature $cand).Status -eq 'Valid') { $signedBase = $cand; break } } catch {}
                }
            }
        }
        if ($signedBase -and -not (Test-Path $VenvPython)) {
            & $signedBase -m venv --copies $VenvDir 2>&1 | Out-Null
        }
        if (-not (Test-Path $VenvPython)) {
            if (Get-Command uv -ErrorAction SilentlyContinue) {
                Write-Step 'Creating venv via uv...'
                & uv venv $VenvDir --allow-existing 2>&1 | Out-Null
                if ($LASTEXITCODE -ne 0) {
                    Write-Step 'uv venv failed -- falling back to python -m venv'
                    & $pythonCmd -m venv $VenvDir 2>&1 | Out-Null
                }
            } else {
                Write-Step 'Creating venv via python -m venv...'
                & $pythonCmd -m venv $VenvDir 2>&1 | Out-Null
            }
        }
        $ErrorActionPreference = $prevEAP
        if (-not (Test-Path $VenvPython)) {
            Write-Fail "Venv creation failed -- $VenvPython not found"
            exit 1
        }
        Write-Ok 'Venv created'
    } else {
        Write-Skip 'Venv already exists'
    }

    # -- install package (uv pip install; [mcp] extra with graceful fallback) --
    # The [mcp] extra pulls `mcp` -> `pyjwt[crypto]` -> `cryptography`, which has
    # no prebuilt wheel on some platforms (notably win-arm64) and needs a Rust +
    # MSVC toolchain to build from source. Per the plugin-services vision's
    # `degrade-gracefully` behavior, a build failure of the OPTIONAL MCP server
    # surface must not abort the whole install: fall back to the base package so
    # the coordinator CLI still deploys; only `agent-dispatch mcp` stays dark
    # until the toolchain is present.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    Remove-ConsoleTrampolines -VenvDir $VenvDir

    $installPkg = {
        param([string]$Spec)
        if (Get-Command uv -ErrorAction SilentlyContinue) {
            $out = & uv pip install --python $VenvPython $Spec 2>&1 | Out-String
        } else {
            $out = & $VenvPython -m pip install $Spec 2>&1 | Out-String
        }
        [pscustomobject]@{ Code = $LASTEXITCODE; Output = $out }
    }

    $mcpResult = & $installPkg "$($PluginDir)[mcp]"
    if ($mcpResult.Code -eq 0) {
        Write-Ok 'Package installed: agent-dispatch [mcp]'
    } else {
        Write-Warn 'Could not install the [mcp] extra (its native deps may not build on this platform) -- falling back to a base install without the MCP server surface'
        $baseResult = & $installPkg "$PluginDir"
        if ($baseResult.Code -ne 0) {
            Write-Fail 'Failed to install agent-dispatch package into venv'
            Write-Host $baseResult.Output
            $ErrorActionPreference = $prevEAP
            exit 1
        }
        Write-Ok 'Package installed: agent-dispatch (base -- `agent-dispatch mcp` server unavailable on this platform)'
    }
    $ErrorActionPreference = $prevEAP
    Remove-ConsoleTrampolines -VenvDir $VenvDir

    # -- binstub (.cmd on Windows -- see init history; POSIX shell elsewhere) --
    $stubName = 'agent-dispatch'
    if ($env:OS -eq 'Windows_NT') {
        $ps1Path = Join-Path $LocalBin "$stubName.ps1"
        if (Test-Path $ps1Path) { Remove-Item $ps1Path -Force -ErrorAction SilentlyContinue }
        $stubPath = Join-Path $LocalBin "$stubName.cmd"
        $stubContent = @"
@echo off
set "PYTHONUTF8=1"
set "_PY=%USERPROFILE%\.agent-dispatch\.venv\Scripts\python.exe"
for /f "tokens=2 delims=[]" %%i in ('dir /a:l "%USERPROFILE%\.agent-dispatch" 2^>nul ^| findstr /i /c:".venv"') do set "_PY=%%i\Scripts\python.exe"
"%_PY%" -m agent_dispatch %*
"@
        [System.IO.File]::WriteAllText($stubPath, $stubContent, $utf8NoBom)
    } else {
        $stubPath = Join-Path $LocalBin $stubName
        $stubContent = @"
#!/usr/bin/env bash
export PYTHONUTF8=1
exec "`$HOME/.agent-dispatch/.venv/bin/python" -m agent_dispatch "`$@"
"@
        [System.IO.File]::WriteAllText($stubPath, $stubContent, $utf8NoBom)
    }
    Write-Ok "Binstub: $stubPath"

    # Versioned layout (#581): health-gate the freshly-built slot in isolation,
    # then swap the stable `.venv` link onto it. Everything below (manifest, task
    # launchers, binstub) resolves through `.venv` (the link). No-op in legacy
    # mode. Remember the previously-active version as the gc keep target (a
    # not-yet-cycled daemon may still run it).
    $prevVersion = ''
    if ($VersionedRuntime) {
        $prevVersion = Get-VersionedCurrent
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & $VenvPython -c 'import agent_dispatch' 2>$null
        $slotOk = ($LASTEXITCODE -eq 0)
        $ErrorActionPreference = $prevEAP
        if (-not $slotOk) {
            Write-Fail "Fresh runtime slot failed its health gate (versions/$SrcVersion) -- not activating"
            exit 1
        }
        if (-not (Invoke-VersionedActivate)) { exit 1 }
    }

    Write-Manifest

    # -- verify (through the stable `.venv` link) --
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $importOk = $false
    for ($i = 0; $i -lt 3; $i++) {
        & $LinkPython -c 'import agent_dispatch' 2>$null
        if ($LASTEXITCODE -eq 0) { $importOk = $true; break }
        Start-Sleep -Seconds 1
    }
    $ErrorActionPreference = $prevEAP
    if ($importOk) { Write-Ok 'Verification: module imports successfully' }
    else { Write-Fail 'Verification: module import failed'; exit 1 }

    # Versioned layout: prune old slots, keeping current + the previous-good (the
    # slot a not-yet-cycled daemon may still run from) + any live-pid-pinned slot.
    if ($VersionedRuntime) { Invoke-VersionedGc -KeepPrev $prevVersion }

    # -- PATH --
    $pathDirs = $env:PATH -split ';'
    if ($pathDirs -contains $LocalBin) {
        Write-Ok "PATH: $LocalBin is on PATH"
    } else {
        $currentUserPath = [System.Environment]::GetEnvironmentVariable('PATH', 'User')
        if (-not ($currentUserPath -split ';' | Where-Object { $_ -eq $LocalBin })) {
            [System.Environment]::SetEnvironmentVariable('PATH', "$LocalBin;$currentUserPath", 'User')
            $env:PATH = "$LocalBin;$env:PATH"
            Write-Ok "PATH: Added $LocalBin to User PATH"
        }
    }

    Register-PickerPivot
}

function Write-Manifest {
    $manifestPath = Join-Path $InstallDir 'deploy-manifest.json'
    $kind = Get-SourceKind -PluginPath $PluginDir
    $ver = '0.0.0'
    $pyproj = Join-Path $PluginDir 'pyproject.toml'
    if (Test-Path $pyproj) {
        $verLine = Select-String -Path $pyproj -Pattern '^\s*version\s*=' | Select-Object -First 1
        if ($verLine) { $ver = ($verLine.Line -replace '.*=\s*"([^"]+)".*', '$1') }
    }
    $commit = $null; $branch = $null; $dirty = $false
    if ($kind -eq 'local') {
        $repoRoot = Split-Path -Parent (Split-Path -Parent $PluginDir)
        $git = Get-GitInfo -Path $repoRoot
        $commit = $git.commit; $branch = $git.branch; $dirty = $git.dirty
    }
    $manifest = [ordered]@{
        schema_version = 3
        service        = 'agent-dispatch'
        deployed_at    = (Get-Date -Format 'o')
        deployed_by    = "$($env:COMPUTERNAME.ToLower())-windows"
        source         = [ordered]@{
            kind    = $kind
            path    = ($PluginDir -replace '\\', '/')
            repo    = 'copilot-extensions'
            plugin  = 'agent-dispatch'
            version = $ver
            commit  = $commit
            branch  = $branch
            dirty   = $dirty
        }
        venv           = ($LinkDir -replace '\\', '/')
        runtime        = 'python'
    }
    $tmp = "$manifestPath.tmp"
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -Path $tmp -Encoding UTF8
    Move-Item -Force -Path $tmp -Destination $manifestPath
    Write-Ok "Deploy manifest written (source: $kind)"
}

function Register-PickerPivot {
    $pivotSrc = Join-Path $PluginDir 'pivots\agent-dispatch.json'
    $pivotDir = Join-Path $env:USERPROFILE '.agent-worktrees\pivots'
    if (Test-Path $pivotSrc) {
        try {
            if (-not (Test-Path $pivotDir)) { New-Item -ItemType Directory -Force -Path $pivotDir | Out-Null }
            Copy-Item -Force $pivotSrc (Join-Path $pivotDir 'agent-dispatch.json')
            Write-Ok "Picker pivot registered: $pivotDir\agent-dispatch.json"
        } catch {
            Write-Skip 'Could not register picker pivot (agent-worktrees runtime root not writable)'
        }
    } else {
        Write-Skip "Picker pivot manifest not found at $pivotSrc"
    }
}

# -- Coordinator Scheduled Task (default-on on deploy machines) --------------

function Remove-CoordinatorTask {
    # Returns 'removed' | 'blocked' | 'absent'. Unregister-ScheduledTask may need
    # elevation (Access denied) for a task in the root folder. Used by the
    # -NoService client path and by uninstall.
    if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
        return 'absent'
    }
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        return 'blocked'
    }
    return 'removed'
}

function Get-ServiceMode {
    # Resolve the service auto-start mode for this host:
    #   'interactive' -- an interactive (RDP/console) logon is required before the
    #                    box is usable (verified: dev6/cloud1/augloop1 must be
    #                    RDP-kicked before SSH works). Such a logon ALWAYS precedes
    #                    dispatch, so the non-elevated HKCU logon auto-start is the
    #                    first-class coordinator service -- no elevated boot task.
    #   'boot'        -- default: a true always-on boot service via an elevated S4U
    #                    Scheduled Task (for a genuinely headless box). Degrades to
    #                    the logon auto-start when registration needs elevation.
    # Precedence: the -Interactive switch > the persisted marker > 'boot'.
    if ($Interactive) { return 'interactive' }
    $marker = Join-Path $InstallDir 'service-mode'
    if (Test-Path $marker) {
        try {
            $m = ((Get-Content $marker -Raw -ErrorAction Stop)).Trim().ToLowerInvariant()
            if ($m -eq 'interactive') { return 'interactive' }
        } catch { }
    }
    return 'boot'
}

function Save-ServiceMode {
    # Persist the service mode so a later `update` (which may not re-pass
    # -Interactive) keeps installing the logon auto-start instead of reverting to
    # the elevated boot task. Kept across a non-purge uninstall (it is config).
    param([Parameter(Mandatory)][ValidateSet('interactive', 'boot')][string]$Mode)
    $marker = Join-Path $InstallDir 'service-mode'
    try { [System.IO.File]::WriteAllText($marker, $Mode, $utf8NoBom) } catch { }
}

function Set-ServiceEnvLoopback {
    # Pin the coordinator to loopback (127.0.0.1) in service.env for interactive
    # mode. Interactive mode targets the Windows-interactive user and ignores the
    # WSL guest, so the NAT vEthernet(WSL) bind -- which needs an elevation-gated
    # firewall rule to be reachable -- is unwanted. Loopback is reachable from the
    # host session with NO elevation on both mirrored and NAT boxes (verified: a
    # NAT box's coordinator was unreachable on its vEthernet IP without the
    # firewall rule, but answered immediately on 127.0.0.1). Idempotent: drops any
    # existing AGENT_DISPATCH_HOST line (active or commented) and appends one
    # ACTIVE pin. The loopback-strip migration above is skipped in interactive
    # mode so this pin survives re-runs.
    $envFile = Join-Path $InstallDir 'service.env'
    $pin = 'AGENT_DISPATCH_HOST=127.0.0.1'
    $kept = @()
    if (Test-Path $envFile) {
        foreach ($l in @(Get-Content $envFile)) {
            if ($l -notmatch '^\s*#?\s*AGENT_DISPATCH_HOST\s*=') { $kept += $l }
        }
    }
    $kept += $pin
    [System.IO.File]::WriteAllText($envFile, (($kept -join "`r`n") + "`r`n"), $utf8NoBom)
    Write-Ok 'Coordinator pinned to loopback 127.0.0.1 (interactive mode: no firewall/elevation; WSL ignored)'
}

function Test-CoordinatorHealthy {
    # True when a coordinator already answers on its rendezvous endpoint. Used to
    # keep the non-elevated fallback idempotent (never start a second instance).
    try {
        $null = & $VenvPython -m agent_dispatch health 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Stop-DispatchProcess {
    # Terminate any RUNNING agent_dispatch process for the given subcommand
    # ('serve' = coordinator, 'supervise' = embody supervisor), regardless of how
    # it was launched. The launcher runs under `conhost.exe --headless`, which
    # DETACHES the powershell+python from the Scheduled Task's tracked process
    # tree, so `Stop-ScheduledTask` does NOT kill the actual `python -m
    # agent_dispatch <subcommand>` process -- a stop/update otherwise leaves the
    # OLD build running and still holding the rendezvous endpoint (#3602). Resolve
    # the live coordinator PID from the rendezvous file (serve only) AND match the
    # `-m agent_dispatch <subcommand>` command line, kill each, then (serve) clear
    # the stale endpoint file so a client doesn't chase a dead endpoint and the
    # fresh coordinator writes a clean one. Returns the count terminated.
    param([Parameter(Mandatory)][ValidateSet('serve', 'supervise')][string]$Subcommand)
    if ($env:OS -ne 'Windows_NT') { return 0 }

    $pidsToKill = New-Object System.Collections.Generic.List[int]
    $endpointFile = Join-Path (Join-Path $InstallDir 'run') 'endpoint.json'

    # 1) The rendezvous-advertised PID (the coordinator that owns the endpoint).
    if ($Subcommand -eq 'serve' -and (Test-Path $endpointFile)) {
        try {
            $ep = Get-Content $endpointFile -Raw | ConvertFrom-Json
            if (($ep.PSObject.Properties.Name -contains 'pid') -and $ep.pid) {
                [void]$pidsToKill.Add([int]$ep.pid)
            }
        } catch { }
    }
    # 2) Any matching `-m agent_dispatch <subcommand>` process (the detached child
    #    + orphans not reflected in the rendezvous file). '\bserve\b' never matches
    #    'supervise' (and vice-versa), so the two never cross-terminate.
    try {
        $needle = '\b' + [regex]::Escape($Subcommand) + '\b'
        Get-CimInstance Win32_Process -Filter "Name LIKE 'python%.exe'" -ErrorAction SilentlyContinue |
            Where-Object {
                $_.CommandLine -and
                ($_.CommandLine -match 'agent_dispatch') -and
                ($_.CommandLine -match $needle)
            } |
            ForEach-Object { [void]$pidsToKill.Add([int]$_.ProcessId) }
    } catch { }

    $killed = 0
    foreach ($procId in ($pidsToKill | Select-Object -Unique)) {
        try {
            Stop-Process -Id $procId -Force -ErrorAction Stop
            $killed++
        } catch { }
    }
    if ($Subcommand -eq 'serve' -and (Test-Path $endpointFile)) {
        Remove-Item $endpointFile -Force -ErrorAction SilentlyContinue
    }
    return $killed
}

function Confirm-CoordinatorRunning {
    # After a start/update, verify a coordinator actually answers AND runs the
    # just-installed build -- catching the "reports success but the old detached
    # build is still serving the rendezvous endpoint" drift (#3602). Compares the
    # running coordinator's health version against the freshly-installed package
    # __version__ (identical string source, so no normalization mismatch). Polls
    # briefly to let a fresh coordinator bind + advertise.
    if (-not (Test-Path $VenvPython)) { return }
    $installed = $null
    try {
        $installed = (& $VenvPython -c 'import agent_dispatch; print(agent_dispatch.__version__)' 2>$null).Trim()
    } catch { $installed = $null }

    $running = $null
    for ($i = 0; $i -lt 12; $i++) {
        try {
            $out = & $VenvPython -m agent_dispatch health 2>$null
            if (($LASTEXITCODE -eq 0) -and $out) {
                $h = ($out | Out-String) | ConvertFrom-Json
                if ($h.PSObject.Properties.Name -contains 'version') { $running = $h.version }
                if ($running) { break }
            }
        } catch { }
        Start-Sleep -Seconds 1
    }

    if (-not $running) {
        Write-Warn 'Coordinator did not answer health after start -- check serve-service.log'
        return
    }
    if ($installed -and ($running -ne $installed)) {
        Write-Warn "Coordinator is serving $running but $installed is installed -- a stale build may still hold the endpoint. Re-run: install.ps1 -Action stop; then -Action start"
    } else {
        Write-Ok "Coordinator running (version $running)"
    }
}

function Remove-CoordinatorAutostart {
    # Remove the non-elevated logon auto-start (HKCU Run) if present. Returns
    # $true when one was removed. Idempotent.
    $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    try {
        if (Get-ItemProperty -Path $runKey -Name $TaskName -ErrorAction SilentlyContinue) {
            Remove-ItemProperty -Path $runKey -Name $TaskName -ErrorAction SilentlyContinue
            return $true
        }
    } catch { }
    return $false
}

function Start-CoordinatorNonElevatedFallback {
    # Non-elevated coordinator service: start it NOW as a detached background
    # process (idempotent via a health probe) and register an HKCU Run key so it
    # (re)starts at each interactive (RDP/console) logon.
    #
    # This is used two ways:
    #   (a) FIRST-CLASS on an -Interactive host (`-Primary`): the box requires an
    #       RDP/console logon before SSH works, so a logon always precedes
    #       dispatch and the Run key always fires -- no boot task, no elevation.
    #   (b) FALLBACK in boot mode when the elevated Scheduled Task can't be
    #       registered (Access denied non-elevated; dotfiles #525).
    # `-Primary` selects the interactive-mode messaging (intended service, not a
    # degraded fallback) and suppresses the "needs elevation" warning.
    param(
        [Parameter(Mandatory)][string]$Launcher,
        [switch]$Primary
    )
    if (-not $Primary) {
        Write-Warn "Coordinator Scheduled Task not registered (registration needs elevation on this host)."
    }
    $taskArgs = "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Launcher`""

    if (Test-CoordinatorHealthy) {
        Write-Ok 'Coordinator already running (health ok) -- not starting a second instance'
    } else {
        try {
            Start-Process -FilePath 'conhost.exe' -ArgumentList $taskArgs -WindowStyle Hidden | Out-Null
            Start-Sleep -Seconds 2
            if (Test-CoordinatorHealthy) {
                Write-Ok 'Coordinator started as a detached background process (health ok)'
            } else {
                Write-Ok 'Coordinator process launched (detached) -- give it a moment; see serve-service.log for bind status'
            }
        } catch {
            Write-Warn "Could not start coordinator process: $($_.Exception.Message)"
        }
    }

    # Durable, non-elevated auto-start at each logon (HKCU Run).
    try {
        $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
        New-ItemProperty -Path $runKey -Name $TaskName -Value "conhost.exe $taskArgs" `
            -PropertyType String -Force | Out-Null
        Write-Ok "Logon auto-start registered (HKCU Run '$TaskName') -- durable without elevation"
    } catch {
        Write-Warn "Could not register logon auto-start (HKCU Run): $($_.Exception.Message)"
    }

    if ($Primary) {
        Write-Ok 'Coordinator installed as an interactive logon service (no elevation; starts at each RDP/console logon)'
    } else {
        Write-Step 'This box looks interactive-required. Re-run with -Interactive to make the logon auto-start the intended mode (silences the warning above).'
        Write-Step "Or for a true boot service, run ONCE elevated:  powershell -File `"$PSCommandPath`" -Action install"
    }
}

function Install-CoordinatorTask {
    # Windows OWNS the coordinator (Phase 2, issue #2818): the always-on Windows
    # host runs the full coordinator and the WSL guest is a client. This reverses
    # the #2777 model (WSL-owned, Windows client). Explicit -NoService still forces
    # a client-only host (e.g. a box that intentionally has no coordinator).
    if ($NoService) {
        # Remove a coordinator task left from a prior full install so a host asked
        # to be client-only stops running one. Removal may be blocked without
        # elevation -- log and continue.
        switch (Remove-CoordinatorTask) {
            'removed' { Write-Ok   'Removed local coordinator Scheduled Task (-NoService: client-only host)' }
            'blocked' { Write-Skip 'Coordinator task present but not removable without elevation (-NoService) -- run elevated to remove it' }
            default   { Write-Skip 'Coordinator service skipped (-NoService: client-only host)' }
        }
        return
    }
    if ($env:OS -ne 'Windows_NT') { return }
    $haveSchedMod = [bool](Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)
    if (-not $haveSchedMod -and (Get-ServiceMode) -ne 'interactive') {
        Write-Skip 'ScheduledTasks module unavailable -- skipping service (run: agent-dispatch serve)'
        return
    }
    $envFile = Join-Path $InstallDir 'service.env'
    if (-not (Test-Path $envFile)) {
        $envDefault = @"
# agent-dispatch coordinator service environment.
# Edit, then: Start-ScheduledTask -TaskName agent-dispatch
# AGENT_DISPATCH_HOST is resolved dynamically at startup by serve-service.ps1
# (mirrored -> 127.0.0.1; NAT -> the vEthernet(WSL) IP). Uncomment only to pin it.
# AGENT_DISPATCH_HOST=127.0.0.1
# AGENT_DISPATCH_PORT=$DefaultPort  # unset = OS-assigned dynamic port (Stage C), advertised via the rendezvous file; uncomment to pin
# AGENT_DISPATCH_DB=%USERPROFILE%\.agent-dispatch\tasks.db   # default; uncomment to override
# AGENT_DISPATCH_TOKEN=                                       # set to require bearer auth
"@
        [System.IO.File]::WriteAllText($envFile, $envDefault, $utf8NoBom)
        Write-Ok "Service env: $envFile (defaults; edit to pin the bind host / add a token)"
    } else {
        # Migrate stale old-default pins so dynamic resolution/binding takes over,
        # leaving any operator-chosen value untouched:
        #  - AGENT_DISPATCH_HOST=127.0.0.1 (#2888): early (dev39) installs pinned an
        #    ACTIVE loopback host. On a NAT box that pin makes the coordinator bind
        #    loopback -- unreachable from WSL -- because both the launcher and `serve`
        #    skip bind-host resolution whenever AGENT_DISPATCH_HOST is set. The host
        #    is now resolved at startup (mirrored -> 127.0.0.1; NAT -> vEthernet(WSL) IP).
        #  - AGENT_DISPATCH_PORT=9847 (durable-service-transport Stage C): the fixed
        #    default port pin defeats the dynamic bind. The coordinator now binds an
        #    OS-assigned port and advertises it via the rendezvous file, so drop the
        #    old-default pin (discovery-capable clients follow the dynamic port).
        $envLines = Get-Content $envFile
        $migrations = @()
        $newEnvLines = foreach ($envLine in $envLines) {
            if ($envLine -match '^\s*AGENT_DISPATCH_HOST\s*=\s*127\.0\.0\.1\s*$' -and (Get-ServiceMode) -ne 'interactive') {
                $migrations += 'AGENT_DISPATCH_HOST=127.0.0.1 (#2888)'
                '# AGENT_DISPATCH_HOST=127.0.0.1  # migrated (#2888): now resolved dynamically at startup (mirrored -> 127.0.0.1; NAT -> vEthernet(WSL) IP)'
            } elseif ($envLine -match '^\s*AGENT_DISPATCH_PORT\s*=\s*9847\s*$') {
                $migrations += 'AGENT_DISPATCH_PORT=9847 (Stage C)'
                '# AGENT_DISPATCH_PORT=9847  # migrated (Stage C): unset = OS-assigned dynamic port advertised via the rendezvous file; uncomment to pin'
            } else {
                $envLine
            }
        }
        if ($migrations.Count -gt 0) {
            [System.IO.File]::WriteAllText($envFile, (($newEnvLines -join "`r`n") + "`r`n"), $utf8NoBom)
            Write-Ok ("Service env: migrated stale pin(s) -> dynamic: " + ($migrations -join ', '))
        } else {
            Write-Skip "Service env already exists: $envFile"
        }
    }

    $launcher = Join-Path $InstallDir 'serve-service.ps1'
    $launcherBody = @"
# agent-dispatch coordinator launcher (generated by install.ps1). Do not edit;
# edit service.env instead. Loads service.env, then runs `serve`. `serve`
# resolves the bind host per WSL networking mode (mirrored -> 127.0.0.1; NAT ->
# the dynamic vEthernet(WSL) IP, re-resolved on each start) with a bounded retry
# that rides out the logon-before-WSL race on NAT (#2889). All output is teed to
# serve-service.log so a NAT bind failure / retry is diagnosable -- the Scheduled
# Task runs headless (conhost --headless), so console output is otherwise lost.
`$ErrorActionPreference = 'Stop'
`$env:PYTHONUTF8 = '1'
`$envFile = Join-Path `$PSScriptRoot 'service.env'
if (Test-Path `$envFile) {
    foreach (`$line in Get-Content `$envFile) {
        `$t = `$line.Trim()
        if (`$t -eq '' -or `$t.StartsWith('#')) { continue }
        `$kv = `$t -split '=', 2
        if (`$kv.Count -eq 2) {
            [Environment]::SetEnvironmentVariable(`$kv[0].Trim(), [Environment]::ExpandEnvironmentVariables(`$kv[1].Trim()), 'Process')
        }
    }
}
`$logFile = Join-Path `$PSScriptRoot 'serve-service.log'
try {
    if ((Test-Path `$logFile) -and ((Get-Item `$logFile).Length -gt 1MB)) {
        Move-Item -Force `$logFile "`$logFile.1"
    }
} catch { }
`$pinned = if (`$env:AGENT_DISPATCH_HOST) { `$env:AGENT_DISPATCH_HOST } else { 'auto (resolved by serve)' }
`$portShown = if (`$env:AGENT_DISPATCH_PORT) { `$env:AGENT_DISPATCH_PORT } else { 'default' }
"[`$(Get-Date -Format o)] agent-dispatch coordinator launch (host=`$pinned port=`$portShown)" |
    Out-File -FilePath `$logFile -Append -Encoding utf8
# Tee every stream (stdout/stderr/warning/info) to the log while still writing
# through, so the retry lines from serve's bind-host resolution are captured.
# serve logs via uvicorn to STDERR; under `$ErrorActionPreference = 'Stop'`
# PowerShell wraps a native command's stderr as a terminating NativeCommandError
# and would kill the long-lived coordinator on its very first log line (observed
# on Lambda-Core: task launched, banner written, no listener). Drop to
# 'Continue' for the serve invocation so stderr is captured, never fatal.
`$_venv = '$($LinkDir -replace "'","''")'
`$_py = '$($LinkPython -replace "'","''")'
# Resolve the .venv junction's target and launch the slot python DIRECTLY -- never
# *traverse* the junction (a RedirectionGuard task context is blocked from that,
# though it may still *read* the target) -- dotfiles #637. Plain-dir keeps `$_py.
try { `$_t = (Get-Item -LiteralPath `$_venv -Force -ErrorAction Stop).Target; if (`$_t) { `$_py = Join-Path (@(`$_t)[0]) 'Scripts\python.exe' } } catch {}
`$ErrorActionPreference = 'Continue'
& `$_py -m agent_dispatch serve 2>&1 | Out-File -FilePath `$logFile -Append -Encoding utf8
"@
    [System.IO.File]::WriteAllText($launcher, $launcherBody, $utf8NoBom)

    # -- Interactive-required host: the logon auto-start IS the service ----------
    # On a box that requires an RDP/console logon before it is usable (and where
    # Task Scheduler registration is admin-gated), an interactive logon always
    # precedes dispatch, so the non-elevated HKCU logon auto-start is the
    # first-class coordinator service -- no boot task, no elevation. See the
    # interactive-service-mode design.
    if ((Get-ServiceMode) -eq 'interactive') {
        if ($Interactive) { Save-ServiceMode 'interactive' }
        Set-ServiceEnvLoopback
        if ($haveSchedMod) {
            switch (Remove-CoordinatorTask) {
                'removed' { Write-Step 'Removed prior boot Scheduled Task (interactive mode: logon auto-start owns startup)' }
                default   { }
            }
        }
        Start-CoordinatorNonElevatedFallback -Launcher $launcher -Primary
        return
    }

    # Use conhost --headless to prevent Windows Terminal from capturing the
    # task's powershell as a visible window/tab when Terminal is the default
    # terminal app. -WindowStyle Hidden alone is ignored by Windows Terminal, so
    # a bare `powershell -WindowStyle Hidden` task surfaces a real console window
    # -- and because the launcher runs the long-lived `-m agent_dispatch serve`
    # in-process, that window persists for the life of the coordinator.
    $action = New-ScheduledTaskAction -Execute 'conhost.exe' `
        -Argument "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`""
    # Two triggers: -AtStartup makes the coordinator a true always-on service that
    # comes up at boot with NO interactive login (essential for a headless box
    # like Borealis, accessed only over SSH); -AtLogOn additionally (re)starts it
    # when the operator logs in (covers a manually-stopped task on a
    # console-driven box). The dev58 bounded bind-host retry
    # (_resolve_bind_host_resilient) rides out the boot-before-WSL race on NAT,
    # where the vEthernet(WSL) IP isn't up yet the instant -AtStartup fires.
    $trigger = @(
        (New-ScheduledTaskTrigger -AtStartup),
        (New-ScheduledTaskTrigger -AtLogOn)
    )
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
    $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    # LogonType S4U ("run whether the user is logged on or not", no stored
    # password): the coordinator must run headless. The prior Interactive logon
    # type only ran while the user had an interactive console session, so on a
    # headless SSH-only box the task registered but never fired (observed on
    # Borealis: State=Ready, LastRunTime=never). S4U runs it in a non-interactive
    # session at boot; validated binding the vEthernet(WSL) IP on Borealis NAT and
    # loopback on mirrored hosts. Set-ScheduledTask/Register with S4U succeeds
    # non-elevated (unlike a password-backed Password logon). NOTE: the supervisor
    # task below deliberately stays Interactive -- it spawns embody CLI sessions
    # that need an interactive session, which S4U's non-interactive station lacks.
    $principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType S4U -RunLevel Limited

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    # Register-ScheduledTask raises a TERMINATING "Access is denied" on a
    # non-elevated host, which would abort the whole installer with a non-zero
    # exit even though the client (venv + binstub + manifest) is already fully
    # deployed above. Per the plugin-services vision's `degrade-gracefully`
    # behavior, a client-only host (e.g. a field terminal that is not a
    # coordinator) must still complete: trap the failure into the existing
    # non-fatal $regOk path instead of terminating.
    $regOk = $false
    try {
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
            -Settings $settings -Principal $principal -Force `
            -Description 'agent-dispatch -- portable agent task-queue coordinator' | Out-Null
        $regOk = $?
    } catch {
        $regOk = $false
    }
    if ($regOk) { Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }
    $ErrorActionPreference = $prevEAP

    if ($regOk) {
        Write-Ok "Coordinator service installed + started (Scheduled Task '$TaskName')"
        # The Scheduled Task now owns startup -- drop any non-elevated logon
        # fallback from a prior unprivileged install so two coordinators don't race.
        if (Remove-CoordinatorAutostart) {
            Write-Step "Removed the non-elevated logon fallback -- the Scheduled Task supersedes it"
        }
    } else {
        Start-CoordinatorNonElevatedFallback -Launcher $launcher
    }
}

# -- Embody supervisor Scheduled Task (Windows; label-gated) ----------------
#
# The Windows peer of the Linux systemd supervisor unit (cross-platform-parity).
# Runs `agent-dispatch supervise --all-repos`, turning queued LABELED tasks into
# host embody autopilots. `--all-repos` avoids the lane-scoping gotcha (a short
# `--repo owner/name` filters every task out), which makes the label opt-in the
# ONLY thing between the supervisor and embodying *every* queued task -- so the
# task is enabled only when supervisor.env sets AGENT_DISPATCH_SUPERVISE_LABELS.
# See #2869.

function Test-SupervisorLabelsConfigured {
    # True when supervisor.env declares a non-empty AGENT_DISPATCH_SUPERVISE_LABELS.
    $envFile = Join-Path $InstallDir 'supervisor.env'
    if (-not (Test-Path $envFile)) { return $false }
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*AGENT_DISPATCH_SUPERVISE_LABELS\s*=\s*(.+?)\s*$') {
            $val = $Matches[1].Trim().Trim('"').Trim("'")
            $val = ($val -replace '[\s,]', '')
            if ($val -ne '') { return $true }
        }
    }
    return $false
}

function Remove-SupervisorTask {
    # Returns 'removed' | 'blocked' | 'absent'. Mirrors Remove-CoordinatorTask.
    if (-not (Get-ScheduledTask -TaskName $SupervisorTaskName -ErrorAction SilentlyContinue)) {
        return 'absent'
    }
    Stop-ScheduledTask -TaskName $SupervisorTaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $SupervisorTaskName -Confirm:$false -ErrorAction SilentlyContinue
    if (Get-ScheduledTask -TaskName $SupervisorTaskName -ErrorAction SilentlyContinue) {
        return 'blocked'
    }
    return 'removed'
}

function Remove-SupervisorAutostart {
    # Remove the supervisor's non-elevated logon auto-start (HKCU Run) if present.
    $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    try {
        if (Get-ItemProperty -Path $runKey -Name $SupervisorTaskName -ErrorAction SilentlyContinue) {
            Remove-ItemProperty -Path $runKey -Name $SupervisorTaskName -ErrorAction SilentlyContinue
            return $true
        }
    } catch { }
    return $false
}

function Install-SupervisorLogonAutostart {
    # Interactive-mode supervisor: start it now (detached) and register an HKCU
    # Run key so it (re)starts at each interactive logon. An interactive logon
    # station is actually the RIGHT fit for the supervisor (it spawns embody CLI
    # sessions that need one), so this is a clean first-class path, not a fallback.
    param([Parameter(Mandatory)][string]$Launcher)
    $taskArgs = "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Launcher`""
    try {
        Start-Process -FilePath 'conhost.exe' -ArgumentList $taskArgs -WindowStyle Hidden | Out-Null
    } catch {
        Write-Warn "Could not start supervisor process: $($_.Exception.Message)"
    }
    try {
        $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
        New-ItemProperty -Path $runKey -Name $SupervisorTaskName -Value "conhost.exe $taskArgs" `
            -PropertyType String -Force | Out-Null
        Write-Ok "Embody supervisor installed as an interactive logon service (HKCU Run '$SupervisorTaskName'; no elevation)"
    } catch {
        Write-Warn "Could not register supervisor logon auto-start (HKCU Run): $($_.Exception.Message)"
    }
}

function Install-SupervisorTask {
    # Install only where the full coordinator lives (a client-only host has no
    # local coordinator for the supervisor to talk to). -NoSupervisor opts a full
    # host out; -NoService (client-only) skips it too. Remove a stale task in
    # either case so a host that became client-only stops supervising.
    if ($NoSupervisor -or $NoService) {
        switch (Remove-SupervisorTask) {
            'removed' { Write-Ok   'Removed embody supervisor task (client-only / -NoSupervisor)' }
            'blocked' { Write-Skip 'Supervisor task present but not removable without elevation -- run elevated to remove it' }
            default   { Write-Skip 'Embody supervisor skipped (client-only / -NoSupervisor)' }
        }
        if (Remove-SupervisorAutostart) { Write-Ok 'Removed supervisor logon auto-start (HKCU Run)' }
        return
    }
    if ($env:OS -ne 'Windows_NT') { return }
    if (-not (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)) {
        Write-Skip 'ScheduledTasks module unavailable -- run "agent-dispatch supervise --all-repos --label <L>" manually'
        return
    }

    $envFile = Join-Path $InstallDir 'supervisor.env'
    if (-not (Test-Path $envFile)) {
        $envDefault = @"
# agent-dispatch embody supervisor environment.
# Edit, then: Start-ScheduledTask -TaskName agent-dispatch-supervisor
#
# SAFETY: the supervisor turns queued tasks into AUTONOMOUS embody sessions. It
# runs with --all-repos, so it is GATED by an explicit label opt-in: only queued
# tasks carrying one of these labels are embodied. With NO labels set, the task
# is left DISABLED -- a label-less supervisor would embody EVERY queued task
# (handoffs, interactive worktree-pinned tasks, ...), which is unsafe.
#
# Opt-in labels, comma- or space-separated (REQUIRED to enable the task):
AGENT_DISPATCH_SUPERVISE_LABELS=
# Poll interval, seconds (default 30):
AGENT_DISPATCH_SUPERVISE_INTERVAL=30
# Max concurrent in-flight embodies (default 1 = max-one-active):
AGENT_DISPATCH_SUPERVISE_MAX_CONCURRENT=1
# Max failed spawn attempts before a task is dead-lettered (default 3; 0=disable):
AGENT_DISPATCH_SUPERVISE_MAX_ATTEMPTS=3
# Extra raw flags appended to the invocation (advanced; e.g. fleet mode:
#   --pool host-a,host-b --origin lambda-core):
AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS=
"@
        [System.IO.File]::WriteAllText($envFile, $envDefault, $utf8NoBom)
        Write-Ok "Supervisor env: $envFile (no labels -> task stays inert; add a label to enable)"
    } else {
        Write-Skip "Supervisor env already exists: $envFile"
    }

    # Launcher: loads supervisor.env, builds the supervise argv (labels -> repeated
    # --label flags), and hard-refuses a label-less run (defense-in-depth: the
    # registration below leaves it disabled without labels, but a hand-enable must
    # not embody everything). supervise logs to STDERR, so -- as with the
    # coordinator launcher -- drop to 'Continue' for the invocation so native
    # stderr is captured, not a terminating NativeCommandError.
    $launcher = Join-Path $InstallDir 'supervise-service.ps1'
    $launcherBody = @"
# agent-dispatch embody supervisor launcher (generated by install.ps1; #2869).
# Do not edit; edit supervisor.env instead.
`$ErrorActionPreference = 'Stop'
`$env:PYTHONUTF8 = '1'
`$envFile = Join-Path `$PSScriptRoot 'supervisor.env'
`$labels = ''
`$interval = '30'
`$maxConcurrent = '1'
`$maxAttempts = '3'
`$extra = ''
if (Test-Path `$envFile) {
    foreach (`$line in Get-Content `$envFile) {
        `$t = `$line.Trim()
        if (`$t -eq '' -or `$t.StartsWith('#')) { continue }
        `$kv = `$t -split '=', 2
        if (`$kv.Count -ne 2) { continue }
        `$k = `$kv[0].Trim(); `$v = `$kv[1].Trim()
        switch (`$k) {
            'AGENT_DISPATCH_SUPERVISE_LABELS'         { `$labels = `$v }
            'AGENT_DISPATCH_SUPERVISE_INTERVAL'       { if (`$v) { `$interval = `$v } }
            'AGENT_DISPATCH_SUPERVISE_MAX_CONCURRENT' { if (`$v) { `$maxConcurrent = `$v } }
            'AGENT_DISPATCH_SUPERVISE_MAX_ATTEMPTS'   { if (`$v) { `$maxAttempts = `$v } }
            'AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS'     { `$extra = `$v }
        }
    }
}
`$argsList = @('supervise', '--all-repos', '--interval', `$interval,
    '--max-concurrent', `$maxConcurrent, '--max-attempts', `$maxAttempts)
`$haveLabel = `$false
foreach (`$l in (`$labels -split '[\s,]+')) {
    if (`$l) { `$argsList += @('--label', `$l); `$haveLabel = `$true }
}
if (-not `$haveLabel) {
    Write-Error 'agent-dispatch-supervisor: refusing to run with no opt-in label. A label-less supervisor would embody EVERY queued task. Set AGENT_DISPATCH_SUPERVISE_LABELS in supervisor.env.'
    exit 78  # EX_CONFIG
}
if (`$extra) { `$argsList += (`$extra -split '\s+') }
`$logFile = Join-Path `$PSScriptRoot 'supervise-service.log'
try {
    if ((Test-Path `$logFile) -and ((Get-Item `$logFile).Length -gt 1MB)) {
        Move-Item -Force `$logFile "`$logFile.1"
    }
} catch { }
"[`$(Get-Date -Format o)] agent-dispatch supervisor launch (labels=`$labels interval=`$interval)" |
    Out-File -FilePath `$logFile -Append -Encoding utf8
`$_venv = '$($LinkDir -replace "'","''")'
`$_py = '$($LinkPython -replace "'","''")'
# Resolve the .venv junction's target and launch the slot python DIRECTLY (never
# traverse the junction; reading its target is allowed) -- RedirectionGuard #637.
try { `$_t = (Get-Item -LiteralPath `$_venv -Force -ErrorAction Stop).Target; if (`$_t) { `$_py = Join-Path (@(`$_t)[0]) 'Scripts\python.exe' } } catch {}
`$ErrorActionPreference = 'Continue'
& `$_py -m agent_dispatch @argsList 2>&1 | Out-File -FilePath `$logFile -Append -Encoding utf8
"@
    [System.IO.File]::WriteAllText($launcher, $launcherBody, $utf8NoBom)

    # Interactive-required host: use the non-elevated logon auto-start (HKCU Run)
    # instead of a Scheduled Task -- registration is admin-gated here, and an
    # interactive station is the right fit for the supervisor anyway. Only when a
    # label opt-in is configured (else stay inert, like the disabled-task case).
    if ((Get-ServiceMode) -eq 'interactive') {
        switch (Remove-SupervisorTask) {
            'removed' { Write-Step 'Removed prior supervisor Scheduled Task (interactive mode)' }
            default   { }
        }
        if (Test-SupervisorLabelsConfigured) {
            Install-SupervisorLogonAutostart -Launcher $launcher
        } else {
            if (Remove-SupervisorAutostart) { Write-Step 'Removed supervisor logon auto-start (no opt-in label)' }
            Write-Ok "Embody supervisor INERT (no opt-in label). Set AGENT_DISPATCH_SUPERVISE_LABELS in $envFile + re-run update to enable."
        }
        return
    }

    $action = New-ScheduledTaskAction -Execute 'conhost.exe' `
        -Argument "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`""
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
    $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $regOk = $false
    try {
        Register-ScheduledTask -TaskName $SupervisorTaskName -Action $action -Trigger $trigger `
            -Settings $settings -Principal $principal -Force `
            -Description 'agent-dispatch -- embody spawn supervisor (labeled queued tasks -> host embody autopilots)' | Out-Null
        $regOk = $?
    } catch {
        $regOk = $false
    }
    if (-not $regOk) {
        $ErrorActionPreference = $prevEAP
        Write-Warn "Embody supervisor not registered (needs elevation) -- coordinator is installed; run elevated to add it"
        return
    }

    if (Test-SupervisorLabelsConfigured) {
        Enable-ScheduledTask -TaskName $SupervisorTaskName -ErrorAction SilentlyContinue | Out-Null
        Start-ScheduledTask -TaskName $SupervisorTaskName -ErrorAction SilentlyContinue
        $ErrorActionPreference = $prevEAP
        Write-Ok "Embody supervisor installed + started (Scheduled Task '$SupervisorTaskName')"
    } else {
        # No opt-in label -> leave the task registered but DISABLED (inert), the
        # Windows analogue of an installed-but-not-enabled systemd unit.
        Disable-ScheduledTask -TaskName $SupervisorTaskName -ErrorAction SilentlyContinue | Out-Null
        $ErrorActionPreference = $prevEAP
        Write-Ok "Embody supervisor installed (INERT: no opt-in label; task disabled). To enable: set"
        Write-Step "AGENT_DISPATCH_SUPERVISE_LABELS in $envFile, then re-run update"
        Write-Step "(or: Enable-ScheduledTask -TaskName $SupervisorTaskName; Start-ScheduledTask -TaskName $SupervisorTaskName)"
    }
}

# -- Port reservation (Windows) ---------------------------------------------

function Test-Elevated {
    # True when the current process holds the Administrators role. Windows-only.
    if ($env:OS -ne 'Windows_NT') { return $false }
    try {
        $id = [System.Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = New-Object System.Security.Principal.WindowsPrincipal($id)
        return $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch {
        return $false
    }
}

# -- Coordinator port reservation: RETIRED (durable-service-transport Stage D) --
# The netsh 9847 excludedportrange reservation (Test-PortExcluded / Add-PortReservation)
# existed only to stop the Windows dynamic-port allocator from stealing the FIXED
# coordinator port (#2818). Stage C flipped the coordinator to an OS-assigned dynamic
# port advertised via the rendezvous file, so there is no fixed port to protect and the
# reservation is retired. Any leftover live 9847 exclusion is harmless (it only keeps
# 9847 out of the ephemeral pool) and can be cleared elevated:
#   netsh int ipv4 delete excludedportrange protocol=tcp startport=9847 numberofports=1

# -- Coordinator firewall (Windows, NAT mode only) --------------------------

function Add-CoordinatorFirewallRule {
    # In NAT mode the coordinator binds the vEthernet(WSL) IP, so inbound WSL
    # traffic arrives on the vEthernet(WSL) interface. Add an inbound allow rule
    # SCOPED to that interface (never profile-wide, never the LAN) so a WSL client
    # can reach the coordinator while the LAN stays isolated. Mirrored mode needs
    # no rule (shared loopback). Idempotent; needs elevation -- degrades to a
    # logged SKIP with the one-time command when not admin.
    if ($env:OS -ne 'Windows_NT') { return }
    if (-not (Test-Path $VenvPython)) { return }

    # Determine the WSL networking mode from the single source of truth (the
    # Python detector). Only NAT needs a firewall rule.
    $mode = ''
    try {
        $mode = (& $VenvPython -c "from agent_dispatch.netinfo import get_wsl_networking_mode; print(get_wsl_networking_mode())" 2>$null).Trim()
    } catch { $mode = '' }
    if ($mode -ne 'nat') {
        Write-Skip "Coordinator firewall rule not needed (WSL networking mode: $(if ($mode) { $mode } else { 'unknown' }); rule is NAT-only)"
        return
    }

    $ruleName = 'agent-dispatch coordinator (WSL)'

    if (-not (Get-Command New-NetFirewallRule -ErrorAction SilentlyContinue)) {
        Write-Skip 'NetSecurity module unavailable -- cannot add coordinator firewall rule'
        return
    }

    # Resolve the vEthernet(WSL) interface alias (exact, else the (WSL*) match).
    $alias = $null
    try {
        $ipObj = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.InterfaceAlias -like 'vEthernet (WSL*' } |
            Select-Object -First 1
        if ($ipObj) { $alias = $ipObj.InterfaceAlias }
    } catch { $alias = $null }
    if (-not $alias) {
        Write-Skip 'Coordinator firewall rule skipped -- no vEthernet(WSL) interface found (WSL networking not up?)'
        return
    }

    # Stage C/D: the coordinator binds a DYNAMIC (OS-assigned) port, so the rule is
    # PROGRAM-scoped (the venv python), not port-scoped -- a fixed -LocalPort would
    # miss the dynamic port (#3499). Interface + program scoped keeps it WSL-only.
    $existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    if ($existing) {
        $appFilter = $existing | Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue
        if ($appFilter -and $appFilter.Program -and ($appFilter.Program -ieq $VenvPython)) {
            Write-Skip "Coordinator firewall rule already present + program-scoped ('$ruleName')"
            return
        }
        # Legacy port-pinned rule -- migrate it to program-scoped (#3499).
        if (-not (Test-Elevated)) {
            Write-Skip "Coordinator firewall rule needs migration to program-scoped (#3499) -- needs elevation"
            return
        }
        $existing | Remove-NetFirewallRule -ErrorAction SilentlyContinue
        Write-Step "Removed legacy port-pinned coordinator firewall rule (migrating to program-scoped, #3499)"
    }
    if (-not (Test-Elevated)) {
        Write-Skip "Coordinator firewall rule not added -- needs elevation (run once, elevated: New-NetFirewallRule -DisplayName '$ruleName' -Direction Inbound -Action Allow -Program '$VenvPython' -InterfaceAlias '$alias')"
        return
    }
    try {
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow `
            -Program $VenvPython -InterfaceAlias $alias -Profile Any `
            -Description 'agent-dispatch coordinator -- WSL-only, interface + program scoped (issues #2818, #3499)' `
            -ErrorAction Stop | Out-Null
        Write-Ok "Coordinator firewall rule added ('$ruleName' on '$alias', program-scoped, WSL-only)"
    } catch {
        Write-Warn "Could not add coordinator firewall rule: $_"
    }
}

# -- Actions ----------------------------------------------------------------

function Invoke-Install {
    Write-Host ''; Write-Host '=== agent-dispatch install ===' -ForegroundColor Cyan; Write-Host ''
    Install-Runtime
    Install-CoordinatorTask
    if (-not $NoService) { Add-CoordinatorFirewallRule }
    Install-SupervisorTask
    if (-not $NoService) { Confirm-CoordinatorRunning }
    Write-Host ''; Write-Host '=== agent-dispatch install complete ===' -ForegroundColor Cyan
}

function Invoke-Update {
    Write-Host ''; Write-Host '=== agent-dispatch update ===' -ForegroundColor Cyan; Write-Host ''
    Invoke-DowngradeGuard
    Install-Runtime
    # Cycle the running services so the freshly-rebuilt venv build actually takes
    # over: `conhost --headless` detaches the coordinator/supervisor from the
    # Scheduled Task, so re-registering + Start-ScheduledTask alone leaves the OLD
    # build serving (MultipleInstances=IgnoreNew no-ops against the survivor, and
    # the non-elevated fallback sees a healthy old build and declines) -- the exact
    # version-drift symptom of #3602. Terminate the old process(es) first; the
    # (re)install below then starts a clean one.
    if (-not $NoService) {
        $stoppedCoord = Stop-DispatchProcess -Subcommand serve
        if ($stoppedCoord -gt 0) {
            Write-Step "Stopped $stoppedCoord stale coordinator process(es) before restart"
        }
    }
    $stoppedSup = Stop-DispatchProcess -Subcommand supervise
    if ($stoppedSup -gt 0) {
        Write-Step "Stopped $stoppedSup stale supervisor process(es) before restart"
    }
    Install-CoordinatorTask
    if (-not $NoService) { Add-CoordinatorFirewallRule }
    Install-SupervisorTask
    if (-not $NoService) { Confirm-CoordinatorRunning }
    Write-Host ''; Write-Host '=== agent-dispatch update complete ===' -ForegroundColor Cyan
}

function Invoke-Start {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Start-ScheduledTask -TaskName $TaskName
        Write-Ok 'Coordinator started'
    } else {
        # Non-elevated host: no Scheduled Task was registrable, so the coordinator
        # runs from the detached fallback launcher. Start that rather than hard-
        # failing (the client is installed; #3602).
        $launcher = Join-Path $InstallDir 'serve-service.ps1'
        if (Test-Path $launcher) {
            Start-CoordinatorNonElevatedFallback -Launcher $launcher -Primary:((Get-ServiceMode) -eq 'interactive')
        } else {
            Write-Fail "No coordinator task or launcher installed -- run: install.ps1 -Action install"; exit 1
        }
    }
    # Start the supervisor too, but only if it is enabled (label-gated). A
    # disabled/inert supervisor is left alone.
    $sup = Get-ScheduledTask -TaskName $SupervisorTaskName -ErrorAction SilentlyContinue
    if ($sup -and $sup.State -ne 'Disabled') {
        Start-ScheduledTask -TaskName $SupervisorTaskName -ErrorAction SilentlyContinue
        Write-Ok 'Embody supervisor started'
    }
    Confirm-CoordinatorRunning
}

function Invoke-Stop {
    # Supervisor first (it spawns work), then the coordinator. Stop the Scheduled
    # Task AND terminate the detached process -- Stop-ScheduledTask alone leaves the
    # `conhost --headless`-detached python alive (#3602).
    if (Get-ScheduledTask -TaskName $SupervisorTaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $SupervisorTaskName -ErrorAction SilentlyContinue
    }
    $killedSup = Stop-DispatchProcess -Subcommand supervise
    if ($killedSup -gt 0) { Write-Ok "Embody supervisor stopped ($killedSup process(es))" }

    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    }
    $killedCoord = Stop-DispatchProcess -Subcommand serve
    if ($killedCoord -gt 0) {
        Write-Ok "Coordinator stopped ($killedCoord process(es) terminated; rendezvous cleared)"
    } else {
        Write-Skip 'No running coordinator process found'
    }
}

function Invoke-Status {
    Write-Host ''; Write-Host '=== agent-dispatch status ===' -ForegroundColor Cyan
    $manifestPath = Join-Path $InstallDir 'deploy-manifest.json'
    if (Test-Path $manifestPath) {
        try {
            $m = Get-Content $manifestPath -Raw | ConvertFrom-Json
            Write-Ok "Deployed: $($m.source.version) (source: $($m.source.kind))"
        } catch { Write-Skip 'Deploy manifest unreadable' }
    } else {
        Write-Skip 'No deploy manifest -- not installed?'
    }
    Write-Ok "Service mode: $(Get-ServiceMode)"
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Write-Ok "Coordinator task: $($task.State)"
    } else {
        $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
        $auto = Get-ItemProperty -Path $runKey -Name $TaskName -ErrorAction SilentlyContinue
        if ($auto) {
            Write-Ok 'Coordinator: interactive logon auto-start (HKCU Run; no boot task)'
        } else {
            Write-Skip 'No coordinator task (client-only host)'
        }
    }
    $sup = Get-ScheduledTask -TaskName $SupervisorTaskName -ErrorAction SilentlyContinue
    if ($sup) {
        if (Test-SupervisorLabelsConfigured) {
            Write-Ok "Embody supervisor task: $($sup.State)"
        } else {
            Write-Ok "Embody supervisor task: $($sup.State) (INERT: no opt-in label set)"
        }
    } else {
        $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
        $supAuto = Get-ItemProperty -Path $runKey -Name $SupervisorTaskName -ErrorAction SilentlyContinue
        if ($supAuto) {
            Write-Ok 'Embody supervisor: interactive logon auto-start (HKCU Run)'
        } else {
            Write-Skip 'No embody supervisor task (client-only host, -NoSupervisor, inert, or unavailable)'
        }
    }
}

function Invoke-Uninstall {
    Write-Host ''; Write-Host '=== agent-dispatch uninstall ===' -ForegroundColor Cyan; Write-Host ''
    if (Get-ScheduledTask -TaskName $SupervisorTaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $SupervisorTaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $SupervisorTaskName -Confirm:$false -ErrorAction SilentlyContinue
        Write-Ok 'Embody supervisor task removed'
    }
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Write-Ok 'Coordinator task removed'
    }
    if (Remove-CoordinatorAutostart) { Write-Ok 'Coordinator logon auto-start (HKCU Run) removed' }
    if (Remove-SupervisorAutostart) { Write-Ok 'Embody supervisor logon auto-start (HKCU Run) removed' }
    if (Get-Command Get-NetFirewallRule -ErrorAction SilentlyContinue) {
        $fwRule = 'agent-dispatch coordinator (WSL)'
        if (Get-NetFirewallRule -DisplayName $fwRule -ErrorAction SilentlyContinue) {
            Remove-NetFirewallRule -DisplayName $fwRule -ErrorAction SilentlyContinue
            Write-Ok 'Coordinator firewall rule removed'
        }
    }
    foreach ($n in @('agent-dispatch.cmd', 'agent-dispatch.ps1', 'agent-dispatch')) {
        $p = Join-Path $LocalBin $n
        if (Test-Path $p) { Remove-Item $p -Force -ErrorAction SilentlyContinue }
    }
    Write-Ok 'Binstub removed'
    $pivot = Join-Path $env:USERPROFILE '.agent-worktrees\pivots\agent-dispatch.json'
    if (Test-Path $pivot) { Remove-Item $pivot -Force -ErrorAction SilentlyContinue }
    if ($Purge) {
        if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir -ErrorAction SilentlyContinue }
        Write-Ok "Runtime purged: $InstallDir (config + DB deleted)"
    } else {
        # Remove the runtime venv. Versioned: the `.venv` link + the versions/
        # tree; otherwise the single real venv dir.
        if ($VersionedRuntime) {
            if (Test-VenvIsLink $LinkDir) { & cmd /c rmdir "$LinkDir" 2>$null }
            elseif (Test-Path $LinkDir) { Remove-Item -Recurse -Force $LinkDir -ErrorAction SilentlyContinue }
            $verRoot = Join-Path $InstallDir 'versions'
            if (Test-Path $verRoot) { Remove-Item -Recurse -Force $verRoot -ErrorAction SilentlyContinue }
        } elseif (Test-Path $VenvDir) {
            Remove-Item -Recurse -Force $VenvDir -ErrorAction SilentlyContinue
        }
        Write-Ok 'Venv removed (config + DB kept; -Purge to delete)'
    }
}

switch ($Action) {
    'install'   { Invoke-Install }
    'update'    { Invoke-Update }
    'start'     { Invoke-Start }
    'stop'      { Invoke-Stop }
    'status'    { Invoke-Status }
    'uninstall' { Invoke-Uninstall }
}
exit 0
