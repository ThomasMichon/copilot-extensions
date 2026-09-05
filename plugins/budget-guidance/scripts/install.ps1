<#
.SYNOPSIS
    Install/update the budget-guidance runtime. PS5+ compatible.
#>
[CmdletBinding()]
param(
    [ValidateSet('install', 'update', 'status', 'uninstall', 'stamp', 'provision')]
    [string]$Action = 'install',
    [string]$InstallDir,
    [switch]$Force
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

if (-not $PSBoundParameters.ContainsKey('Action')) {
    $PSBoundParameters['Action'] = $Action
}

function Start-Process {
    <# Start-Process joins ArgumentList and lets the child re-tokenize it. For
       staged PowerShell -File launches, encode an invocation whose script and
       forwarded arguments are PowerShell literals so paths with spaces remain
       single values. Other process launches retain native behavior. #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [object[]]$ArgumentList,
        [string]$WorkingDirectory,
        [switch]$PassThru,
        [switch]$NoNewWindow,
        [string]$WindowStyle
    )

    $arguments = @()
    foreach ($item in @($ArgumentList)) {
        if ($item -is [Array]) {
            $arguments += @($item)
        } else {
            $arguments += $item
        }
    }
    $fileIndex = [Array]::IndexOf($arguments, '-File')
    if ($fileIndex -ge 0 -and $fileIndex + 1 -lt $arguments.Count) {
        $script = [string]$arguments[$fileIndex + 1]
        $forwarded = @()
        if ($fileIndex + 2 -lt $arguments.Count) {
            $forwarded = @($arguments[($fileIndex + 2)..($arguments.Count - 1)])
        }
        $quote = {
            param([AllowNull()][object]$Value)
            "'" + ([string]$Value).Replace("'", "''") + "'"
        }
        $command = '& ' + (& $quote $script)
        foreach ($argument in $forwarded) {
            $text = [string]$argument
            if ($text -cmatch '^-[A-Za-z][A-Za-z0-9]*$') {
                $command += ' ' + $text
            } else {
                $command += ' ' + (& $quote $argument)
            }
        }
        $encoded = [Convert]::ToBase64String(
            [Text.Encoding]::Unicode.GetBytes($command)
        )
        $arguments = @(
            '-NoProfile',
            '-ExecutionPolicy',
            'Bypass',
            '-EncodedCommand',
            $encoded
        )
    }

    $parameters = @{
        FilePath = $FilePath
        ArgumentList = $arguments
    }
    if ($WorkingDirectory) { $parameters.WorkingDirectory = $WorkingDirectory }
    if ($PassThru) { $parameters.PassThru = $true }
    if ($NoNewWindow) { $parameters.NoNewWindow = $true }
    if ($WindowStyle) { $parameters.WindowStyle = $WindowStyle }
    Microsoft.PowerShell.Management\Start-Process @parameters
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

if (
    $env:COPILOT_EXTENSIONS_TEST_CONTAINED -eq '1' -and
    $env:BUDGET_GUIDANCE_TEST_ARGUMENT_CAPTURE
) {
    $capture = [ordered]@{
        action = $Action
        installDir = $InstallDir
        force = [bool]$Force
        staged = [bool]$env:COPILOT_PLUGIN_INSTALL_STAGED
    }
    $capture | ConvertTo-Json -Compress |
        Set-Content -LiteralPath $env:BUDGET_GUIDANCE_TEST_ARGUMENT_CAPTURE -Encoding UTF8
    exit 0
}

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
function Write-Step    { param([string]$Msg) Write-Host "  ...    $Msg" -ForegroundColor DarkGray }

function Install-AgentSshPackage {
    param(
        [Parameter(Mandatory = $true)][string]$Python,
        [Parameter(Mandatory = $true)][string]$Source,
        [string[]]$Dependencies = @()
    )

    $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
    if ($uvCommand) {
        $uvResult = 1
        try {
            & $uvCommand.Source pip install --python $Python $Source --quiet 2>&1 | Out-Null
            $uvResult = $LASTEXITCODE
        } catch {
            Write-Step 'uv package install could not start -- falling back to python -m pip'
        }
        if ($uvResult -eq 0) { return $true }
        if ($uvResult -ne 1) {
            Write-Step "uv package install exited $uvResult -- falling back to python -m pip"
        }
    }

    $pipSources = @($Dependencies) + @($Source)
    & $Python -m pip install --quiet @pipSources 2>&1 | Out-Null
    return $LASTEXITCODE -eq 0
}

$PluginDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PkgSrcDir = Join-Path $PluginDir 'src\budget_guidance'

if (-not $InstallDir) {
    $InstallDir = Join-Path $env:USERPROFILE '.budget-guidance'
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

# === install-contract:v3 versioned-venv (budget-guidance: .venv-as-junction) ===
# Immutable per-version runtime (#581). Build the venv into versions/<version>
# and make the historical `.venv` path a junction into it, so the binstubs and
# deploy-manifest resolve through the link unchanged. budget-guidance is a CLI (no
# daemon). LinkDir/LinkPython is the stable `.venv` path; VenvDir/VenvPython is the
# versions/<v> slot (build + health-gate). ALWAYS versioned -- the env opt-out
# (COPILOT_EXT_NO_VERSIONED / BUDGET_GUIDANCE_VERSIONED) and the legacy in-place fork are
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
    & $VenvPython -c 'import budget_guidance' 2>$null
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

function Deploy-SelfProvisioningBinstub {
    # Windows tool binstub (.ps1 primary + .cmd fallback), SELF-PROVISIONING
    # (#1393): resolve the interpreter the ONE uniform way -- the deployed
    # canonical resolve-runtime.ps1 marker chain (uniform-runtime-resolution,
    # #765): current-version -> last-known-good -> newest complete slot, never a
    # `.venv` junction, never a PATH python. If no slot is built yet (a `stamp`
    # deferred the venv), provision on first use by running the slot-local
    # snapshot's `scripts/install.ps1 provision`, then dispatch. Opt out with
    # BUDGET_GUIDANCE_NO_SELFPROVISION=1. POSIX gets its sh shim from install.sh.
    # Co-deploy the canonical resolvers so every launcher resolves identically.
    $binDir = Join-Path $InstallDir 'bin'
    if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Path $binDir -Force | Out-Null }
    foreach ($r in @('resolve-runtime.ps1', 'resolve-runtime.sh')) {
        $rSrc = Join-Path $PSScriptRoot $r
        if (Test-Path $rSrc) { Copy-Item $rSrc (Join-Path $binDir $r) -Force }
    }
    if ($env:OS -ne 'Windows_NT') {
        $stubPath = Join-Path $LocalBin 'budget-guidance'
        $stubContent = @(
            '#!/usr/bin/env bash',
            'export PYTHONUTF8=1',
            '_root="$HOME/.budget-guidance"',
            'AGENT_RT_PY=""',
            'if [ -f "$_root/bin/resolve-runtime.sh" ]; then AGENT_RT_ROOT="$_root"; . "$_root/bin/resolve-runtime.sh"; fi',
            '[ -n "$AGENT_RT_PY" ] && exec "$AGENT_RT_PY" -m budget_guidance "$@"',
            'echo "[budget-guidance] runtime not provisioned; run scripts/install.sh" >&2; exit 1'
        ) -join "`n"
        [System.IO.File]::WriteAllText($stubPath, $stubContent, $utf8NoBom)
        Write-Ok "Binstub: $stubPath"
        return
    }
    $ps1Path = Join-Path $LocalBin 'budget-guidance.ps1'
    $ps1Content = @'
$env:PYTHONUTF8 = '1'
$_root = Join-Path $env:USERPROFILE '.budget-guidance'
$_resolver = Join-Path $_root 'bin\resolve-runtime.ps1'
function _Resolve-Py {
    $AgentRtPy = $null
    if (Test-Path -LiteralPath $_resolver) { $env:AGENT_RT_ROOT = $_root; . $_resolver }
    return $AgentRtPy
}
$_py = _Resolve-Py
if ($_py) { & $_py -m budget_guidance @args; exit $LASTEXITCODE }
if ($env:BUDGET_GUIDANCE_NO_SELFPROVISION) { [Console]::Error.WriteLine('[budget-guidance] runtime not provisioned (BUDGET_GUIDANCE_NO_SELFPROVISION set).'); exit 1 }
if (-not (Test-Path -LiteralPath $_root)) { New-Item -ItemType Directory -Path $_root -Force | Out-Null }
$_lockPath = Join-Path $_root '.provision.lock'
$_lock = $null
while (-not $_lock) {
    try {
        $_lock = [IO.File]::Open($_lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    } catch {
        Start-Sleep -Milliseconds 200
    }
}
$_provisionRc = 0
try {
    $_py = _Resolve-Py
    if (-not $_py) {
        $_snap = ''
        try { $_snap = ([IO.File]::ReadAllText((Join-Path $_root 'payload-dir'))).Trim() } catch {}
        $_inst = if ($_snap) { Join-Path $_snap 'scripts\install.ps1' } else { '' }
        if (-not ($_inst -and (Test-Path -LiteralPath $_inst))) {
            [Console]::Error.WriteLine("[budget-guidance] cannot self-provision: owning snapshot installer unavailable: $_inst")
            $_provisionRc = 127
        } else {
            [Console]::Error.WriteLine('[budget-guidance] runtime not provisioned -- provisioning on first use (acquires uv + builds a venv; ~30-120s). Do not kill; extend your timeout.')
            [Console]::Error.WriteLine('::agent-provisioning:: plugin=budget-guidance eta_seconds=120 reason=first-use')
            $_pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
            $_exe = if ($_pwsh) { $_pwsh.Source } else { 'powershell.exe' }
            & $_exe -NoProfile -ExecutionPolicy Bypass -File $_inst provision 2>&1 | ForEach-Object { [Console]::Error.WriteLine($_) }
            $_provisionRc = $LASTEXITCODE
            if ($_provisionRc -eq 0) { $_py = _Resolve-Py }
        }
    }
} finally {
    if ($_lock) { $_lock.Dispose() }
}
if ($_provisionRc -ne 0) { exit $_provisionRc }
if ($_py) { & $_py -m budget_guidance @args; exit $LASTEXITCODE }
[Console]::Error.WriteLine('[budget-guidance] provisioning did not yield a runtime. See the log above; retry, or run the snapshot installer manually.')
exit 1
'@
    [System.IO.File]::WriteAllText($ps1Path, $ps1Content, $utf8NoBom)

    $cmdPath = Join-Path $LocalBin 'budget-guidance.cmd'
    # cmd fallback: delegate entirely to the .ps1 binstub so resolution stays
    # uniform with the canonical resolve-runtime.ps1 chain (current-version ->
    # last-known-good -> newest complete slot) and self-provisioning is shared.
    # Pure-batch version-sorting can't match the resolver without reintroducing a
    # lexicographic bug, and PowerShell is always present on Windows (this cmd
    # already shells to it to provision), so one delegation is the correct parity.
    $cmdContent = @'
@echo off
setlocal
set "PYTHONUTF8=1"
set "_PS1=%USERPROFILE%\.local\bin\budget-guidance.ps1"
if not exist "%_PS1%" (echo [budget-guidance] binstub not found: %_PS1%>&2 & exit /b 127)
where pwsh >nul 2>&1
if %ERRORLEVEL%==0 (pwsh -NoProfile -ExecutionPolicy Bypass -File "%_PS1%" %*) else (powershell -NoProfile -ExecutionPolicy Bypass -File "%_PS1%" %*)
exit /b %ERRORLEVEL%
'@
    [System.IO.File]::WriteAllText($cmdPath, $cmdContent, $utf8NoBom)
    Write-Ok "Binstub: $ps1Path (+ .cmd fallback, self-provisioning)"
}

function Invoke-Stamp {
    # Fast base install (#1393, snapshot slot model): copy the payload SOURCE
    # into a per-version snapshot under ~/.budget-guidance/snapshots/<ver>/, record
    # markers, and deploy the self-provisioning binstub -- deferring the heavy
    # venv build to the binstub's first use. No venv, no uv; fits a sessionStart
    # grace window and NEVER holds the marketplace payload open (it copies from
    # the already self-staged $PluginDir, freeing the singleton immediately).
    Write-Host ''
    Write-Host '=== budget-guidance stamp (defer runtime to first use) ===' -ForegroundColor Cyan
    if (-not $SrcVersion) { Write-Fail 'Cannot stamp: no version in pyproject.toml'; exit 1 }
    foreach ($dir in @($InstallDir, $LocalBin)) {
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    }
    $snapDir = Join-Path (Join-Path $InstallDir 'snapshots') $SrcVersion
    $snapTmp = "$snapDir.tmp-$PID"
    if (Test-Path $snapTmp) { Remove-Item $snapTmp -Recurse -Force -ErrorAction SilentlyContinue }
    New-Item -ItemType Directory -Path $snapTmp -Force | Out-Null
    # Copy everything needed to `uv pip install .` from the slot (src, libs,
    # scripts, pyproject, plugin.json, hooks, README); skip VCS/build/test junk.
    $exclude = @('.git', '__pycache__', '.venv', 'node_modules', 'build', 'dist', '.pytest_cache', '.mypy_cache', 'tests')
    Get-ChildItem -LiteralPath $PluginDir -Force | Where-Object { $exclude -notcontains $_.Name } | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $snapTmp $_.Name) -Recurse -Force
    }
    if (Test-Path $snapDir) { Remove-Item $snapDir -Recurse -Force -ErrorAction SilentlyContinue }
    Move-Item -LiteralPath $snapTmp -Destination $snapDir -Force
    [System.IO.File]::WriteAllText((Join-Path $InstallDir 'payload-dir'), $snapDir, $utf8NoBom)
    [System.IO.File]::WriteAllText((Join-Path $InstallDir 'stamped-version'), $SrcVersion, $utf8NoBom)
    Write-Ok "Snapshot: $snapDir"
    Deploy-SelfProvisioningBinstub
    Write-Ok 'Stamped: budget-guidance binstub on PATH; runtime provisions on first use.'
}

if ($Action -eq 'stamp') { Invoke-Stamp; exit 0 }

if ($Action -eq 'status') {
    Write-Host '=== budget-guidance status ===' -ForegroundColor Cyan
    if (Test-Path $LinkPython) { Write-Ok "Venv: $LinkDir" } else { Write-Skip "Venv missing: $LinkDir" }
    $ps1 = Join-Path $LocalBin 'budget-guidance.ps1'
    $cmd = Join-Path $LocalBin 'budget-guidance.cmd'
    if (Test-Path $ps1) { Write-Ok "Binstub: $ps1 (+ .cmd fallback)" } elseif (Test-Path $cmd) { Write-Skip "Only fallback binstub exists: $cmd" } else { Write-Skip "Binstub missing: $ps1" }
    if (Test-Path $ManifestPath) { Write-Ok "Deploy manifest: $ManifestPath" } else { Write-Skip 'Deploy manifest missing' }
    exit 0
}

if ($Action -eq 'uninstall') {
    Remove-Item (Join-Path $LocalBin 'budget-guidance.ps1') -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $LocalBin 'budget-guidance.cmd') -Force -ErrorAction SilentlyContinue
    Remove-Item $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Ok 'budget-guidance runtime removed'
    exit 0
}

Write-Host ''
Write-Host '=== budget-guidance install ===' -ForegroundColor Cyan
Write-Host ''

if (-not (Test-Path $PkgSrcDir)) {
    Write-Fail "Package source not found at $PkgSrcDir"
    exit 1
}

$hasWinget = $null -ne (Get-Command winget -ErrorAction SilentlyContinue)
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    if ($hasWinget) {
        Write-Step 'uv not found -- installing via winget...'
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & winget install --id astral-sh.uv --accept-source-agreements --accept-package-agreements 2>&1 | Out-Null
        $ErrorActionPreference = $prevEAP
        $env:PATH = (Get-CopilotPersistentEnvironmentVariable -Name 'PATH' -Target 'Machine') + ';' + (Get-CopilotPersistentEnvironmentVariable -Name 'PATH' -Target 'User')
        if (Get-Command uv -ErrorAction SilentlyContinue) { Write-Ok 'uv installed' }
    }
}
$uvCommand = Get-Command uv -ErrorAction SilentlyContinue

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
if (-not $uvCommand -and -not $pythonCmd) {
    Write-Fail 'Neither standalone uv nor Python 3.10+ is available'
    exit 1
}
if ($pythonCmd) { Write-Ok "Python fallback: $pythonCmd" }

foreach ($dir in @($InstallDir, $LocalBin)) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
}
Write-Ok "Directories: $InstallDir"

# -- Deploy the session-start hook (version-gated runtime reconcile) --
# hooks.json runs ~/.budget-guidance/bin/bootstrap-check.ps1 at session start; it
# re-runs this installer only when the deployed version drifts from the payload.
$BinHookDir = Join-Path $InstallDir 'bin'
if (-not (Test-Path $BinHookDir)) { New-Item -ItemType Directory -Path $BinHookDir -Force | Out-Null }
foreach ($h in @('bootstrap-check.ps1', 'bootstrap-check.sh', 'emit-mesh-pointer.ps1', 'emit-mesh-pointer.sh')) {
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
        if ($uvCommand) {
            Write-Step 'Creating Python 3.10+ venv via uv...'
            Invoke-VersionedSlotClean
            & $uvCommand.Source venv $VenvDir --python 3.10 --allow-existing 2>&1 |
                Out-Null
            if ($LASTEXITCODE -ne 0) {
                if (-not $pythonCmd) {
                    $ErrorActionPreference = $prevEAP
                    Write-Fail 'uv could not obtain a compatible Python interpreter'
                    exit 1
                }
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
$vendoredDependencies = @()
$pkgInstalled = Install-AgentSshPackage `
    -Python $VenvPython `
    -Source $PluginDir `
    -Dependencies $vendoredDependencies
$ErrorActionPreference = $prevEAP
if (-not $pkgInstalled) {
    Write-Fail 'Failed to install budget-guidance package into venv'
    exit 1
}
Remove-ConsoleTrampolines -VenvDir $VenvDir
Write-Ok 'Package installed: budget-guidance'

# Versioned layout (#581): health-gate the slot + swap the `.venv` junction.
if (-not (Invoke-VersionedActivate)) { exit 1 }

Deploy-SelfProvisioningBinstub

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
    service        = 'budget-guidance'
    deployed_at    = (Get-Date -Format 'o')
    deployed_by    = "$($env:COMPUTERNAME.ToLower())-windows"
    source         = [ordered]@{
        kind    = $kind
        path    = ($PluginDir -replace '\\', '/')
        repo    = 'copilot-extensions'
        plugin  = 'budget-guidance'
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
    & $LinkPython -c 'import budget_guidance' 2>$null
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
Write-Host '=== budget-guidance install complete ===' -ForegroundColor Cyan
Write-Host '  Try: budget-guidance version' -ForegroundColor DarkGray
exit 0
