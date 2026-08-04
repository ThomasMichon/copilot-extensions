<#
.SYNOPSIS
    agent-index installer / lifecycle manager. PS5+ compatible.
.DESCRIPTION
    Canonical installer for the agent-index runtime service shell.
#>
[CmdletBinding()]
param(
    [ValidateSet('install', 'update', 'status', 'start', 'stop', 'uninstall', 'engine')]
    [string]$Action = 'install',
    [string]$InstallDir,
    [switch]$NoService,
    [switch]$Purge,
    [switch]$Force
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

if ($env:AGENT_INDEX_ALLOW_DOWNGRADE -eq '1') { $Force = $true }

function Write-Ok      { param([string]$Msg) Write-Host "  [OK]   $Msg" -ForegroundColor Green }
function Write-Skip    { param([string]$Msg) Write-Host "  [SKIP] $Msg" -ForegroundColor Cyan }
function Write-Fail    { param([string]$Msg) Write-Host "  [FAIL] $Msg" -ForegroundColor Red }
function Write-Warn    { param([string]$Msg) Write-Host "  [WARN] $Msg" -ForegroundColor Yellow }

$PluginDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PkgSrcDir = Join-Path $PluginDir 'src\agent_index'
if (-not $InstallDir) { $InstallDir = Join-Path $env:USERPROFILE '.agent-index' }
$VenvDir  = Join-Path $InstallDir '.venv'
$LocalBin = Join-Path $env:USERPROFILE '.local\bin'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
$TaskName = 'agent-index'
$EnvFile = Join-Path $InstallDir 'service.env'
$Launcher = Join-Path $InstallDir 'service.ps1'

# === engine-daemon: durable, persistent embedding-engine runtime =============
# The heavy embedding stack (torch + transformers + sentence-transformers) lives
# in a DURABLE venv OUTSIDE the versioned service runtime, at
# AGENT_INDEX_ENGINE_HOME (default ~/.agent-index/engine). It is provisioned ONCE
# and preserved across service updates -- a routine `update` swaps only the
# versioned service runtime + junction and never rebuilds torch or restarts the
# warm engine daemon (effort agent-index-engine-daemon; vision §warm-durable-engine).
$EngineHome = if ($env:AGENT_INDEX_ENGINE_HOME) { $env:AGENT_INDEX_ENGINE_HOME } else { '~/.agent-index/engine' }
$EngineHome = $EngineHome -replace '^~', $env:USERPROFILE
$EngineHome = [System.IO.Path]::GetFullPath(($EngineHome -replace '/', '\'))
$EngineVenv       = Join-Path $EngineHome '.venv'
$EngineVenvPython = Join-Path $EngineVenv 'Scripts\python.exe'
$EngineTaskName   = 'agent-index-engine'
$EngineEnvFile    = Join-Path $EngineHome 'engine.env'
$EngineLauncher   = Join-Path $EngineHome 'engine.ps1'
# === end engine-daemon ======================================================

# === install-contract:v3 versioned-venv (agent-index: .venv-as-junction) ===
# Build each version into versions/<version> and make the historical `.venv`
# path a junction into the active slot. Enabled by default (set AGENT_INDEX_VERSIONED=0
# or COPILOT_EXT_NO_VERSIONED=1 to opt out); COPILOT_EXT_NO_VERSIONED=1 force-disables.
$LinkDir          = $VenvDir
$LinkPython       = $VenvPython
$VersionedRuntime = $false
$SrcVersion       = $null
if (($env:COPILOT_EXT_NO_VERSIONED -ne '1') -and
    ($env:AGENT_INDEX_VERSIONED -notin @('0', 'false', 'no', 'off'))) {
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

function Test-VenvIsLink {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $false }
    try { return [bool]((Get-Item $Path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) }
    catch { return $false }
}

function Invoke-VersionedActivate {
    if (-not $VersionedRuntime) { return $true }
    if ((Test-Path $LinkDir) -and -not (Test-VenvIsLink $LinkDir)) {
        try { Invoke-Stop | Out-Null } catch {}
    }
    $vr = Join-Path $PSScriptRoot 'versioned_runtime.py'
    $py = if (Test-Path $VenvPython) { $VenvPython } else { $LinkPython }
    & $py $vr --root $InstallDir --link-name '.venv' activate $SrcVersion --replace-nonlink 2>&1 |
        ForEach-Object { Write-Host "  ...    $_" -ForegroundColor DarkGray }
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
    & $py @gcArgs 2>&1 | ForEach-Object { Write-Host "  ...    gc: $_" -ForegroundColor DarkGray }
    $ErrorActionPreference = $prevEAP
}
# === end install-contract:v3 versioned-venv ===

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

# Resolve a vendored library path (libs\<LibName>) across multiple layouts.
# Returns the path string, or $null if not found.
function Resolve-VendoredLib {
    param([Parameter(Mandatory)][string]$LibName)
    # 1. Vendored inside agent-index (marketplace install layout)
    $candidate = Join-Path $PluginDir "libs\$LibName"
    if (Test-Path (Join-Path $candidate 'pyproject.toml')) {
        return (Resolve-Path $candidate).Path
    }

    # 2. Relative path (git checkout layout)
    $candidate = Join-Path $PluginDir "..\..\libs\$LibName"
    if (Test-Path (Join-Path $candidate 'pyproject.toml')) {
        return (Resolve-Path $candidate).Path
    }

    # 3. Git repo registry (~/.git-repos) -- use Python for safe YAML parsing
    $gitRepos = Join-Path $env:USERPROFILE '.git-repos'
    if (Test-Path $gitRepos) {
        try {
            $result = & python3 -c @"
import pathlib, os
try:
    import yaml
except ImportError:
    raise SystemExit(1)
reg = yaml.safe_load(pathlib.Path.home().joinpath('.git-repos').read_text())
repo = (reg or {}).get('repos', {}).get('copilot-extensions', {})
if repo:
    p = repo.get('path', os.path.join(reg.get('srcroot', ''), 'copilot-extensions'))
    p = os.path.expanduser(p)
    lib = os.path.join(p, 'libs', '$LibName')
    if os.path.isfile(os.path.join(lib, 'pyproject.toml')):
        print(lib)
        raise SystemExit(0)
raise SystemExit(1)
"@ 2>$null
            if ($LASTEXITCODE -eq 0 -and $result) {
                return $result.Trim()
            }
        } catch { }
    }

    # 4. Common checkout path (repo exists but registry absent/stale)
    $candidate = Join-Path $env:USERPROFILE "src\copilot-extensions\libs\$LibName"
    if (Test-Path (Join-Path $candidate 'pyproject.toml')) {
        return (Resolve-Path $candidate).Path
    }

    return $null
}

# zero-downtime cutover primitives (module ``zdd``), extracted from agent-bridge.
function Resolve-Zdd { return (Resolve-VendoredLib -LibName 'zdd') }

# Check if the zdd cutover lib is already importable in the venv.
function Test-ZddInstalled {
    if (-not (Test-Path $VenvPython)) { return $false }
    & $VenvPython -c 'from zdd.cutover import CutoverOrchestrator' 2>$null
    return $LASTEXITCODE -eq 0
}

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

function Get-InstalledVersion {
    if (-not (Test-Path $LinkPython)) { return $null }
    try {
        $v = & $LinkPython -c 'from importlib.metadata import version; print(version("agent-index"))' 2>$null
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
            Write-Warn "Downgrade $installed -> $source forced (-Force / AGENT_INDEX_ALLOW_DOWNGRADE)"
            return
        }
        Write-Fail "Refusing to downgrade agent-index: installed $installed > source $source"
        exit 1
    }
}

function Remove-ConsoleTrampolines {
    param([Parameter(Mandatory)][string]$VenvDir)
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

function Install-Runtime {
    if (-not (Test-Path $PkgSrcDir)) { Write-Fail "Package source not found at $PkgSrcDir"; exit 1 }
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
    if (-not $pythonCmd) { Write-Fail 'Python not found on PATH (need 3.10+)'; exit 1 }
    Write-Ok "Python: $pythonCmd"

    foreach ($dir in @($InstallDir, $LocalBin)) {
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    }
    Write-Ok "Directories: $InstallDir"

    if (-not (Test-Path $VenvPython)) {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        if (Get-Command uv -ErrorAction SilentlyContinue) {
            & uv venv $VenvDir --allow-existing 2>&1 | Out-Null
        } else {
            & $pythonCmd -m venv $VenvDir 2>&1 | Out-Null
        }
        $ErrorActionPreference = $prevEAP
        if (-not (Test-Path $VenvPython)) { Write-Fail "Venv creation failed -- $VenvPython not found"; exit 1 }
        Write-Ok 'Venv created'
    } else { Write-Skip 'Venv already exists' }

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'

    # zdd (zero-downtime cutover primitives: routing table + orchestrator).
    $ZddDir = Resolve-Zdd
    if ($ZddDir) {
        if (Get-Command uv -ErrorAction SilentlyContinue) {
            $zddOut = & uv pip install --python $VenvPython "$ZddDir" --reinstall-package agent-zdd --refresh-package agent-zdd --quiet 2>&1
        } else {
            $zddOut = & $VenvPython -m pip install "$ZddDir" 2>&1
        }
        if ($LASTEXITCODE -ne 0) {
            $ErrorActionPreference = $prevEAP
            Write-Fail "zdd install failed (exit $LASTEXITCODE)"
            if ($zddOut) { Write-Host ($zddOut | Out-String) }
            exit 1
        }
    } elseif (Test-ZddInstalled) {
        Write-Skip 'zdd already installed in venv (marketplace layout)'
    } else {
        Write-Fail 'Cannot locate zdd library. Reinstall the agent-index plugin from the marketplace (copilot plugin install agent-index@copilot-extensions), then rerun this installer.'
        exit 1
    }
    Remove-ConsoleTrampolines -VenvDir $VenvDir
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        $out = & uv pip install --python $VenvPython $PluginDir 2>&1 | Out-String
    } else {
        $out = & $VenvPython -m pip install $PluginDir 2>&1 | Out-String
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Fail 'Failed to install agent-index package into venv'
        Write-Host $out
        $ErrorActionPreference = $prevEAP
        exit 1
    }
    $ErrorActionPreference = $prevEAP
    Remove-ConsoleTrampolines -VenvDir $VenvDir
    Write-Ok 'Package installed: agent-index'

    $ps1Path = Join-Path $LocalBin 'agent-index.ps1'
    $ps1Content = @"
`$env:PYTHONUTF8 = '1'
`$_venv = "`$env:USERPROFILE\.agent-index\.venv"
# Resolve the .venv junction's target and launch the slot python DIRECTLY, never
# traversing the junction (reading its target is allowed) -- RedirectionGuard #637.
`$_py = Join-Path `$_venv 'Scripts\python.exe'
try { `$_t = (Get-Item -LiteralPath `$_venv -Force -ErrorAction Stop).Target; if (`$_t) { `$_py = Join-Path (@(`$_t)[0]) 'Scripts\python.exe' } } catch {}
& `$_py -m agent_index @args
exit `$LASTEXITCODE
"@
    [System.IO.File]::WriteAllText($ps1Path, $ps1Content, $utf8NoBom)
    $cmdPath = Join-Path $LocalBin 'agent-index.cmd'
    $cmdContent = @"
@echo off
set "PYTHONUTF8=1"
set "_PY=%USERPROFILE%\.agent-index\.venv\Scripts\python.exe"
for /f "tokens=2 delims=[]" %%i in ('dir /a:l "%USERPROFILE%\.agent-index" 2^>nul ^| findstr /i /c:".venv"') do set "_PY=%%i\Scripts\python.exe"
"%_PY%" -m agent_index %*
"@
    [System.IO.File]::WriteAllText($cmdPath, $cmdContent, $utf8NoBom)
    Write-Ok "Binstub: $ps1Path"

    $prevVersion = ''
    if ($VersionedRuntime) {
        $prevVersion = Get-VersionedCurrent
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & $VenvPython -c 'import agent_index' 2>$null
        $slotOk = ($LASTEXITCODE -eq 0)
        $ErrorActionPreference = $prevEAP
        if (-not $slotOk) {
            Write-Fail "Fresh runtime slot failed its health gate (versions/$SrcVersion) -- not activating"
            exit 1
        }
        if (-not (Invoke-VersionedActivate)) { exit 1 }
    }

    Write-Manifest

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $LinkPython -c 'import agent_index' 2>$null
    $importOk = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prevEAP
    if ($importOk) { Write-Ok 'Verification: module imports successfully' }
    else { Write-Fail 'Verification: module import failed'; exit 1 }

    if ($VersionedRuntime) { Invoke-VersionedGc -KeepPrev $prevVersion }
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
        service        = 'agent-index'
        deployed_at    = (Get-Date -Format 'o')
        deployed_by    = "$($env:COMPUTERNAME.ToLower())-windows"
        source         = [ordered]@{
            kind    = $kind
            path    = ($PluginDir -replace '\\', '/')
            repo    = 'copilot-extensions'
            plugin  = 'agent-index'
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

function Test-EnginePort {
    $eh = '127.0.0.1'; $ep = 8421
    if ($env:AGENT_INDEX_ENGINE_HOST) { $eh = $env:AGENT_INDEX_ENGINE_HOST }
    if ($env:AGENT_INDEX_ENGINE_PORT) { $ep = [int]$env:AGENT_INDEX_ENGINE_PORT }
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $iar = $c.BeginConnect($eh, $ep, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(2000)
        $connected = ($ok -and $c.Connected)
        $c.Close()
        return $connected
    } catch { return $false }
}

function Install-Engine {
    # Provision the DURABLE engine venv (agent-index[engine], the torch stack) at
    # AGENT_INDEX_ENGINE_HOME. Built ONCE and skipped if present (idempotent);
    # never rebuilt by a service `update`. Non-fatal -- a failure here leaves the
    # light, torch-free service fully functional.
    if ($env:AGENT_INDEX_NO_ENGINE_DEPS -eq '1') {
        Write-Skip 'Engine runtime skipped (AGENT_INDEX_NO_ENGINE_DEPS=1)'
        return $false
    }
    if (Test-Path $EngineVenvPython) {
        Write-Skip "Engine runtime already provisioned (durable venv preserved): $EngineVenv"
        return $true
    }
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
    if (-not $pythonCmd) { Write-Warn 'Python not found -- cannot provision engine runtime'; return $false }

    Write-Host '  ...    Provisioning durable engine runtime (torch stack) -- one-time, may take a while' -ForegroundColor DarkGray
    if (-not (Test-Path $EngineHome)) { New-Item -ItemType Directory -Path $EngineHome -Force | Out-Null }

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        & uv venv $EngineVenv --allow-existing 2>&1 | Out-Null
    } else {
        & $pythonCmd -m venv $EngineVenv 2>&1 | Out-Null
    }
    if (-not (Test-Path $EngineVenvPython)) {
        $ErrorActionPreference = $prevEAP
        Write-Warn "Engine venv creation failed -- $EngineVenvPython not found"
        return $false
    }

    # zdd is a declared dependency of agent-index but is not on PyPI -- install it
    # from the vendored lib first so pip can satisfy the requirement.
    $ZddDir = Resolve-Zdd
    if ($ZddDir) {
        if (Get-Command uv -ErrorAction SilentlyContinue) {
            & uv pip install --python $EngineVenvPython "$ZddDir" --reinstall-package agent-zdd --refresh-package agent-zdd --quiet 2>&1 |
                ForEach-Object { Write-Host "  ...    $_" -ForegroundColor DarkGray }
        } else {
            & $EngineVenvPython -m pip install "$ZddDir" 2>&1 |
                ForEach-Object { Write-Host "  ...    $_" -ForegroundColor DarkGray }
        }
    }

    # agent-index[engine] -- the heavy embedding stack into the DURABLE venv only.
    # Default PyPI torch is the CPU wheel; set AGENT_INDEX_TORCH_INDEX to a CUDA
    # wheel index (e.g. https://download.pytorch.org/whl/cu121) for a GPU host.
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        $pipArgs = @('pip', 'install', '--python', $EngineVenvPython, "$PluginDir[engine]")
        if ($env:AGENT_INDEX_TORCH_INDEX) { $pipArgs += @('--extra-index-url', $env:AGENT_INDEX_TORCH_INDEX) }
        $engOut = & uv @pipArgs 2>&1
    } else {
        $pipArgs = @('-m', 'pip', 'install', "$PluginDir[engine]")
        if ($env:AGENT_INDEX_TORCH_INDEX) { $pipArgs += @('--extra-index-url', $env:AGENT_INDEX_TORCH_INDEX) }
        $engOut = & $EngineVenvPython @pipArgs 2>&1
    }
    $engRc = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP
    if ($engRc -ne 0) {
        Write-Warn 'Engine runtime install failed (torch stack) -- light service unaffected; provision later with the "engine" action'
        Write-Host ($engOut | Out-String)
        return $false
    }
    & $EngineVenvPython -c 'import torch' 2>$null
    if ($LASTEXITCODE -ne 0) { Write-Warn 'Engine venv built but torch import failed'; return $false }
    Write-Ok "Engine runtime provisioned (durable venv): $EngineVenv"
    return $true
}

function Write-EngineFiles {
    if (-not (Test-Path $EngineEnvFile)) {
        [System.IO.File]::WriteAllText($EngineEnvFile, "# agent-index engine daemon environment`nAGENT_INDEX_ENGINE_HOST=127.0.0.1`nAGENT_INDEX_ENGINE_PORT=8421`n", $utf8NoBom)
        Write-Ok "Engine env: $EngineEnvFile"
    } else { Write-Skip "Engine env already exists: $EngineEnvFile" }
    $engLauncher = @"
`$env:PYTHONUTF8 = '1'
`$env:AGENT_INDEX_ENGINE_HOME = '$($EngineHome -replace "'","''")'
`$envFile = '$($EngineEnvFile -replace "'","''")'
if (Test-Path `$envFile) {
    Get-Content `$envFile | ForEach-Object {
        `$line = `$_.Trim()
        if (`$line -and -not `$line.StartsWith('#') -and `$line.Contains('=')) {
            `$k, `$v = `$line.Split('=', 2)
            [Environment]::SetEnvironmentVariable(`$k.Trim(), `$v.Trim(), 'Process')
        }
    }
}
& '$($EngineVenvPython -replace "'","''")' -m agent_index engine run
exit `$LASTEXITCODE
"@
    [System.IO.File]::WriteAllText($EngineLauncher, $engLauncher, $utf8NoBom)
}

function Register-EngineDaemon {
    # Register the persistent, platform-native daemon task that runs the warm
    # engine from the durable venv. A warm engine is left untouched (never
    # restarted) when it is already serving.
    if ($NoService) { Write-Skip 'Engine daemon task skipped (-NoService)'; return }
    if (-not (Test-Path $EngineVenvPython)) { Write-Skip 'Engine runtime not provisioned -- daemon task not registered'; return }
    Write-EngineFiles
    try {
        $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$EngineLauncher`""
        $trigger = New-ScheduledTaskTrigger -AtLogOn
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
        $principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $EngineTaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
        if (Test-EnginePort) {
            Write-Skip "Engine daemon already serving -- leaving the warm engine untouched: $EngineTaskName"
        } else {
            Start-ScheduledTask -TaskName $EngineTaskName -ErrorAction SilentlyContinue
            Write-Ok "Engine daemon task installed + started: $EngineTaskName"
        }
    } catch { Write-Warn "Could not install/start engine daemon task: $($_.Exception.Message)" }
}

function Write-ServiceFiles {
    if (-not (Test-Path $EnvFile)) {
        [System.IO.File]::WriteAllText($EnvFile, "# agent-index service environment`nAGENT_INDEX_HOST=127.0.0.1`n# AGENT_INDEX_PORT=0  # unset/0 = OS-assigned dynamic port advertised via rendezvous`n", $utf8NoBom)
        Write-Ok "Service env: $EnvFile"
    } else { Write-Skip "Service env already exists: $EnvFile" }
    $launcherContent = @"
`$env:PYTHONUTF8 = '1'
`$envFile = '$EnvFile'
if (Test-Path `$envFile) {
    Get-Content `$envFile | ForEach-Object {
        `$line = `$_.Trim()
        if (`$line -and -not `$line.StartsWith('#') -and `$line.Contains('=')) {
            `$k, `$v = `$line.Split('=', 2)
            [Environment]::SetEnvironmentVariable(`$k.Trim(), `$v.Trim(), 'Process')
        }
    }
}
`$_venv = '$($LinkDir -replace "'","''")'
`$_py = '$($LinkPython -replace "'","''")'
# Resolve the .venv junction's target and launch the slot python DIRECTLY (never
# traverse the junction; reading its target is allowed) -- RedirectionGuard #637.
try { `$_t = (Get-Item -LiteralPath `$_venv -Force -ErrorAction Stop).Target; if (`$_t) { `$_py = Join-Path (@(`$_t)[0]) 'Scripts\python.exe' } } catch {}
& `$_py -m agent_index start
exit `$LASTEXITCODE
"@
    [System.IO.File]::WriteAllText($Launcher, $launcherContent, $utf8NoBom)
}

function Install-Service {
    if ($NoService) { Write-Skip 'Service skipped (-NoService)'; return }
    Write-ServiceFiles
    try {
        $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Launcher`""
        $trigger = New-ScheduledTaskTrigger -AtLogOn
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
        $principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
        Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Write-Ok "Service task installed: $TaskName"
    } catch { Write-Warn "Could not install/start service task: $($_.Exception.Message)" }
}

function Invoke-Status {
    if (Test-Path $LinkPython) { & $LinkPython -m agent_index status }
    else { Write-Skip "Runtime not installed: $InstallDir" }
}

function Invoke-Start {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Start-ScheduledTask -TaskName $TaskName
        Write-Ok "Service task started: $TaskName"
    } elseif (Test-Path $LinkPython) {
        Start-Process -FilePath $LinkPython -ArgumentList @('-m', 'agent_index', 'start') -WorkingDirectory $InstallDir
        Write-Ok 'Service process started'
    } else { Write-Fail 'Runtime not installed'; exit 1 }
}

function Invoke-Stop {
    if (Test-Path $LinkPython) { & $LinkPython -m agent_index stop | Out-Host }
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Write-Ok "Service task stopped: $TaskName"
    }
}

function Invoke-Uninstall {
    Invoke-Stop
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    }
    if (Get-ScheduledTask -TaskName $EngineTaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $EngineTaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $EngineTaskName -Confirm:$false -ErrorAction SilentlyContinue
    }
    Remove-Item (Join-Path $LocalBin 'agent-index.ps1') -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $LocalBin 'agent-index.cmd') -Force -ErrorAction SilentlyContinue
    if ($Purge) {
        if (Test-Path $EngineHome) { Remove-Item $EngineHome -Recurse -Force -ErrorAction SilentlyContinue }
        if (Test-Path $InstallDir) { Remove-Item $InstallDir -Recurse -Force }
    }
    Write-Ok 'agent-index uninstalled'
}

switch ($Action) {
    'install' { Install-Runtime; Install-Service; Install-Engine | Out-Null; Register-EngineDaemon }
    'update' { Invoke-DowngradeGuard; Install-Runtime; Install-Service }  # engine venv + daemon left untouched by design
    'engine' { Install-Engine | Out-Null; Register-EngineDaemon }
    'status' { Invoke-Status }
    'start' { Invoke-Start }
    'stop' { Invoke-Stop }
    'uninstall' { Invoke-Uninstall }
}
