<#
.SYNOPSIS
    Bootstrap the agent-dispatch runtime. PS5+ compatible.

.DESCRIPTION
    Creates the shared runtime at ~/.agent-dispatch/ -- a venv with the
    agent_dispatch package installed (via uv pip install) -- and deploys the
    `agent-dispatch` binstub into ~/.local/bin.

    Run once per machine. Idempotent -- safe to re-run for repairs or upgrades.

.PARAMETER InstallDir
    Override the runtime install directory (default: ~/.agent-dispatch).

.PARAMETER Force
    Re-create the venv even if it already exists.

.PARAMETER Service
    Install the coordinator as an auto-starting Windows Scheduled Task (the
    Windows analogue of the Linux systemd user unit). Opt-in: a machine that is
    only a client of a remote coordinator omits this.
#>
[CmdletBinding()]
param(
    [string]$InstallDir,
    [switch]$Force,
    [switch]$Service
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

# -- Output helpers (PS5-safe) ------------------------------------------

function Write-Ok      { param([string]$Msg) Write-Host "  [OK]   $Msg" -ForegroundColor Green }
function Write-Skip    { param([string]$Msg) Write-Host "  [SKIP] $Msg" -ForegroundColor Cyan }
function Write-Fail    { param([string]$Msg) Write-Host "  [FAIL] $Msg" -ForegroundColor Red }
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

# -- Preflight checks --------------------------------------------------

Write-Host ''
Write-Host '=== agent-dispatch init ===' -ForegroundColor Cyan
Write-Host ''

if (-not (Test-Path $PkgSrcDir)) {
    Write-Fail "Package source not found at $PkgSrcDir"
    Write-Host "  Are you running this from the correct plugin directory?"
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

# Check for uv -- install via winget if missing
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

# -- 1. Create directories ---------------------------------------------

foreach ($dir in @($InstallDir, $LocalBin)) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
}
Write-Ok "Directories: $InstallDir"

# -- 2. Create venv ----------------------------------------------------

if ($Force -or -not (Test-Path $VenvPython)) {
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    # Prefer a SAC-trusted signed base Python via `--copies` so the venv
    # python.exe is signed (Smart App Control blocks the unsigned uv-managed
    # python); then uv; then plain python -m venv.
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

# -- 3. Install the package into the venv (uv pip install) -------------

$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
# Pre-strip any locked console-script trampoline so uv can overwrite it (os err 5).
Remove-ConsoleTrampolines -VenvDir $VenvDir
# The [mcp] extra ships the `agent-dispatch mcp` stdio server dependency.
if (Get-Command uv -ErrorAction SilentlyContinue) {
    & uv pip install --python $VenvPython "$($PluginDir)[mcp]" --quiet 2>&1 | Out-Null
} else {
    & $VenvPython -m pip install --quiet "$($PluginDir)[mcp]" 2>&1 | Out-Null
}
$pkgResult = $LASTEXITCODE
$ErrorActionPreference = $prevEAP
if ($pkgResult -ne 0) {
    Write-Fail 'Failed to install agent-dispatch package into venv'
    exit 1
}

# Strip the uv-regenerated console-script trampoline(s) (SAC-blocked, unused).
Remove-ConsoleTrampolines -VenvDir $VenvDir
Write-Ok 'Package installed: agent-dispatch'

# -- 4. Deploy binstub -------------------------------------------------

$stubName = 'agent-dispatch'
if ($env:OS -eq 'Windows_NT') {
    # Single .cmd binstub (npx / uv parity) -- and NO .ps1.
    #
    # Unlike the sibling plugins (CLIs invoked interactively, where a .ps1 wins
    # PowerShell's command discovery and forwards argv verbatim), agent-dispatch is
    # spawned by Copilot as a stdio MCP server via a bare `command: agent-dispatch`.
    # PowerShell prefers a same-named .ps1 over a .cmd, but a .ps1 shim does not
    # reliably stream stdin into the child python the way an stdio MCP requires.
    # A .cmd forwards stdin verbatim and is what `where`/PATHEXT resolution and
    # (absent a .ps1) PowerShell both pick. So ship ONLY the .cmd, and remove any
    # stale .ps1 from earlier installs so it can't shadow the .cmd. The .cmd
    # launches the signed venv python via -m, never the SAC-blocked console-script
    # trampoline .exe.
    $ps1Path = Join-Path $LocalBin "$stubName.ps1"
    if (Test-Path $ps1Path) { Remove-Item $ps1Path -Force -ErrorAction SilentlyContinue }

    $stubPath = Join-Path $LocalBin "$stubName.cmd"
    $stubContent = @"
@echo off
set "PYTHONUTF8=1"
"%USERPROFILE%\.agent-dispatch\.venv\Scripts\python.exe" -m agent_dispatch %*
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

# -- 5. Write deploy manifest ------------------------------------------

# Unified schema_version 3 manifest (install-contract): records the source
# footprint (marketplace vs local) so deploys are auditable like the siblings.
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
    venv           = ($VenvDir -replace '\\', '/')
    runtime        = 'python'
}
$tmp = "$manifestPath.tmp"
$manifest | ConvertTo-Json -Depth 4 | Set-Content -Path $tmp -Encoding UTF8
Move-Item -Force -Path $tmp -Destination $manifestPath
Write-Ok "Deploy manifest written (source: $kind)"

# -- 6. Verify ----------------------------------------------------------

Write-Host ''
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$importOk = $false
for ($i = 0; $i -lt 3; $i++) {
    & $VenvPython -c 'import agent_dispatch' 2>$null
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

# Ensure ~/.local/bin is on PATH
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

# -- 7. Optional coordinator service (Windows Scheduled Task) -----------
# The coordinator is the always-on single writer. Install it as an auto-starting
# Scheduled Task only when asked (-Service) -- the Windows analogue of the Linux
# systemd user unit. A client-only machine (pointing AGENT_DISPATCH_URL at a
# remote coordinator) omits it.
if ($Service -and $env:OS -eq 'Windows_NT') {
    $taskName = 'agent-dispatch'
    if (-not (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)) {
        Write-Skip 'ScheduledTasks module unavailable -- skipping service (run: agent-dispatch serve)'
    } else {
        # Editable env file (parity with the Linux service.env).
        $envFile = Join-Path $InstallDir 'service.env'
        if (-not (Test-Path $envFile)) {
            $envDefault = @"
# agent-dispatch coordinator service environment.
# Edit, then: Start-ScheduledTask -TaskName agent-dispatch  (or re-run init.ps1 -Service)
AGENT_DISPATCH_HOST=127.0.0.1
AGENT_DISPATCH_PORT=9330
# AGENT_DISPATCH_DB=%USERPROFILE%\.agent-dispatch\tasks.db   # default; uncomment to override
# AGENT_DISPATCH_TOKEN=                                       # set to require bearer auth
"@
            [System.IO.File]::WriteAllText($envFile, $envDefault, $utf8NoBom)
            Write-Ok "Service env: $envFile (defaults; edit to expose on the network / add a token)"
        } else {
            Write-Skip "Service env already exists: $envFile"
        }

        # Launcher: load service.env, then exec the coordinator via the venv python.
        # A scheduled task has no EnvironmentFile, so a thin launcher sources it.
        $launcher = Join-Path $InstallDir 'serve-service.ps1'
        $launcherBody = @"
# agent-dispatch coordinator launcher (generated by init.ps1 -Service). Do not edit;
# edit service.env instead. Loads service.env, then runs the coordinator.
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
& '$VenvPython' -m agent_dispatch serve
"@
        [System.IO.File]::WriteAllText($launcher, $launcherBody, $utf8NoBom)

        $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
            -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`""
        $trigger = New-ScheduledTaskTrigger -AtLogOn
        # Restart-on-failure + no run-time limit (long-running server); start on battery.
        $settings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
        $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
            -LogonType Interactive -RunLevel Limited

        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
            -Settings $settings -Principal $principal -Force `
            -Description 'agent-dispatch -- portable agent task-queue coordinator' | Out-Null
        $regOk = $?
        if ($regOk) { Start-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue }
        $ErrorActionPreference = $prevEAP

        if ($regOk) {
            Write-Ok "Coordinator service installed + started (Scheduled Task '$taskName')"
        } else {
            Write-Fail "Failed to register Scheduled Task '$taskName' (run: agent-dispatch serve)"
        }
    }
}

Write-Host ''
Write-Host '=== agent-dispatch init complete ===' -ForegroundColor Cyan
if ($Service -and $env:OS -eq 'Windows_NT') {
    Write-Host '  Coordinator: Get-ScheduledTask -TaskName agent-dispatch | Get-ScheduledTaskInfo' -ForegroundColor DarkGray
} else {
    Write-Host '  Try: agent-dispatch version   (add -Service to run the coordinator as a Scheduled Task)' -ForegroundColor DarkGray
}
exit 0
