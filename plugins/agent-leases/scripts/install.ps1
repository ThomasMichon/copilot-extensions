<#
.SYNOPSIS
    Install or update the agent-leases runtime. PS5+ compatible.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'update', 'status', 'uninstall')]
    [string]$Action = 'status'
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

function Write-Ok   { param([string]$Message) Write-Host "  [OK]   $Message" -ForegroundColor Green }
function Write-Fail { param([string]$Message) Write-Host "  [FAIL] $Message" -ForegroundColor Red }
function Write-Step { param([string]$Message) Write-Host "  ...    $Message" -ForegroundColor DarkGray }

$PluginDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$InstallDir = Join-Path $env:USERPROFILE '.agent-leases'
$LocalBin = Join-Path $env:USERPROFILE '.local\bin'
$LinkDir = Join-Path $InstallDir '.venv'
$LinkPython = Join-Path $LinkDir 'Scripts\python.exe'
$BinstubPs1 = Join-Path $LocalBin 'agent-leases.ps1'
$BinstubCmd = Join-Path $LocalBin 'agent-leases.cmd'
$ManifestPath = Join-Path $InstallDir 'deploy-manifest.json'
$Utf8NoBom = New-Object System.Text.UTF8Encoding $false

if ($Action -eq 'status') {
    if (-not (Test-Path $LinkPython) -or -not (Test-Path $BinstubCmd)) {
        Write-Fail 'agent-leases is not installed'
        exit 1
    }
    & $LinkPython -m agent_leases --version
    if (Test-Path $ManifestPath) { Write-Ok "Deploy manifest: $ManifestPath" }
    exit 0
}
if ($Action -eq 'uninstall') {
    Remove-Item $BinstubPs1, $BinstubCmd -Force -ErrorAction SilentlyContinue
    if (Test-Path $InstallDir) { Remove-Item $InstallDir -Recurse -Force }
    Write-Ok 'Removed agent-leases runtime'
    exit 0
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Fail 'uv is required to install agent-leases'
    exit 1
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Fail 'git is required to run agent-leases'
    exit 1
}

# === install-contract:v3 versioned-venv -- keep byte-identical across plugins ===
$VenvDir = $LinkDir
$VenvPython = $LinkPython
$VersionedRuntime = $env:COPILOT_EXT_NO_VERSIONED -ne '1'
$VersionLine = Select-String -Path (Join-Path $PluginDir 'pyproject.toml') -Pattern '^\s*version\s*=' | Select-Object -First 1
$SrcVersion = ($VersionLine.Line -replace '.*=\s*"([^"]+)".*', '$1')
if ($VersionedRuntime -and $SrcVersion) {
    $VenvDir = Join-Path (Join-Path $InstallDir 'versions') $SrcVersion
    $VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
} else {
    $VersionedRuntime = $false
}
# === end install-contract:v3 versioned-venv ===

foreach ($Directory in @($InstallDir, $LocalBin, (Join-Path $InstallDir 'bin'))) {
    if (-not (Test-Path $Directory)) {
        New-Item -ItemType Directory -Path $Directory -Force | Out-Null
    }
}
foreach ($Hook in @('bootstrap-check.ps1', 'bootstrap-check.sh')) {
    Copy-Item (Join-Path $PSScriptRoot $Hook) (Join-Path $InstallDir "bin\$Hook") -Force
}

if (-not (Test-Path $VenvPython)) {
    $SignedBase = $null
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($Version in @('3.13', '3.12', '3.11', '3.10')) {
            $Candidate = (& py "-$Version" -c 'import sys;print(sys.executable)' 2>$null | Out-String).Trim()
            if ($LASTEXITCODE -eq 0 -and $Candidate -and (Test-Path $Candidate)) {
                try {
                    if ((Get-AuthenticodeSignature $Candidate).Status -eq 'Valid') {
                        $SignedBase = $Candidate
                        break
                    }
                } catch {}
            }
        }
    }
    if ($SignedBase) {
        & $SignedBase -m venv --copies $VenvDir
    } else {
        & uv venv $VenvDir --allow-existing
    }
}
if (-not (Test-Path $VenvPython)) {
    Write-Fail "Venv creation failed: $VenvPython"
    exit 1
}

# The Windows launcher uses python.exe -m, never the generated console-script exe.
Get-ChildItem (Join-Path $VenvDir 'Scripts\agent-*.exe') -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue
& uv pip install --python $VenvPython "$PluginDir" --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Fail 'uv pip install failed'
    exit 1
}
Get-ChildItem (Join-Path $VenvDir 'Scripts\agent-*.exe') -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue

# === install-contract:v3 versioned-venv activate ===
if ($VersionedRuntime) {
    $VersionedScript = Join-Path $PSScriptRoot 'versioned_runtime.py'
    $Previous = ("" + (& $VenvPython $VersionedScript --root $InstallDir --link-name '.venv' current 2>$null)).Trim()
    & $VenvPython -c 'import agent_leases'
    if ($LASTEXITCODE -ne 0) {
        Write-Fail 'Fresh runtime slot failed its import health gate'
        exit 1
    }
    & $VenvPython $VersionedScript --root $InstallDir --link-name '.venv' activate $SrcVersion --replace-nonlink | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Fail 'Failed to activate versioned runtime'
        exit 1
    }
    $GcArgs = @($VersionedScript, '--root', $InstallDir, '--link-name', '.venv', 'gc', '--protect-pids')
    if ($Previous) { $GcArgs += @('--keep', $Previous) }
    & $LinkPython @GcArgs 2>$null | Out-Null
}
# === end install-contract:v3 versioned-venv activate ===

$Ps1Content = @'
$env:PYTHONUTF8 = '1'
& "$env:USERPROFILE\.agent-leases\.venv\Scripts\python.exe" -m agent_leases @args
exit $LASTEXITCODE
'@
$CmdContent = @'
@echo off
set "PYTHONUTF8=1"
"%USERPROFILE%\.agent-leases\.venv\Scripts\python.exe" -m agent_leases %*
'@
[IO.File]::WriteAllText($BinstubPs1, $Ps1Content, $Utf8NoBom)
[IO.File]::WriteAllText($BinstubCmd, $CmdContent, $Utf8NoBom)

# === install-contract:v3 source-kind -- keep byte-identical across plugins ===
function Get-SourceKind {
    param([string]$PluginPath)
    if (($PluginPath -replace '\\', '/') -match '/\.copilot/installed-plugins/') {
        return 'marketplace'
    }
    return 'local'
}
# === end install-contract:v3 source-kind ===

$SourceKind = Get-SourceKind $PluginDir
$Commit = $null
$Branch = $null
$Dirty = $false
if ($SourceKind -eq 'local') {
    $RepoRoot = (Resolve-Path (Join-Path $PluginDir '..\..')).Path
    $Commit = (git -C $RepoRoot rev-parse --short HEAD 2>$null | Out-String).Trim()
    $Branch = (git -C $RepoRoot rev-parse --abbrev-ref HEAD 2>$null | Out-String).Trim()
    $Dirty = [bool](git -C $RepoRoot status --porcelain 2>$null)
}
$Manifest = [ordered]@{
    schema_version = 3
    service = 'agent-leases'
    deployed_at = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
    source = [ordered]@{
        kind = $SourceKind
        path = $PluginDir
        repo = 'copilot-extensions'
        plugin = 'agent-leases'
        version = $SrcVersion
        commit = $Commit
        branch = $Branch
        dirty = $Dirty
    }
    venv = $LinkDir
    runtime = 'python'
}
[IO.File]::WriteAllText(
    "$ManifestPath.tmp",
    ($Manifest | ConvertTo-Json -Depth 5),
    $Utf8NoBom
)
Move-Item "$ManifestPath.tmp" $ManifestPath -Force

& $LinkPython -m agent_leases --version
Write-Ok "Runtime: $InstallDir"
Write-Ok "Binstubs: $BinstubPs1, $BinstubCmd"
Write-Step "Configure ~/.agent-leases/config.json key 'origin' before acquiring leases"
