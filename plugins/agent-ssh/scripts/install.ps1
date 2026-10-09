<#
.SYNOPSIS
    Install/update the agent-ssh runtime. PS5+ compatible.
#>
[CmdletBinding()]
param(
    [ValidateSet('install', 'update', 'status', 'uninstall', 'stamp', 'provision')]
    [string]$Action = 'install',
    [string]$InstallDir,
    [switch]$Force,

    # Preview mode for the 'uninstall' action: print what WOULD be removed
    # without touching the filesystem.
    [switch]$DryRun
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

# === install-contract:test-persistent-environment -- keep byte-identical across installers ===
function Get-CopilotPersistentEnvironmentVariable {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][ValidateSet('User', 'Machine')][string]$Target
    )
    $testMode = $env:COPILOT_EXTENSIONS_TEST_CONTAINED -eq '1' -or [bool]$env:PYTEST_CURRENT_TEST
    $effectiveTarget = if ($testMode) { 'Process' } else { $Target }
    return [Environment]::GetEnvironmentVariable($Name, $effectiveTarget)
}

function Set-CopilotPersistentEnvironmentVariable {
    param(
        [Parameter(Mandatory)][string]$Name,
        [AllowNull()][string]$Value,
        [Parameter(Mandatory)][ValidateSet('User', 'Machine')][string]$Target
    )
    $testMode = $env:COPILOT_EXTENSIONS_TEST_CONTAINED -eq '1' -or [bool]$env:PYTEST_CURRENT_TEST
    $effectiveTarget = if ($testMode) { 'Process' } else { $Target }
    [Environment]::SetEnvironmentVariable($Name, $Value, $effectiveTarget)
}
# === end install-contract:test-persistent-environment ===

# === install-contract:v4 self-stage -- keep byte-identical across plugins ===
# dotfiles #935: a plugin installer reads its own payload (src/, libs/,
# pyproject.toml) to build the venv, so while it runs -- especially if it wedges
# or times out -- it holds the SINGLETON `installed-plugins/<mkt>/<plugin>`
# payload dir open (CWD/handles). A concurrent `copilot plugin update <plugin>`
# then fails on Windows with os error 32 ("used by another process"): the payload
# freezes at the old version and reconcile keeps reverting the runtime toward it
# (the version-drift saga). Fix: when running from the marketplace payload, copy
# the WHOLE payload into a UNIQUE per-invocation staging dir OUTSIDE the payload
# and re-exec from there, so the singleton is touched only for the fast copy. A
# stalled run then holds only its own throwaway stage dir, never blocking the
# next invocation or a `copilot plugin update`. COPILOT_PLUGIN_STAGED_FROM tells
# Get-SourceKind the payload was really the marketplace (see below). Env-guarded
# against re-exec loops; the stage-dir path (not under installed-plugins) is a
# second guard. Best-effort, non-blocking reap of old stage dirs.
if (-not $env:COPILOT_PLUGIN_INSTALL_STAGED) {
    try {
        $__selfStageScriptDir = $PSScriptRoot
        $__selfStagePayload = (Resolve-Path (Join-Path $__selfStageScriptDir '..')).Path
        if (($__selfStagePayload -replace '\\', '/') -match '/\.copilot/installed-plugins/') {
            $__selfStageName = (Get-Content (Join-Path $__selfStagePayload 'plugin.json') -Raw | ConvertFrom-Json).name
            if ($__selfStageName) {
                # CWD guard (#1366): the sessionStart hook launches this installer
                # with CWD = the SINGLETON payload dir, so our process CWD is an
                # open directory handle that blocks `copilot plugin update` (os
                # error 32) for our whole lifetime -- including the watchdog
                # WaitForExit below and, on a self-stage failure, an in-place run.
                # Self-stage relocates our FILE reads but NOT the CWD handle, so
                # re-root the process CWD OFF the payload BEFORE the copy (absolute
                # paths make this safe). Set the WIN32 cwd (the real dir handle),
                # not just the PS provider location.
                try {
                    Set-Location -LiteralPath $env:USERPROFILE
                    [System.IO.Directory]::SetCurrentDirectory($env:USERPROFILE)
                } catch {}
                $__selfStageRoot = Join-Path (Join-Path $env:USERPROFILE ".$__selfStageName") '.install-stage'
                $__selfStageDir = Join-Path $__selfStageRoot ((Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfff') + "-$PID")
                New-Item -ItemType Directory -Force -Path $__selfStageDir | Out-Null
                Copy-Item -LiteralPath $__selfStagePayload -Destination $__selfStageDir -Recurse -Force
                $__selfStagedPayload = Join-Path $__selfStageDir (Split-Path -Leaf $__selfStagePayload)
                $__selfStagedEntry = Join-Path (Join-Path $__selfStagedPayload 'scripts') (Split-Path -Leaf $PSCommandPath)
                # Best-effort reap of prior stage dirs; NEVER touch a live one.
                # Only remove a sibling whose owner pid (the <ts>-<pid> suffix) is
                # DEAD -- so a concurrent or wedged installer's dir is left alone
                # (it uses its own unique dir), honoring "a stalled install must
                # never block another copy". Dead leftovers are cleaned up.
                Get-ChildItem $__selfStageRoot -Directory -Force -ErrorAction SilentlyContinue |
                    Where-Object { $_.FullName -ne $__selfStageDir } |
                    ForEach-Object {
                        $__selfStageOwnerPid = 0
                        if ($_.Name -match '-(\d+)$') { [void][int]::TryParse($Matches[1], [ref]$__selfStageOwnerPid) }
                        $__selfStageOwnerAlive = $false
                        if ($__selfStageOwnerPid -gt 0) {
                            $__selfStageOwnerAlive = [bool](Get-Process -Id $__selfStageOwnerPid -ErrorAction SilentlyContinue)
                        }
                        if (-not $__selfStageOwnerAlive) {
                            try { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction Stop } catch {}
                        }
                    }
                # Faithful arg forwarding, independent of this script's param()
                # shape AND of the invocation form. Rebuild the child arg list from
                # $PSBoundParameters (a switch as a bare -Name, else -Name Value), so
                # the staged re-exec carries the SAME action/flags whether the
                # installer was launched via `pwsh -File install.ps1 update` OR the
                # call/`-Command` form `.\install.ps1 update` (the documented
                # interactive form). The old approach -- slicing args after `-File`
                # out of GetCommandLineArgs() -- returned NOTHING for the call form,
                # so the staged child re-ran with the DEFAULT action: a silent no-op
                # that still reported success (#205). All installer args are declared
                # params, so nothing is unbound -- and $args is unavailable in a
                # param()-script under StrictMode, so it is deliberately not consulted.
                $__selfStageFwd = @()
                foreach ($__selfStageK in $PSBoundParameters.Keys) {
                    $__selfStageV = $PSBoundParameters[$__selfStageK]
                    if ($__selfStageV -is [System.Management.Automation.SwitchParameter]) {
                        if ($__selfStageV.IsPresent) { $__selfStageFwd += "-$__selfStageK" }
                    } else {
                        $__selfStageFwd += "-$__selfStageK"
                        $__selfStageFwd += [string]$__selfStageV
                    }
                }
                $env:COPILOT_PLUGIN_INSTALL_STAGED = '1'
                $env:COPILOT_PLUGIN_STAGED_FROM = $__selfStagePayload
                $__selfStageExe = (Get-Process -Id $PID).Path
                # WATCHDOG (#935): the staging parent is already outside the
                # payload and wraps the child's whole lifetime, so it doubles as
                # a watchdog -- launch the staged child, then enforce a deadline.
                # A stalled install (the (4) session-start-hook failure class)
                # self-terminates instead of leaking forever: kill the WHOLE tree
                # (taskkill /T -- Windows' subprocess kill leaves grandchildren)
                # and log. The killed child's stage dir has a dead owner pid, so
                # the next run's pid-guarded reap cleans it; its half-built slot
                # has no completion marker, so it is tossed + rebuilt (retry).
                # Deadline: <NAME>_INSTALL_DEADLINE_SEC, else
                # COPILOT_PLUGIN_INSTALL_DEADLINE_SEC, else 480s; <=0 disables.
                $__wdDeadline = 480
                $__wdEnvVar = (($__selfStageName -replace '[^A-Za-z0-9]+', '_').ToUpper()) + '_INSTALL_DEADLINE_SEC'
                $__wdRaw = [Environment]::GetEnvironmentVariable($__wdEnvVar)
                if (-not $__wdRaw) { $__wdRaw = $env:COPILOT_PLUGIN_INSTALL_DEADLINE_SEC }
                if ($__wdRaw) { [void][int]::TryParse([string]$__wdRaw, [ref]$__wdDeadline) }
                $__wdChild = Start-Process -FilePath $__selfStageExe -PassThru -NoNewWindow `
                    -WorkingDirectory $__selfStagedPayload `
                    -ArgumentList (@('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $__selfStagedEntry) + $__selfStageFwd)
                if ($__wdDeadline -gt 0 -and -not $__wdChild.WaitForExit($__wdDeadline * 1000)) {
                    try { & taskkill.exe /PID $__wdChild.Id /T /F 2>&1 | Out-Null } catch {}
                    try { Stop-Process -Id $__wdChild.Id -Force -ErrorAction SilentlyContinue } catch {}
                    $__wdLog = Join-Path (Join-Path $env:USERPROFILE ".$__selfStageName") 'reconcile.err.log'
                    try {
                        Add-Content -LiteralPath $__wdLog -Value ("[{0}] WATCHDOG-KILL {1}: install exceeded {2}s deadline (child pid {3}); killed tree. Slot lacks a completion marker -> will be tossed + retried. Stage: {4}" -f ((Get-Date).ToUniversalTime().ToString('s') + 'Z'), $__selfStageName, $__wdDeadline, $__wdChild.Id, $__selfStageDir)
                    } catch {}
                    exit 124
                }
                $__wdChild.WaitForExit()
                exit $__wdChild.ExitCode
            }
        }
    } catch {
        Write-Host "  [WARN] self-stage failed, running in place: $_" -ForegroundColor Yellow
    }
}
# === end install-contract:v4 self-stage ===

# === install-contract:v4 smoke seam (test-only) -- keep byte-identical ===
# #935 install-flow test hook. When COPILOT_PLUGIN_INSTALL_SMOKE is set, prove
# the self-stage/lock behavior WITHOUT a heavy venv build: this (post-stage)
# process records where it is running from + the recorded marketplace origin,
# then sleeps to simulate a slow/wedged install so a test can assert the
# SINGLETON payload dir stays replaceable meanwhile. Never set in production.
if ($env:COPILOT_PLUGIN_INSTALL_SMOKE) {
    try {
        $__smokePayload = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
        $__smokeName = (Get-Content (Join-Path $__smokePayload 'plugin.json') -Raw | ConvertFrom-Json).name
        $__smokeHome = Join-Path $env:USERPROFILE ".$__smokeName"
        New-Item -ItemType Directory -Force -Path $__smokeHome | Out-Null
        $__smokeSleep = 6
        [void][int]::TryParse([string]$env:COPILOT_PLUGIN_INSTALL_SMOKE_SLEEP, [ref]$__smokeSleep)
        # Optionally spawn a GRANDCHILD sleeper so a watchdog test can prove the
        # WHOLE tree is killed (Windows subprocess-kill leaves grandchildren).
        $__smokeGrandPid = 0
        if ($env:COPILOT_PLUGIN_INSTALL_SMOKE_GRANDCHILD) {
            try {
                $__g = Start-Process -FilePath (Get-Process -Id $PID).Path -PassThru -WindowStyle Hidden `
                    -ArgumentList @('-NoProfile', '-Command', "Start-Sleep -Seconds $([Math]::Max($__smokeSleep, 3600))")
                $__smokeGrandPid = $__g.Id
            } catch {}
        }
        ([ordered]@{
            ran_from     = $PSScriptRoot
            staged_from  = [string]$env:COPILOT_PLUGIN_STAGED_FROM
            staged       = [bool]$env:COPILOT_PLUGIN_INSTALL_STAGED
            child_pid    = $PID
            grandchild_pid = $__smokeGrandPid
        } | ConvertTo-Json -Compress) | Set-Content -LiteralPath (Join-Path $__smokeHome 'smoke.json')
        Start-Sleep -Seconds $__smokeSleep
    } catch {}
    exit 0
}
# === end install-contract:v4 smoke seam ===

# #935: bound uv's per-request network wait so a hung index/download degrades to
# "failed + retryable" rather than wedging the install; the self-stage watchdog
# is the authoritative TOTAL bound, this just shortens single-request stalls.
if (-not $env:UV_HTTP_TIMEOUT) { $env:UV_HTTP_TIMEOUT = '60' }


function Write-Ok      { param([string]$Msg) Write-Host "  [OK]   $Msg" -ForegroundColor Green }
function Write-Skip    { param([string]$Msg) Write-Host "  [SKIP] $Msg" -ForegroundColor Cyan }
function Write-Fail    { param([string]$Msg) Write-Host "  [FAIL] $Msg" -ForegroundColor Red }
function Write-Warn    { param([string]$Msg) Write-Host "  [WARN] $Msg" -ForegroundColor Yellow }
function Write-Step    { param([string]$Msg) Write-Host "  ...    $Msg" -ForegroundColor DarkGray }

. (Join-Path $PSScriptRoot 'installer-engine.ps1')

function Install-AgentSshPackage {
    param(
        [Parameter(Mandatory = $true)][string]$Python,
        [Parameter(Mandatory = $true)][string]$Source,
        [string[]]$Dependencies = @(),
        [string]$UvCommand
    )

    if ($UvCommand) {
        $resolvedVenueCopilot = Resolve-VenueCopilot
        foreach ($dependency in @('pyyaml>=6.0.3') + $Dependencies) {
            if ($resolvedVenueCopilot -and $dependency -eq $resolvedVenueCopilot) {
                $depResult = Invoke-UvPipInstallResilient -UvCommand $UvCommand -Arguments @('--python', $Python, '--reinstall-package', 'agent-venue-copilot', $dependency, '--quiet')
            } else {
                $depResult = Invoke-UvPipInstallResilient -UvCommand $UvCommand -Arguments @('--python', $Python, $dependency, '--quiet')
            }
            if ($depResult.ExitCode -ne 0) {
                if ($depResult.Output) { Write-Host ($depResult.Output | Out-String) }
                Write-Step 'uv package install failed -- falling back to python -m pip'
                $UvCommand = $null
                break
            }
        }
        if ($UvCommand) {
            $pkgResult = Invoke-UvPipInstallResilient -UvCommand $UvCommand -PayloadDirToScrub $Source -Arguments @('--python', $Python, '--no-deps', $Source, '--quiet')
            if ($pkgResult.ExitCode -eq 0) { return $true }
            if ($pkgResult.Output) { Write-Host ($pkgResult.Output | Out-String) }
            Write-Step "uv package install exited $($pkgResult.ExitCode) -- falling back to python -m pip"
        }
    }

    $pipSources = @($Dependencies) + @($Source)
    & $Python -m pip install --quiet @pipSources 2>&1 | Out-Null
    return $LASTEXITCODE -eq 0
}

function Resolve-VendoredLib {
    param([Parameter(Mandatory)][string]$LibName)
    # 1. Vendored inside agent-ssh (marketplace install layout)
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

# Resolve the ssh-manager / agent-procutil / venue-copilot vendored libs
# (thin wrappers) -- all 3 are now consumed as `uv`-editable canonical
# references (vendor-pointer-generalization effort, Phase 1), so a dev
# checkout has no `$PluginDir\libs\<lib>` copy for any of them.
function Resolve-SshManager { return (Resolve-VendoredLib -LibName 'ssh-manager') }
function Resolve-AgentProcutil { return (Resolve-VendoredLib -LibName 'agent-procutil') }
function Resolve-VenueCopilot { return (Resolve-VendoredLib -LibName 'venue-copilot') }
function Resolve-Zdd { return (Resolve-VendoredLib -LibName 'zdd') }
function Resolve-RemoteLoginShell { return (Resolve-VendoredLib -LibName 'remote-login-shell') }
function Resolve-FleetContracts { return (Resolve-VendoredLib -LibName 'fleet-contracts') }

function Ensure-UvIndex {
    if ($env:UV_INDEX_URL -or $env:UV_DEFAULT_INDEX) { return }
    $idx = ''
    foreach ($candidate in @('pip', 'pip3')) {
        $pipCommand = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $pipCommand) { continue }
        $result = Invoke-NativeCapture { & $pipCommand.Source config get global.index-url }
        if ($result.ExitCode -eq 0 -and $result.Output) {
            $idx = ('' + $result.Output).Trim()
            if ($idx) { break }
        }
    }
    if (-not $idx) {
        foreach ($path in @(
            [string]$env:PIP_CONFIG_FILE,
            (Join-Path $env:USERPROFILE '.config\pip\pip.conf'),
            (Join-Path $env:USERPROFILE '.pip\pip.conf'),
            'C:\ProgramData\pip\pip.ini'
        )) {
            if (-not $path -or -not (Test-Path $path)) { continue }
            $line = Select-String -Path $path -Pattern '^\s*index-url\s*=' | Select-Object -First 1
            if ($line) {
                $idx = ($line.Line -replace '^\s*index-url\s*=\s*', '').Trim()
                if ($idx) { break }
            }
        }
    }
    if ($idx) {
        $env:UV_DEFAULT_INDEX = $idx
        Write-Step 'uv index derived from pip config (governed-feed bridge)'
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
# versions/<v> slot (build + health-gate). ALWAYS versioned -- the env opt-out
# (COPILOT_EXT_NO_VERSIONED / AGENT_SSH_VERSIONED) and the legacy in-place fork are
# retired; the code below reads neither var.
# scripts/versioned_runtime.py owns the swap + migration + gc.
$LinkDir          = $VenvDir
$LinkPython       = $VenvPython
$VersionedRuntime = $false
$SrcVersion       = $null
if ($true) {  # always versioned (junction-free marker model; COPILOT_EXT_NO_VERSIONED retired)
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
        $LinkDir = $VenvDir
        $LinkPython = $VenvPython
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
    Invoke-VersionedMarkComplete
    $prev = (& $py $vr --root $InstallDir --link-name '.venv' current 2>$null); $prev = ("$prev").Trim()
    & $py $vr --root $InstallDir --link-name '.venv' activate $SrcVersion --no-link 2>&1 |
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
# === install-contract:v4 marker/toss helpers (#935) ===
function Get-BootstrapPython {
    <# A python to run the stdlib-only versioned_runtime.py helper (#935).
       Prefers the freshly-built slot venv python ($VenvDir, present at
       mark-complete before the link is swapped), then the active link's
       python, then a real base python via the `py` launcher -- avoiding the
       Windows Store 'python' alias stub. Returns $null if none. #>
    foreach ($d in @($VenvDir, $LinkDir)) {
        if ($d) { $p = Join-Path $d 'Scripts\python.exe'; if (Test-Path $p) { return $p } }
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $exe = (& py -3 -c 'import sys; print(sys.executable)' 2>$null | Out-String).Trim()
        if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Path $exe)) { return $exe }
    }
    foreach ($cand in 'python3', 'python') {
        $c = Get-Command $cand -ErrorAction SilentlyContinue
        if ($c -and $c.Source -notmatch 'WindowsApps') { return $c.Source }
    }
    return $null
}

function Get-PayloadHash {
    <# Cheap payload fingerprint for the completion marker (#935): sha256 of
       pyproject.toml + the vendored-lib version set. Never throws -> '' on error. #>
    try {
        $parts = @()
        $pp = Join-Path $PluginDir 'pyproject.toml'
        if (Test-Path $pp) { $parts += (Get-Content $pp -Raw) }
        $libs = Join-Path $PluginDir 'libs'
        if (Test-Path $libs) {
            Get-ChildItem $libs -Recurse -Filter 'pyproject.toml' -ErrorAction SilentlyContinue |
                Sort-Object FullName | ForEach-Object { $parts += (Get-Content $_.FullName -Raw) }
        }
        $joined = [string]::Join("`n", $parts)
        $sha = [System.Security.Cryptography.SHA256]::Create()
        $bytes = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($joined))
        return (-join ($bytes | ForEach-Object { $_.ToString('x2') }))
    } catch { return '' }
}

function Invoke-VersionedSlotClean {
    <# Toss an INCOMPLETE prior slot before building so we never `uv venv
       --allow-existing` over a corpse (#935); the current/active slot is never
       tossed (link-name derived from $LinkDir so the guard works per plugin).
       No-op in legacy mode. #>
    if (-not $VersionedRuntime) { return }
    $vr = Join-Path $PSScriptRoot 'versioned_runtime.py'
    $py = Get-BootstrapPython
    if (-not $py) { return }
    & $py $vr --root $InstallDir --link-name (Split-Path -Leaf $LinkDir) slot $SrcVersion --clean-incomplete 2>&1 |
        ForEach-Object { Write-Host "  ...    $_" }
}

function Invoke-VersionedMarkComplete {
    <# Write the slot's completion marker AFTER its isolated health gate passed,
       so "marker present" == "healthy, complete build". A crashed / watchdog-
       killed install never reaches here, leaving its slot markerless and thus
       tossable + retryable (#935). No-op in legacy mode. #>
    if (-not $VersionedRuntime) { return }
    $vr = Join-Path $PSScriptRoot 'versioned_runtime.py'
    $py = Get-BootstrapPython
    if (-not $py) { return }
    $mcArgs = @($vr, '--root', $InstallDir, '--link-name', (Split-Path -Leaf $LinkDir), 'mark-complete', $SrcVersion)
    $ph = Get-PayloadHash
    if ($ph) { $mcArgs += @('--payload-hash', $ph) }
    & $py @mcArgs 2>&1 | ForEach-Object { Write-Host "  ...    $_" }
}
# === end install-contract:v4 marker/toss helpers ===

function Get-SourceKind {
    param([string]$PluginPath)
    # #935: when the installer self-staged out of the marketplace payload, its
    # live path is a throwaway stage dir, so infer the kind from the ORIGINAL
    # payload path the self-stage prologue recorded (else the current path).
    $__srcPath = if ($env:COPILOT_PLUGIN_STAGED_FROM) { $env:COPILOT_PLUGIN_STAGED_FROM } else { $PluginPath }
    if (($__srcPath -replace '\\', '/') -match '/\.copilot/installed-plugins/') {
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

function Write-Binstubs {
    Write-SimpleBinstub `
        -CommandName 'agent-ssh' `
        -ModuleName 'agent_ssh' `
        -RuntimeRoot $InstallDir `
        -LocalBin $LocalBin `
        -InstallBinDir (Join-Path $InstallDir 'bin') `
        -SnapshotInstallerPath 'scripts\install.ps1' `
        -NoSelfProvisionEnv 'AGENT_SSH_NO_SELFPROVISION' `
        -ResolverPs1Source (Join-Path $PSScriptRoot 'resolve-runtime.ps1') `
        -ResolverShSource (Join-Path $PSScriptRoot 'resolve-runtime.sh')
    if ($env:OS -ne 'Windows_NT') {
        $stubPath = Join-Path $LocalBin 'agent-ssh'
        $singleQuote = [string][char]39
        $installDirShellLiteral = $singleQuote + $InstallDir.Replace($singleQuote, $singleQuote + '\' + $singleQuote + $singleQuote) + $singleQuote
        $stubContent = @(
            '#!/usr/bin/env bash',
            'export PYTHONUTF8=1',
            "_root=$installDirShellLiteral",
            'AGENT_RT_PY=""',
            'if [ -f "$_root/bin/resolve-runtime.sh" ]; then AGENT_RT_ROOT="$_root"; . "$_root/bin/resolve-runtime.sh"; fi',
            '[ -n "$AGENT_RT_PY" ] && exec "$AGENT_RT_PY" -m agent_ssh "$@"',
            'echo "[agent-ssh] runtime not provisioned; run scripts/install.sh" >&2; exit 1'
        ) -join "`n"
        [System.IO.File]::WriteAllText($stubPath, $stubContent, $utf8NoBom)
        if (-not $IsWindows) { & chmod +x $stubPath 2>$null | Out-Null }
    }
}

function Resolve-SnapshotInstallerEngineSource {
    param([Parameter(Mandatory)][ValidateSet('ps1', 'sh')][string]$Ext)
    $localEngine = Join-Path $PSScriptRoot ("installer-engine.$Ext")
    if (Test-Path -LiteralPath $localEngine) { return $localEngine }
    return Join-Path (Join-Path $PSScriptRoot '..\..\..\libs\installer-engine') ("installer-engine.$Ext")
}

function Materialize-SnapshotLibs {
    param([Parameter(Mandatory)][string]$SnapshotDir)
    $libsDir = Join-Path $SnapshotDir 'libs'
    if (-not (Test-Path $libsDir)) { New-Item -ItemType Directory -Path $libsDir -Force | Out-Null }
    foreach ($lib in @('agent-procutil', 'ssh-manager', 'venue-copilot', 'zdd', 'remote-login-shell', 'fleet-contracts')) {
        $source = Resolve-VendoredLib -LibName $lib
        if (-not $source) { throw "Cannot locate required snapshot library: $lib" }
        $destination = Join-Path $libsDir $lib
        if ([System.IO.Path]::GetFullPath($source) -eq [System.IO.Path]::GetFullPath($destination)) {
            continue
        }
        Remove-Item -LiteralPath $destination -Recurse -Force -ErrorAction SilentlyContinue
        Copy-Item -LiteralPath $source -Destination $destination -Recurse -Force
    }
}

function Materialize-SnapshotInstallerEngine {
    param([Parameter(Mandatory)][string]$SnapshotDir)
    $scriptsDir = Join-Path $SnapshotDir 'scripts'
    if (-not (Test-Path $scriptsDir)) { New-Item -ItemType Directory -Path $scriptsDir -Force | Out-Null }
    foreach ($name in @('installer-engine.ps1', 'installer-engine.sh')) {
        $ext = [System.IO.Path]::GetExtension($name).TrimStart('.')
        $source = Resolve-SnapshotInstallerEngineSource -Ext $ext
        $destination = Join-Path $scriptsDir $name
        if ([System.IO.Path]::GetFullPath($source) -ne [System.IO.Path]::GetFullPath($destination)) {
            Copy-Item -LiteralPath $source -Destination $destination -Force
        }
    }
    $installSh = Join-Path $scriptsDir 'install.sh'
    if (Test-Path $installSh) {
        $shText = [System.IO.File]::ReadAllText($installSh)
        $shText = $shText.Replace('. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"', '. "$SCRIPT_DIR/installer-engine.sh"')
        [System.IO.File]::WriteAllText($installSh, $shText, $utf8NoBom)
    }
    $installPs1 = Join-Path $scriptsDir 'install.ps1'
    if (Test-Path $installPs1) {
        $ps1Text = [System.IO.File]::ReadAllText($installPs1)
        $ps1Text = $ps1Text.Replace('. (Join-Path $PSScriptRoot ''..\..\..\libs\installer-engine\installer-engine.ps1'')', '. (Join-Path $PSScriptRoot ''installer-engine.ps1'')')
        [System.IO.File]::WriteAllText($installPs1, $ps1Text, $utf8NoBom)
    }
}

function Get-SnapshotSourceMarkerPath {
    param([Parameter(Mandatory)][string]$SnapshotDir)
    return Join-Path $SnapshotDir '.source-payload-path'
}

function Get-SnapshotVersionMarkerPath {
    param([Parameter(Mandatory)][string]$SnapshotDir)
    return Join-Path $SnapshotDir '.snapshot-version'
}

function ConvertTo-AgentSshVersionKey {
    param([string]$Value)
    if ($Value -notmatch '^(\d+)\.(\d+)\.(\d+)(?:-dev(\d+))?$') { return "1:$Value" }
    $build = if ($Matches[4]) { [int]$Matches[4] } else { [int]::MaxValue }
    return ('0:{0:D20}.{1:D20}.{2:D20}.{3}.{4:D20}' -f [int]$Matches[1], [int]$Matches[2], [int]$Matches[3], $(if ($Matches[4]) { 0 } else { 1 }), $build)
}

function Test-AgentSshVersionGreater {
    param([string]$Left, [string]$Right)
    return [string]::CompareOrdinal((ConvertTo-AgentSshVersionKey $Left), (ConvertTo-AgentSshVersionKey $Right)) -gt 0
}

function Test-PublishedSnapshotIsNewer {
    param(
        [string]$SnapshotDir,
        [string]$SourcePath,
        [string]$SourceVersion
    )
    if (-not $SnapshotDir -or -not (Test-Path $SnapshotDir)) { return $false }
    $sourceMarker = Get-SnapshotSourceMarkerPath -SnapshotDir $SnapshotDir
    $versionMarker = Get-SnapshotVersionMarkerPath -SnapshotDir $SnapshotDir
    if (-not (Test-Path $sourceMarker) -or -not (Test-Path $versionMarker)) { return $false }
    $publishedSource = (Get-Content -LiteralPath $sourceMarker -Raw).Trim()
    $publishedVersion = (Get-Content -LiteralPath $versionMarker -Raw).Trim()
    if (-not $publishedSource -or -not $publishedVersion) { return $false }
    if ($publishedSource -ne $SourcePath) { return $false }
    return Test-AgentSshVersionGreater $publishedVersion $SourceVersion
}

function Test-SnapshotReusable {
    param(
        [string]$SnapshotDir,
        [string]$SourceKind,
        [string]$SourcePath,
        [string]$SourceVersion
    )
    if ($SourceKind -eq 'local') { return $false }
    if (-not $SnapshotDir -or -not (Test-Path $SnapshotDir)) { return $false }
    foreach ($rel in @(
        'scripts\installer-engine.sh',
        'scripts\installer-engine.ps1',
        'libs\agent-procutil\pyproject.toml',
        'libs\ssh-manager\pyproject.toml',
        'libs\venue-copilot\pyproject.toml',
        'libs\zdd\pyproject.toml',
        'libs\remote-login-shell\pyproject.toml',
        'libs\fleet-contracts\pyproject.toml'
    )) {
        if (-not (Test-Path (Join-Path $SnapshotDir $rel))) { return $false }
    }
    if (-not (Test-Path (Get-SnapshotVersionMarkerPath -SnapshotDir $SnapshotDir))) { return $false }
    if (((Get-Content -LiteralPath (Get-SnapshotVersionMarkerPath -SnapshotDir $SnapshotDir) -Raw).Trim()) -ne $SourceVersion) { return $false }
    if (-not (Test-Path (Get-SnapshotSourceMarkerPath -SnapshotDir $SnapshotDir))) { return $false }
    if (((Get-Content -LiteralPath (Get-SnapshotSourceMarkerPath -SnapshotDir $SnapshotDir) -Raw).Trim()) -ne $SourcePath) { return $false }
    return $true
}

function Get-CurrentSnapshotPath {
    $payloadPath = Join-Path $InstallDir 'payload-dir'
    if (-not (Test-Path $payloadPath)) { return '' }
    try { return ([System.IO.File]::ReadAllText($payloadPath)).Trim() } catch { return '' }
}

function Acquire-StampPublicationLock {
    $lockPath = Join-Path $InstallDir '.stamp-publication.lock'
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        try {
            return [System.IO.File]::Open(
                $lockPath,
                [System.IO.FileMode]::OpenOrCreate,
                [System.IO.FileAccess]::ReadWrite,
                [System.IO.FileShare]::None
            )
        } catch [System.IO.IOException] {
            Start-Sleep -Milliseconds 200
        }
    }
    throw "Timed out acquiring stamp publication lock: $lockPath"
}

function Invoke-Stamp {
    # Fast base install (#1393, snapshot slot model): copy the payload SOURCE
    # into a per-version snapshot under ~/.agent-ssh/snapshots/<ver>/, record
    # markers, and deploy the self-provisioning binstub -- deferring the heavy
    # venv build to the binstub's first use. No venv, no uv; fits a sessionStart
    # grace window and NEVER holds the marketplace payload open (it copies from
    # the already self-staged $PluginDir, freeing the singleton immediately).
    Write-Host ''
    Write-Host '=== agent-ssh stamp (defer runtime to first use) ===' -ForegroundColor Cyan
    if (-not $SrcVersion) { Write-Fail 'Cannot stamp: no version in pyproject.toml'; exit 1 }
    foreach ($dir in @($InstallDir, $LocalBin)) {
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    }
    $stampPublicationLock = Acquire-StampPublicationLock
    try {
        $sourcePath = if ($env:COPILOT_PLUGIN_STAGED_FROM) { $env:COPILOT_PLUGIN_STAGED_FROM } else { $PluginDir }
        $sourceKind = Get-SourceKind -PluginPath $sourcePath
        $currentSnapshot = Get-CurrentSnapshotPath
        if (Test-PublishedSnapshotIsNewer -SnapshotDir $currentSnapshot -SourcePath $sourcePath -SourceVersion $SrcVersion) {
            Write-Skip "Published snapshot $currentSnapshot is newer than $SrcVersion; leaving payload-dir unchanged"
            Write-Binstubs
            return
        }
        if (Test-SnapshotReusable -SnapshotDir $currentSnapshot -SourceKind $sourceKind -SourcePath $sourcePath -SourceVersion $SrcVersion) {
            $payloadTmp = Join-Path $InstallDir ("payload-dir.$PID.tmp")
            [System.IO.File]::WriteAllText($payloadTmp, $currentSnapshot, $utf8NoBom)
            Move-Item -LiteralPath $payloadTmp -Destination (Join-Path $InstallDir 'payload-dir') -Force
            Write-Binstubs
            Write-Ok "Stamped: reused snapshot $currentSnapshot"
            return
        }

        $snapDir = Join-Path (Join-Path $InstallDir 'snapshots') ($SrcVersion + '-' + (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfff') + "-$PID")
        $snapTmp = "$snapDir.tmp-$PID"
        if (Test-Path $snapTmp) { Remove-Item $snapTmp -Recurse -Force -ErrorAction SilentlyContinue }
        New-Item -ItemType Directory -Path $snapTmp -Force | Out-Null
        # Copy everything needed to `uv pip install .` from the slot (src, libs,
        # scripts, pyproject, plugin.json, hooks, README); skip VCS/build/test junk.
        $exclude = @('.git', '__pycache__', '.venv', 'node_modules', 'build', 'dist', '.pytest_cache', '.mypy_cache', 'tests')
        Get-ChildItem -LiteralPath $PluginDir -Force | Where-Object { $exclude -notcontains $_.Name } | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $snapTmp $_.Name) -Recurse -Force
        }
        Materialize-SnapshotLibs -SnapshotDir $snapTmp
        Materialize-SnapshotInstallerEngine -SnapshotDir $snapTmp
        $currentSnapshot = Get-CurrentSnapshotPath
        if (Test-PublishedSnapshotIsNewer -SnapshotDir $currentSnapshot -SourcePath $sourcePath -SourceVersion $SrcVersion) {
            Remove-Item -LiteralPath $snapTmp -Recurse -Force -ErrorAction SilentlyContinue
            Write-Skip "Published snapshot $currentSnapshot is newer than $SrcVersion; skipping older snapshot publication"
            Write-Binstubs
            return
        }
        Move-Item -LiteralPath $snapTmp -Destination $snapDir -Force
        [System.IO.File]::WriteAllText((Get-SnapshotSourceMarkerPath -SnapshotDir $snapDir), $sourcePath, $utf8NoBom)
        [System.IO.File]::WriteAllText((Get-SnapshotVersionMarkerPath -SnapshotDir $snapDir), $SrcVersion, $utf8NoBom)
        $payloadTmp = Join-Path $InstallDir ("payload-dir.$PID.tmp")
        [System.IO.File]::WriteAllText($payloadTmp, $snapDir, $utf8NoBom)
        Move-Item -LiteralPath $payloadTmp -Destination (Join-Path $InstallDir 'payload-dir') -Force
        $versionTmp = Join-Path $InstallDir ("stamped-version.$PID.tmp")
        [System.IO.File]::WriteAllText($versionTmp, $SrcVersion, $utf8NoBom)
        Move-Item -LiteralPath $versionTmp -Destination (Join-Path $InstallDir 'stamped-version') -Force
        Write-Ok "Snapshot: $snapDir"
        Write-Binstubs
        Write-Ok 'Stamped: agent-ssh binstub on PATH; runtime provisions on first use.'
    } finally {
        if ($stampPublicationLock) { $stampPublicationLock.Dispose() }
    }
}

if ($Action -eq 'stamp') { Invoke-Stamp; exit 0 }

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
    if ($DryRun) {
        Write-Host '(dry run -- nothing will be changed)' -ForegroundColor Yellow
        $ps1 = Join-Path $LocalBin 'agent-ssh.ps1'
        $cmd = Join-Path $LocalBin 'agent-ssh.cmd'
        if (Test-Path $ps1) { Write-Host "[dry-run] would remove binstub: $ps1" }
        if (Test-Path $cmd) { Write-Host "[dry-run] would remove binstub: $cmd" }
        if (Test-Path $InstallDir) { Write-Host "[dry-run] would remove (config + DB + venv): $InstallDir" }
        Write-Host 'agent-ssh uninstall dry run complete -- nothing was changed' -ForegroundColor Yellow
        exit 0
    }
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

$pythonCmd = Resolve-PythonCommand
if (-not $pythonCmd) {
    Write-Fail 'Python not found on PATH (need 3.10+)'
    exit 1
}
Write-Ok "Python: $pythonCmd"

Ensure-UvIndex
$uvPath = Ensure-Uv -InstallRoot $InstallDir

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
foreach ($h in @('bootstrap-check.ps1', 'bootstrap-check.sh', 'bootstrap-killswitch-guard.ps1', 'bootstrap-killswitch-guard.sh', 'emit-mesh-pointer.ps1', 'emit-mesh-pointer.sh')) {
    $hSrc = Join-Path $PSScriptRoot $h
    if (Test-Path $hSrc) { Copy-Item $hSrc (Join-Path $BinHookDir $h) -Force }
}
Write-Ok "Session-start hook: $BinHookDir\bootstrap-check.ps1"

if ($Force -or -not (Test-Path $VenvPython)) {
    Invoke-VersionedSlotClean
    if ($uvPath) {
        if (-not (New-SignedVenv -VenvDir $VenvDir -VenvPython $VenvPython -PythonVersion '3.10' -UvCommand $uvPath -RequireSignedBase ($env:OS -eq 'Windows_NT') -AllowExisting $true)) {
            Write-Step 'uv/signed-Python venv creation failed -- falling back to system python -m venv'
            $fallback = Invoke-NativeCapture { & $pythonCmd -m venv $VenvDir }
            if ($fallback.ExitCode -ne 0) {
                if ($fallback.Output) { Write-Host ($fallback.Output | Out-String) }
                Write-Fail "Failed to create venv at $VenvDir"
                exit 1
            }
        }
    } else {
        Write-Step 'uv unavailable -- falling back to python -m venv'
        $signedBase = Get-SignedBasePython
        if ($signedBase) {
            & $signedBase -m venv --copies $VenvDir 2>&1 | Out-Null
        } else {
            & $pythonCmd -m venv $VenvDir 2>&1 | Out-Null
        }
    }
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
$agentProcutilDir = Resolve-AgentProcutil
if (-not $agentProcutilDir) {
    Write-Fail 'Cannot locate agent-procutil library'
    exit 1
}
$sshManagerDir = Resolve-SshManager
if (-not $sshManagerDir) {
    Write-Fail 'Cannot locate ssh-manager library'
    exit 1
}
$venueCopilotDir = Resolve-VenueCopilot
if (-not $venueCopilotDir) {
    Write-Fail 'Cannot locate venue-copilot library'
    exit 1
}
$zddDir = Resolve-Zdd
if (-not $zddDir) {
    Write-Fail 'Cannot locate zdd library'
    exit 1
}
$remoteLoginShellDir = Resolve-RemoteLoginShell
if (-not $remoteLoginShellDir) {
    Write-Fail 'Cannot locate remote-login-shell library'
    exit 1
}
$fleetContractsDir = Resolve-FleetContracts
if (-not $fleetContractsDir) {
    Write-Fail 'Cannot locate fleet-contracts library'
    exit 1
}
$vendoredDependencies = @(
    $agentProcutilDir,
    (Join-Path $PluginDir 'libs\dropin-registry'),
    $sshManagerDir,
    $venueCopilotDir,
    $zddDir,
    $remoteLoginShellDir,
    $fleetContractsDir
)
$pkgInstalled = Install-AgentSshPackage `
    -Python $VenvPython `
    -Source $PluginDir `
    -Dependencies $vendoredDependencies `
    -UvCommand $uvPath
$ErrorActionPreference = $prevEAP
if (-not $pkgInstalled) {
    Write-Fail 'Failed to install agent-ssh package into venv'
    exit 1
}
Remove-ConsoleTrampolines -VenvDir $VenvDir
Write-Ok 'Package installed: agent-ssh'

# Versioned layout (#581): health-gate the slot + swap the `.venv` junction.
if (-not (Invoke-VersionedActivate)) { exit 1 }

Write-Binstubs

$sourcePath = if ($env:COPILOT_PLUGIN_STAGED_FROM) { $env:COPILOT_PLUGIN_STAGED_FROM } else { $PluginDir }
$snapshotSourceMarker = Get-SnapshotSourceMarkerPath -SnapshotDir $PluginDir
if (Test-Path $snapshotSourceMarker) {
    $sourcePath = (Get-Content -LiteralPath $snapshotSourceMarker -Raw).Trim()
}
Write-DeployManifest `
    -Service 'agent-ssh' `
    -Plugin 'agent-ssh' `
    -InstallPath $InstallDir `
    -PluginPath $PluginDir `
    -VenvPath $LinkDir `
    -GetSourceKind ${function:Get-SourceKind} `
    -GetGitInfo ${function:Get-GitInfo} `
    -SourcePathOverride $sourcePath `
    -VersionOverride $SrcVersion `
    -PayloadHash (Get-PayloadHash)

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
    $currentUserPath = Get-CopilotPersistentEnvironmentVariable -Name 'PATH' -Target 'User'
    if (-not ($currentUserPath -split ';' | Where-Object { $_ -eq $LocalBin })) {
        Set-CopilotPersistentEnvironmentVariable -Name 'PATH' -Value "$LocalBin;$currentUserPath" -Target 'User'
        $env:PATH = "$LocalBin;$env:PATH"
        Write-Ok "PATH: Added $LocalBin to User PATH"
    }
}

Write-Host ''
Write-Host '=== agent-ssh install complete ===' -ForegroundColor Cyan
Write-Host '  Try: agent-ssh version' -ForegroundColor DarkGray
exit 0
