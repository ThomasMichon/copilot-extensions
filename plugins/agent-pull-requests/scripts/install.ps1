<#
.SYNOPSIS
    Install/update the agent-pull-requests runtime. PS5+ compatible.
#>
[CmdletBinding()]
param(
    [ValidateSet('install', 'update', 'status', 'uninstall', 'stamp', 'provision', 'snapshot-prepare', 'snapshot-stamp', 'snapshot-validate', 'slot-provision', 'slot-validate', 'slot-complete', 'slot-completion-validate')]
    [string]$Action = 'install',
    [string]$InstallDir,
    [string]$Context,
    [string]$DurableHome,
    [string]$ExpectedMarketplaceId,
    [string]$SnapshotId,
    [string]$RuntimeVersion,
    [string]$ExpectedNamespaceGeneration,
    [string]$ExpectedInstallGeneration,
    [switch]$Force
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

if ($env:COPILOT_EXTENSIONS_HUMAN_UTF8 -eq '1') {
    if (-not [IO.File]::Exists((Join-Path $PSScriptRoot 'installer-human-output.ps1'))) {
        throw 'Required installer human-output initializer is unavailable.'
    }
    . (Join-Path $PSScriptRoot 'installer-human-output.ps1')
}

# Cell dispatch precedes self-staging, locks, and every legacy-root write.
$cellParameters = @('Context', 'DurableHome', 'ExpectedMarketplaceId', 'SnapshotId',
    'RuntimeVersion', 'ExpectedNamespaceGeneration', 'ExpectedInstallGeneration')
$cellRequested = $Action -like 'snapshot-*' -or $Action -like 'slot-*'
foreach ($name in $cellParameters) {
    if ($PSBoundParameters.ContainsKey($name)) { $cellRequested = $true }
}
if ($cellRequested) {
    if (-not $PSBoundParameters.ContainsKey('Context') -or [string]::IsNullOrWhiteSpace($Context)) {
        throw 'Cell actions require explicit -Context; ambient COPILOT_EXTENSIONS_CONTEXT is not authorization'
    }
    if ($PSBoundParameters.ContainsKey('InstallDir')) {
        throw '-Context and -InstallDir cannot be combined'
    }
    if ($Force) { throw '-Force cannot replace an immutable cell slot' }
    $forward = @($Action, '--context', $Context)
    foreach ($pair in @(
        @('DurableHome', '--durable-home'), @('ExpectedMarketplaceId', '--expected-marketplace-id'),
        @('SnapshotId', '--snapshot-id'), @('RuntimeVersion', '--runtime-version'),
        @('ExpectedNamespaceGeneration', '--expected-namespace-generation'),
        @('ExpectedInstallGeneration', '--expected-install-generation')
    )) {
        if ($PSBoundParameters.ContainsKey($pair[0])) {
            $forward += @($pair[1], [string]$PSBoundParameters[$pair[0]])
        }
    }
    $cellEntry = Join-Path $PSScriptRoot 'cell-install.ps1'
    $cellWorkingDirectory = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }
    Set-Location -LiteralPath $cellWorkingDirectory
    [IO.Directory]::SetCurrentDirectory($cellWorkingDirectory)
    & $cellEntry @forward
    exit $LASTEXITCODE
}

if ($Action -cne 'status') {
    $mode = & (Join-Path $PSScriptRoot 'payload-mode.ps1')
    if ($mode -cne 'legacy') {
        throw 'Governed installation requires explicit -Context; use payload init for first touch'
    }
}

. (Join-Path $PSScriptRoot 'versioned_runtime_native.ps1')

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

if (-not $env:UV_HTTP_TIMEOUT) { $env:UV_HTTP_TIMEOUT = '60' }

function Write-Ok      { param([string]$Msg) Write-Host "  [OK]   $Msg" -ForegroundColor Green }
function Write-Skip    { param([string]$Msg) Write-Host "  [SKIP] $Msg" -ForegroundColor Cyan }
function Write-Fail    { param([string]$Msg) Write-Host "  [FAIL] $Msg" -ForegroundColor Red }
function Write-Warn    { param([string]$Msg) Write-Host "  [WARN] $Msg" -ForegroundColor Yellow }
function Write-Step    { param([string]$Msg) Write-Host "  ...    $Msg" -ForegroundColor DarkGray }

$PluginDir = (Get-Item (Join-Path $PSScriptRoot '..')).FullName
$PkgSrcDir = Join-Path $PluginDir 'src\agent_pull_requests'
if (-not $InstallDir) {
    $InstallDir = Join-Path $env:USERPROFILE '.agent-pull-requests'
}
$VenvDir = Join-Path $InstallDir '.venv'
$LocalBin = Join-Path $env:USERPROFILE '.local\bin'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
$ManifestPath = Join-Path $InstallDir 'deploy-manifest.json'
$utf8NoBom = New-Object System.Text.UTF8Encoding $false

# === install-contract:v3 versioned-venv (agent-pull-requests: .venv-as-junction) ===
$LinkDir = $VenvDir
$LinkPython = $VenvPython
$VersionedRuntime = $false
$SrcVersion = $null
if ($true) {
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

$PackageVersion = $SrcVersion
$SourceRuntimeIdentity = $null
$script:uvPath = $null

function Invoke-VersionedActivate {
    param([string]$PayloadHash = '')
    Test-SourceRuntimeUnchanged
    if (-not $VersionedRuntime) { return $true }
    $vr = Join-Path $PSScriptRoot 'versioned_runtime.py'
    $py = if (Test-Path $VenvPython) { $VenvPython } else { $LinkPython }
    if (-not (Test-Path $py)) { return $true }
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $VenvPython -c 'import agent_pull_requests' 2>$null
    $slotOk = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prevEAP
    if (-not $slotOk) {
        Write-Fail "Fresh runtime slot failed its health gate (versions/$SrcVersion) -- not activating"
        return $false
    }
    if (-not (Invoke-VersionedMarkComplete -PayloadHash $PayloadHash)) {
        return $false
    }
    $prev = (& $py $vr --root $InstallDir --link-name '.venv' current 2>$null); $prev = ("$prev").Trim()
    Test-SourceRuntimeUnchanged
    & $py $vr --root $InstallDir --link-name '.venv' activate $SrcVersion --no-link 2>&1 |
        ForEach-Object { Write-Step $_ }
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Failed to activate versioned venv (.venv -> versions/$SrcVersion)"
        return $false
    }
    Write-Ok "Runtime version $SrcVersion active (.venv -> versions/$SrcVersion)"
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $gcArgs = @($vr, '--root', $InstallDir, '--link-name', '.venv', 'gc', '--protect-pids')
    if ($prev) { $gcArgs += @('--keep', $prev) }
    & $LinkPython @gcArgs 2>&1 | ForEach-Object { Write-Step "gc: $_" }
    $ErrorActionPreference = $prevEAP
    return $true
}
# === end install-contract:v3 versioned-venv ===

# === install-contract:v3 strip-trampolines -- keep byte-identical across plugins ===
function Remove-ConsoleTrampolines {
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
# === install-contract:v4 marker/toss helpers (#935) ===
function Get-BootstrapPython {
    foreach ($d in @($VenvDir, $LinkDir)) {
        # Windows venv layout first; `bin/python` is the POSIX layout pwsh sees off Windows.
        if ($d) { foreach ($rel in 'Scripts\python.exe', 'bin/python') { $p = Join-Path $d $rel; if (Test-Path $p) { return $p } } }
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $launcher = (Get-Command py -ErrorAction Stop).Source
        $exe = (Invoke-Hidden -FilePath $launcher -ArgList @('-3', '-c', 'import sys; print(sys.executable)')).Trim()
        if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Path $exe)) { return $exe }
    }
    foreach ($cand in 'python3', 'python') {
        $c = Get-Command $cand -ErrorAction SilentlyContinue
        if ($c -and $c.Source -notmatch 'WindowsApps') { return $c.Source }
    }
    # No working system Python found -- uv (already a hard install
    # prerequisite, resolved earlier into $script:uvPath) can provision one
    # on demand. Without this fallback, a genuinely python-less first
    # install would compute no payload hash at all, write an unhashed
    # completion marker, then find a REAL hash available on the very next
    # run (once the venv's own python exists) -- wrongly reporting
    # unchanged content as a payload conflict.
    if ($script:uvPath) {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $found = (& $script:uvPath python find 2>$null | Out-String).Trim()
        if (-not $found) {
            & $script:uvPath python install --quiet 2>$null | Out-Null
            $found = (& $script:uvPath python find 2>$null | Out-String).Trim()
        }
        $ErrorActionPreference = $prevEAP
        if ($found -and (Test-Path $found)) { return $found }
    }
    return $null
}

function Get-SourceRuntimeIdentity {
    param([string]$Payload = $PluginDir, [string]$ExpectedVersion = '')
    $py = Get-BootstrapPython
    if (-not $py) { throw 'Source runtime identity validation requires a bootstrap Python interpreter' }
    $arguments = @('-I', '-B', (Join-Path $PSScriptRoot 'versioned_runtime_source.py'),
        '--payload', $Payload, '--package-version', $PackageVersion,
        '--plugin', 'agent-pull-requests', '--format', 'json')
    if ($ExpectedVersion) { $arguments += @('--expected-runtime-version', $ExpectedVersion) }
    $nativeError = ''
    $detail = Invoke-Hidden -FilePath $py -ArgList $arguments -ErrorOutput ([ref]$nativeError)
    $code = $LASTEXITCODE
    if ($code -ne 0) { throw "Source runtime identity validation failed (exit ${code}): $detail$nativeError" }
    return $detail.Trim()
}

function Initialize-SourceRuntime {
    try {
        [IO.File]::GetAttributes((Join-Path $PluginDir 'runtime-source.json')) | Out-Null
    } catch [IO.FileNotFoundException] { return
    } catch [IO.DirectoryNotFoundException] { return }
    $script:SourceRuntimeIdentity = Get-SourceRuntimeIdentity
    $fields = $SourceRuntimeIdentity | ConvertFrom-Json
    if (-not $fields.PSObject.Properties['runtime_version']) {
        throw 'Source runtime identity disappeared during runtime selection'
    }
    $script:SrcVersion = $fields.runtime_version
    $script:VenvDir = Join-Path (Join-Path $InstallDir 'versions') $SrcVersion
    $script:VenvPython = if ($env:OS -eq 'Windows_NT') {
        Join-Path $VenvDir 'Scripts\python.exe'
    } else { Join-Path $VenvDir 'bin/python' }
    $script:LinkDir = $VenvDir
    $script:LinkPython = $VenvPython
}

function Test-SourceRuntimeUnchanged {
    if (-not $SourceRuntimeIdentity) {
        try {
            [IO.File]::GetAttributes((Join-Path $PluginDir 'runtime-source.json')) | Out-Null
        } catch [IO.FileNotFoundException] { return
        } catch [IO.DirectoryNotFoundException] { return }
    }
    $identity = Get-SourceRuntimeIdentity -ExpectedVersion $SrcVersion
    $expected = if ($SourceRuntimeIdentity) { $SourceRuntimeIdentity } else { '{}' }
    if ($identity -ne $expected) {
        throw 'Source runtime identity changed after runtime selection; refusing publication'
    }
}

function Get-SourceManifestFields {
    if (-not $SourceRuntimeIdentity) { Test-SourceRuntimeUnchanged }
    $fields = @{}
    if ($SourceRuntimeIdentity) {
        foreach ($property in ($SourceRuntimeIdentity | ConvertFrom-Json).PSObject.Properties) {
            $fields[$property.Name] = $property.Value
        }
    }
    return $fields
}

function Get-PayloadHash {
    # Use the canonical fingerprint_source() (libs/versioned-runtime), not a
    # bespoke pyproject.toml-only hash: a prior audit (#5472) found exactly
    # this narrower scope on another adopter -- changing src\agent_pull_requests
    # WITHOUT touching pyproject.toml must still be detected as a content
    # change, or an installed slot could wrongly report `reuse` for a
    # genuinely different payload.
    #
    # Returns '' only for the two legitimate "nothing to hash yet" cases
    # (no bootstrap Python available on a first-ever install; no declared
    # roots exist at all) -- both already degrade the same way the pre-fix
    # hash did. Any OTHER failure (the fingerprint command itself erroring,
    # e.g. on an unsupported filesystem object) THROWS: a caller must treat
    # that as a hard install failure, never silently proceed as if "no
    # content changed", which would be exactly the stale-slot-reuse this
    # seam exists to prevent.
    $vr = Join-Path $PSScriptRoot 'versioned_runtime.py'
    $py = Get-BootstrapPython
    if (-not $py) { return '' }
    $roots = @()
    $pp = Join-Path $PluginDir 'pyproject.toml'
    if (Test-Path $pp) { $roots += $pp }
    if (Test-Path $PkgSrcDir) { $roots += $PkgSrcDir }
    $libs = Join-Path $PluginDir 'libs'
    if (Test-Path $libs) {
        Get-ChildItem $libs -Recurse -Filter 'pyproject.toml' -ErrorAction SilentlyContinue |
            Sort-Object FullName | ForEach-Object { $roots += $_.FullName }
    }
    # This plugin's own dependencies on shared libraries are resolved live
    # via [tool.uv.sources] `path = "..."` entries in pyproject.toml (e.g.
    # "../../libs/work-coalescing-singleton") -- never vendored -- so
    # those libraries' OWN content is just as much a part of this plugin's
    # actual runtime payload as src\agent_pull_requests is. Fingerprint
    # each referenced library's COMPLETE directory (not just its own
    # pyproject.toml): a shared library's source changing without a
    # version bump here must still be detected as content drift.
    if (Test-Path $pp) {
        $ppText = Get-Content $pp -Raw
        $sourcesMatch = [regex]::Match($ppText, '(?ms)^\[tool\.uv\.sources\](.*?)(?=^\[|\z)')
        if ($sourcesMatch.Success) {
            $pathMatches = [regex]::Matches($sourcesMatch.Groups[1].Value, 'path\s*=\s*"([^"]*)"')
            foreach ($m in ($pathMatches | Sort-Object { $_.Groups[1].Value })) {
                $resolved = Join-Path $PluginDir $m.Groups[1].Value
                if (Test-Path $resolved) {
                    $roots += (Resolve-Path $resolved).Path
                }
            }
        }
    }
    if ($roots.Count -eq 0) { return '' }
    # Capture stdout (the hash) and stderr (diagnostics) SEPARATELY via a
    # temp file: a bare `2>&1` would merge stderr into the returned hash
    # even on success, silently contaminating it if the command ever
    # emits a warning/notice on stderr while still exiting 0.
    $fpArgs = @($vr, '--root', $InstallDir, 'fingerprint') + $roots
    $errFile = [System.IO.Path]::GetTempFileName()
    try {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $stdout = (& $py @fpArgs 2>$errFile | Out-String).Trim()
        $rc = $LASTEXITCODE
        $ErrorActionPreference = $prevEAP
        $stderrText = (Get-Content -LiteralPath $errFile -Raw -ErrorAction SilentlyContinue)
    } finally {
        Remove-Item -LiteralPath $errFile -ErrorAction SilentlyContinue
    }
    if ($rc -ne 0) {
        throw "Failed to compute payload fingerprint: $($stderrText.Trim())"
    }
    return $stdout
}

function Invoke-VersionedSlotClean {
    Test-SourceRuntimeUnchanged
    if (-not $VersionedRuntime) { throw 'Construction requires a versioned runtime candidate' }
    $vr = Join-Path $PSScriptRoot 'versioned_runtime.py'
    $py = Get-BootstrapPython
    if (-not $py) { throw 'Construction requires a bootstrap Python interpreter' }
    if (-not (Get-Command Start-RuntimeConstruction -ErrorAction SilentlyContinue)) {
        $helpers = (& $py -I $vr --root $InstallDir construction-shell --shell ps1 | Out-String)
        if ($LASTEXITCODE -ne 0) { throw 'Canonical construction adapter is unavailable' }
        Invoke-Expression $helpers
    }
    $script:RuntimeConstructionReuse = Start-RuntimeConstruction `
        -Python $py -Helper $vr -Root $InstallDir -Version $SrcVersion `
        -PayloadHash (Get-PayloadHash) -LinkName (Split-Path -Leaf $LinkDir)
    Test-SourceRuntimeUnchanged
    return $true
}

function Invoke-VersionedMarkComplete {
    param([string]$PayloadHash = '')
    $actualHash = Get-PayloadHash
    if ($PayloadHash -and $PayloadHash -ne $actualHash) {
        throw 'Payload changed during construction; completion refused'
    }
    Test-SourceRuntimeUnchanged
    Complete-RuntimeConstruction -PayloadHash $actualHash
    return $true
}

function Test-VersionedSlotComplete {
    param([string]$ExpectedHash = '')
    Test-SourceRuntimeUnchanged
    if (-not $VersionedRuntime) { return $false }
    $vr = Join-Path $PSScriptRoot 'versioned_runtime.py'
    $py = Get-BootstrapPython
    if (-not $py) { return $false }
    $args = @($vr, '--root', $InstallDir, '--link-name', (Split-Path -Leaf $LinkDir), 'is-complete', $SrcVersion)
    if ($ExpectedHash) { $args += @('--expect-hash', $ExpectedHash) }
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $py @args *> $null
    $isComplete = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prevEAP
    return $isComplete
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

. (Join-Path $PSScriptRoot '..\..\..\libs\installer-engine\installer-engine.ps1')

function Install-HookFiles {
    $BinHookDir = Join-Path $InstallDir 'bin'
    if (-not (Test-Path $BinHookDir)) { New-Item -ItemType Directory -Path $BinHookDir -Force | Out-Null }
    foreach ($h in @('bootstrap-check.ps1', 'bootstrap-check.sh', 'bootstrap-killswitch-guard.ps1', 'bootstrap-killswitch-guard.sh')) {
        $hSrc = Join-Path $PSScriptRoot $h
        if (Test-Path $hSrc) { Copy-Item $hSrc (Join-Path $BinHookDir $h) -Force }
    }
    Write-Ok "Session-start hook: $BinHookDir\bootstrap-check.ps1"
}

function Enter-InstallLock {
    param([int]$TimeoutSec = 300)
    if (-not (Test-Path $InstallDir)) { New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null }
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
            $win32 = $_.Exception.HResult -band 0xFFFF
            if ($win32 -eq 32 -or $win32 -eq 33) {
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

function Invoke-Stamp {
    Test-SourceRuntimeUnchanged
    Write-Host ''
    Write-Host '=== agent-pull-requests stamp (defer runtime to first use) ===' -ForegroundColor Cyan
    if (-not $SrcVersion) { Write-Fail 'Cannot stamp: no version in pyproject.toml'; exit 1 }
    foreach ($dir in @($InstallDir, $LocalBin)) {
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    }
    $snapDir = Join-Path (Join-Path $InstallDir 'snapshots') $SrcVersion
    $reuseSnapshot = $false
    if ($SourceRuntimeIdentity) {
        try {
            [IO.File]::GetAttributes($snapDir) | Out-Null
            $reuseSnapshot = $true
        } catch [IO.FileNotFoundException] {
        } catch [IO.DirectoryNotFoundException] { }
        if ($reuseSnapshot -and
            (Get-SourceRuntimeIdentity -Payload $snapDir -ExpectedVersion $SrcVersion) -ne $SourceRuntimeIdentity) {
            throw 'Existing source snapshot identity differs from the selected source; refusing replacement'
        }
    }
    if (-not $reuseSnapshot) {
    $snapTmp = "$snapDir.tmp-$PID"
    if (Test-Path $snapTmp) { Remove-Item $snapTmp -Recurse -Force -ErrorAction SilentlyContinue }
    New-Item -ItemType Directory -Path $snapTmp -Force | Out-Null
    $exclude = if ($SourceRuntimeIdentity) { @() } else {
        @('.git', '__pycache__', '.venv', 'node_modules', 'build', 'dist', '.pytest_cache', '.mypy_cache', 'tests')
    }
    Get-ChildItem -LiteralPath $PluginDir -Force | Where-Object { $exclude -notcontains $_.Name } | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $snapTmp $_.Name) -Recurse -Force
    }
    if ($SourceRuntimeIdentity -and
        (Get-SourceRuntimeIdentity -Payload $snapTmp -ExpectedVersion $SrcVersion) -ne $SourceRuntimeIdentity) {
        throw 'Stamped source snapshot identity changed; redirects were not published'
    }
    Test-SourceRuntimeUnchanged
    }
    $candidate = if ($SourceRuntimeIdentity) { '' } else { $snapTmp }
    Write-SimpleBinstub `
        -CommandName 'agent-pull-requests' `
        -ModuleName 'agent_pull_requests' `
        -RuntimeRoot $InstallDir `
        -LocalBin $LocalBin `
        -InstallBinDir (Join-Path $InstallDir 'bin') `
        -SnapshotInstallerPath 'scripts\install.ps1' `
        -NoSelfProvisionEnv 'AGENT_PULL_REQUESTS_NO_SELFPROVISION' `
        -ResolverPs1Source (Join-Path $PSScriptRoot 'resolve-runtime.ps1') `
        -ResolverShSource (Join-Path $PSScriptRoot 'resolve-runtime.sh') `
        -SnapshotSourcePath $candidate
    if (-not $reuseSnapshot) {
        if (Test-Path $snapDir) { Remove-Item $snapDir -Recurse -Force -ErrorAction SilentlyContinue }
        Move-Item -LiteralPath $snapTmp -Destination $snapDir -Force
    }
    Test-SourceRuntimeUnchanged
    if ($SourceRuntimeIdentity) {
        $py = Get-BootstrapPython
        if (-not $py) { throw 'Source redirect publication requires a bootstrap Python interpreter' }
        $nativeError = ''
        $result = Invoke-Hidden -FilePath $py -ArgList @(
            '-I', '-B', (Join-Path $PSScriptRoot 'versioned_runtime.py'),
            '--root', $InstallDir, 'publish-redirects', '--payload-dir', $snapDir,
            '--version', $SrcVersion, '--expected-source-identity', $SourceRuntimeIdentity
        ) -ErrorOutput ([ref]$nativeError)
        $code = $LASTEXITCODE
        if ($code -ne 0) { throw "Source redirect publication failed (exit ${code}): $result$nativeError" }
    } else {
        [System.IO.File]::WriteAllText((Join-Path $InstallDir 'payload-dir'), $snapDir, $utf8NoBom)
        [System.IO.File]::WriteAllText((Join-Path $InstallDir 'stamped-version'), $SrcVersion, $utf8NoBom)
    }
    Write-Ok "Snapshot: $snapDir"
    Install-HookFiles
    Write-Ok 'Stamped: agent-pull-requests binstub on PATH; runtime provisions on first use.'
}

$needsInstallLock = $Action -in @('install', 'update', 'stamp', 'provision', 'uninstall')
if ($Action -in @('install', 'update', 'stamp', 'provision')) { Initialize-SourceRuntime }
if ($needsInstallLock -and -not (Enter-InstallLock)) {
    Write-Fail "Timed out waiting for the install transaction lock: $(Join-Path $InstallDir '.install.lock')"
    exit 1
}

try {
    if ($Action -in @('install', 'update', 'stamp', 'provision')) { Test-SourceRuntimeUnchanged }
    if ($Action -eq 'stamp') { Invoke-Stamp; exit 0 }

    if ($Action -eq 'status') {
        Write-Host '=== agent-pull-requests status ===' -ForegroundColor Cyan
        if (Test-Path $LinkPython) { Write-Ok "Venv: $LinkDir" } else { Write-Skip "Venv missing: $LinkDir" }
        $ps1 = Join-Path $LocalBin 'agent-pull-requests.ps1'
        $cmd = Join-Path $LocalBin 'agent-pull-requests.cmd'
        if (Test-Path $ps1) { Write-Ok "Binstub: $ps1 (+ .cmd fallback)" } elseif (Test-Path $cmd) { Write-Skip "Only fallback binstub exists: $cmd" } else { Write-Skip "Binstub missing: $ps1" }
        if (Test-Path $ManifestPath) { Write-Ok "Deploy manifest: $ManifestPath" } else { Write-Skip 'Deploy manifest missing' }
        exit 0
    }

    if ($Action -eq 'uninstall') {
        Remove-Item (Join-Path $LocalBin 'agent-pull-requests.ps1') -Force -ErrorAction SilentlyContinue
        Remove-Item (Join-Path $LocalBin 'agent-pull-requests.cmd') -Force -ErrorAction SilentlyContinue
        Remove-InstallRuntimeContent -InstallRoot $InstallDir
        Write-Ok 'agent-pull-requests runtime removed (transaction/staging coordination retained)'
        exit 0
    }

    Write-Host ''
    Write-Host '=== agent-pull-requests install ===' -ForegroundColor Cyan
    Write-Host ''

    if (-not (Test-Path $PkgSrcDir)) {
        Write-Fail "Package source not found at $PkgSrcDir"
        exit 1
    }

    $uvPath = Ensure-Uv -InstallRoot $InstallDir
    if (-not $uvPath) {
        Write-Fail 'uv is required but could not be resolved or acquired'
        exit 1
    }
    # Script-scoped so Get-BootstrapPython (defined earlier, but only ever
    # CALLED after this point) can reach it as a last-resort Python
    # provisioner without needing an explicit parameter threaded through
    # every helper that transitively calls Get-BootstrapPython.
    $script:uvPath = $uvPath
    foreach ($dir in @($InstallDir, $LocalBin)) {
        if (-not (Test-Path $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
    }
    Write-Ok "Directories: $InstallDir"
    Install-HookFiles

    $payloadHash = $null
    try {
        $payloadHash = Get-PayloadHash
    } catch {
        Write-Fail "Could not compute payload fingerprint: $($_.Exception.Message)"
        exit 1
    }
    [void](Invoke-VersionedSlotClean)
    $slotAlreadyComplete = $script:RuntimeConstructionReuse
    if ($VersionedRuntime) {
        if (Test-VersionedSlotComplete -ExpectedHash $payloadHash) {
            $slotAlreadyComplete = $true
            Write-Skip "Runtime slot versions/$SrcVersion already matches the current payload"
        } elseif (Test-VersionedSlotComplete) {
            Write-Fail "Runtime slot versions/$SrcVersion is already complete for different payload content; bump the plugin version instead of rebuilding an immutable slot in place"
            if ($Force) {
                Write-Fail '-Force cannot rebuild a complete immutable runtime slot in place'
            }
            exit 1
        } elseif (Test-Path $VenvDir) {
            if (-not (Invoke-VersionedSlotClean)) { exit 1 }
        }
    }

    if (-not $slotAlreadyComplete) {
        if ($Force -or -not (Test-Path $VenvPython) -or -not (Test-Path (Join-Path $VenvDir 'pyvenv.cfg'))) {
            if (-not (New-SignedVenv -VenvDir $VenvDir -VenvPython $VenvPython -PythonVersion '3.10' -UvCommand $uvPath -RequireSignedBase ($env:OS -eq 'Windows_NT') -AllowExisting $true)) {
                Write-Fail "Venv creation failed -- $VenvPython not found"
                exit 1
            }
            Write-Ok 'Venv created'
        } else {
            Write-Skip 'Venv already exists'
        }

        Remove-ConsoleTrampolines -VenvDir $VenvDir
        $pkgResult = Invoke-UvPipInstallResilient -UvCommand $uvPath -PayloadDirToScrub $PluginDir `
            -Arguments @('--python', $VenvPython, "$PluginDir", '--quiet')
        # install-contract:v3 versioned-venv / uv pip install marker: wrapper intentionally
        # keeps the shared contract seam literal while the vendored engine owns the helper body.
        if ($pkgResult.ExitCode -ne 0) {
            Write-Fail "Package install failed (exit $($pkgResult.ExitCode))"
            if ($pkgResult.Output) { Write-Host $pkgResult.Output }
            exit 1
        }
        Remove-ConsoleTrampolines -VenvDir $VenvDir
        Write-Ok 'Package installed: agent-pull-requests'

    }
    # Source manifest inputs are captured before activation.
    if ($SourceRuntimeIdentity) {
        Test-SourceRuntimeUnchanged
        $sourceOwner = if ($env:COPILOT_PLUGIN_STAGED_FROM) { $env:COPILOT_PLUGIN_STAGED_FROM } else { $PluginDir }
        $sourceManifestGit = Get-GitInfo -Path (Split-Path $sourceOwner)
    }
    if (-not (Invoke-VersionedActivate -PayloadHash $payloadHash)) { exit 1 }

    Write-SimpleBinstub `
        -CommandName 'agent-pull-requests' `
        -ModuleName 'agent_pull_requests' `
        -RuntimeRoot $InstallDir `
        -LocalBin $LocalBin `
        -InstallBinDir (Join-Path $InstallDir 'bin') `
        -SnapshotInstallerPath 'scripts\install.ps1' `
        -NoSelfProvisionEnv 'AGENT_PULL_REQUESTS_NO_SELFPROVISION' `
        -ResolverPs1Source (Join-Path $PSScriptRoot 'resolve-runtime.ps1') `
        -ResolverShSource (Join-Path $PSScriptRoot 'resolve-runtime.sh')

    $sourceFields = Get-SourceManifestFields
    Write-DeployManifest `
        -Service 'agent-pull-requests' `
        -Plugin 'agent-pull-requests' `
        -InstallPath $InstallDir `
        -PluginPath $PluginDir `
        -VenvPath $VenvDir `
        -GetSourceKind ${function:Get-SourceKind} `
        -GetGitInfo {
            param($path)
            if ($SourceRuntimeIdentity) { $sourceManifestGit }
            else { Get-GitInfo -Path $path }
        } `
        -PayloadHash $payloadHash `
        -VersionOverride $PackageVersion `
        -AdditionalFields $sourceFields

    Write-Host ''
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $LinkPython -c 'import agent_pull_requests' 2>$null
    $importOk = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prevEAP
    if ($importOk) {
        Write-Ok 'Verification: module imports successfully'
    } else {
        Write-Fail 'Verification: module import failed'
        exit 1
    }

    if ($env:PATH -notlike "*$LocalBin*") {
        Write-Step "Add $LocalBin to your PATH"
    } else {
        Write-Ok "PATH: $LocalBin is on PATH"
    }

    Write-Host ''
    Write-Host '=== agent-pull-requests install complete ===' -ForegroundColor Cyan
    Write-Host '  Try: agent-pull-requests --version' -ForegroundColor DarkGray
    exit 0
} finally {
    try {
        if (Test-Path -LiteralPath 'Function:\Stop-RuntimeConstruction') {
            Stop-RuntimeConstruction
        }
    } finally {
        if ($needsInstallLock) { Exit-InstallLock }
    }
}
