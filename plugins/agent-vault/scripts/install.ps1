<#
.SYNOPSIS
    agent-vault installer / lifecycle manager. PS5+ compatible.

.DESCRIPTION
    Canonical installer for the agent-vault runtime. Creates the runtime at
    ~/.agent-vault/ (.venv + state), deploys ~/.local/bin/agent-vault.ps1 and
    agent-vault.cmd binstubs. Scheduling is opt-in through the native global
    configuration's windows_scheduled_task policy; on-demand startup remains
    available without Task Scheduler.

.PARAMETER Action
    install (default) | update | status | start | stop | uninstall | schedule.
    schedule reconciles native task policy; absent removes only the owned task.

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
    [ValidateSet('install', 'update', 'status', 'start', 'stop', 'uninstall', 'stamp', 'provision', 'schedule')]
    [string]$Action = 'install',

    [Alias('install-dir')]
    [string]$InstallDir,

    [Alias('no-service')]
    [switch]$NoService,

    [switch]$Purge,
    [switch]$Force,

    [ValidateSet('base', 'ssh')]
    [string]$Profile,

    # Preview mode for the 'uninstall' action: print what WOULD be removed
    # without touching the filesystem or scheduled task.
    [switch]$DryRun,
    [string]$WheelProfile,
    [string]$WheelDirectory,
    [string]$WheelPython,
    [string]$WheelUv
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

if ($env:COPILOT_EXTENSIONS_HUMAN_UTF8 -eq '1') {
    if (-not [IO.File]::Exists((Join-Path $PSScriptRoot 'installer-human-output.ps1'))) {
        throw 'Required installer human-output initializer is unavailable.'
    }
    . (Join-Path $PSScriptRoot 'installer-human-output.ps1')
}

if ($WheelProfile -or $WheelDirectory -or $WheelPython -or $WheelUv) {
    if (-not $WheelProfile -or -not $WheelDirectory -or -not $WheelPython -or -not $WheelUv -or
        $Action -notin @('install', 'update', 'provision')) {
        throw 'Closed wheels require profile, directory, Python and UV for install, update or provision'
    }
}
$NativeWheelSelection = $null


if (-not $Profile -and $env:AGENT_VAULT_INSTALL_PROFILE) {
    if ($env:AGENT_VAULT_INSTALL_PROFILE -cnotin @('base', 'ssh')) {
        throw 'Unsupported Vault installation profile'
    }
    $Profile = $env:AGENT_VAULT_INSTALL_PROFILE
}
if ($Profile) {
    if ($Profile -cnotin @('base', 'ssh')) { throw 'Unsupported Vault installation profile' }
    if ($Action -notin @('install', 'update', 'stamp', 'provision')) {
        throw 'Vault dependency selection requires install, update, stamp or provision'
    }
    $env:AGENT_VAULT_INSTALL_PROFILE = $Profile
}

# Retain the native selector before activation, driver handoff or self-staging
# can move the filesystem cwd. This also applies to task-only schedule actions.
if ($env:AGENT_VAULT_CONFIG) {
    $vaultConfigLocation = Get-Location
    if ($vaultConfigLocation.Provider.Name -cne 'FileSystem') {
        throw 'Vault configuration requires a filesystem invocation directory'
    }
    $vaultConfigProvider = $null
    $vaultConfigDrive = $null
    $vaultConfigPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath(
        $env:AGENT_VAULT_CONFIG, [ref]$vaultConfigProvider, [ref]$vaultConfigDrive
    )
    if ($vaultConfigProvider.Name -cne 'FileSystem') {
        throw 'Vault configuration selector must name a filesystem path'
    }
    $env:AGENT_VAULT_CONFIG = $vaultConfigPath
}

if ($Action -in @('install', 'update', 'stamp', 'provision', 'start', 'schedule')) {
    if ((Get-Location).Provider.Name -cne 'FileSystem') {
        throw 'Vault project selection requires a filesystem invocation directory'
    }
    $activationProject = if ($env:COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT) {
        $env:COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT
    } else { (Get-Location).ProviderPath }
    $activationProject = (Resolve-Path -LiteralPath $activationProject -ErrorAction Stop).ProviderPath
    if (-not (Test-Path -LiteralPath $activationProject -PathType Container)) {
        throw 'Vault project selection must name an available directory'
    }
    $env:COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT = $activationProject
}

if ($Action -in @('install', 'update', 'stamp', 'provision', 'start')) {
    . (Join-Path $PSScriptRoot 'runtime-activation.ps1')
    $activation = Get-VaultRuntimeActivation -Project $activationProject
    if ($activation.state -ceq 'configuration-empty') {
        Write-Output "[SKIP] $($activation.detail)"
        exit 0
    }
}

# === install-contract:v4 payload-copy -- keep byte-identical across plugins ===
function Copy-InstallerStageDirectory {
    param([string]$Source, [string]$Destination)
    # Retain provider semantics for links; ordinary payload files use native copying.
    if ([IO.File]::GetAttributes($Source) -band [IO.FileAttributes]::ReparsePoint) {
        Copy-Item -LiteralPath $Source -Destination $Destination -Recurse -Force
        return
    }
    [void][IO.Directory]::CreateDirectory($Destination)
    foreach ($file in [IO.Directory]::EnumerateFiles($Source)) {
        $target = [IO.Path]::Combine($Destination, [IO.Path]::GetFileName($file))
        if ([IO.File]::GetAttributes($file) -band [IO.FileAttributes]::ReparsePoint) {
            Copy-Item -LiteralPath $file -Destination $target -Force
        } else {
            [IO.File]::Copy($file, $target, $true)
        }
    }
    foreach ($directory in [IO.Directory]::EnumerateDirectories($Source)) {
        Copy-InstallerStageDirectory $directory ([IO.Path]::Combine($Destination, [IO.Path]::GetFileName($directory)))
    }
}
# === end install-contract:v4 payload-copy ===
. (Join-Path $PSScriptRoot 'versioned_runtime_native.ps1')

if ($env:OS -eq 'Windows_NT' -and
    $env:COPILOT_EXTENSIONS_TEST_CONTAINED -ne '1' -and
    $Action -in @('install', 'update', 'provision', 'uninstall') -and
    -not ($Action -eq 'uninstall' -and $DryRun)) {
    $driverPayload = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
    $driverRoot = if ($InstallDir) { [IO.Path]::GetFullPath($InstallDir) }
        else { Join-Path $env:USERPROFILE '.agent-vault' }
    $driverOptions = @()
    foreach ($name in @('NoService', 'Purge', 'Force', 'DryRun')) {
        if ($PSBoundParameters.ContainsKey($name) -and [bool]$PSBoundParameters[$name]) {
            $driverOptions += $name
        }
    }
    $driverEntry = Enter-NativeInstallerDriver -Payload $driverPayload -Root $driverRoot `
        -Plugin 'agent-vault' -Action $Action -Options $driverOptions `
        -WheelProfile $WheelProfile -WheelDirectory $WheelDirectory -WheelPython $WheelPython -WheelUv $WheelUv
    if (-not $driverEntry.Participant -or $driverEntry.ExitCode -ne 0) { exit $driverEntry.ExitCode }
    $driverPython = $driverEntry.Python
    $driverArguments = $driverEntry.Arguments
}

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
if (-not $env:COPILOT_PLUGIN_INSTALL_STAGED -or
    (($PSScriptRoot -replace '\\', '/') -match '/\.copilot/installed-plugins/')) {
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
                Set-Location -LiteralPath $env:USERPROFILE
                [System.IO.Directory]::SetCurrentDirectory($env:USERPROFILE)
                $__selfStageRoot = Join-Path (Join-Path $env:USERPROFILE ".$__selfStageName") '.install-stage'
                $__selfStageDir = Join-Path $__selfStageRoot ((Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfff') + "-$PID")
                New-Item -ItemType Directory -Force -Path $__selfStageDir | Out-Null
                $__selfStagedPayload = Join-Path $__selfStageDir (Split-Path -Leaf $__selfStagePayload)
                Copy-InstallerStageDirectory $__selfStagePayload $__selfStagedPayload
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
                $__selfStageArgs = (@('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $__selfStagedEntry) + $__selfStageFwd |
                    ForEach-Object {
                        '"' + ([regex]::Replace(
                            [regex]::Replace([string]$_, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1'
                        )) + '"'
                    }) -join ' '
                $__wdChild = Start-Process -FilePath $__selfStageExe -PassThru -NoNewWindow `
                    -WorkingDirectory $env:USERPROFILE `
                    -ArgumentList $__selfStageArgs
                # Windows PowerShell must retain the handle before waiting to preserve ExitCode.
                $null = $__wdChild.Handle
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
            } else {
                throw 'self-stage requires a plugin identity before leaving the payload'
            }
        }
    } catch {
        [Console]::Error.WriteLine("self-stage failed; refusing to run from the replaceable payload: $_")
        exit 1
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
            working_dir  = [System.IO.Directory]::GetCurrentDirectory()
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


if ($env:AGENT_VAULT_ALLOW_DOWNGRADE -eq '1') { $Force = $true }

function Write-Ok      { param([string]$Msg) Write-Host "  [OK]   $Msg" -ForegroundColor Green }
function Write-Skip    { param([string]$Msg) Write-Host "  [SKIP] $Msg" -ForegroundColor Cyan }
function Write-Fail    { param([string]$Msg) Write-Host "  [FAIL] $Msg" -ForegroundColor Red }
function Write-Warn    { param([string]$Msg) Write-Host "  [WARN] $Msg" -ForegroundColor Yellow }
function Write-Step    { param([string]$Msg) Write-Host "  ...    $Msg" -ForegroundColor DarkGray }

. (Join-Path $PSScriptRoot '..\..\..\libs\installer-engine\installer-engine.ps1')

$PluginDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PkgSrcDir = Join-Path $PluginDir 'src\agent_vault'

if (-not $InstallDir) {
    $InstallDir = Join-Path $env:USERPROFILE '.agent-vault'
}
$InstallDir = [IO.Path]::GetFullPath($InstallDir)
$legacyInstallDir = [IO.Path]::GetFullPath((Join-Path $env:USERPROFILE '.agent-vault'))
$serviceSuffix = if ([StringComparer]::OrdinalIgnoreCase.Equals($InstallDir, $legacyInstallDir)) {
    ''
} else {
    $serviceSha = [Security.Cryptography.SHA256]::Create()
    try {
        ([BitConverter]::ToString(
            $serviceSha.ComputeHash(
                [Text.Encoding]::UTF8.GetBytes($InstallDir.ToLowerInvariant())
            )
        )).Replace('-', '').Substring(0, 12).ToLowerInvariant()
    } finally {
        $serviceSha.Dispose()
    }
}
$installationId = [string]$env:AGENT_VAULT_INSTALLATION_ID
$RunDir = Join-Path $InstallDir 'run'
$SocketPath = Join-Path $RunDir 'agent-vault.sock'
$PipePath = if ($serviceSuffix) { "\\.\pipe\agent-vault-$serviceSuffix" } else { "\\.\pipe\agent-vault" }
$PidFile = Join-Path $RunDir 'agent-vault-service.pid'
$LogFile = Join-Path (Join-Path $InstallDir 'logs') 'agent-vault-service.log'
$VenvDir     = Join-Path $InstallDir '.venv'
$LocalBin    = Join-Path $env:USERPROFILE '.local\bin'
$VenvPython  = Join-Path $VenvDir 'Scripts\python.exe'
$BinstubPs1  = Join-Path $LocalBin 'agent-vault.ps1'
$BinstubCmd  = Join-Path $LocalBin 'agent-vault.cmd'
$Binstub     = $BinstubPs1
$TaskName    = if ($serviceSuffix) { "AgentVault-$serviceSuffix" } else { 'AgentVault' }
$TaskLauncher = Join-Path $InstallDir 'service.ps1'
$utf8NoBom   = New-Object System.Text.UTF8Encoding $false

# === install-contract:v3 versioned-venv (agent-vault: .venv-as-junction) ===
# Immutable per-version runtime (#581). Build the venv into versions/<version>
# and make the historical `.venv` path a junction (Windows) / symlink (POSIX)
# into it, so the binstubs, scheduled task, and deploy-manifest -- all of which
# reference `.venv` -- resolve through the link unchanged. LinkDir/LinkPython is
# the stable `.venv` path (runtime-facing, never a versions/<v> absolute a `gc`
# could remove); VenvDir/VenvPython is redirected to the versions/<v> slot
# (build + health-gate). ALWAYS versioned -- the env opt-out
# (COPILOT_EXT_NO_VERSIONED / AGENT_VAULT_VERSIONED) and the legacy in-place fork
# are retired; the code below reads neither var. scripts/versioned_runtime.py owns
# the swap + legacy migration + gc.
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
        $VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
        $LinkDir = $VenvDir
        $LinkPython = $VenvPython
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
    Assert-SourceRuntime
    if ($script:SourceRuntimeIdentity) { Publish-SourceSnapshot | Out-Null }
    Assert-SourceRuntime
    if (-not $VersionedRuntime) { return $true }
    $legacyVenv = Join-Path $InstallDir '.venv'
    if ((Test-Path $legacyVenv) -and -not (Test-VenvIsLink $legacyVenv)) {
        Write-Step 'Releasing legacy .venv for versioned migration (stopping daemon)...'
        Invoke-Stop | Out-Null
    }
    $vr = Join-Path $PSScriptRoot 'versioned_runtime.py'
    $py = if (Test-Path $VenvPython) { $VenvPython } else { $LinkPython }
    & $py $vr --root $InstallDir --link-name '.venv' activate $SrcVersion --no-link 2>&1 |
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
    $py = if ($script:RuntimeProfile) { Get-BootstrapPython } elseif (Test-Path $LinkPython) { $LinkPython } elseif (Test-Path $VenvPython) { $VenvPython } else { $null }
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

function Test-UvConfiguredIndex {
    $configPaths = if ($env:UV_CONFIG_FILE) {
        @($env:UV_CONFIG_FILE)
    } else {
        $paths = @()
        $roaming = [Environment]::GetFolderPath('ApplicationData')
        if ($roaming) {
            $paths += Join-Path $roaming 'uv\uv.toml'
        }
        if ($env:PROGRAMDATA) {
            $paths += Join-Path $env:PROGRAMDATA 'uv\uv.toml'
        }
        $paths
    }
    foreach ($configPath in $configPaths) {
        if (-not $configPath -or -not (Test-Path -LiteralPath $configPath)) { continue }
        $inIndex = $false
        foreach ($line in Get-Content -LiteralPath $configPath) {
            $value = ($line -replace '\s+#.*$', '').Trim()
            if ($value -match '^index-url\s*=') { return $true }
            if ($value -match '^\[\[index\]\]$') {
                $inIndex = $true
                continue
            }
            if ($value -match '^\[') { $inIndex = $false }
            if ($inIndex -and $value -match '^default\s*=\s*true$') {
                return $true
            }
        }
    }
    return $false
}

function Ensure-UvIndex {
    if ($env:UV_DEFAULT_INDEX -or $env:UV_INDEX_URL -or (Test-UvConfiguredIndex)) { return }
    $idx = ''
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        if (Get-Command pip -CommandType Application -ErrorAction SilentlyContinue) {
            $out = & pip config get global.index-url 2>$null
            if ($LASTEXITCODE -eq 0) { $idx = ($out | Out-String).Trim() }
        }
        if (-not $idx) {
            if (Get-Command py -CommandType Application -ErrorAction SilentlyContinue) {
                $out = & py -3 -m pip config get global.index-url 2>$null
                if ($LASTEXITCODE -eq 0) { $idx = ($out | Out-String).Trim() }
            } elseif (Get-Command python -CommandType Application -ErrorAction SilentlyContinue) {
                $out = & python -m pip config get global.index-url 2>$null
                if ($LASTEXITCODE -eq 0) { $idx = ($out | Out-String).Trim() }
            }
        }
    } finally {
        $ErrorActionPreference = $prevEAP
    }
    if (-not $idx) {
        $configPaths = @($env:PIP_CONFIG_FILE)
        $roaming = [Environment]::GetFolderPath('ApplicationData')
        if ($roaming) {
            $configPaths += Join-Path $roaming 'pip\pip.ini'
        }
        if ($env:PROGRAMDATA) {
            $configPaths += Join-Path $env:PROGRAMDATA 'pip\pip.ini'
        }
        foreach ($configPath in $configPaths) {
            if (-not $configPath -or -not (Test-Path -LiteralPath $configPath)) { continue }
            $match = Select-String -LiteralPath $configPath `
                -Pattern '^\s*index-url\s*=\s*(\S+)\s*$' |
                Select-Object -First 1
            if ($match) {
                $idx = $match.Matches[0].Groups[1].Value
                break
            }
        }
    }
    if ($idx) {
        $env:UV_DEFAULT_INDEX = $idx
        Write-Step 'uv index derived from pip config (governed-feed bridge)'
    }
}

# === install-contract:v3 source-kind -- keep byte-identical across plugins ===
# A runtime footprint's source is inferred from where the installer runs.
# Vendored under the Copilot CLI installed-plugins dir => marketplace;
# anything else (a git checkout) => local. `update` re-installs from whatever
# the recorded footprint is, because the same installer is invoked from the
# same place.
# === install-contract:v4 marker/toss helpers (#935) ===
function Test-VaultRuntimeProfilePresent {
    try {
        [void][IO.File]::GetAttributes((Join-Path $InstallDir 'runtime-profile.json'))
        return $true
    } catch [IO.FileNotFoundException] {
        return $false
    } catch [IO.DirectoryNotFoundException] {
        return $false
    }
}

function Get-BootstrapPython {
    if ($WheelProfile) { return $WheelPython }
    if ($script:RuntimeProfilePython) { return $script:RuntimeProfilePython }
    <# Construction supervision must run outside the candidate being built.
       Probe a native launcher, then real PATH interpreters (not Store aliases). #>
    $launcher = Get-Command py -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($launcher -and $launcher.Source -notmatch '\.(cmd|bat)$') {
        $exe = (Invoke-Hidden -FilePath $launcher.Source -ArgList @('-3', '-c', 'import sys; print(sys.executable)') | Out-String).Trim()
        if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Path $exe)) { return $exe }
    }
    foreach ($cand in 'python3', 'python') {
        $c = Get-Command $cand -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($c -and $c.Source -notmatch 'WindowsApps') { return $c.Source }
    }
    return $null
}

function Get-PayloadHash {
    if ($NativeWheelSelection) {
        return (Get-NativeWheelSelection -Python $WheelPython `
            -Helper (Join-Path $PSScriptRoot 'install_source.py') -Profile $WheelProfile `
            -Directory $WheelDirectory -Payload $PluginDir -Plugin 'agent-vault' `
            -Expected $NativeWheelSelection).payload_hash
    }
    if ($script:RuntimeProfile) {
        $py = Get-BootstrapPython
        $result = Invoke-Hidden -FilePath $py -ArgList @(
            '-I', '-B', (Get-RuntimeProfileHelper),
            '--root', $InstallDir, 'fingerprint'
        )
        if ($LASTEXITCODE -ne 0 -or $result.Trim() -cnotmatch '^[0-9a-f]{64}$') {
            throw 'Vault dependency-profile fingerprint is unavailable'
        }
        return $result.Trim()
    }
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
    Assert-SourceRuntime
    if (-not $VersionedRuntime) { throw 'Construction requires a versioned runtime candidate' }
    $vr = Join-Path $PSScriptRoot 'versioned_runtime.py'
    $py = Get-BootstrapPython
    if (-not $py) { throw 'Construction requires a bootstrap Python interpreter' }
    if (-not (Get-Command Start-RuntimeConstruction -ErrorAction SilentlyContinue)) {
        $helpers = (Invoke-Hidden -FilePath $py -ArgList @('-I', $vr, '--root', $InstallDir, 'construction-shell', '--shell', 'ps1') | Out-String)
        if ($LASTEXITCODE -ne 0) { throw 'Canonical construction adapter is unavailable' }
        Invoke-Expression $helpers
    }
    if ($script:RuntimeProfile) { $vr = Get-RuntimeProfileHelper }
    $wheelOptions = Get-NativeWheelConstructionOptions -Profile $WheelProfile `
        -Directory $WheelDirectory -Payload $PluginDir -Plugin 'agent-vault' `
        -Uv $WheelUv -HealthModule 'agent_vault' -Repair:$Force
    $script:RuntimeConstructionReuse = Start-RuntimeConstruction `
        -Python $py -Helper $vr -Root $InstallDir -Version $SrcVersion `
        -PayloadHash (Get-PayloadHash) -LinkName (Split-Path -Leaf $LinkDir) @wheelOptions
    if ($NativeWheelSelection) {
        Set-NativeWheelRuntimeVersion -Root $InstallDir -Version $script:RuntimeConstructionSession.Version
    }
    Assert-SourceRuntime
}

function Invoke-VersionedMarkComplete {
    param([string]$PayloadHash = '')
    Assert-SourceRuntime
    $actualHash = Get-PayloadHash
    if ($PayloadHash -and $PayloadHash -ne $actualHash) {
        throw 'Payload changed during construction; completion refused'
    }
    if ($script:SourceRuntimeIdentity -and -not $script:RuntimeProfile) {
        [void](Invoke-Hidden -FilePath (Get-BootstrapPython) -ArgList @(
            '-I', '-B', (Join-Path $ScriptDir 'runtime_source_target.py'),
            '--root', $InstallDir, '--record-slot', $SrcVersion, '--payload-hash', $actualHash
        ))
        if ($LASTEXITCODE -ne 0) { throw 'Vault source-profile completion evidence failed' }
    }
    Complete-RuntimeConstruction -PayloadHash $actualHash -RetainHelpers
    return $true
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
    param([string]$SnapshotSourcePath)
    foreach ($name in @('resolve-runtime.ps1', 'resolve-runtime.sh')) {
        if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot $name) -PathType Leaf)) {
            throw "Required Vault runtime helper is unavailable: $name"
        }
    }
    $previousErrorAction = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Stop'
        Write-SimpleBinstub `
        -CommandName 'agent-vault' `
        -ModuleName 'agent_vault' `
        -RuntimeRoot $InstallDir `
        -LocalBin $LocalBin `
        -InstallBinDir (Join-Path $InstallDir 'bin') `
        -SnapshotInstallerPath 'scripts\install.ps1' `
        -NoSelfProvisionEnv 'AGENT_VAULT_NO_SELFPROVISION' `
        -ResolverPs1Source (Join-Path $PSScriptRoot 'resolve-runtime.ps1') `
        -ResolverShSource (Join-Path $PSScriptRoot 'resolve-runtime.sh') `
        -SnapshotSourcePath $SnapshotSourcePath
    } catch {
        [Console]::Error.WriteLine("Vault binstub publication failed: $($_.Exception.Message)")
        throw
    } finally {
        $ErrorActionPreference = $previousErrorAction
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
    foreach ($lib in @('configuration-layers', 'zdd', 'agent-procutil', 'single-instance-lease', 'update-driver')) {
        $source = Join-Path $PluginDir "libs\$lib"
        if (-not (Test-Path (Join-Path $source 'pyproject.toml'))) {
            $source = Join-Path $PluginDir "..\..\libs\$lib"
        }
        if (-not (Test-Path (Join-Path $source 'pyproject.toml'))) { continue }
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

function Copy-VaultSnapshotInputs {
    param([Parameter(Mandatory)][string]$Destination)
    [void][IO.Directory]::CreateDirectory($Destination)
    $exclude = @('.git', '__pycache__', '.venv', 'node_modules', 'build', 'dist', '.pytest_cache', '.mypy_cache', 'tests', 'snapshot-provenance.json')
    Get-ChildItem -LiteralPath $PluginDir -Force | Where-Object { $exclude -notcontains $_.Name } | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $Destination $_.Name) -Recurse -Force
    }
    Materialize-SnapshotLibs -SnapshotDir $Destination
    Materialize-SnapshotInstallerEngine -SnapshotDir $Destination
}

function Get-RuntimeProfileHelper {
    if ($script:RuntimeProfileHelper) { return $script:RuntimeProfileHelper }
    $sourceSnapshot = $null
    if ($script:SourceRuntimeIdentity) {
        $sourceSnapshot = Publish-SourceSnapshot
    }
    if ($sourceSnapshot -and -not (Test-Path -LiteralPath (Join-Path $sourceSnapshot 'snapshot-provenance.json'))) {
        $snapshot = $sourceSnapshot
    } else {
        $snapshot = Join-Path $InstallDir ('.profile-inputs-' + [Guid]::NewGuid().ToString('N'))
        $directory = [IO.Directory]::CreateDirectory($snapshot)
        $script:RuntimeProfileInputs = @{ Path = $directory.FullName; Created = $directory.CreationTimeUtc }
        if ($sourceSnapshot) {
            Get-ChildItem -LiteralPath $sourceSnapshot -Force | Where-Object {
                $_.Name -cne 'snapshot-provenance.json'
            } | ForEach-Object {
                Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $snapshot $_.Name) -Recurse -Force
            }
        } else {
            Copy-VaultSnapshotInputs -Destination $snapshot
        }
    }
    $script:RuntimeProfileHelper = Join-Path $snapshot 'scripts\runtime_profile.py'
    return $script:RuntimeProfileHelper
}

function Remove-RuntimeProfileInputs {
    if (-not $script:RuntimeProfileInputs) { return }
    $path = $script:RuntimeProfileInputs.Path
    $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
    if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        $item.CreationTimeUtc -ne $script:RuntimeProfileInputs.Created) {
        throw 'Vault profile input custody changed; preserving construction inputs'
    }
    Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction Stop
    $script:RuntimeProfileInputs = $null
}

function Test-RuntimeHealthy {
    $nativeError = ''
    [void](Invoke-Hidden -FilePath $VenvPython -ArgList @('-I', '-m', 'agent_vault', '--help') -ErrorOutput ([ref]$nativeError))
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Isolated agent_vault CLI health probe failed: $nativeError"
        return $false
    }
    return $true
}

function Write-VaultDeployManifest {
    $manifestSource = ''
    $gitInfo = ${function:Get-GitInfo}
    if ($script:SourceRuntimeIdentity) {
        if (-not $script:SourceRuntimeSnapshot) { throw 'Source manifest requires a committed immutable snapshot' }
        $PluginDir = $script:SourceRuntimeSnapshot
        $ScriptDir = Join-Path $PluginDir 'scripts'
        $manifestSource = $script:SourceManifestOrigin
        $gitInfo = { param($Path) $script:SourceManifestGitInfo }
    }
    Assert-SourceRuntime
    Write-DeployManifest -Service 'agent-vault' -Plugin 'agent-vault' -InstallPath $InstallDir -PluginPath $PluginDir -VenvPath $LinkDir -GetSourceKind ${function:Get-SourceKind} -GetGitInfo $gitInfo -SourcePathOverride $manifestSource -PayloadHash (Get-PayloadHash) -VersionOverride $PackageVersion -AdditionalFields (Get-SourceRuntimeFields)
}

function Install-Runtime {
    Assert-SourceRuntime
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

    if (-not $WheelProfile) { Ensure-UvIndex }
    $uvPath = if ($WheelProfile) { $WheelUv } else { Ensure-Uv -InstallRoot $InstallDir }
    if (-not $uvPath) {
        Write-Fail 'uv is required but could not be resolved or acquired'
        exit 1
    }
    if ($script:RuntimeProfile) { $env:AGENT_VAULT_INSTALL_UV = $uvPath }

    foreach ($dir in @($InstallDir, $LocalBin)) {
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    }
    Write-Ok "Directories: $InstallDir"

    $prevVersion = Get-VersionedCurrent
    [void](Invoke-VersionedSlotClean)
    if ($script:RuntimeConstructionReuse) {
        if (-not (Invoke-VersionedMarkComplete)) { throw 'Retained runtime completion failed' }
        if ($script:SourceRuntimeIdentity -or $script:RuntimeProfile) {
            if (-not (Test-RuntimeHealthy)) { throw 'Retained runtime slot failed its health gate; not activating' }
            Assert-SourceRuntime
            Write-Binstubs
            Assert-SourceRuntime
            $selected = Join-Path $InstallDir 'current-version'
            if (-not (Test-Path -LiteralPath $selected) -or
                ([IO.File]::ReadAllText($selected)).Trim() -ne $SrcVersion) {
                if (-not (Invoke-VersionedActivate)) { throw 'Source runtime activation failed' }
            }
            if ($script:SourceRuntimeIdentity) { Publish-SourcePayload }
            Write-VaultDeployManifest
            Complete-RuntimeInstallation -KeepPrev $prevVersion
        }
        return
    }

    Invoke-VersionedSlotClean
    if (-not (New-SignedVenv -VenvDir $VenvDir -VenvPython $VenvPython -PythonVersion '3.10' -UvCommand $uvPath -RequireSignedBase ($env:OS -eq 'Windows_NT') -AllowExisting $true -WithoutPip)) {
        Write-Fail "Failed to create venv at $VenvDir"
        exit 1
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
    # Every `[tool.uv.sources]` workspace path dep -- both the zdd
    # canonical-on-dev / materialized-on-release reference and the other
    # `uv`-editable canonical references (`agent-procutil`,
    # `agent-single-instance-lease`: vendor-pointer-generalization effort,
    # Phase 1) -- needs an explicit pre-install here: `uv` resolves
    # `[tool.uv.sources]` fine when installing the main package directly,
    # but the non-uv (bare `pip install`) fallback below ignores that
    # table entirely.
    foreach ($lib in @('configuration-layers', 'zdd', 'agent-procutil', 'single-instance-lease')) {
        $libDir = Join-Path $PluginDir "libs\$lib"
        if (-not (Test-Path (Join-Path $libDir 'pyproject.toml'))) {
            $libDir = Join-Path $PluginDir "..\..\libs\$lib"
        }
        if (Test-Path (Join-Path $libDir 'pyproject.toml')) {
            if (Get-Command uv -ErrorAction SilentlyContinue) {
                $libResult = Invoke-UvPipInstallResilient -UvCommand 'uv' -Arguments @('--python', $VenvPython, "$libDir", '--quiet')
                $libOut = $libResult.Output
                $libExit = $libResult.ExitCode
            } else {
                $libOut = & $VenvPython -I (Join-Path $PSScriptRoot 'installer_dependency_policy.py') --uv uv -- --python $VenvPython --quiet "$libDir" 2>&1
                $libExit = $LASTEXITCODE
            }
            if ($libExit -ne 0) {
                $ErrorActionPreference = $prevEAP
                Write-Fail "$lib library install failed"
                if ($libOut) { Write-Host ($libOut | Out-String) }
                exit 1
            }
        }
    }
    if (Get-Command uv -ErrorAction SilentlyContinue) {
            $pkgResultObj = Invoke-UvPipInstallResilient -UvCommand 'uv' -PayloadDirToScrub $PluginDir -Arguments @('--python', $VenvPython, "$PluginDir", '--quiet')
            $pkgOut = $pkgResultObj.Output
        $pkgResult = $pkgResultObj.ExitCode
    } else {
            $pkgOut = & $VenvPython -I (Join-Path $PSScriptRoot 'installer_dependency_policy.py') --uv uv -- --python $VenvPython --quiet "$PluginDir" 2>&1
            $pkgResult = $LASTEXITCODE
    }
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
        if (-not (Test-RuntimeHealthy)) {
            Write-Fail "Fresh runtime slot failed its health gate (versions/$SrcVersion) -- not activating"
            exit 1
        }
    }

    Assert-SourceRuntime
    Write-Binstubs
    Assert-SourceRuntime
    if ($VersionedRuntime) {
        if (-not (Invoke-VersionedMarkComplete)) { exit 1 }
        if (-not (Invoke-VersionedActivate)) { exit 1 }
    }
    # Binstub + manifest resolve through the stable `.venv` link ($LinkPython /
    # $LinkDir), never a versions/<v> absolute a later `gc` could remove.
    if ($script:SourceRuntimeIdentity) { Publish-SourcePayload }
    else { Assert-SourceRuntime }
    Write-VaultDeployManifest

    Complete-RuntimeInstallation -KeepPrev $prevVersion
}

function Complete-RuntimeInstallation {
    param([string]$KeepPrev)
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $LinkPython -I -c 'import agent_vault' 2>$null
    $importOk = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prevEAP
    if ($importOk) { Write-Ok 'Verification: module imports successfully' }
    else { Write-Fail 'Verification: module import failed'; exit 1 }

    # Versioned layout: prune old slots, keeping current + the previous-good (the
    # slot a not-yet-restarted daemon may still run from) + live-pid-pinned.
    if ($VersionedRuntime) { Invoke-VersionedGc -KeepPrev $KeepPrev }

    if (Test-KeePassXCCli) {
        Write-Ok 'Prerequisite: keepassxc-cli found'
    } else {
        Write-Warn 'Prerequisite missing: keepassxc-cli (KeePassXC). agent-vault installed, but unlocks will fail until KeePassXC is present.'
    }

    $currentUserPath = Get-CopilotPersistentEnvironmentVariable -Name 'PATH' -Target 'User'
    if (-not ($currentUserPath -split ';' | Where-Object { $_ -eq $LocalBin })) {
        Set-CopilotPersistentEnvironmentVariable -Name 'PATH' -Value "$LocalBin;$currentUserPath" -Target 'User'
        $env:PATH = "$LocalBin;$env:PATH"
        Write-Ok "PATH: Added $LocalBin to User PATH"
    }
}

function Get-WindowsScheduledTaskPolicy {
    # Scheduling is machine deployment policy, not portable vault selection.
    $path = if ($env:AGENT_VAULT_CONFIG) { $env:AGENT_VAULT_CONFIG } else {
        $appData = if ($env:APPDATA) { $env:APPDATA } else { Join-Path $env:USERPROFILE 'AppData\Roaming' }
        Join-Path $appData 'agent-vault\config.json'
    }
    $policy = 'preserve'
    if (Test-Path -LiteralPath $path) {
        $data = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json -ErrorAction Stop
        if ($data -isnot [pscustomobject]) { throw 'Vault configuration must be an object' }
        if ($data.PSObject.Properties['windows_scheduled_task']) {
            $policy = $data.windows_scheduled_task
        }
    }
    if ($policy -isnot [string] -or $policy -cnotin @('preserve', 'enabled', 'absent')) {
        throw 'windows_scheduled_task must be preserve, enabled or absent'
    }
    return $policy
}

function Assert-OwnedVaultTask {
    param($Task)
    if (@($Task.Actions).Count -ne 1) { throw 'Refusing ambiguous Vault task actions' }
    $taskAction = @($Task.Actions)[0]
    $launcher = [regex]::Escape((Join-Path $InstallDir 'service.ps1'))
    $executor = [string]$taskAction.Execute
    $arguments = [string]$taskAction.Arguments
    # -File terminates shell options; later -File tokens are only script input.
    $shell = '(?:"(?:[^"]*\\)?(?:pwsh|powershell)\.exe"|(?:[^\s"]*\\)?(?:pwsh|powershell)\.exe)'
    if ((Split-Path -Leaf $executor) -ieq 'conhost.exe') {
        if ($arguments -notmatch "^\s*--headless\s+$shell\s+(?<shellArguments>.*)$") {
            throw 'Refusing foreign Vault scheduled task'
        }
        $arguments = $Matches.shellArguments
    } elseif ((Split-Path -Leaf $executor) -notin @('pwsh.exe', 'powershell.exe')) {
        throw 'Refusing foreign Vault scheduled task'
    }
    $options = '(?:(?:-NoProfile|-NonInteractive|-NoLogo)\s+|-ExecutionPolicy\s+Bypass\s+|-WindowStyle\s+Hidden\s+)*'
    $operand = "`"$launcher`""
    if ((Join-Path $InstallDir 'service.ps1') -notmatch '\s') { $operand += "|$launcher" }
    if ($arguments -notmatch "^\s*$options-File\s+(?:$operand)\s*$") {
        throw 'Refusing foreign Vault scheduled task'
    }
}

function Get-SchedulingPolicyTask {
    param([string]$Policy)
    if (-not (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue)) {
        if ($Policy -eq 'preserve') { return $null }
        throw 'Cannot reconcile Vault scheduling: ScheduledTasks module unavailable'
    }
    try {
        return (Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop)
    } catch {
        if (($_.FullyQualifiedErrorId -split ',')[0] -ceq 'CmdletizationQuery_NotFound_TaskName') {
            return $null
        }
        throw
    }
}

function Register-AgentVaultTask {
    if ($NoService -and $Action -ne 'schedule') {
        Write-Skip 'agent-vault service skipped (-NoService): this host is a client only'
        return
    }
    $policy = Get-WindowsScheduledTaskPolicy
    $existing = Get-SchedulingPolicyTask -Policy $policy
    if ($policy -eq 'absent') {
        if ($existing) {
            Assert-OwnedVaultTask -Task $existing
            if ($existing.State -eq 'Disabled') {
                throw 'Disabled Vault task retains a startup hold; clear that intent explicitly before task retirement'
            }
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
        }
        Write-Ok 'Vault scheduling absent; service and state preserved'
        if ($Action -eq 'schedule') { return }
    }
    if ($existing) {
        if ($policy -eq 'enabled') { Assert-OwnedVaultTask -Task $existing }
        if ($existing.State -eq 'Disabled') {
            Write-Skip 'Vault scheduled task is disabled; startup hold preserved'
            return
        }
    }
    if ($Action -eq 'schedule' -and $policy -eq 'preserve') { return }
    if ($Action -eq 'schedule' -and $policy -eq 'enabled') {
        if ($NoService) { throw 'Client-only NoService intent excludes scheduling opt-in' }
        . (Join-Path $PSScriptRoot 'runtime-activation.ps1')
        $project = if ($env:COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT) {
            $env:COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT
        } else { (Get-Location).ProviderPath }
        $activation = Get-VaultRuntimeActivation -Project $project
        if ($activation.state -ceq 'configuration-empty') {
            throw 'Vault runtime is inactive; scheduling does not enable the service'
        }
        $resolver = Join-Path $InstallDir 'bin\resolve-runtime.ps1'
        if (-not (Test-Path -LiteralPath $resolver)) { throw 'Vault runtime resolver is unavailable' }
        $previousRoot = $env:AGENT_RT_ROOT
        try {
            $env:AGENT_RT_ROOT = $InstallDir
            $AgentRtPy = $null
            . $resolver
            if (-not $AgentRtPy) { throw 'Vault selected runtime is unavailable' }
            $LinkPython = $AgentRtPy
        } finally { $env:AGENT_RT_ROOT = $previousRoot }
    }
    if (-not (Test-Path $LinkPython)) {
        if ($Action -eq 'schedule') { throw 'Vault selected runtime is unavailable' }
        Write-Warn 'agent-vault venv not found -- skipping scheduled task'
        return
    }
    if ($policy -eq 'enabled' -and -not (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)) {
        throw 'Vault scheduling explicitly enabled but ScheduledTasks module unavailable'
    }

    # #1836: the launcher itself resolves the active slot via the canonical
    # marker chain (uniform-runtime-resolution, #765; the SAME resolve-runtime.ps1
    # chain the binstub uses) at PROCESS START, exactly like agent-bridge's/
    # agent-dispatch's launchers -- never a concrete versions/<v> path baked in
    # at registration time. `.venv` itself is never traversed here (a
    # RedirectionGuard-enforcing task context would be blocked from *traversing*
    # the junction, dotfiles #637); $LinkPython is only the last-resort fallback
    # for a host where the resolver hasn't been deployed yet.
    New-Item -ItemType Directory -Force -Path $RunDir, (Join-Path $InstallDir 'logs') | Out-Null
    $configurationProject = if ($env:COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT) {
        $env:COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT
    } else { (Get-Location).ProviderPath }
    $serviceConfigurationJson = & $LinkPython -I -B -c `
        'import json,sys; from agent_vault.configuration import service_configuration_snapshot; print(json.dumps(service_configuration_snapshot(sys.argv[1]),ensure_ascii=True))' `
        $configurationProject
    if ($LASTEXITCODE -ne 0 -or -not $serviceConfigurationJson) {
        throw 'Vault service configuration context could not be captured'
    }
    $serviceConfiguration = ([string]$serviceConfigurationJson) | ConvertFrom-Json -ErrorAction Stop
    $serviceConfigurationRoot = [string]$serviceConfiguration.root
    $serviceConfigurationPath = [string]$serviceConfiguration.config
    $portDirective = if ($installationId) {
        "`$env:AGENT_VAULT_PORT = '0'"
    } else {
        "Remove-Item Env:AGENT_VAULT_PORT -ErrorAction SilentlyContinue"
    }
    [System.IO.File]::WriteAllText($TaskLauncher, @"
`$env:PYTHONUTF8 = '1'
`$env:AGENT_VAULT_HOME = '$($InstallDir -replace "'","''")'
`$env:AGENT_VAULT_RUN_DIR = '$($RunDir -replace "'","''")'
`$env:AGENT_VAULT_CORE_RUN_DIR = '$((Join-Path $InstallDir 'core') -replace "'","''")'
`$env:AGENT_VAULT_CACHE_DIR = '$((Join-Path $InstallDir 'cache') -replace "'","''")'
`$env:AGENT_VAULT_SOCKET = '$($SocketPath -replace "'","''")'
`$env:AGENT_VAULT_PIPE = '$($PipePath -replace "'","''")'
`$env:AGENT_VAULT_PID = '$($PidFile -replace "'","''")'
`$env:AGENT_VAULT_LOG = '$($LogFile -replace "'","''")'
`$env:AGENT_VAULT_TASK_NAME = '$($TaskName -replace "'","''")'
`$env:AGENT_VAULT_INSTALLATION_ID = '$($installationId -replace "'","''")'
`$env:AGENT_VAULT_CONFIG = '$($serviceConfigurationPath -replace "'","''")'
$portDirective
`$_root = '$($InstallDir -replace "'","''")'
`$_resolver = Join-Path `$_root 'bin\resolve-runtime.ps1'
`$_py = `$null
if (Test-Path -LiteralPath `$_resolver) { `$env:AGENT_RT_ROOT = `$_root; . `$_resolver; `$_py = `$AgentRtPy }
if (-not (`$_py -and (Test-Path -LiteralPath `$_py))) { `$_py = '$($LinkPython -replace "'","''")' }
`$_cacheId = [Guid]::NewGuid().ToString('N')
`$_pythonCache = [IO.Path]::GetFullPath((Join-Path `$_root "cache\python\unsealed-`$_cacheId"))
if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT -and -not `$_pythonCache.StartsWith('\\?\')) {
    `$_pythonCache = if (`$_pythonCache.StartsWith('\\')) {
        '\\?\UNC\' + `$_pythonCache.Substring(2)
    } else { '\\?\' + `$_pythonCache }
}
# Reuse the sealed installed-<digest> bytecode cache when install evidence is valid.
`$_mintedCache = `$_pythonCache
try {
    `$_selectedCache = & `$_py -I -B -X ('pycache_prefix=' + `$_pythonCache) -c "import json,os,sys; from agent_procutil.python_cache import cache_arguments as c; p=c(sys.argv[1],runtime=sys.executable)[1].split('=',1)[1]; print(json.dumps(p if os.path.basename(p).startswith('installed-') else ''))" `$_root 2>`$null
    if (`$LASTEXITCODE -eq 0 -and @(`$_selectedCache).Count -eq 1) {
        `$_selectedCache = [string](`$_selectedCache | ConvertFrom-Json)
        if (`$_selectedCache) { `$_pythonCache = `$_selectedCache }
    }
} catch {}
`$_supportsContext = & `$_py -I -B -c "import importlib.util; print(int(importlib.util.find_spec('agent_vault.service_arguments') is not None))"
if (`$LASTEXITCODE -ne 0) { throw 'Vault service argument capability could not be read' }
`$_serviceArguments = @('--foreground', '--persistent')
if (([string]`$_supportsContext).Trim() -ceq '1') {
    `$_serviceArguments += @('--configuration-root', '$($serviceConfigurationRoot -replace "'","''")')
}
& `$_py -X "pycache_prefix=`$_pythonCache" -m agent_vault.service @_serviceArguments @args
`$_exitCode = `$LASTEXITCODE
if (`$_pythonCache -eq `$_mintedCache) { try { [IO.Directory]::Delete(`$_mintedCache, `$true) } catch {} }
exit `$_exitCode
"@, [Text.UTF8Encoding]::new($true))
    if ($policy -eq 'enabled') {
        $desiredTaskAction = New-ScheduledTaskAction `
            -Execute 'conhost.exe' `
            -Argument "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$TaskLauncher`"" `
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

        # Register-ONCE model (#1836): update the stable launcher above on every
        # runtime cutover; change the task definition only on an explicit opt-in.
        $existing = Get-SchedulingPolicyTask -Policy $policy
        if ($existing) {
            $existingAction = $existing.Actions | Select-Object -First 1
            $matchesDesired = $existingAction -and
                $existingAction.Execute -eq $desiredTaskAction.Execute -and
                $existingAction.Arguments -eq $desiredTaskAction.Arguments -and
                $existingAction.WorkingDirectory -eq $desiredTaskAction.WorkingDirectory
            if ($matchesDesired) {
                Write-Ok "Scheduled task already correct ($TaskName, at logon, 15s delay) -- left registered as-is"
            } else {
                Set-ScheduledTask -TaskName $TaskName -Action $desiredTaskAction -Trigger $trigger -Settings $settings | Out-Null
                Write-Ok "Scheduled task updated ($TaskName, at logon, 15s delay)"
            }
        } else {
            Register-ScheduledTask -TaskName $TaskName `
                -Action $desiredTaskAction -Trigger $trigger -Settings $settings `
                -Description 'agent-vault -- local KeePassXC-backed secret store.' | Out-Null
            Write-Ok "Scheduled task registered ($TaskName, at logon, 15s delay)"
        }

        if ($Action -ne 'schedule') {
            Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        }
    }
    if ($Action -eq 'schedule') { return }

    # IgnoreNew may leave the predecessor's task running. The selected owner
    # starts its successor independently and verifies its generation, not merely
    # the continued health of whichever daemon still serves the old endpoint.
    if (Test-Path $LinkPython) {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $TaskLauncher --ensure-selected
        $ready = ($LASTEXITCODE -eq 0)
        $ErrorActionPreference = $prevEAP
        if ($ready) {
            Write-Ok 'agent-vault service is running (post-(re)start readiness confirmed)'
        } else {
            Write-Fail 'agent-vault runtime installed, but its selected service generation is not ready'
            exit 1
        }
    }
}


function Invoke-Stamp {
    # Fast base install (#1393, snapshot slot model): copy the payload SOURCE into
    # ~/.agent-vault/snapshots/<ver>/, record markers, and deploy the self-
    # provisioning binstub -- deferring the heavy venv build (and the daemon
    # service registration) to the binstub's first use. No venv, no uv; fits a
    # sessionStart grace window and NEVER holds the marketplace payload open (it
    # copies from the already self-staged $PluginDir, freeing the singleton).
    Write-Step 'agent-vault stamp; runtime deferred to first use'
    if (-not $SrcVersion) { Write-Fail 'Cannot stamp: no version in pyproject.toml'; exit 1 }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    foreach ($dir in @($InstallDir, $LocalBin)) {
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    }
    $snapDir = Join-Path (Join-Path $InstallDir 'snapshots') $SrcVersion
    Assert-SourceRuntime
    if ($script:SourceRuntimeIdentity) {
        Write-Binstubs
        Publish-SourcePayload
        Write-Ok 'Stamped: agent-vault binstub on PATH; runtime provisions on first use.'
        return
    }
    $snapTmp = "$snapDir.tmp-$PID"
    if (Test-Path $snapTmp) { Remove-Item $snapTmp -Recurse -Force -ErrorAction SilentlyContinue }
    Copy-VaultSnapshotInputs -Destination $snapTmp
    Write-Binstubs -SnapshotSourcePath $snapTmp
    if (Test-Path $snapDir) { Remove-Item $snapDir -Recurse -Force -ErrorAction SilentlyContinue }
    Move-Item -LiteralPath $snapTmp -Destination $snapDir -Force
    [System.IO.File]::WriteAllText((Join-Path $InstallDir 'payload-dir'), $snapDir, $utf8NoBom)
    [System.IO.File]::WriteAllText((Join-Path $InstallDir 'stamped-version'), $SrcVersion, $utf8NoBom)
    Write-Ok "Snapshot: $snapDir"
    Write-Ok 'Stamped: agent-vault binstub on PATH; runtime provisions on first use.'
}

function Invoke-Install {
    Write-Step 'agent-vault install'
    Install-Runtime
    Register-AgentVaultTask
    Write-Ok 'agent-vault install'
}

function Invoke-Update {
    Write-Step 'agent-vault update'
    Invoke-DowngradeGuard
    Install-Runtime
    Register-AgentVaultTask
    Write-Ok 'agent-vault update'
}

function Invoke-Start {
    $task = if (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue) {
        Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    }
    if ($task) {
        if ($task.State -eq 'Disabled') {
            throw 'Vault scheduled task is disabled; refusing direct-start fallback'
        }
        Start-ScheduledTask -TaskName $TaskName
        Write-Ok 'agent-vault service started (Scheduled Task)'
        return
    }
    # #1836: `start` must not depend on a registered Scheduled Task (a
    # client-only -NoService host, or the ScheduledTasks module being
    # unavailable, must still be able to start the daemon). Converge on the
    # SAME idempotent, health-gated user-mode ensure path the CLI's own
    # `agent-vault start` uses (cmd_start -> ensure_service/start_service in
    # cli.py) -- no PowerShell-side process-spawning logic duplicated here.
    $startPython = Get-ActiveVaultPython
    if (-not $startPython -or -not (Test-Path -LiteralPath $startPython)) {
        Write-Fail "agent-vault runtime not installed -- run: install.ps1 -Action install"
        exit 1
    }
    $project = if ($env:COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT) {
        $env:COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT
    } else { (Get-Location).ProviderPath }
    $previousDirectory = [IO.Directory]::GetCurrentDirectory()
    $previousPreference = $ErrorActionPreference
    Push-Location -LiteralPath $project
    try {
        [IO.Directory]::SetCurrentDirectory($project)
        $ErrorActionPreference = 'Continue'
        & $startPython -m agent_vault start
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
        Pop-Location
        [IO.Directory]::SetCurrentDirectory($previousDirectory)
    }
    exit $code
}

function Get-ActiveVaultPython {
    $resolver = Join-Path $InstallDir 'bin\resolve-runtime.ps1'
    if (-not (Test-Path -LiteralPath $resolver)) { return $LinkPython }
    $previousRoot = $env:AGENT_RT_ROOT
    try {
        $env:AGENT_RT_ROOT = $InstallDir
        $AgentRtPy = $null
        . $resolver -TraceMode read-only
        return $AgentRtPy
    } finally {
        $env:AGENT_RT_ROOT = $previousRoot
    }
}

function Invoke-Stop {
    $stopPython = $LinkPython
    $resolver = Join-Path $InstallDir 'bin\resolve-runtime.ps1'
    if (Test-Path -LiteralPath $resolver) {
        $previousRoot = $env:AGENT_RT_ROOT
        try {
            $env:AGENT_RT_ROOT = $InstallDir
            . $resolver -TraceMode read-only
            $stopPython = $AgentRtPy
        } finally {
            $env:AGENT_RT_ROOT = $previousRoot
        }
    }
    if ($stopPython -and (Test-Path -LiteralPath $stopPython)) {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & $stopPython -m agent_vault.service --stop 2>$null | Out-Null
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

    $statusPython = Get-ActiveVaultPython
    if ($statusPython -and (Test-Path -LiteralPath $statusPython)) {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $ping = & $statusPython -m agent_vault.service --ping 2>$null
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
    Write-Host ''; Write-Host '=== agent-vault uninstall ===' -ForegroundColor Cyan
    if ($DryRun) { Write-Host '(dry run -- nothing will be changed)' -ForegroundColor Yellow }
    Write-Host ''
    . (Join-Path $PSScriptRoot 'uninstall-runtime.ps1')
    $ownedProcesses = @(Get-VaultUninstallProcesses)
    if ($DryRun) {
        Write-Host '[dry-run] would stop agent-vault'
    } else {
        if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        }
        foreach ($row in $ownedProcesses) {
            Stop-VaultUninstallProcess -Row $row
        }
    }
    if (-not $DryRun -and $env:OS -eq 'Windows_NT' -and
        $env:COPILOT_EXTENSIONS_TEST_CONTAINED -ne '1') {
        & $driverPython @driverArguments --remove-broker
        if ($LASTEXITCODE -ne 0) { throw 'Owned broker removal failed; runtimes preserved' }
    }
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        if ($DryRun) {
            Write-Host "[dry-run] would remove scheduled task: $TaskName"
        } else {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
            Write-Ok 'Scheduled task removed'
        }
    }
    if ($Purge) {
        if (Test-Path $InstallDir) {
            if ($DryRun) { Write-Host "[dry-run] would purge runtime/config contents (coordination retained): $InstallDir" }
            else {
                Invoke-FiniteRuntimeUninstall -InstallRoot $InstallDir
                Remove-InstallRuntimeContent -InstallRoot $InstallDir
                Write-Ok "Runtime purged (transaction/staging coordination retained): $InstallDir"
            }
        }
    } else {
        Remove-VaultRuntimeCode
        if ($DryRun) { Write-Host "[dry-run] state at $InstallDir would be kept (-Purge to delete)" }
        else { Write-Ok 'Venv removed (state kept; -Purge to delete)' }
    }
    if ($DryRun) { Write-Host 'agent-vault uninstall dry run complete -- nothing was changed' -ForegroundColor Yellow }
}

function Get-VaultUninstallPathInfo {
    param([string[]]$Paths)
    $python = Get-FiniteUninstallPython -InstallRoot $InstallDir
    if (-not $python) { throw 'Vault uninstall requires an external Python for native file observations.' }
    $probe = @'
import json, os, sys
from pathlib import Path
paths = json.load(sys.stdin)
if os.name != "nt" or not isinstance(paths, list) or not 1 <= len(paths) <= 8:
    raise ValueError("Invalid native file observation")
result = []
for value in paths:
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError("Native file observation requires an absolute path")
    resolved = str(Path(value).resolve(strict=True))
    if resolved.startswith("\\\\?\\UNC\\"):
        resolved = "\\\\" + resolved[8:]
    elif resolved.startswith("\\\\?\\"):
        resolved = resolved[4:]
    path = Path(resolved)
    info = path.stat()
    result.append({"path": str(path), "identity": f"{info.st_dev}:{info.st_ino}"})
print(json.dumps(result))
'@
    $nativeError = ''
    $output = Invoke-Hidden -FilePath $python -ArgList @('-I', '-S', '-B', '-X', 'utf8', '-c', $probe) `
        -InputText (ConvertTo-Json -InputObject @($Paths) -Compress) -ErrorOutput ([ref]$nativeError)
    if ($LASTEXITCODE -ne 0) { throw "Vault native file observation failed: $nativeError" }
    $decoded = ($output | Out-String) | ConvertFrom-Json
    $records = @($decoded)
    if ($records.Count -ne $Paths.Count) { throw 'Vault native file observation is incomplete.' }
    foreach ($record in $records) {
        if ($record.path -isnot [string] -or -not [IO.Path]::IsPathRooted($record.path) -or
            $record.identity -isnot [string] -or $record.identity -cnotmatch '^\d+:\d+$') {
            throw 'Vault native file observation is invalid.'
        }
    }
    return $records
}

function Stop-VaultUninstallProcess {
    param($Row)
    $now = Get-CimInstance Win32_Process -Filter "ProcessId=$($Row.ProcessId)" -ErrorAction Stop
    if (-not $now) { return }
    if ($now.CreationDate -ne $Row.CreationDate -or $now.CommandLine -cne $Row.CommandLine -or
        $now.ExecutablePath -ine $Row.ExecutablePath -or $now.ParentProcessId -ne $Row.ParentProcessId -or
        $Row.CreationDate -isnot [DateTime]) {
        throw 'Vault process identity changed; refusing uninstall'
    }
    $process = [Diagnostics.Process]::GetProcessById([int]$Row.ProcessId)
    try {
        # Cache the native handle before validation; Kill and WaitForExit retain that identity.
        [void]$process.Handle
        $birth = $process.StartTime.ToUniversalTime().ToFileTimeUtc()
        $captured = $Row.CreationDate.ToUniversalTime().ToFileTimeUtc()
        $images = @(Get-VaultUninstallPathInfo @($process.Path, $Row.ExecutablePath))
        if ($birth - ($birth % 10) -ne $captured - ($captured % 10) -or
            $images[0].path -ine $images[1].path -or
            $images[0].identity -cne $images[1].identity) {
            throw 'Vault process native identity changed; refusing uninstall'
        }
        $process.Kill()
        if (-not $process.WaitForExit(15000)) { throw 'Owned Vault daemon did not stop' }
    } finally {
        $process.Dispose()
    }
}

function Test-VaultUninstallRedirectorChild {
    param($Parent, $Child, [string]$Root)
    if ($Child.ParentProcessId -ne $Parent.ProcessId) { return $false }
    $service = '^\s*(?:"(?<exe>[^"]+)"|(?<exe>\S+))\s+(?<args>-m\s+agent_vault\.service(?:\s+--foreground)?(?:\s+--persistent)?)\s*$'
    $parentCommand = [regex]::Match([string]$Parent.CommandLine, $service)
    $childCommand = [regex]::Match([string]$Child.CommandLine, $service)
    if (-not $parentCommand.Success -or -not $childCommand.Success -or
        ($parentCommand.Groups['args'].Value -replace '\s+', ' ') -cne
        ($childCommand.Groups['args'].Value -replace '\s+', ' ')) { return $false }
    if ((Split-Path -Leaf $Parent.ExecutablePath) -ine 'python.exe' -or
        (Split-Path -Leaf (Split-Path -Parent $Parent.ExecutablePath)) -ine 'Scripts') { return $false }
    $parentImage = (Get-VaultUninstallPathInfo @($Parent.ExecutablePath)).path
    if (-not $parentImage.StartsWith($Root + '\', [StringComparison]::OrdinalIgnoreCase)) { return $false }
    $venv = Split-Path -Parent (Split-Path -Parent $parentImage)
    $config = Join-Path $venv 'pyvenv.cfg'
    if (-not (Test-Path -LiteralPath $config -PathType Leaf)) { return $false }
    $item = Get-Item -LiteralPath $config -Force -ErrorAction Stop
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { return $false }
    # CPython's Windows venv launcher selects its physical interpreter from this home.
    $homes = @(Get-Content -LiteralPath $config -Encoding UTF8 -ErrorAction Stop |
        Where-Object { $_ -cmatch '^home\s*=' } |
        ForEach-Object { ($_ -split '=', 2)[1].Trim() })
    if ($homes.Count -ne 1 -or -not [IO.Path]::IsPathRooted($homes[0])) { return $false }
    $images = @(Get-VaultUninstallPathInfo @(
        $config, (Join-Path $homes[0] 'python.exe'), $Child.ExecutablePath,
        $childCommand.Groups['exe'].Value, $parentCommand.Groups['exe'].Value
    ))
    if ($images[0].path -ine $config -or $images[2].path -ine $images[1].path -or
        $images[4].path -ine $parentImage -or
        ($images[3].path -ine $images[1].path -and $images[3].path -ine $parentImage)) { return $false }
    $owner = Invoke-CimMethod -InputObject $Child -MethodName GetOwnerSid -ErrorAction Stop
    if ($owner.ReturnValue -ne 0 -or $owner.Sid -ne [Security.Principal.WindowsIdentity]::GetCurrent().User.Value) {
        return $false
    }
    $handles = @()
    try {
        foreach ($row in @($Parent, $Child)) {
            if ($row.CreationDate -isnot [DateTime]) { return $false }
            $process = [Diagnostics.Process]::GetProcessById([int]$row.ProcessId)
            $handles += $process
            [void]$process.Handle
            $birth = $process.StartTime.ToUniversalTime().ToFileTimeUtc()
            $captured = $row.CreationDate.ToUniversalTime().ToFileTimeUtc()
            $files = @(Get-VaultUninstallPathInfo @($process.Path, $row.ExecutablePath))
            # CIM exposes microseconds; the native handle retains 100ns FILETIME precision.
            if ($birth - ($birth % 10) -ne $captured - ($captured % 10) -or $process.HasExited -or
                $files[0].path -ine $files[1].path -or
                $files[0].identity -cne $files[1].identity) { return $false }
        }
        $parentBirth = $handles[0].StartTime.ToUniversalTime().ToFileTimeUtc()
        $childBirth = $handles[1].StartTime.ToUniversalTime().ToFileTimeUtc()
        return $parentBirth -le $childBirth -and $childBirth -le [DateTime]::UtcNow.ToFileTimeUtc()
    } finally {
        foreach ($process in $handles) { $process.Dispose() }
    }
}

function Test-VaultUninstallBootstrappedProcess {
    param($Row, [string]$Root)
    $python = Get-FiniteUninstallPython -InstallRoot $InstallDir
    if (-not $python) { throw 'Vault uninstall requires an external Python for native process observations.' }
    $request = @{
        root = $Root; image = [string]$Row.ExecutablePath; command = [string]$Row.CommandLine
    } | ConvertTo-Json -Compress
    $nativeError = ''
    $output = Invoke-Hidden -FilePath $python -ArgList @(
        '-I', '-S', '-B', '-X', 'utf8', (Join-Path $PSScriptRoot 'vault-uninstall-process.py')
    ) -InputText $request -ErrorOutput ([ref]$nativeError)
    if ($LASTEXITCODE -ne 0) { throw "Vault canonical daemon observation failed: $nativeError" }
    $admitted = ($output | Out-String) | ConvertFrom-Json -ErrorAction Stop
    if ($admitted -isnot [bool]) { throw 'Vault canonical daemon observation is invalid.' }
    if (-not $admitted -or $Row.CreationDate -isnot [DateTime]) { return $false }
    $owner = Invoke-CimMethod -InputObject $Row -MethodName GetOwnerSid -ErrorAction Stop
    if ($owner.ReturnValue -ne 0 -or $owner.Sid -ne [Security.Principal.WindowsIdentity]::GetCurrent().User.Value) {
        return $false
    }
    $process = [Diagnostics.Process]::GetProcessById([int]$Row.ProcessId)
    try {
        [void]$process.Handle
        $birth = $process.StartTime.ToUniversalTime().ToFileTimeUtc()
        $captured = $Row.CreationDate.ToUniversalTime().ToFileTimeUtc()
        $images = @(Get-VaultUninstallPathInfo @($process.Path, $Row.ExecutablePath))
        return -not $process.HasExited -and
            $birth - ($birth % 10) -eq $captured - ($captured % 10) -and
            $images[0].path -ieq $images[1].path -and $images[0].identity -ceq $images[1].identity
    } finally {
        $process.Dispose()
    }
}

function Get-VaultUninstallProcesses {
    Invoke-FiniteRuntimeUninstall -InstallRoot $InstallDir -Check
    if ('..' -in ($InstallDir -split '[\\/]')) { throw 'Refusing traversal in uninstall root' }
    $root = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\', '/')
    if ($root -eq [IO.Path]::GetPathRoot($root).TrimEnd('\', '/') -or
        $root -eq [IO.Path]::GetFullPath($env:USERPROFILE).TrimEnd('\', '/')) {
        throw 'Refusing uninstall of a filesystem or home root'
    }
    $ancestor = $root
    while ($ancestor) {
        $item = Get-Item -LiteralPath $ancestor -Force -ErrorAction Stop
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing linked uninstall root' }
        $ancestor = Split-Path -Parent $ancestor
    }
    $manifest = Join-Path $root 'deploy-manifest.json'
    if (Test-Path -LiteralPath $manifest) {
        $item = Get-Item -LiteralPath $manifest -Force -ErrorAction Stop
        if ($item.Attributes -band ([IO.FileAttributes]::ReparsePoint -bor [IO.FileAttributes]::Directory)) {
            throw 'Refusing linked or non-file Vault deploy manifest'
        }
        $record = Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json -ErrorAction Stop
        if ($record.service -ne 'agent-vault' -or $record.source.plugin -ne 'agent-vault') {
            throw 'Refusing foreign Vault deploy manifest'
        }
    }
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Assert-OwnedVaultTask -Task $task
    }
    $rows = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    $owned = @()
    $bootstrapped = @($rows | Where-Object {
        $_.ExecutablePath -and $_.CommandLine -cmatch 'runpy\.run_module' -and
        $_.CommandLine -cmatch 'agent_vault\.service'
    })
    if ($bootstrapped.Count -gt 16) { throw 'Vault canonical daemon inventory exceeds its bound.' }
    foreach ($row in $bootstrapped) {
        if (Test-VaultUninstallBootstrappedProcess -Row $row -Root $root) { $owned += $row }
    }
    $canonicalIds = @($owned | ForEach-Object { [int]$_.ProcessId })
    foreach ($row in $rows) {
        if ([int]$row.ProcessId -in $canonicalIds) { continue }
        if (-not $row.ExecutablePath -or -not ([string]$row.ExecutablePath).StartsWith(
                $root + '\', [StringComparison]::OrdinalIgnoreCase)) { continue }
        if ([string]$row.CommandLine -notmatch '(?i)(?:^|\s)-m\s+agent_vault\.service(?:\s+--foreground)?(?:\s+--persistent)?\s*$') {
            throw "Runtime still in use by an unclassified process (pid=$($row.ProcessId))"
        }
        $owner = Invoke-CimMethod -InputObject $row -MethodName GetOwnerSid -ErrorAction Stop
        if ($owner.ReturnValue -ne 0 -or $owner.Sid -ne [Security.Principal.WindowsIdentity]::GetCurrent().User.Value) {
            throw 'Vault daemon user ownership cannot be validated'
        }
        $owned += $row
    }
    $ownedIds = @($owned | ForEach-Object { [int]$_.ProcessId })
    foreach ($parent in @($owned)) {
        $children = @($rows | Where-Object {
            $_.ParentProcessId -eq $parent.ProcessId -and $_.ProcessId -notin $ownedIds
        })
        if ($children.Count -eq 1 -and (Test-VaultUninstallRedirectorChild -Parent $parent -Child $children[0] -Root $root)) {
            # Retire the physical interpreter before its waiting venv launcher.
            $owned = @($children[0]) + $owned
        }
    }
    $ownedIds = @($owned | ForEach-Object { [int]$_.ProcessId })
    foreach ($row in $rows) {
        if ([int]$row.ParentProcessId -in $ownedIds -and [int]$row.ProcessId -notin $ownedIds) {
            throw "Vault still owns a live child (pid=$($row.ProcessId)); refusing uninstall"
        }
    }
    if (Test-Path -LiteralPath $PidFile) {
        $value = (Get-Content -LiteralPath $PidFile -Raw).Trim()
        if ($value -notmatch '^[1-9][0-9]*$') { throw 'Refusing invalid Vault PID receipt' }
        if (@($rows | Where-Object { $_.ProcessId -eq [int]$value }).Count -and [int]$value -notin $ownedIds) {
            throw 'Vault PID receipt names a foreign process; refusing uninstall'
        }
    }
    return $owned
}

function Remove-VaultRuntimeCode {
    if ($DryRun) {
        Write-Host "[dry-run] would remove owned runtime code at: $InstallDir"
        return
    }
    Invoke-FiniteRuntimeUninstall -InstallRoot $InstallDir
}

function Remove-VaultHelperFiles {
    param([string]$Directory, [string[]]$Names)
    $item = Get-Item -LiteralPath $Directory -Force -ErrorAction Stop
    if (-not $item.PSIsContainer -or $item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'Refusing linked or non-directory runtime helpers'
    }
    foreach ($name in $Names) {
        $path = Join-Path $Directory $name
        if (-not (Test-Path -LiteralPath $path)) { continue }
        $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
        if ($item.Attributes -band ([IO.FileAttributes]::ReparsePoint -bor [IO.FileAttributes]::Directory)) {
            throw "Refusing linked or non-file runtime helper: $path"
        }
    }
    foreach ($name in $Names) {
        $path = Join-Path $Directory $name
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force -ErrorAction Stop }
    }
}

# === install-transaction lock (fail-closed) ===
# Serializes every mutating action of this runtime (stamp/install/update/
# provision): one exclusive, OS-held handle on $InstallDir\.install.lock for the
# whole build + publish transaction. A concurrent run waits up to the timeout
# (COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC, default 300s) and then FAILS -- it
# never proceeds unlocked. The OS drops the handle if this process dies.
$script:InstallLockHandle = $null

function Get-InstallLockTimeoutSec {
    $value = 0
    $raw = [string]$env:COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC
    if ($raw -and [int]::TryParse($raw, [ref]$value) -and $value -gt 0) { return $value }
    return 300
}

function Enter-InstallLock {
    param([int]$TimeoutSec = (Get-InstallLockTimeoutSec))
    try {
        if (-not (Test-Path -LiteralPath $InstallDir)) { New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null }
    } catch {
        Write-Fail "Install-lock error: $($_.Exception.Message)"
        return $false
    }
    $lockPath = Join-Path $InstallDir '.install.lock'
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ($true) {
        try {
            $fs = [System.IO.File]::Open(
                $lockPath,
                [System.IO.FileMode]::OpenOrCreate,
                [System.IO.FileAccess]::ReadWrite,
                [System.IO.FileShare]::None
            )
            try {
                $stamp = [System.Text.Encoding]::UTF8.GetBytes("pid=$PID at=$((Get-Date).ToUniversalTime().ToString('o'))")
                $fs.SetLength(0)
                $fs.Write($stamp, 0, $stamp.Length)
                $fs.Flush()
            } catch { }
            $script:InstallLockHandle = $fs
            return $true
        } catch [System.IO.IOException] {
            # Win32 sharing/lock violation, or (non-Windows) the advisory lock
            # .NET takes for FileShare.None: another install holds the lock.
            $win32 = $_.Exception.HResult -band 0xFFFF
            if ($win32 -eq 32 -or $win32 -eq 33 -or $env:OS -ne 'Windows_NT') {
                if ((Get-Date) -ge $deadline) { return $false }
                Start-Sleep -Milliseconds 750
            } else {
                Write-Fail "Install-lock IO error: $($_.Exception.Message)"
                return $false
            }
        } catch {
            Write-Fail "Install-lock error: $($_.Exception.Message)"
            return $false
        }
    }
}

function Exit-InstallLock {
    if ($script:InstallLockHandle) {
        try { $script:InstallLockHandle.Close(); $script:InstallLockHandle.Dispose() } catch { }
        $script:InstallLockHandle = $null
    }
}
# === end install-transaction lock ===

$ScriptDir = $PSScriptRoot
. (Join-Path $ScriptDir 'versioned_runtime_native.ps1')
. (Join-Path $ScriptDir 'source-runtime.ps1')
$PackageVersion = $SrcVersion
$SourceRuntimeIdentity = $null
$RuntimeProfile = ''
$RuntimeProfilePython = ''
$RuntimeProfileHelper = ''
$RuntimeProfileInputs = $null
$installFailure = $null
$profilePresent = $false
if ($Action -in @('install', 'update', 'provision', 'stamp')) {
    $profilePresent = Test-VaultRuntimeProfilePresent
}
if ($Action -in @('install', 'update', 'provision', 'stamp') -and ($Profile -or $profilePresent)) {
    $RuntimeProfilePython = Resolve-NativeBootstrapPython -MinimumMinor 11 -Action $Action -Root $InstallDir
    if (-not $RuntimeProfilePython) { throw 'Vault dependency profiles require an existing Python 3.11+ bootstrap interpreter' }
}
if ($Action -in @('install', 'update', 'provision', 'stamp')) { Initialize-SourceRuntime }
if ($WheelProfile) {
    Initialize-NativeWheelRuntime -Python $WheelPython -Profile $WheelProfile `
        -Directory $WheelDirectory -Payload $PluginDir -Plugin 'agent-vault' -Root $InstallDir
    if ($Profile -or $profilePresent) {
        $profileArguments = @('-I', '-B', (Join-Path $ScriptDir 'runtime_profile.py'),
            '--root', $InstallDir, 'select')
        if ($Profile) { $profileArguments += @('--profile', $Profile) }
        $selectedProfile = Invoke-Hidden -FilePath $WheelPython -ArgList $profileArguments
        if ($LASTEXITCODE -ne 0 -or ($selectedProfile | ConvertFrom-Json).profile -cne 'base') {
            throw 'This closed-wheel input does not replace the selected Vault dependency profile'
        }
    }
}
$needsInstallLock = ($Action -in @('install', 'update', 'stamp', 'provision', 'schedule')) -or
    ($Action -eq 'uninstall' -and -not $DryRun)
if ($needsInstallLock -and -not (Enter-InstallLock)) {
    Write-Fail "Could not acquire the install transaction lock ($(Join-Path $InstallDir '.install.lock')) within $(Get-InstallLockTimeoutSec)s -- another install is still running; refusing to proceed unlocked"
    exit 1
}

try {
    if ($script:SourceRuntimeIdentity -and (Test-Path Env:COPILOT_EXTENSIONS_RUNTIME_SOURCE_TARGET)) {
        [void](Invoke-Hidden -FilePath (Get-BootstrapPython) -ArgList @(
            '-I', '-B', (Join-Path $ScriptDir 'runtime_source_target.py'),
            '--root', $InstallDir, '--check-target'
        ))
        if ($LASTEXITCODE -ne 0) { throw 'Vault source target changed before installation' }
    }
    if ($Action -in @('install', 'update', 'provision', 'stamp')) {
        $profilePresent = Test-VaultRuntimeProfilePresent
    }
    if (-not $WheelProfile -and $Action -in @('install', 'update', 'provision', 'stamp') -and ($Profile -or $profilePresent)) {
        if (-not $RuntimeProfilePython) {
            $RuntimeProfilePython = Resolve-NativeBootstrapPython -MinimumMinor 11 -Action $Action -Root $InstallDir
        }
        $py = Get-BootstrapPython
        if (-not $py) { throw 'Vault dependency selection requires a bootstrap Python interpreter' }
        $arguments = @('-I', '-B', (Join-Path $ScriptDir 'runtime_profile.py'),
            '--root', $InstallDir, 'select')
        if (-not $DryRun) { $arguments += '--persist' }
        if ($Profile) { $arguments += @('--profile', $Profile) }
        $selected = Invoke-Hidden -FilePath $py -ArgList $arguments
        if ($LASTEXITCODE -ne 0) { throw 'Vault dependency selection failed' }
        $selected = $selected | ConvertFrom-Json
        $RuntimeProfile = $selected.profile
        $SrcVersion = $selected.runtime_version
        $VenvDir = Join-Path (Join-Path $InstallDir 'versions') $SrcVersion
        $VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
        $LinkDir = $VenvDir
        $LinkPython = $VenvPython
    }
    if ($Action -in @('install', 'update', 'provision', 'stamp')) { Assert-SourceRuntime }
    if ($Action -in @('install', 'update', 'provision') -and -not $DryRun) {
        . (Join-Path $PSScriptRoot 'installer-instance-reconcile.ps1')
        Invoke-InstallerInstanceReconcile -HelperRoot $PSScriptRoot -RuntimeRoot $InstallDir
    }
    switch ($Action) {
        'install'   { Invoke-Install }
        'update'    { Invoke-Update }
        'start'     { Invoke-Start }
        'stop'      { Invoke-Stop }
        'status'    { Invoke-Status }
        'uninstall' { Invoke-Uninstall }
        'stamp'     { Invoke-Stamp }
        'provision' { Invoke-Install }
        'schedule'  { Register-AgentVaultTask }
    }
} catch {
    $installFailure = $_
    throw
} finally {
    try {
        if (Test-Path -LiteralPath 'Function:\Stop-RuntimeConstruction') {
            Stop-RuntimeConstruction
        }
        Remove-RuntimeProfileInputs
    } catch {
        if (-not $installFailure) { throw }
        Write-Warn "Vault construction inputs retained after settlement/cleanup refusal: $($_.Exception.Message)"
    } finally {
        Exit-InstallLock
    }
}
exit 0
