$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$OutputEncoding = [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$PluginDir = Split-Path -Parent $PSScriptRoot
$Root = if ($env:AGENT_INDEX_HOME) { $env:AGENT_INDEX_HOME } else { Join-Path $env:USERPROFILE '.agent-index' }
$Resolver = Join-Path $PSScriptRoot 'resolve-runtime.ps1'
$InvocationArgs = @($args)
$Command = if ($InvocationArgs.Count -gt 0) { [string]$InvocationArgs[0] } else { 'status' }
$VersionLine = Select-String -LiteralPath (Join-Path $PluginDir 'pyproject.toml') -Pattern '^\s*version\s*=' | Select-Object -First 1
$PackageVersion = if ($VersionLine) { $VersionLine.Line -replace '.*=\s*"([^"]+)".*', '$1' } else { '' }

function Get-ConfiguredRole {
    $role = if ($env:AGENT_INDEX_ROLE) { $env:AGENT_INDEX_ROLE.Trim().ToLowerInvariant() } else { '' }
    if ($role -in @('host', 'client')) { return $role }
    $config = if ($env:AGENT_INDEX_CONFIG) { $env:AGENT_INDEX_CONFIG } else { Join-Path $Root 'config.yaml' }
    if (Test-Path -LiteralPath $config -PathType Leaf) {
        $match = Select-String -LiteralPath $config -Pattern '^\s*(?:role|engine)\s*:\s*["'']?([A-Za-z]+)' -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($match) { $role = $match.Matches[0].Groups[1].Value.ToLowerInvariant() }
    }
    if ($role -in @('host', 'engine', 'server', 'indexer')) { return 'host' }
    if ($role -in @('client', 'none', 'consumer')) { return 'client' }
    return $null
}

function Resolve-ReadyRuntime {
    $AgentRtPy = $null
    if (Test-Path -LiteralPath $Resolver -PathType Leaf) {
        $env:AGENT_RT_ROOT = $Root
        . $Resolver
    }
    if ($AgentRtPy) {
        $previous = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & $AgentRtPy -c 'import agent_index' *> $null
        $ready = $LASTEXITCODE -eq 0
        $ErrorActionPreference = $previous
        if ($ready) { return $AgentRtPy }
    }
    return $null
}

function Get-RuntimeState([string]$Python) {
    if ($Python) { return 'ready' }
    $versions = Join-Path $Root 'versions'
    $hasSlot = Test-Path -LiteralPath (Join-Path $Root 'current-version') -PathType Leaf
    $hasSlot = $hasSlot -or (Test-Path -LiteralPath (Join-Path $Root 'last-known-good') -PathType Leaf)
    if (-not $hasSlot -and (Test-Path -LiteralPath $versions -PathType Container)) {
        $hasSlot = $null -ne (Get-ChildItem -LiteralPath $versions -Directory -ErrorAction SilentlyContinue | Select-Object -First 1)
    }
    if ($hasSlot) { return 'broken' }
    foreach ($name in @('payload-dir', 'stamped-version', 'deploy-manifest.json')) {
        if (Test-Path -LiteralPath (Join-Path $Root $name) -PathType Leaf) { return 'stamped' }
    }
    return 'absent'
}

function Write-SetupRequired(
    [string]$RuntimeState,
    [string]$ErrorMessage = ''
) {
    $payload = [ordered]@{
        schema = 'agent-index.lifecycle'
        schema_version = 1
        version = $PackageVersion
        plugin = 'agent-index'
        state = 'setup_required'
        setup_required = $true
        configured = $false
        role = $null
        running = $false
        runtime = [ordered]@{ state = $RuntimeState }
        setup = [ordered]@{
            interactive = 'agent-index setup'
            noninteractive = @(
                'agent-index setup --single --yes'
                'agent-index setup --indexer <machine> --ssh <alias> --yes'
            )
        }
    }
    if ($ErrorMessage) { $payload.error = $ErrorMessage }
    $payload | ConvertTo-Json -Compress -Depth 4
}

function Write-RuntimeUnavailable([string]$RuntimeState, [string]$ConfiguredRole) {
    [ordered]@{
        schema = 'agent-index.lifecycle'
        schema_version = 1
        version = $PackageVersion
        plugin = 'agent-index'
        state = 'runtime_unavailable'
        setup_required = $false
        configured = $true
        role = $ConfiguredRole
        running = $false
        runtime = [ordered]@{ state = $RuntimeState }
    } | ConvertTo-Json -Compress -Depth 3
}

function Write-Readiness([string]$State, [string]$Detail) {
    [ordered]@{
        schema = 'copilot-extensions.module-readiness'
        version = 1
        module = 'agent-index/runtime'
        state = $State
        detail = $Detail
    } | ConvertTo-Json -Compress
}

function Test-SetupChoice {
    for ($i = 0; $i -lt $InvocationArgs.Count; $i++) {
        $arg = [string]$InvocationArgs[$i]
        if ($arg -eq '--single') { return $true }
        if ($arg -eq '--indexer') {
            if ($i + 1 -ge $InvocationArgs.Count) { return $false }
            $value = [string]$InvocationArgs[$i + 1]
            return [bool]($value -and -not $value.StartsWith('-'))
        }
        if ($arg.StartsWith('--indexer=')) {
            return [bool]$arg.Substring('--indexer='.Length)
        }
    }
    return $false
}

function Test-InteractiveSetup {
    if ($InvocationArgs -contains '--yes') { return $false }
    try { return -not [Console]::IsInputRedirected } catch { return $false }
}

function Get-SetupRole {
    $indexer = ''
    for ($i = 0; $i -lt $InvocationArgs.Count; $i++) {
        $arg = [string]$InvocationArgs[$i]
        if ($arg -eq '--single') { return 'host' }
        if ($arg -eq '--indexer' -and $i + 1 -lt $InvocationArgs.Count) {
            $indexer = [string]$InvocationArgs[$i + 1]
        } elseif ($arg.StartsWith('--indexer=')) {
            $indexer = $arg.Substring('--indexer='.Length)
        }
    }
    if ($indexer) {
        $this = if ($env:AGENT_INDEX_MACHINE) { $env:AGENT_INDEX_MACHINE } else { [Environment]::MachineName }
        $this = $this.Split('.')[0]
        if ([StringComparer]::OrdinalIgnoreCase.Equals($indexer.Trim(), $this.Trim())) {
            return 'host'
        }
        return 'client'
    }
    return $null
}

$script:SnapshotInstaller = ''
function Select-SnapshotInstaller {
    $snapshot = ''
    try { $snapshot = ([IO.File]::ReadAllText((Join-Path $Root 'payload-dir'))).Trim() } catch {}
    $script:SnapshotInstaller = if ($snapshot) { Join-Path $snapshot 'scripts\install.ps1' } else { '' }
    if ($script:SnapshotInstaller -and (Test-Path -LiteralPath $script:SnapshotInstaller -PathType Leaf)) {
        return $true
    }
    $installer = Join-Path $PluginDir 'scripts\install.ps1'
    if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) { return $false }
    $hostCommand = Get-Command pwsh -ErrorAction SilentlyContinue
    $hostExe = if ($hostCommand) { $hostCommand.Source } else { 'powershell.exe' }
    & $hostExe -NoProfile -ExecutionPolicy Bypass -File $installer stamp 2>&1 |
        ForEach-Object { [Console]::Error.WriteLine($_) }
    if ($LASTEXITCODE -ne 0) { return $false }
    try { $snapshot = ([IO.File]::ReadAllText((Join-Path $Root 'payload-dir'))).Trim() } catch { $snapshot = '' }
    $script:SnapshotInstaller = if ($snapshot) { Join-Path $snapshot 'scripts\install.ps1' } else { '' }
    return [bool]($script:SnapshotInstaller -and (Test-Path -LiteralPath $script:SnapshotInstaller -PathType Leaf))
}

function Invoke-RuntimeProvision([string]$SetupRole) {
    try {
        $origin = ([IO.File]::ReadAllText((Join-Path $Root 'payload-origin'))).Trim()
        if ($origin) { $env:COPILOT_PLUGIN_STAGED_FROM = $origin }
    } catch {}
    $probe = Join-Path $PluginDir 'scripts\installation-context\legacy-entrypoint-probe.ps1'
    if (-not (Test-Path -LiteralPath $probe -PathType Leaf)) {
        [Console]::Error.WriteLine('[agent-index] legacy mutation probe is unavailable.')
        return 1
    }
    $probeHost = (Get-Process -Id $PID).Path
    if (-not $probeHost) { return 1 }
    $probePayloadRoot = if ($env:COPILOT_PLUGIN_STAGED_FROM) {
        $env:COPILOT_PLUGIN_STAGED_FROM
    } else {
        $PluginDir
    }
    $global:LASTEXITCODE = 1
    & $probeHost -NoProfile -ExecutionPolicy Bypass -File $probe `
        -PayloadRoot $probePayloadRoot `
        -LegacyRoot $Root | Out-Null
    if ($LASTEXITCODE -ne 0) { return $LASTEXITCODE }
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        New-Item -ItemType Directory -Path $Root -Force | Out-Null
    }
    $lock = $null
    while (-not $lock) {
        try {
            $lock = [IO.File]::Open(
                (Join-Path $Root '.provision.lock'),
                [IO.FileMode]::OpenOrCreate,
                [IO.FileAccess]::ReadWrite,
                [IO.FileShare]::None
            )
        } catch {
            Start-Sleep -Milliseconds 200
        }
    }
    try {
        if (-not $SetupRole -and (Resolve-ReadyRuntime)) { return 0 }
        if (-not (Select-SnapshotInstaller)) { return 127 }
        $hadRole = Test-Path Env:AGENT_INDEX_ROLE
        $priorRole = $env:AGENT_INDEX_ROLE
        if ($SetupRole) {
            $env:AGENT_INDEX_ROLE = $SetupRole
        } elseif (-not (Get-ConfiguredRole)) {
            $env:AGENT_INDEX_ROLE = 'client'
        }
        [Console]::Error.WriteLine('[agent-index] provisioning the runtime after explicit setup/configuration.')
        [Console]::Error.WriteLine('::agent-provisioning:: plugin=agent-index eta_seconds=120 reason=setup')
        $hostCommand = Get-Command pwsh -ErrorAction SilentlyContinue
        $hostExe = if ($hostCommand) { $hostCommand.Source } else { 'powershell.exe' }
        & $hostExe -NoProfile -ExecutionPolicy Bypass -File $script:SnapshotInstaller provision 2>&1 |
            ForEach-Object { [Console]::Error.WriteLine($_) }
        $rc = $LASTEXITCODE
        if ($hadRole) { $env:AGENT_INDEX_ROLE = $priorRole } else { Remove-Item Env:AGENT_INDEX_ROLE -ErrorAction SilentlyContinue }
        return $rc
    } finally {
        if ($lock) { $lock.Dispose() }
    }
}

$Python = Resolve-ReadyRuntime
$Role = Get-ConfiguredRole
$RuntimeState = Get-RuntimeState $Python
$SetupProvisioned = $false

switch ($Command) {
    '--version' {
        if ($Python) { & $Python -m agent_index @InvocationArgs; exit $LASTEXITCODE }
        Write-Output $PackageVersion
        exit 0
    }
    'version' {
        if ($Python) { & $Python -m agent_index @InvocationArgs; exit $LASTEXITCODE }
        Write-Output $PackageVersion
        exit 0
    }
    'status' {
        if (-not $Python) {
            if ($Role) { Write-RuntimeUnavailable $RuntimeState $Role }
            else { Write-SetupRequired $RuntimeState }
            exit 0
        }
        & $Python -m agent_index @InvocationArgs
        exit $LASTEXITCODE
    }
    'installer-readiness' {
        if (-not $Role) {
            Write-Readiness 'configuration-empty' 'agent-index is dormant until a role is selected; readiness did not provision or start anything.'
            exit 0
        }
        if (-not $Python) {
            Write-Readiness 'failed' "agent-index role is configured, but the runtime is $RuntimeState; run setup again to repair it."
            exit 1
        }
        & $Python -m agent_index @InvocationArgs
        exit $LASTEXITCODE
    }
    'role' {
        if (-not $Role) {
            if ($InvocationArgs -contains '--json') {
                [ordered]@{ role = $null; state = 'setup_required'; setup_required = $true } | ConvertTo-Json -Compress
            } else {
                Write-Output 'unconfigured'
            }
            exit 0
        }
        if ($Python) { & $Python -m agent_index @InvocationArgs; exit $LASTEXITCODE }
        if ($InvocationArgs -contains '--json') {
            [ordered]@{ role = $Role; state = 'ready'; setup_required = $false } | ConvertTo-Json -Compress
        } else {
            Write-Output $Role
        }
        exit 0
    }
    'setup' {
        if (-not (Test-SetupChoice) -and -not (Test-InteractiveSetup)) {
            Write-SetupRequired $RuntimeState 'Non-interactive setup requires an explicit role choice: pass --single or --indexer <machine>.'
            exit 2
        }
    }
    default {
        if (-not $Role) {
            Write-SetupRequired $RuntimeState
            exit 2
        }
    }
}

if (-not $Python) {
    if (Test-Path Env:AGENT_INDEX_NO_SELFPROVISION) {
        [Console]::Error.WriteLine('[agent-index] runtime is not ready and self-provisioning is disabled.')
        exit 1
    }
    $setupRole = if ($Command -eq 'setup') { Get-SetupRole } else { $null }
    $rc = Invoke-RuntimeProvision $setupRole
    if ($rc -ne 0) { exit $rc }
    if ($Command -eq 'setup' -and $setupRole) { $SetupProvisioned = $true }
    $Python = Resolve-ReadyRuntime
    if (-not $Python) {
        [Console]::Error.WriteLine('[agent-index] provisioning completed without an importable runtime.')
        exit 1
    }
}

if ($Command -eq 'setup') {
    $setupOutput = & $Python -m agent_index @InvocationArgs | Out-String
    $setupRc = $LASTEXITCODE
    if ($setupOutput) { Write-Output $setupOutput.TrimEnd() }
    if ($setupRc -ne 0) { exit $setupRc }
    if (-not $SetupProvisioned) {
        $previousRebuild = $env:AGENT_INDEX_REBUILD_CURRENT
        $env:AGENT_INDEX_REBUILD_CURRENT = '1'
        try {
            $rebuildRc = Invoke-RuntimeProvision (Get-ConfiguredRole)
        } finally {
            if ($null -eq $previousRebuild) { Remove-Item Env:AGENT_INDEX_REBUILD_CURRENT -ErrorAction SilentlyContinue }
            else { $env:AGENT_INDEX_REBUILD_CURRENT = $previousRebuild }
        }
        exit $rebuildRc
    }
    exit 0
}

& $Python -m agent_index @InvocationArgs
exit $LASTEXITCODE
