<#
.SYNOPSIS
    Install/update the agent-ssh runtime. PS5+ compatible.
#>
[CmdletBinding()]
param(
    [ValidateSet('install', 'update', 'status', 'uninstall')]
    [string]$Action = 'install',
    [string]$InstallDir,
    [switch]$Force
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

function Write-Ok      { param([string]$Msg) Write-Host "  [OK]   $Msg" -ForegroundColor Green }
function Write-Skip    { param([string]$Msg) Write-Host "  [SKIP] $Msg" -ForegroundColor Cyan }
function Write-Fail    { param([string]$Msg) Write-Host "  [FAIL] $Msg" -ForegroundColor Red }
function Write-Step    { param([string]$Msg) Write-Host "  ...    $Msg" -ForegroundColor DarkGray }

$PluginDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PkgSrcDir = Join-Path $PluginDir 'src\agent_ssh'

if (-not $InstallDir) {
    $InstallDir = Join-Path $env:USERPROFILE '.agent-ssh'
}
$VenvDir  = Join-Path $InstallDir '.venv'
$LocalBin = Join-Path $env:USERPROFILE '.local\bin'

if ($env:OS -eq 'Windows_NT') {
    $VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
} else {
    $VenvPython = Join-Path $VenvDir 'bin/python'
}
$ManifestPath = Join-Path $InstallDir 'deploy-manifest.json'
$utf8NoBom = New-Object System.Text.UTF8Encoding $false

# === install-contract:v3 versioned-venv (agent-ssh: .venv-as-junction) ===
# Immutable per-version runtime (#581). Build the venv into versions/<version>
# and make the historical `.venv` path a junction into it, so the binstubs and
# deploy-manifest resolve through the link unchanged. agent-ssh is a CLI (no
# daemon). LinkDir/LinkPython is the stable `.venv` path; VenvDir/VenvPython is the
# versions/<v> slot (build + health-gate). Legacy mode: Link == Venv. Gated behind
# AGENT_SSH_VERSIONED=0 (default ON); COPILOT_EXT_NO_VERSIONED=1 force-disables.
# scripts/versioned_runtime.py owns the swap + migration + gc.
$LinkDir          = $VenvDir
$LinkPython       = $VenvPython
$VersionedRuntime = $false
$SrcVersion       = $null
if (($env:COPILOT_EXT_NO_VERSIONED -ne '1') -and
    ($env:AGENT_SSH_VERSIONED -notin @('0', 'false', 'no', 'off'))) {
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

function Invoke-VersionedActivate {
    <# CLI (no daemon): health-gate the freshly-built slot, swap the stable `.venv`
       junction onto it (first migration moves a legacy real `.venv` aside), then
       gc old slots keeping current + the previous-good. Returns $false on failure.
       No-op ($true) in legacy mode. #>
    if (-not $VersionedRuntime) { return $true }
    $vr = Join-Path $PSScriptRoot 'versioned_runtime.py'
    $py = if (Test-Path $VenvPython) { $VenvPython } else { $LinkPython }
    if (-not (Test-Path $py)) { return $true }
    $prevEAP = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & $VenvPython -c 'import agent_ssh' 2>$null
    $slotOk = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prevEAP
    if (-not $slotOk) {
        Write-Fail "Fresh runtime slot failed its health gate (versions/$SrcVersion) -- not activating"
        return $false
    }
    $prev = (& $py $vr --root $InstallDir --link-name '.venv' current 2>$null); $prev = ("$prev").Trim()
    & $py $vr --root $InstallDir --link-name '.venv' activate $SrcVersion --replace-nonlink 2>&1 |
        ForEach-Object { Write-Step $_ }
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Failed to activate versioned venv (.venv -> versions/$SrcVersion)"
        return $false
    }
    Write-Ok "Runtime version $SrcVersion active (.venv -> versions/$SrcVersion)"
    $prevEAP = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $gcArgs = @($vr, '--root', $InstallDir, '--link-name', '.venv', 'gc', '--protect-pids')
    if ($prev) { $gcArgs += @('--keep', $prev) }
    & $LinkPython @gcArgs 2>&1 | ForEach-Object { Write-Step "gc: $_" }
    $ErrorActionPreference = $prevEAP
    return $true
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

if ($Action -eq 'status') {
    Write-Host '=== agent-ssh status ===' -ForegroundColor Cyan
    if (Test-Path $LinkPython) { Write-Ok "Venv: $LinkDir" } else { Write-Skip "Venv missing: $LinkDir" }
    $ps1 = Join-Path $LocalBin 'agent-ssh.ps1'
    $cmd = Join-Path $LocalBin 'agent-ssh.cmd'
    if (Test-Path $ps1) { Write-Ok "Binstub: $ps1 (+ .cmd fallback)" } elseif (Test-Path $cmd) { Write-Skip "Only fallback binstub exists: $cmd" } else { Write-Skip "Binstub missing: $ps1" }
    if (Test-Path $ManifestPath) { Write-Ok "Deploy manifest: $ManifestPath" } else { Write-Skip 'Deploy manifest missing' }
    exit 0
}

if ($Action -eq 'uninstall') {
    Remove-Item (Join-Path $LocalBin 'agent-ssh.ps1') -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $LocalBin 'agent-ssh.cmd') -Force -ErrorAction SilentlyContinue
    Remove-Item $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Ok 'agent-ssh runtime removed'
    exit 0
}

Write-Host ''
Write-Host '=== agent-ssh install ===' -ForegroundColor Cyan
Write-Host ''

if (-not (Test-Path $PkgSrcDir)) {
    Write-Fail "Package source not found at $PkgSrcDir"
    exit 1
}

$hasWinget = $null -ne (Get-Command winget -ErrorAction SilentlyContinue)
$pythonCmd = $null
foreach ($candidate in @('python', 'python3', 'py')) {
    $found = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($found) {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $testOut = & $found.Source --version 2>&1
            if ($LASTEXITCODE -eq 0 -and $testOut -match 'Python') {
                $pythonCmd = $found.Source
            }
        } catch { }
        $ErrorActionPreference = $prevEAP
        if ($pythonCmd) { break }
    }
}
if (-not $pythonCmd) {
    Write-Fail 'Python not found on PATH (need 3.10+)'
    Write-Host '  Install Python from https://python.org or via winget:' -ForegroundColor DarkGray
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
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
}
Write-Ok "Directories: $InstallDir"

# -- Deploy the session-start hook (version-gated runtime reconcile) --
# hooks.json runs ~/.agent-ssh/bin/bootstrap-check.ps1 at session start; it
# re-runs this installer only when the deployed version drifts from the payload.
$BinHookDir = Join-Path $InstallDir 'bin'
if (-not (Test-Path $BinHookDir)) { New-Item -ItemType Directory -Path $BinHookDir -Force | Out-Null }
foreach ($h in @('bootstrap-check.ps1', 'bootstrap-check.sh')) {
    $hSrc = Join-Path $PSScriptRoot $h
    if (Test-Path $hSrc) { Copy-Item $hSrc (Join-Path $BinHookDir $h) -Force }
}
Write-Ok "Session-start hook: $BinHookDir\bootstrap-check.ps1"

if ($Force -or -not (Test-Path $VenvPython)) {
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
    if ($signedBase -and (Test-Path $VenvPython)) {
        try { if ((Get-AuthenticodeSignature $VenvPython).Status -ne 'Valid') { Remove-Item -Recurse -Force $VenvDir -ErrorAction Stop } } catch {}
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

$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
Remove-ConsoleTrampolines -VenvDir $VenvDir
if (Get-Command uv -ErrorAction SilentlyContinue) {
    & uv pip install --python $VenvPython "$PluginDir" --quiet 2>&1 | Out-Null
} else {
    & $VenvPython -m pip install --quiet "$PluginDir" 2>&1 | Out-Null
}
$pkgResult = $LASTEXITCODE
$ErrorActionPreference = $prevEAP
if ($pkgResult -ne 0) {
    Write-Fail 'Failed to install agent-ssh package into venv'
    exit 1
}
Remove-ConsoleTrampolines -VenvDir $VenvDir
Write-Ok 'Package installed: agent-ssh'

# Versioned layout (#581): health-gate the slot + swap the `.venv` junction.
if (-not (Invoke-VersionedActivate)) { exit 1 }

$stubName = 'agent-ssh'
if ($env:OS -eq 'Windows_NT') {
    $ps1Path = Join-Path $LocalBin "$stubName.ps1"
    $ps1Content = @(
        '$env:PYTHONUTF8 = ''1''',
        '$_venv = "$env:USERPROFILE\.agent-ssh\.venv"',
        '$_py = Join-Path $_venv ''Scripts\python.exe''',
        'try { $_t = (Get-Item -LiteralPath $_venv -Force -ErrorAction Stop).Target; if ($_t) { $_py = Join-Path (@($_t)[0]) ''Scripts\python.exe'' } } catch {}',
        '& $_py -m agent_ssh @args',
        'exit $LASTEXITCODE'
    ) -join "`r`n"
    [System.IO.File]::WriteAllText($ps1Path, $ps1Content, $utf8NoBom)

    $stubPath = Join-Path $LocalBin "$stubName.cmd"
    $stubContent = @(
        '@echo off',
        'set "PYTHONUTF8=1"',
        'set "_PY=%USERPROFILE%\.agent-ssh\.venv\Scripts\python.exe"',
        'for /f "tokens=2 delims=[]" %%i in (''dir /a:l "%USERPROFILE%\.agent-ssh" 2^>nul ^| findstr /i /c:".venv"'') do set "_PY=%%i\Scripts\python.exe"',
        '"%_PY%" -m agent_ssh %*'
    ) -join "`r`n"
    [System.IO.File]::WriteAllText($stubPath, $stubContent, $utf8NoBom)
    $stubPath = "$ps1Path (+ .cmd fallback)"
} else {
    $stubPath = Join-Path $LocalBin $stubName
    $stubContent = @(
        '#!/usr/bin/env bash',
        'export PYTHONUTF8=1',
        'exec "$HOME/.agent-ssh/.venv/bin/python" -m agent_ssh "$@"'
    ) -join "`r`n"
    [System.IO.File]::WriteAllText($stubPath, $stubContent, $utf8NoBom)
}
Write-Ok "Binstub: $stubPath"

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
    service        = 'agent-ssh'
    deployed_at    = (Get-Date -Format 'o')
    deployed_by    = "$($env:COMPUTERNAME.ToLower())-windows"
    source         = [ordered]@{
        kind    = $kind
        path    = ($PluginDir -replace '\\', '/')
        repo    = 'copilot-extensions'
        plugin  = 'agent-ssh'
        version = $ver
        commit  = $commit
        branch  = $branch
        dirty   = $dirty
    }
    venv           = ($LinkDir -replace '\\', '/')
    runtime        = 'python'
}
$tmp = "$ManifestPath.tmp"
$manifest | ConvertTo-Json -Depth 4 | Set-Content -Path $tmp -Encoding UTF8
Move-Item -Force -Path $tmp -Destination $ManifestPath
Write-Ok "Deploy manifest written (source: $kind)"

Write-Host ''
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$importOk = $false
for ($i = 0; $i -lt 3; $i++) {
    & $LinkPython -c 'import agent_ssh' 2>$null
    if ($LASTEXITCODE -eq 0) { $importOk = $true; break }
    Start-Sleep -Seconds 1
}
$ErrorActionPreference = $prevEAP
if ($importOk) {
    Write-Ok 'Verification: module imports successfully'
} else {
    Write-Fail 'Verification: module import failed'
    exit 1
}

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

Write-Host ''
Write-Host '=== agent-ssh install complete ===' -ForegroundColor Cyan
Write-Host '  Try: agent-ssh version' -ForegroundColor DarkGray
exit 0
