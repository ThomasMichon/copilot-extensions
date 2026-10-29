# --- bootstrap-killswitch guard (vendored; see libs/bootstrap-killswitch/README.md) ---
# One shared, repo-wide switch (not per-plugin) that pauses EVERY adopting
# plugin's reconcile-on-session-start at once, for when an operator/agent is
# hand-diagnosing a venv/install and a background reconcile must not race it.
# Legacy/default installation ONLY: a namespaced marketplace cell
# (COPILOT_EXTENSIONS_CONTEXT set) reconciles through its own cell-scoped
# mechanism, never this global state file -- crossing that installation-cell
# boundary would let one marketplace's switch pause an unrelated,
# independently-owned cell's reconcile (visions/plugin-services/
# installation-cells). This guard is therefore a deliberate no-op under a
# cell context, same as this hook's own existing cell-context exit below.
if (-not $env:COPILOT_EXTENSIONS_CONTEXT) {
  $_bksGuardDir = Split-Path -Parent $MyInvocation.MyCommand.Path
  $_bksGuard = Join-Path $_bksGuardDir "bootstrap-killswitch-guard.ps1"
  if (Test-Path -LiteralPath $_bksGuard) {
    & $_bksGuard check
    if ($LASTEXITCODE -eq 0) { [Console]::Out.Write('{}'); exit 0 }
  }
}
# --- end bootstrap-killswitch guard ---

<#
    Session-start runtime reconcile -- generic, self-locating; shipped
    byte-identical across agent-* runtime plugins. Invoked (via hooks.json) from
    the plugin's own scripts/ dir. Derives the plugin from its own location and
    the install dir from plugin.json's name (~/.<name>), then re-runs the
    installer in the BACKGROUND only when the deployed runtime version drifts
    from the payload -- so a `copilot plugin update` is picked up automatically.
    Reconciles the TOOL, never machine state/config. PS5.1+.

    NO OPT-IN GATE (agent-bridge-unified-zdd-cutover Phase 0): background
    reconcile used to require a checked-in, per-project opt-in flag in
    ``<project>/.copilot-extensions/config.yaml``, because a raw reconcile
    could race a live daemon/session. Now that every reconcile-capable
    plugin's update path is always-ZDD (safe to run unattended), that
    justification is gone; the gate was removed rather than kept as a
    redundant consent checkbox. Frequency/trigger stays deliberately
    bounded -- still only once per session start, only on a real version
    drift. What DOES still bound concurrency is the single-flight +
    stale-reap guard below (a lock file, not the removed opt-in).
#>
$ErrorActionPreference = 'SilentlyContinue'
$script:SessionStartJsonEmitted = $false
function Write-SessionStartJson {
    if (-not $script:SessionStartJsonEmitted) {
        [Console]::Out.Write('{}')
        $script:SessionStartJsonEmitted = $true
    }
}
function Exit-SessionStart {
    Write-SessionStartJson
    exit 0
}
$PluginDir = Split-Path -Parent $PSScriptRoot
function Assert-BootstrapPublication {
    $helper = Join-Path $PSScriptRoot 'installation-context\plugin-publication-probe.ps1'
    if ([IO.File]::Exists($helper)) {
        try {
            $global:LASTEXITCODE = 3
            & $helper -PayloadRoot $PluginDir -PluginId $name -LegacyRoot $InstallDir
            if ($LASTEXITCODE -eq 0) { return }
        } catch {
            [Console]::Error.WriteLine($_.Exception.Message)
        }
    } else {
        try {
            $null = Get-Item -LiteralPath (Join-Path $InstallDir 'runtime-deployment.json') -Force -ErrorAction Stop
        } catch [System.Management.Automation.ItemNotFoundException] {
            return
        } catch {
            [Console]::Error.WriteLine($_.Exception.Message)
        }
        [Console]::Error.WriteLine("[$name] Runtime publication refused: deployment policy requires a capable installation helper.")
    }
    [Console]::Error.WriteLine("[$name] Runtime publication refused: bootstrap admission did not succeed. Use explicit installation management to repair it.")
    Write-SessionStartJson
    exit 3
}
try {
    if ($env:COPILOT_EXTENSIONS_CONTEXT) { Exit-SessionStart }
    $name = (Get-Content (Join-Path $PluginDir 'plugin.json') -Raw | ConvertFrom-Json).name
    if (-not $name) { Exit-SessionStart }
    $InstallDir = Join-Path $env:USERPROFILE ".$name"
    $Manifest = Join-Path $InstallDir 'deploy-manifest.json'
    if (-not (Test-Path $Manifest)) {
        # Not provisioned yet -- do the cheap FIRST install ('stamp') so the
        # binstub is on PATH this session; the self-provisioning binstub then
        # builds the venv on first use (#1393). Fires only when the installer
        # (init.ps1 or install.ps1) declares a 'stamp' action; else a safe no-op.
        $stampInst = @("$PluginDir\scripts\init.ps1", "$PluginDir\scripts\install.ps1") |
            Where-Object { (Test-Path $_) -and (Select-String -Path $_ -Pattern "'stamp'" -Quiet) } |
            Select-Object -First 1
        if ($stampInst) {
            Assert-BootstrapPublication
            $pw = Get-Command pwsh -ErrorAction SilentlyContinue
            $exe = if ($pw) { $pw.Source } else { 'powershell.exe' }
            & $exe -NoProfile -ExecutionPolicy Bypass -File $stampInst stamp *> $null
        }
        Exit-SessionStart
    }
    $deployment = Get-Content $Manifest -Raw | ConvertFrom-Json
    $deployed = "" + $deployment.source.version
    $current = $deployed
    $pyproj = Join-Path $PluginDir 'pyproject.toml'
    if (Test-Path $pyproj) {
        $vl = Select-String -Path $pyproj -Pattern '^\s*version\s*=' | Select-Object -First 1
        if ($vl) { $current = ($vl.Line -replace '.*=\s*"([^"]+)".*', '$1') }
    }
    $provisioned = $false
    $expectedSlot = $current
    $sourceReceipt = Join-Path $PluginDir 'runtime-source.json'
    if ((Test-Path -LiteralPath $sourceReceipt) -or $deployment.runtime_source) {
        $expectedSlot = ''
        $native = Join-Path $PSScriptRoot 'versioned_runtime_native.ps1'
        if ([IO.File]::Exists($native)) {
            . $native
            foreach ($candidate in @(Get-Command python3, python, py -All -CommandType Application |
                    Where-Object { $_.Source -notmatch '[\\/]WindowsApps[\\/]' })) {
                $arguments = @()
                if ($candidate.Name -match '^py(\.exe)?$') { $arguments += '-3' }
                $arguments += @('-I', '-B', (Join-Path $PSScriptRoot 'versioned_runtime_readiness.py'),
                    '--payload', $PluginDir, '--package-version', $current, '--plugin', $name,
                    '--deployment-manifest', $Manifest)
                try {
                    $detail = ''
                    $value = Invoke-Hidden -FilePath $candidate.Source -ArgList $arguments -ErrorOutput ([ref]$detail)
                    if ($LASTEXITCODE -eq 0 -and $value) {
                        $expectedSlot = $value.Trim()
                        break
                    }
                } catch { }
            }
        }
    } elseif ($deployment.runtime_version) {
        $expectedSlot = $deployment.runtime_version
        $separator = if ($current.Contains('+')) { '.' } else { '+' }
        $repairPattern = '^' + [regex]::Escape($current + $separator) + 'repair\.[0-9a-f]{32}$'
        if ($expectedSlot -ne $current -and $expectedSlot -cnotmatch $repairPattern) { $expectedSlot = '' }
    }
    $cvMarker = Join-Path $InstallDir 'current-version'
    if ($deployed -eq $current -and $expectedSlot) {
        if (-not (Test-Path -LiteralPath $cvMarker) -and
            -not (Test-Path -LiteralPath $sourceReceipt) -and $expectedSlot -eq $current -and
            (Test-Path -LiteralPath (Join-Path $InstallDir '.venv'))) {
            $provisioned = $true
        } elseif ([IO.File]::Exists($cvMarker)) {
            $cv = [IO.File]::ReadAllText($cvMarker).Trim()
            $resolver = Join-Path $PSScriptRoot 'resolve-runtime.ps1'
            if ($cv -ceq $expectedSlot -and [IO.File]::Exists($resolver)) {
                $previousRoot = $env:AGENT_RT_ROOT
                try {
                    $env:AGENT_RT_ROOT = $InstallDir
                    . $resolver -TraceMode read-only
                    $provisioned = $AgentRtPy -and (
                        $AgentRtPy -eq (Join-Path $InstallDir "versions\$cv\Scripts\python.exe") -or
                        $AgentRtPy -eq (Join-Path $InstallDir "versions\$cv\bin\python"))
                } finally {
                    $env:AGENT_RT_ROOT = $previousRoot
                }
            }
        }
    }
    if ($provisioned -and $deployed -eq $current) { Exit-SessionStart }

    $init = Join-Path $PluginDir 'scripts\init.ps1'
    if (Test-Path $init) {
        $reCmd = "& `"$init`""
    } else {
        $inst = Join-Path $PluginDir 'scripts\install.ps1'
        if (-not (Test-Path $inst)) { Exit-SessionStart }
        $reCmd = "& `"$inst`" install"
    }

    Assert-BootstrapPublication

    # --- Good boot-citizen guard: single-flight + stale-reap ---
    # This hook fires on EVERY new session now that the opt-in gate is gone
    # (agent-bridge-unified-zdd-cutover Phase 0 review finding): without
    # this, a slow or wedged reconcile gets re-spawned every session,
    # stacking orphaned background installers. If a prior reconcile PID
    # recorded in reconcile.lock is still alive:
    #   YOUNG  -> a reconcile is already in flight; do nothing (never stack).
    #   STALE  -> it is wedged; reap it, then relaunch (self-heal, so a
    #             one-off wedge can't poison every future session).
    $staleSeconds = 600
    $lockFile = Join-Path $InstallDir 'reconcile.lock'
    try {
        if (Test-Path $lockFile) {
            $lockPid = 0
            [void][int]::TryParse((Get-Content $lockFile -Raw -ErrorAction SilentlyContinue), [ref]$lockPid)
            if ($lockPid -gt 0 -and (Get-Process -Id $lockPid -ErrorAction SilentlyContinue)) {
                $ageSec = ((Get-Date) - (Get-Item $lockFile).LastWriteTime).TotalSeconds
                if ($ageSec -lt $staleSeconds) { Exit-SessionStart }         # in flight -- don't stack
                Stop-Process -Id $lockPid -Force -ErrorAction SilentlyContinue  # wedged -- reap
            }
        }
    } catch { }

    [Console]::Error.WriteLine("[$name] runtime $deployed -> $current; reconciling in background...")
    $pw = Get-Command pwsh -ErrorAction SilentlyContinue
    $exe = if ($pw) { $pw.Source } else { 'powershell.exe' }
    # Launch the background reconcile through conhost --headless so Windows
    # Terminal / the DefTerm handoff cannot surface it as a visible window --
    # -WindowStyle Hidden ALONE is ignored by DefTerm (proven pattern; see
    # agent-bridge). The reconcile command is base64-encoded to avoid any arg
    # quoting under conhost; children (uv/python building the venv) inherit the
    # headless console and stay hidden too.
    $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($reCmd))
    $proc = Start-Process -FilePath 'conhost.exe' -WorkingDirectory $env:USERPROFILE -PassThru -WindowStyle Hidden `
        -ArgumentList @('--headless', "`"$exe`"", '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-EncodedCommand', $enc)
    try {
        if ($proc) { Set-Content -LiteralPath $lockFile -Value ([string]$proc.Id) -NoNewline -ErrorAction SilentlyContinue }
    } catch { }
} catch { }
Exit-SessionStart