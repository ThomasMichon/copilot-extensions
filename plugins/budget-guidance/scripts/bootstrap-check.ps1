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
    Session-start Budget reconcile. The first stamp stays Python/uv-free.
    Windows/Linux drift submits the fixed install action to the shared owner;
    closed-wheel readiness uses the prepared managed interpreter.
    Reconciles the TOOL, never machine state/config. PS5.1+.

    NO OPT-IN GATE (agent-bridge-unified-zdd-cutover Phase 0): background
    reconcile used to require a checked-in, per-project opt-in flag in
    ``<project>/.copilot-extensions/config.yaml``, because a raw reconcile
    could race a live daemon/session. Now that every reconcile-capable
    plugin's update path is always-ZDD (safe to run unattended), that
    justification is gone; the gate was removed rather than kept as a
    redundant consent checkbox. Frequency/trigger stays deliberately
    bounded -- still only once per session start, only on a real version
    drift. A legacy reconcile record defers automatic work; it cannot prove
    process ownership or authorize retirement.
#>
$ErrorActionPreference = 'Stop'
$hookClock = [Diagnostics.Stopwatch]::StartNew()
$OutputEncoding = [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$script:SessionStartJsonEmitted = $false
function Write-SessionStartJson {
    if (-not $script:SessionStartJsonEmitted) {
        [Console]::Out.Write('{}')
        $script:SessionStartJsonEmitted = $true
    }
}
function Exit-SessionStart {
    param([int]$Code = 0)
    Write-SessionStartJson
    exit $Code
}
$PluginDir = Split-Path -Parent $PSScriptRoot
function Test-BudgetManagedRuntimeCurrent {
    param($Deployment)
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $bootstrap = $env:COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON
    if (-not $bootstrap) {
        if ($Deployment.service -cne 'budget-guidance') { return $false }
        $bootstrap = $Deployment.bootstrap_python
    }
    if ($bootstrap -isnot [string] -or -not [IO.Path]::IsPathRooted($bootstrap) -or
        -not [IO.File]::Exists($bootstrap)) {
        return $false
    }
    . (Join-Path $PSScriptRoot 'bootstrap-uv.ps1')
    . (Join-Path $PSScriptRoot 'versioned_runtime_native.ps1')
    $processHelper = Join-Path $PSScriptRoot 'bootstrap_uv_capture.py'
    $selected = Resolve-UvBootstrapPython `
        -RequestFile (Join-Path $PSScriptRoot 'bootstrap-python-version.txt') `
        -Operation Inspect -IsolateSelection -InvokeCaptured {
            param($FilePath, $ArgList, $Environment)
            $errors = ''
            $remaining = [int][Math]::Max(0, 6000 - $clock.ElapsedMilliseconds)
            if ($remaining -le 1000) { throw 'Readiness deadline exceeded.' }
            $output = Invoke-UvBootstrapCapturedProcess -BootstrapPython $bootstrap `
                -ProcessHelper $processHelper -FilePath $FilePath -ArgList $ArgList `
                -Environment $Environment -ErrorOutput ([ref]$errors) `
                -TimeoutMilliseconds $remaining
            [pscustomobject]@{
                ExitCode = $global:LASTEXITCODE; Stdout = [string]$output; Stderr = $errors
            }
        }
    if ($selected.Status -cne 'selected') { return $false }
    $remaining = [int][Math]::Max(0, 6000 - $clock.ElapsedMilliseconds)
    if ($remaining -le 1000) { throw 'Readiness deadline exceeded.' }
    $errors = ''
    [void](Invoke-Hidden -FilePath $selected.Python -ArgList @(
        '-I', '-B', (Join-Path $PSScriptRoot 'install_source.py'),
        '--native-current', '--root', $InstallDir, '--payload', $PluginDir, '--component', $name
    ) -Environment (Get-UvBootstrapEnvironment) -ErrorOutput ([ref]$errors) `
        -TimeoutMilliseconds $remaining -CleanupMilliseconds 1000)
    if ($LASTEXITCODE -notin @(0, 1)) { throw "Managed runtime readiness failed: $errors" }
    return $LASTEXITCODE -eq 0
}
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
    if ($name -isnot [string] -or $name -cne 'budget-guidance') {
        throw 'The owning payload is not Budget Guidance.'
    }
    $InstallDir = Join-Path $env:USERPROFILE ".$name"
    $Manifest = Join-Path $InstallDir 'deploy-manifest.json'
    $manifestPresent = $true
    try { $null = [IO.File]::GetAttributes($Manifest) }
    catch [IO.FileNotFoundException] { $manifestPresent = $false }
    catch [IO.DirectoryNotFoundException] { $manifestPresent = $false }
    if (-not $manifestPresent) {
        if ($env:BUDGET_GUIDANCE_NO_SELFPROVISION -or (Test-Path Env:BUDGET_GUIDANCE_PINNED_CONTEXT)) {
            [Console]::Error.WriteLine("[$name] bootstrap deferred: self-provisioning is disabled or identity reads are pinned.")
            Exit-SessionStart
        }
        # Not provisioned yet -- do the cheap FIRST install ('stamp') so the
        # binstub is on PATH this session; explicit snapshot provision builds
        # the runtime before command use. Fires only when the installer
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
    $manifestFile = Get-Item -LiteralPath $Manifest -Force
    if ($manifestFile.PSIsContainer -or $manifestFile.Length -gt 65536 -or
        ($manifestFile.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'The deployment manifest is not a bounded plain file.'
    }
    $deployment = [IO.File]::ReadAllText($manifestFile.FullName) | ConvertFrom-Json
    $runtimeVersion = if ($deployment.PSObject.Properties['runtime_version']) {
        "" + $deployment.runtime_version
    } else { '' }
    $deployed = $deployment.source.version
    if ($deployed -isnot [string] -or -not $deployed) {
        throw 'The deployed runtime version is unavailable.'
    }
    $current = $deployed
    $pyproj = Join-Path $PluginDir 'pyproject.toml'
    if (Test-Path $pyproj) {
        $vl = Select-String -Path $pyproj -Pattern '^\s*version\s*=' | Select-Object -First 1
        if ($vl) { $current = ($vl.Line -replace '.*=\s*"([^"]+)".*', '$1') }
    }
    # "Provisioned" no longer implies a `.venv`: the marker runtime model (#581)
    # publishes the active slot via a `current-version` marker with NO junction on
    # Windows (RedirectionGuard), so a healthy current runtime has no `.venv` there.
    # Treat a marker whose slot python exists as provisioned too -- otherwise a
    # current runtime needlessly background-rebuilds every session.
    if ($runtimeVersion -cmatch '\.profile\.[0-9a-f]{16}') {
        $provisioned = Test-BudgetManagedRuntimeCurrent -Deployment $deployment
    } else {
        $provisioned = Test-Path (Join-Path $InstallDir '.venv')
        if (-not $provisioned) {
            $cvMarker = Join-Path $InstallDir 'current-version'
            if (Test-Path $cvMarker) {
                $cv = ('' + (Get-Content $cvMarker -Raw)).Trim()
                if ($cv -and ($cv -eq $current -or $cv -eq $runtimeVersion) -and ((Test-Path (Join-Path $InstallDir "versions\$cv\Scripts\python.exe")) -or (Test-Path (Join-Path $InstallDir "versions/$cv/bin/python")))) { $provisioned = $true }
            }
        }
    }
    if ($provisioned -and $deployed -eq $current) { Exit-SessionStart }

    if ($env:BUDGET_GUIDANCE_NO_SELFPROVISION -or (Test-Path Env:BUDGET_GUIDANCE_PINNED_CONTEXT)) {
        [Console]::Error.WriteLine("[$name] bootstrap deferred: self-provisioning is disabled or identity reads are pinned.")
        Exit-SessionStart
    }

    Assert-BootstrapPublication

    # A PID and file age cannot identify an owned process, even if it is live.
    # Keep automatic work off an unsettled legacy owner; explicit setup is separate.
    $lockFile = Join-Path $InstallDir 'reconcile.lock'
    try {
        $null = [IO.File]::GetAttributes($lockFile)
        [Console]::Error.WriteLine("[$name] bootstrap deferred: legacy reconcile ownership is unsettled; use explicit installation management.")
        Exit-SessionStart
    } catch [IO.FileNotFoundException] {
    } catch [IO.DirectoryNotFoundException] {
    }

    if (Test-Path Env:UPDATE_DRIVER_WITNESS) {
        throw 'Nested bootstrap submission is unsupported; retain the current installation owner.'
    }
    if (-not [StringComparer]::OrdinalIgnoreCase.Equals(
        [IO.Path]::GetDirectoryName([IO.Path]::GetDirectoryName($PluginDir)),
        (Join-Path $env:USERPROFILE '.copilot\installed-plugins')
    ) -or [IO.Path]::GetFileName($PluginDir) -cne 'budget-guidance') {
        throw 'Budget bootstrap publication requires its canonical installed payload.'
    }
    $location = Get-Location
    if ($location.Provider.Name -cne 'FileSystem') { throw 'Bootstrap requires a filesystem project location.' }
    $project = $location.ProviderPath
    $executionRoot = Join-Path $env:USERPROFILE '.copilot-extensions\update-driver'
    $suffix = if ($env:OS -eq 'Windows_NT') { 'ps1' } else { 'sh' }
    $firstUse = Join-Path $PluginDir "libs/update-driver/scripts/first-use.$suffix"
    if (-not [IO.File]::Exists($firstUse) -or -not (
        Select-String -LiteralPath $firstUse -SimpleMatch `
            '# Installation driver lifecycle contract: budget-install-v1' -Quiet
    )) { throw 'The packaged installation driver lacks the Budget install lifecycle contract.' }
    if ($suffix -ceq 'ps1') {
        $shell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $arguments = @('-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
            '-File', $firstUse, '-Root', $executionRoot, '-Payload', $PluginDir,
            '-RuntimeRoot', $InstallDir, '-Project', $project, '-InstallerAction', 'install')
    } else {
        $shell = (Get-Command bash -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
        $arguments = @($firstUse, $executionRoot, $PluginDir, $InstallDir, $project, '--installer-action', 'install')
    }
    . (Join-Path $PSScriptRoot 'versioned_runtime_native.ps1')
    $driverError = ''
    $remaining = [int][Math]::Max(0, 14000 - $hookClock.ElapsedMilliseconds)
    if ($remaining -le 1000) { throw 'Bootstrap deadline expired before driver admission.' }
    $acknowledgementText = Invoke-Hidden -FilePath $shell -ArgList $arguments `
        -ErrorOutput ([ref]$driverError) -TimeoutMilliseconds $remaining -CleanupMilliseconds 1000 `
        -CaptureEncoding ([Text.UTF8Encoding]::new($false)) -InputText ''
    if ($LASTEXITCODE -ne 0) { throw "Installation driver admission failed (exit $LASTEXITCODE): $driverError" }
    $acknowledgementText = $acknowledgementText.TrimEnd("`r", "`n")
    if ($suffix -ceq 'ps1') {
        $ack = $acknowledgementText | ConvertFrom-Json -ErrorAction Stop
        if ($ack.launched -isnot [bool] -or -not $ack.launched -or
            ($ack.pid -isnot [int] -and $ack.pid -isnot [long]) -or $ack.pid -le 0 -or
            ($ack.created -isnot [int] -and $ack.created -isnot [long]) -or $ack.created -le 0 -or
            $ack.log -isnot [string]) {
            throw 'Installation driver launch acknowledgement is malformed.'
        }
        $acknowledgedLog = $ack.log
        $logPattern = '\Afirst-use-[0-9a-f]{32}\.log\z'
        $comparer = [StringComparer]::OrdinalIgnoreCase
    } else {
        if ($acknowledgementText -cnotmatch '\Alaunched:([1-9][0-9]*) log:(.+)\z') {
            throw 'Installation driver launch acknowledgement is malformed.'
        }
        $acknowledgedLog = $Matches[2]
        $logPattern = '\Afirst-use-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.log\z'
        $comparer = [StringComparer]::Ordinal
    }
    if (-not $comparer.Equals([IO.Path]::GetDirectoryName($acknowledgedLog), $executionRoot) -or
        [IO.Path]::GetFileName($acknowledgedLog) -cnotmatch $logPattern) {
        throw 'Installation driver launch diagnostics name another execution root.'
    }
    [Console]::Error.WriteLine(
        "[$name] bootstrap install supervision launched; inspect first-use diagnostics in $executionRoot. " +
        'Launch is not durable request acceptance.'
    )
    [Console]::Error.WriteLine("[$name] driver launch acknowledgement: $acknowledgementText")
} catch {
    [Console]::Error.WriteLine("[budget-guidance] Runtime publication refused: $($_.Exception.Message)")
    Exit-SessionStart -Code 3
}
Exit-SessionStart