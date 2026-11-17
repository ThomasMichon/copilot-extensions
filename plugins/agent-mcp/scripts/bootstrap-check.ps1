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

$ErrorActionPreference = 'Stop'
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
try {
    if ($env:COPILOT_EXTENSIONS_CONTEXT) { Exit-SessionStart }
    $location = Get-Location
    if ($location.Provider.Name -cne 'FileSystem') { throw 'Bootstrap requires a filesystem project location.' }
    $project = $location.ProviderPath
    $name = ([IO.File]::ReadAllText((Join-Path $PluginDir 'plugin.json')) | ConvertFrom-Json).name
    if ($name -isnot [string] -or $name -cne 'agent-mcp') { throw 'The owning payload is not Agent MCP.' }
    $InstallDir = Join-Path $env:USERPROFILE '.agent-mcp'
    $Manifest = Join-Path $InstallDir 'deploy-manifest.json'
    $installerAction = 'stamp'
    if (Test-Path -LiteralPath $Manifest) {
        $deployment = Get-Content $Manifest -Raw | ConvertFrom-Json
        $deployed = $deployment.source.version
        if ($deployed -isnot [string] -or -not $deployed) { throw 'The deployed runtime version is unavailable.' }
        $current = ''
        $pyproj = Join-Path $PluginDir 'pyproject.toml'
        if (Test-Path $pyproj) {
            $vl = Select-String -Path $pyproj -Pattern '^\s*version\s*=' | Select-Object -First 1
            if ($vl) { $current = ($vl.Line -replace '.*=\s*"([^"]+)".*', '$1') }
        }
        if (-not $current) { throw 'The owning payload version is unavailable.' }
        $provisioned = $false
        $expectedSlot = $current
        $sourceReceipt = Join-Path $PluginDir 'runtime-source.json'
        if ((Test-Path -LiteralPath $sourceReceipt) -or $deployment.runtime_source) {
            $expectedSlot = ''
            $native = Join-Path $PSScriptRoot 'versioned_runtime_native.ps1'
            if ([IO.File]::Exists($native)) {
                . $native
                foreach ($candidate in @(Get-Command python3, python, py -All -CommandType Application -ErrorAction SilentlyContinue |
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
                if (-not $expectedSlot -and $detail) { [Console]::Error.WriteLine($detail) }
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
                            $AgentRtPy -eq (Join-Path $InstallDir "versions\$cv\bin\python") -or
                            $AgentRtPy -eq (Join-Path $InstallDir "versions\$cv\.venv\Scripts\python.exe") -or
                            $AgentRtPy -eq (Join-Path $InstallDir "versions\$cv\.venv\bin\python"))
                    } finally {
                        $env:AGENT_RT_ROOT = $previousRoot
                    }
                }
            }
        }
        if ($provisioned -and $deployed -eq $current) { Exit-SessionStart }
        $installerAction = 'provision'
    }
    if ($env:AGENT_MCP_NO_SELFPROVISION -or (Test-Path Env:AGENT_MCP_PINNED_CONTEXT)) {
        [Console]::Error.WriteLine('[agent-mcp] bootstrap deferred: self-provisioning is disabled or identity reads are pinned.')
        Exit-SessionStart
    }
    if (Test-Path Env:UPDATE_DRIVER_WITNESS) {
        throw 'Nested bootstrap submission is unsupported; retain the current installation owner.'
    }
    if (-not [StringComparer]::OrdinalIgnoreCase.Equals(
        [IO.Path]::GetDirectoryName([IO.Path]::GetDirectoryName($PluginDir)),
        (Join-Path $env:USERPROFILE '.copilot\installed-plugins')
    ) -or [IO.Path]::GetFileName($PluginDir) -cne 'agent-mcp') {
        throw 'MCP bootstrap publication requires its canonical installed payload.'
    }
    $executionRoot = Join-Path $env:USERPROFILE '.copilot-extensions\update-driver'
    $suffix = if ($env:OS -eq 'Windows_NT') { 'ps1' } else { 'sh' }
    $firstUse = Join-Path $PluginDir "libs/update-driver/scripts/first-use.$suffix"
    if (-not (Test-Path -LiteralPath $firstUse -PathType Leaf) -or -not (
        Select-String -LiteralPath $firstUse -SimpleMatch `
            '# Installation driver lifecycle contract: mcp-recipes-v1' -Quiet
    )) { throw 'The packaged installation driver lacks the MCP recipe lifecycle contract.' }
    $previousEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        if ($suffix -ceq 'ps1') {
            $shell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
            $acknowledgement = @(& $shell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
                -File $firstUse -Root $executionRoot -Payload $PluginDir `
                -RuntimeRoot $InstallDir -Project $project -InstallerAction $installerAction)
        } else {
            $shell = Get-Command bash -CommandType Application -ErrorAction Stop | Select-Object -First 1
            $acknowledgement = @(& $shell.Source $firstUse $executionRoot $PluginDir $InstallDir `
                $project --installer-action $installerAction)
        }
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previousEap }
    if ($code -ne 0) { throw "Installation driver admission failed (exit $code)." }
    $acknowledgementText = $acknowledgement -join "`n"
    if ($suffix -ceq 'ps1') {
        if ($acknowledgementText -cnotmatch '\A\s*\{') { throw 'Installation driver launch acknowledgement is unavailable.' }
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
        "[agent-mcp] bootstrap $installerAction supervision launched; inspect first-use diagnostics in $executionRoot. " +
        'Launch is not durable request acceptance.'
    )
    [Console]::Error.WriteLine('[agent-mcp] driver launch acknowledgement: ' + $acknowledgementText)
} catch {
    [Console]::Error.WriteLine("[agent-mcp] Runtime publication refused: $($_.Exception.Message)")
    Exit-SessionStart -Code 3
}
Exit-SessionStart