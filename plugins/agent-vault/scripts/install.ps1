<#
.SYNOPSIS
    agent-vault installer / lifecycle manager. PS5+ compatible.

.DESCRIPTION
    Canonical installer for the agent-vault runtime. Creates the runtime at
    ~/.agent-vault/ (.venv + state), deploys ~/.local/bin/agent-vault.ps1 and
    agent-vault.cmd binstubs, and registers a windowless Scheduled Task named
    AgentVault that runs the persistent daemon at logon unless -NoService is
    specified.

.PARAMETER Action
    install (default) | update | status | start | stop | uninstall.

.PARAMETER InstallDir
    Override the runtime install directory (default: ~/.agent-vault).

.PARAMETER NoService
    Install/update the client (venv + binstub) only; do NOT register/start the
    AgentVault Scheduled Task (client-only host).

.PARAMETER Purge
    On uninstall: also delete daemon state under the install directory.

.PARAMETER Force
    On update: bypass the downgrade guard (deliberate rollback). Env:
    AGENT_VAULT_ALLOW_DOWNGRADE=1.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'update', 'status', 'start', 'stop', 'uninstall')]
    [string]$Action = 'install',

    [Alias('install-dir')]
    [string]$InstallDir,

    [Alias('no-service')]
    [switch]$NoService,

    [switch]$Purge,
    [switch]$Force
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

if ($env:AGENT_VAULT_ALLOW_DOWNGRADE -eq '1') { $Force = $true }

function Write-Ok      { param([string]$Msg) Write-Host "  [OK]   $Msg" -ForegroundColor Green }
function Write-Skip    { param([string]$Msg) Write-Host "  [SKIP] $Msg" -ForegroundColor Cyan }
function Write-Fail    { param([string]$Msg) Write-Host "  [FAIL] $Msg" -ForegroundColor Red }
function Write-Warn    { param([string]$Msg) Write-Host "  [WARN] $Msg" -ForegroundColor Yellow }
function Write-Step    { param([string]$Msg) Write-Host "  ...    $Msg" -ForegroundColor DarkGray }

$PluginDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PkgSrcDir = Join-Path $PluginDir 'src\agent_vault'

if (-not $InstallDir) {
    $InstallDir = Join-Path $env:USERPROFILE '.agent-vault'
}
$VenvDir     = Join-Path $InstallDir '.venv'
$LocalBin    = Join-Path $env:USERPROFILE '.local\bin'
$VenvPython  = Join-Path $VenvDir 'Scripts\python.exe'
$BinstubPs1  = Join-Path $LocalBin 'agent-vault.ps1'
$BinstubCmd  = Join-Path $LocalBin 'agent-vault.cmd'
$Binstub     = $BinstubPs1
$TaskName    = 'AgentVault'
$utf8NoBom   = New-Object System.Text.UTF8Encoding $false

# === install-contract:v3 versioned-venv (agent-vault: .venv-as-junction) ===
# Immutable per-version runtime (#581). Build the venv into versions/<version>
# and make the historical `.venv` path a junction (Windows) / symlink (POSIX)
# into it, so the binstubs, scheduled task, and deploy-manifest -- all of which
# reference `.venv` -- resolve through the link unchanged. LinkDir/LinkPython is
# the stable `.venv` path (runtime-facing, never a versions/<v> absolute a `gc`
# could remove); VenvDir/VenvPython is redirected to the versions/<v> slot
# (build + health-gate). Legacy mode: Link == Venv (byte-for-byte old behavior).
# Gated behind AGENT_VAULT_VERSIONED=0 (default ON) until validated;
# COPILOT_EXT_NO_VERSIONED=1 force-disables. scripts/versioned_runtime.py owns
# the swap + legacy migration + gc.
$LinkDir          = $VenvDir
$LinkPython       = $VenvPython
$VersionedRuntime = $false
$SrcVersion       = $null
if (($env:COPILOT_EXT_NO_VERSIONED -ne '1') -and
    ($env:AGENT_VAULT_VERSIONED -notin @('0', 'false', 'no', 'off'))) {
    $pyprojForVer = Join-Path $PluginDir 'pyproject.toml'
    if (Test-Path $pyprojForVer) {
        $vl = Select-String -Path $pyprojForVer -Pattern '^\s*version\s*=' | Select-Object -First 1
        if ($vl) { $SrcVersion = ($vl.Line -replace '.*=\s*"([^"]+)".*', '$1') }
    }
    if ($SrcVersion) {
        $VersionedRuntime = $true
        $VenvDir = Join-Path (Join-Path $InstallDir 'versions') $SrcVersion
        $VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
    }
}
# === end install-contract:v3 versioned-venv ===

# === install-contract:v3 strip-trampolines -- keep byte-identical across plugins ===
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

# === install-contract:v3 versioned-venv helpers (agent-vault) ===
function Test-VenvIsLink {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $false }
    try { return [bool]((Get-Item $Path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) }
    catch { return $false }
}

function Invoke-VersionedActivate {
    <# Swap the stable `.venv` link to this version's freshly-built slot. No-op in
       legacy mode. First migration: the `.venv` path is still a REAL dir the
       running daemon may hold open -- Windows can't rename it aside while a
       loaded python.exe locks it, so stop the daemon first to release it (the
       task re-registers + restarts on the new slot). A later version-bump swaps
       only the link (the daemon runs from its own immutable slot), so no stop is
       needed and the in-memory unlock survives. #>
    if (-not $VersionedRuntime) { return $true }
    if ((Test-Path $LinkDir) -and -not (Test-VenvIsLink $LinkDir)) {
        Write-Step 'Releasing legacy .venv for versioned migration (stopping daemon)...'
        Invoke-Stop | Out-Null
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
    <# The version the `.venv` link currently points at (empty for a legacy real
       venv or a fresh box). Used as the gc keep + rollback target. #>
    if (-not $VersionedRuntime) { return '' }
    $vr = Join-Path $PSScriptRoot 'versioned_runtime.py'
    $py = if (Test-Path $LinkPython) { $LinkPython } elseif (Test-Path $VenvPython) { $VenvPython } else { $null }
    if (-not $py) { return '' }
    $out = & $py $vr --root $InstallDir --link-name '.venv' current 2>$null
    return ("$out").Trim()
}

function Invoke-VersionedGc {
    <# Prune old version slots, keeping current + the given previous-good (the
       slot a not-yet-restarted daemon may still run from) + any live-pid-pinned
       slot. Best-effort. No-op in legacy mode. #>
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

function Get-SignedBasePython {
    <# Return a SAC-trusted (Authenticode-signed) base Python (>=3.10), or $null.
       Smart App Control blocks the unsigned uv-managed Python and console-script
       trampoline; a venv built from a signed base with `--copies` has a signed
       python.exe that SAC allows. #>
    if ($env:OS -ne 'Windows_NT') { return $null }
    $cands = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in '3.13', '3.12', '3.11', '3.10') {
            $p = (& py "-$v" -c "import sys;print(sys.executable)" 2>$null | Out-String).Trim()
            if ($LASTEXITCODE -eq 0 -and $p) { $cands += $p }
        }
    }
    foreach ($c in ($cands | Select-Object -Unique)) {
        if (Test-Path $c) {
            try { if ((Get-AuthenticodeSignature $c).Status -eq 'Valid') { return $c } } catch {}
        }
    }
    return $null
}

function New-SignedVenv {
    <# Create or rebuild $VenvDir so its python.exe is SAC-trusted. Prefers a
       signed base Python via `--copies`; rebuilds an existing unsigned venv;
       falls back to uv (unsigned) when no signed Python exists. Returns $true
       if $VenvPython is present afterward. #>
    if ((Test-Path $VenvPython) -and ($env:OS -eq 'Windows_NT')) {
        $sig = try { (Get-AuthenticodeSignature $VenvPython).Status } catch { 'Unknown' }
        if ($sig -ne 'Valid' -and (Get-SignedBasePython)) {
            Write-Step 'Existing venv python is unsigned (Smart App Control-incompatible) -- rebuilding from signed Python'
            try { Remove-Item -Recurse -Force $VenvDir -ErrorAction Stop }
            catch { Write-Warn "Could not remove existing venv (in use?): $_" }
        }
    }
    if (Test-Path $VenvPython) { return $true }

    $signedBase = Get-SignedBasePython
    if ($signedBase) {
        & $signedBase -m venv --copies $VenvDir 2>&1 | Out-Null
        if (Test-Path $VenvPython) {
            Write-Ok "Venv created from signed Python ($signedBase)"
            return $true
        }
        Write-Warn 'Signed-Python venv creation failed -- falling back to uv'
    } elseif ($env:OS -eq 'Windows_NT') {
        Write-Warn 'No signed system Python found -- using uv (unsigned). On Smart App Control machines, install python.org Python 3.10+ and re-run.'
    }
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & uv venv $VenvDir --python 3.10 --allow-existing 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { & uv venv $VenvDir --allow-existing 2>&1 | Out-Null }
    $ErrorActionPreference = $prevEAP
    return (Test-Path $VenvPython)
}

# === install-contract:v3 source-kind -- keep byte-identical across plugins ===
# A runtime footprint's source is inferred from where the installer runs.
# Vendored under the Copilot CLI installed-plugins dir => marketplace;
# anything else (a git checkout) => local. `update` re-installs from whatever
# the recorded footprint is, because the same installer is invoked from the
# same place.
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
        $dirtyOut = git -C $Path status --porcelain 2>$null
        if ($dirtyOut) { $dirty = $true }
        return @{
            commit = $(if ($commit) { $commit } else { 'unknown' })
            branch = $(if ($branch) { $branch } else { 'unknown' })
            dirty  = $dirty
        }
    } catch {
        return @{ commit = 'unknown'; branch = 'unknown'; dirty = $false }
    }
}

function Get-InstalledVersion {
    if (-not (Test-Path $LinkPython)) { return $null }
    try {
        $v = & $LinkPython -c 'from importlib.metadata import version; print(version("agent-vault"))' 2>$null
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
            Write-Warn "Downgrade $installed -> $source forced (-Force / AGENT_VAULT_ALLOW_DOWNGRADE)"
            return
        }
        Write-Host ''
        Write-Fail "Refusing to downgrade agent-vault: installed $installed > source $source"
        Write-Fail 'Override intentionally (deliberate rollback):'
        Write-Fail "    install.ps1 -Action $Action -Force"
        Write-Host ''
        exit 1
    }
}

function Resolve-PythonCommand {
    foreach ($candidate in @('python', 'python3', 'py')) {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($found) {
            $prevEAP = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            try {
                $testOut = & $found.Source --version 2>&1
                if ($LASTEXITCODE -eq 0 -and $testOut -match 'Python') { return $found.Source }
            } catch { }
            $ErrorActionPreference = $prevEAP
        }
    }
    return $null
}

function Test-KeePassXCCli {
    return ($null -ne (Get-Command keepassxc-cli -ErrorAction SilentlyContinue))
}

function Write-Binstubs {
    param([Parameter(Mandatory)][string]$PythonExe)

    # Resolve the .venv link's reparse target and launch the slot python DIRECTLY,
    # never *traversing* the junction (a RedirectionGuard-enforcing process is
    # blocked from that but may still *read* the target) -- dotfiles #637. The .ps1
    # reads (Get-Item .venv).Target; the .cmd parses `dir /a:l`. Plain-dir falls back.
    $stubVenv = Split-Path (Split-Path $PythonExe)
    $stubRoot = Split-Path $stubVenv

    $ps1 = @(
        "`$env:PYTHONUTF8 = '1'",
        "`$_venv = '$stubVenv'",
        "`$_py = Join-Path `$_venv 'Scripts\python.exe'",
        "try { `$_t = (Get-Item -LiteralPath `$_venv -Force -ErrorAction Stop).Target; if (`$_t) { `$_py = Join-Path (@(`$_t)[0]) 'Scripts\python.exe' } } catch {}",
        "& `$_py -m agent_vault @args",
        "exit `$LASTEXITCODE"
    ) -join "`r`n"
    [System.IO.File]::WriteAllText($BinstubPs1, $ps1, $utf8NoBom)

    $cmd = @(
        "@echo off",
        "set `"PYTHONUTF8=1`"",
        "set `"_PY=$stubVenv\Scripts\python.exe`"",
        "for /f `"tokens=2 delims=[]`" %%i in ('dir /a:l `"$stubRoot`" 2^>nul ^| findstr /i /c:`".venv`"') do set `"_PY=%%i\Scripts\python.exe`"",
        "`"%_PY%`" -m agent_vault %*"
    ) -join "`r`n"
    [System.IO.File]::WriteAllText($BinstubCmd, $cmd, $utf8NoBom)

    Write-Ok "Binstub: $BinstubPs1 (+ .cmd fallback)"
}

function Write-Manifest {
    $manifestPath = Join-Path $InstallDir 'deploy-manifest.json'
    $kind = Get-SourceKind -PluginPath $PluginDir
    $ver = '0.0.0'
    $pyproj = Join-Path $PluginDir 'pyproject.toml'
    if (Test-Path $pyproj) {
        $verLine = Select-String -Path $pyproj -Pattern '^\s*version\s*=' | Select-Object -First 1
        if ($verLine) { $ver = ($verLine.Line -replace '.*=\s*"([^"]+)".*','$1') }
    }
    if ($ver -eq '0.0.0') {
        $sourceVersion = Get-SourceVersion
        if ($sourceVersion) { $ver = $sourceVersion }
    }
    $commit = $null; $branch = $null; $dirty = $false
    if ($kind -eq 'local') {
        $repoRoot = Split-Path -Parent (Split-Path -Parent $PluginDir)
        $git = Get-GitInfo -Path $repoRoot
        $commit = $git.commit; $branch = $git.branch; $dirty = $git.dirty
    }
    $manifest = [ordered]@{
        schema_version = 3
        service        = 'agent-vault'
        deployed_at    = (Get-Date -Format 'o')
        deployed_by    = "$($env:COMPUTERNAME.ToLower())-windows"
        source         = [ordered]@{
            kind    = $kind
            path    = ($PluginDir -replace '\\', '/')
            repo    = 'copilot-extensions'
            plugin  = 'agent-vault'
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

function Install-Runtime {
    if (-not (Test-Path $PkgSrcDir)) {
        Write-Fail "Package source not found at $PkgSrcDir"
        exit 1
    }

    $pythonCmd = Resolve-PythonCommand
    if (-not $pythonCmd) {
        Write-Fail 'Python not found on PATH (need 3.10+)'
        exit 1
    }
    Write-Ok "Python: $pythonCmd"

    foreach ($dir in @($InstallDir, $LocalBin)) {
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    }
    Write-Ok "Directories: $InstallDir"

    if (-not (New-SignedVenv)) {
        Write-Step 'Creating venv via python -m venv...'
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & $pythonCmd -m venv $VenvDir 2>&1 | Out-Null
        $ErrorActionPreference = $prevEAP
    }
    if (-not (Test-Path $VenvPython)) {
        Write-Fail "Venv creation failed -- $VenvPython not found"
        exit 1
    }
    Write-Ok 'Venv ready'

    Write-Step 'Installing agent-vault package...'
    Remove-ConsoleTrampolines -VenvDir $VenvDir
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        $pkgOut = & uv pip install --python $VenvPython "$PluginDir" --quiet 2>&1
    } else {
        $pkgOut = & $VenvPython -m pip install --quiet "$PluginDir" 2>&1
    }
    $pkgResult = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP
    if ($pkgResult -ne 0) {
        Write-Fail "Package install failed (exit $pkgResult)"
        if ($pkgOut) { Write-Host ($pkgOut | Out-String) }
        exit 1
    }
    Remove-ConsoleTrampolines -VenvDir $VenvDir
    Write-Ok 'Package installed: agent-vault'

    # Versioned layout (#581): health-gate the freshly-built slot in isolation,
    # then swap the stable `.venv` link onto it. Everything below resolves through
    # `.venv` (the link). No-op in legacy mode. Remember the previously-active
    # version as the gc keep target (a not-yet-restarted daemon may still run it).
    $prevVersion = ''
    if ($VersionedRuntime) {
        $prevVersion = Get-VersionedCurrent
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & $VenvPython -c 'import agent_vault' 2>$null
        $slotOk = ($LASTEXITCODE -eq 0)
        $ErrorActionPreference = $prevEAP
        if (-not $slotOk) {
            Write-Fail "Fresh runtime slot failed its health gate (versions/$SrcVersion) -- not activating"
            exit 1
        }
        if (-not (Invoke-VersionedActivate)) { exit 1 }
    }

    # Binstub + manifest resolve through the stable `.venv` link ($LinkPython /
    # $LinkDir), never a versions/<v> absolute a later `gc` could remove.
    Write-Binstubs -PythonExe $LinkPython
    Write-Manifest

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $LinkPython -c 'import agent_vault' 2>$null
    $importOk = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prevEAP
    if ($importOk) { Write-Ok 'Verification: module imports successfully' }
    else { Write-Fail 'Verification: module import failed'; exit 1 }

    # Versioned layout: prune old slots, keeping current + the previous-good (the
    # slot a not-yet-restarted daemon may still run from) + live-pid-pinned.
    if ($VersionedRuntime) { Invoke-VersionedGc -KeepPrev $prevVersion }

    if (Test-KeePassXCCli) {
        Write-Ok 'Prerequisite: keepassxc-cli found'
    } else {
        Write-Warn 'Prerequisite missing: keepassxc-cli (KeePassXC). agent-vault installed, but unlocks will fail until KeePassXC is present.'
    }

    $currentUserPath = [System.Environment]::GetEnvironmentVariable('PATH', 'User')
    if (-not ($currentUserPath -split ';' | Where-Object { $_ -eq $LocalBin })) {
        [System.Environment]::SetEnvironmentVariable('PATH', "$LocalBin;$currentUserPath", 'User')
        $env:PATH = "$LocalBin;$env:PATH"
        Write-Ok "PATH: Added $LocalBin to User PATH"
    }
}

function Register-AgentVaultTask {
    if ($NoService) {
        Write-Skip 'agent-vault service skipped (-NoService): this host is a client only'
        return
    }
    if (-not (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)) {
        Write-Skip 'ScheduledTasks module unavailable -- skipping service'
        return
    }
    if (-not (Test-Path $LinkPython)) {
        Write-Warn 'agent-vault venv not found -- skipping scheduled task'
        return
    }

    # Launch the daemon through the RESOLVED versions/<v> slot python, not the
    # `.venv` junction: a RedirectionGuard-enforcing task context would be blocked
    # from *traversing* the junction (dotfiles #637), and conhost execs the target
    # directly with no launcher script to resolve it at runtime -- so resolve the
    # junction's target here (reading it is allowed) and bake the slot path. The
    # task is re-registered every install/update and `gc` keeps the current slot,
    # so this tracks the active version. Plain-dir `.venv` keeps $LinkPython.
    $taskPy = $LinkPython
    try { $_t = (Get-Item -LiteralPath $LinkDir -Force -ErrorAction Stop).Target; if ($_t) { $taskPy = Join-Path (@($_t)[0]) 'Scripts\python.exe' } } catch {}
    $action = New-ScheduledTaskAction `
        -Execute 'conhost.exe' `
        -Argument "--headless `"$taskPy`" -m agent_vault.service --foreground --persistent" `
        -WorkingDirectory $InstallDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $trigger.Delay = 'PT15S'
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -MultipleInstances IgnoreNew `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1)

    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Set-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings | Out-Null
        Write-Ok "Scheduled task updated ($TaskName, at logon, 15s delay)"
    } else {
        Register-ScheduledTask -TaskName $TaskName `
            -Action $action -Trigger $trigger -Settings $settings `
            -Description 'agent-vault -- local KeePassXC-backed secret store.' | Out-Null
        Write-Ok "Scheduled task registered ($TaskName, at logon, 15s delay)"
    }

    Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}

function Invoke-Install {
    Write-Host ''; Write-Host '=== agent-vault install ===' -ForegroundColor Cyan; Write-Host ''
    Install-Runtime
    Register-AgentVaultTask
    Write-Host ''; Write-Host '=== agent-vault install complete ===' -ForegroundColor Cyan
}

function Invoke-Update {
    Write-Host ''; Write-Host '=== agent-vault update ===' -ForegroundColor Cyan; Write-Host ''
    Invoke-DowngradeGuard
    Install-Runtime
    Register-AgentVaultTask
    Write-Host ''; Write-Host '=== agent-vault update complete ===' -ForegroundColor Cyan
}

function Invoke-Start {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Fail "No AgentVault task installed -- run: install.ps1 -Action install"
        exit 1
    }
    Start-ScheduledTask -TaskName $TaskName
    Write-Ok 'agent-vault service started'
}

function Invoke-Stop {
    if (Test-Path $LinkPython) {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & $LinkPython -m agent_vault.service --stop 2>$null | Out-Null
        $ErrorActionPreference = $prevEAP
    }
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Write-Ok 'agent-vault service stopped'
    } else {
        Write-Skip 'AgentVault task not installed'
    }
}

function Invoke-Status {
    Write-Host ''; Write-Host '=== agent-vault status ===' -ForegroundColor Cyan
    $manifestPath = Join-Path $InstallDir 'deploy-manifest.json'
    if (Test-Path $manifestPath) {
        try {
            $m = Get-Content $manifestPath -Raw | ConvertFrom-Json
            Write-Ok "Deployed: $($m.source.version) (source: $($m.source.kind))"
        } catch { Write-Skip 'Deploy manifest unreadable' }
    } else {
        Write-Skip 'No deploy manifest -- not installed?'
    }
    if (Test-Path $BinstubPs1) { Write-Ok "Binstub: $BinstubPs1 (+ .cmd fallback)" }
    elseif (Test-Path $BinstubCmd) { Write-Warn "Only fallback binstub exists: $BinstubCmd" }
    else { Write-Skip "No binstub at $Binstub" }

    if (Test-KeePassXCCli) { Write-Ok 'Prerequisite: keepassxc-cli found' }
    else { Write-Warn 'Prerequisite missing: keepassxc-cli (KeePassXC)' }

    if (Test-Path $LinkPython) {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $ping = & $LinkPython -m agent_vault.service --ping 2>$null
        $pingCode = $LASTEXITCODE
        $ErrorActionPreference = $prevEAP
        if ($pingCode -eq 0 -and $ping) { Write-Ok ($ping | Out-String).Trim() }
        else { Write-Skip 'Daemon not responding to ping' }
    } else {
        Write-Skip 'Venv not installed'
    }

    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) { Write-Ok "Scheduled task: $($task.State)" }
    else { Write-Skip 'No AgentVault scheduled task (client-only host)' }
}

function Invoke-Uninstall {
    Write-Host ''; Write-Host '=== agent-vault uninstall ===' -ForegroundColor Cyan; Write-Host ''
    Invoke-Stop
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Write-Ok 'Scheduled task removed'
    }
    foreach ($stub in @($BinstubPs1, $BinstubCmd)) {
        if (Test-Path $stub) {
            Remove-Item $stub -Force -ErrorAction SilentlyContinue
            Write-Ok "Binstub removed: $stub"
        }
    }
    if ($Purge) {
        if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir -ErrorAction SilentlyContinue }
        Write-Ok "Runtime purged: $InstallDir"
    } else {
        # Remove the runtime venv. In the versioned layout this is the `.venv`
        # link AND the whole versions/ tree; otherwise the single real venv dir.
        if ($VersionedRuntime) {
            if (Test-VenvIsLink $LinkDir) { & cmd /c rmdir "$LinkDir" 2>$null }
            elseif (Test-Path $LinkDir) { Remove-Item -Recurse -Force $LinkDir -ErrorAction SilentlyContinue }
            $verRoot = Join-Path $InstallDir 'versions'
            if (Test-Path $verRoot) { Remove-Item -Recurse -Force $verRoot -ErrorAction SilentlyContinue }
        } elseif (Test-Path $VenvDir) {
            Remove-Item -Recurse -Force $VenvDir -ErrorAction SilentlyContinue
        }
        Write-Ok 'Venv removed (state kept; -Purge to delete)'
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
