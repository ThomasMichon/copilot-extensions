$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
Remove-Item Env:AGENT_INDEX_RUNTIME_VERSION -ErrorAction SilentlyContinue
$OutputEncoding = [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)

if (-not $env:AGENT_INDEX_REPO) {
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) {
        $originalCwd = [IO.Directory]::GetCurrentDirectory()
        $previousPreference = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $repoOutput = @(
                & $git.Source -C $originalCwd rev-parse --show-toplevel 2>$null
            )
            $repoExit = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previousPreference
        }
        if ($repoExit -eq 0 -and $repoOutput.Count -gt 0) {
            $repoCandidate = ("$($repoOutput[-1])").Trim()
            if (Test-Path -LiteralPath $repoCandidate -PathType Container) {
                $env:AGENT_INDEX_REPO = (
                    Resolve-Path -LiteralPath $repoCandidate
                ).Path
            }
        }
    }
}
$PluginDir = Split-Path -Parent $PSScriptRoot
$PayloadRoot = if ($env:AGENT_INDEX_PAYLOAD_ROOT) {
    $env:AGENT_INDEX_PAYLOAD_ROOT
} else {
    $PluginDir
}
$ModeRunner = Join-Path $PSScriptRoot 'installation-context\installation-context.ps1'
$LegacyRoot = if ($env:AGENT_INDEX_HOME) {
    $env:AGENT_INDEX_HOME
} else {
    Join-Path $env:USERPROFILE '.agent-index'
}
$Root = $LegacyRoot
$Resolver = Join-Path $PSScriptRoot 'resolve-runtime.ps1'
$InvocationArgs = @($args)
$Command = if ($InvocationArgs.Count -gt 0) { [string]$InvocationArgs[0] } else { 'status' }
$VersionLine = Select-String -LiteralPath (Join-Path $PluginDir 'pyproject.toml') -Pattern '^\s*version\s*=' | Select-Object -First 1
$PackageVersion = if ($VersionLine) { $VersionLine.Line -replace '.*=\s*"([^"]+)".*', '$1' } else { '' }
$GatePython = Get-Command python -ErrorAction SilentlyContinue
if (-not $GatePython) { $GatePython = Get-Command py -ErrorAction SilentlyContinue }
$EffectiveResolver = Join-Path $PSScriptRoot 'resolve_effective_config.py'

function Write-Inactive {
    [ordered]@{
        schema = 'agent-index.lifecycle'
        schema_version = 1
        plugin = 'agent-index'
        state = 'inactive'
        opted_in = $false
        configured = $false
        running = $false
    } | ConvertTo-Json -Compress
}

if (-not $GatePython -or -not (Test-Path -LiteralPath $EffectiveResolver -PathType Leaf)) {
    if ($Command -eq 'installer-readiness') {
        [ordered]@{
            schema = 'copilot-extensions.module-readiness'
            version = 1
            module = 'agent-index/runtime'
            state = 'configuration-empty'
            detail = 'No effective repository configuration is available; session startup remains non-mutating.'
        } | ConvertTo-Json -Compress
        exit 0
    }
    Write-Inactive
    exit $(if ($Command -eq 'status') { 0 } else { 2 })
}
try {
    $effectiveCwd = if ($env:AGENT_INDEX_REPO) {
        $env:AGENT_INDEX_REPO
    } else {
        (Get-Location).Path
    }
    $Effective = (& $GatePython.Source -E -X utf8 $EffectiveResolver `
        --cwd $effectiveCwd 2>$null | Out-String | ConvertFrom-Json -ErrorAction Stop)
} catch {
    $Effective = $null
}
if (-not $Effective -or -not $Effective.opted_in) {
    if ($Command -eq 'installer-readiness') {
        [ordered]@{
            schema = 'copilot-extensions.module-readiness'
            version = 1
            module = 'agent-index/runtime'
            state = 'configuration-empty'
            detail = 'No effective .agent-index/config.yaml opts this repository in; session startup remains non-mutating.'
        } | ConvertTo-Json -Compress
        exit 0
    }
    Write-Inactive
    exit $(if ($Command -eq 'status') { 0 } else { 2 })
}
if ($Effective.config) {
    $env:AGENT_INDEX_EFFECTIVE_CONFIG = [string]$Effective.config
}
if ($Effective.repo_root) {
    $env:AGENT_INDEX_REPO = [string]$Effective.repo_root
} elseif ($Effective.source -ceq 'forwarded') {
    $env:AGENT_INDEX_FORWARDED = '1'
    Remove-Item Env:AGENT_INDEX_REPO -ErrorAction SilentlyContinue
}

try {
    $PayloadRoot = (Resolve-Path -LiteralPath $PayloadRoot).Path
    $PluginDir = (Resolve-Path -LiteralPath $PluginDir).Path
} catch {
    [Console]::Error.WriteLine('[agent-index] owning payload root is unavailable.')
    exit 126
}
if (-not [StringComparer]::OrdinalIgnoreCase.Equals($PayloadRoot, $PluginDir)) {
    [Console]::Error.WriteLine(
        '[agent-index] owning payload root does not match the dispatcher.'
    )
    exit 126
}
if (
    -not (Test-Path -LiteralPath $ModeRunner -PathType Leaf) -or
    -not (Test-Path -LiteralPath $Resolver -PathType Leaf)
) {
    [Console]::Error.WriteLine(
        '[agent-index] installation-context runtime resolver is unavailable.'
    )
    exit 126
}

$Context = ''
$MarketplaceId = ''
$ResolutionStatus = 'ready'
$ResolutionReason = 'policy-default-false'
$ActualMode = 'legacy'
$DesiredMode = 'legacy'
$Policy = Join-Path $env:USERPROFILE '.copilot-extensions\installation-mode.json'
$PolicyPresent = (
    (Test-Path -LiteralPath $Policy) -or
    $null -ne (Get-Item -LiteralPath $Policy -Force -ErrorAction SilentlyContinue)
)
$ProvenanceBoundary = (
    ($PayloadRoot -replace '\\', '/') -match
        '/\.copilot/installed-plugins/[^/]+/[^/]+/?$'
)
if (-not $ProvenanceBoundary) {
    $probeRoot = $PayloadRoot
    while ($probeRoot) {
        if (
            Test-Path -LiteralPath (
                Join-Path $probeRoot '.github\plugin\marketplace.json'
            ) -PathType Leaf
        ) {
            $ProvenanceBoundary = $true
            break
        }
        $parent = Split-Path -Parent $probeRoot
        if (-not $parent -or $parent -eq $probeRoot) { break }
        $probeRoot = $parent
    }
}
$hostExe = (Get-Process -Id $PID).Path
if (-not $hostExe) {
    [Console]::Error.WriteLine('[agent-index] PowerShell host executable is unavailable.')
    exit 126
}
$statusArgs = @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $ModeRunner,
    'status',
    '-PayloadRoot', $PayloadRoot,
    '-PluginId', 'agent-index',
    '-LegacyRoot', $LegacyRoot
)
if ($env:COPILOT_EXTENSIONS_CONTEXT) {
    $statusArgs += @('-Context', $env:COPILOT_EXTENSIONS_CONTEXT)
    $contextDurableHome = $env:COPILOT_EXTENSIONS_CONTEXT
    1..5 | ForEach-Object {
        $contextDurableHome = Split-Path -Parent $contextDurableHome
    }
    $statusArgs += @('-DurableHome', $contextDurableHome)
}
$resolutionJson = @(& $hostExe @statusArgs)
if ($LASTEXITCODE -ne 0) {
    [Console]::Error.WriteLine(
        '[agent-index] installation context could not be resolved.'
    )
    exit 126
}
try {
    $resolution = ($resolutionJson -join "`n") | ConvertFrom-Json
} catch {
    [Console]::Error.WriteLine(
        '[agent-index] installation context returned malformed status.'
    )
    exit 126
}
$ResolutionStatus = [string]$resolution.status
$ResolutionReason = [string]$resolution.reason
$ActualMode = [string]$resolution.actualMode
$DesiredMode = [string]$resolution.desiredMode
$simplePolicyLegacy = $false
if (
    -not $env:COPILOT_EXTENSIONS_CONTEXT -and
    -not $PolicyPresent -and
    -not $ProvenanceBoundary -and
    $ResolutionStatus -ceq 'provenance-blocked'
) {
    $simplePolicyLegacy = $true
}
elseif (
    -not $env:COPILOT_EXTENSIONS_CONTEXT -and
    $ResolutionStatus -ceq 'provenance-blocked' -and
    $PolicyPresent
) {
    try {
        $policyDocument = Get-Content -LiteralPath $Policy -Raw |
            ConvertFrom-Json
        $installationMode = $policyDocument.PSObject.Properties[
            'installationMode'
        ]
        $enabled = if ($null -ne $installationMode) {
            $installationMode.Value.PSObject.Properties['enabled']
        } else {
            $null
        }
        $marketplaces = if ($null -ne $installationMode) {
            $installationMode.Value.PSObject.Properties['marketplaces']
        } else {
            $null
        }
        $simplePolicyLegacy = (
            $null -ne $enabled -and
            $enabled.Value -is [bool] -and
            -not $enabled.Value -and
            (
                $null -eq $marketplaces -or
                $marketplaces.Value.PSObject.Properties.Count -eq 0
            )
        )
    } catch {
        $simplePolicyLegacy = $false
    }
}
if (
    (
        $ResolutionStatus -ceq 'ready' -and
        $ActualMode -ceq 'legacy' -and
        $DesiredMode -ceq 'legacy'
    ) -or
    $simplePolicyLegacy
) {
    if ($env:COPILOT_EXTENSIONS_CONTEXT) {
        [Console]::Error.WriteLine(
            '[agent-index] requested installation context is not active.'
        )
        exit 126
    }
}
elseif (
    (
        $ResolutionStatus -ceq 'ready' -and
        $ResolutionReason -ceq 'namespaced-active'
    ) -or
    $ResolutionStatus -ceq 'deactivation-required'
) {
    if ($ActualMode -cne 'namespaced') {
        [Console]::Error.WriteLine(
            "[agent-index] installation context blocks invocation: " +
            "status=$ResolutionStatus reason=$ResolutionReason."
        )
        exit 126
    }
    $Context = [string]$resolution.context
    $MarketplaceId = [string]$resolution.marketplaceId
    if (-not $Context -or -not $MarketplaceId) {
        [Console]::Error.WriteLine(
            '[agent-index] active installation context is incomplete.'
        )
        exit 126
    }
    $contextDurableHome = $Context
    1..5 | ForEach-Object {
        $contextDurableHome = Split-Path -Parent $contextDurableHome
    }
    $validatedJson = @(
        & $hostExe -NoProfile -ExecutionPolicy Bypass -File $ModeRunner `
            validate `
            -Context $Context `
            -ExpectedMarketplaceId $MarketplaceId `
            -ExpectedPluginId agent-index `
            -ExpectedPayloadRoot $PayloadRoot `
            -DurableHome $contextDurableHome
    )
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine(
            '[agent-index] active installation context validation failed.'
        )
        exit 126
    }
    try {
        $validated = ($validatedJson -join "`n") | ConvertFrom-Json
    } catch {
        [Console]::Error.WriteLine(
            '[agent-index] active installation context validation was malformed.'
        )
        exit 126
    }
    $Root = [string]$validated.pluginRoot
    $stateRoot = [string]$validated.stateRoot
    $runRoot = [string]$validated.runRoot
    $logsRoot = [string]$validated.logsRoot
    $cacheRoot = [string]$validated.cacheRoot
    if (-not $Root -or -not $stateRoot -or -not $runRoot -or
        -not $logsRoot -or -not $cacheRoot) {
        [Console]::Error.WriteLine(
            '[agent-index] active installation context roots are incomplete.'
        )
        exit 126
    }
    $env:COPILOT_EXTENSIONS_CONTEXT = $Context
    $env:AGENT_INDEX_HOME = $Root
    $env:AGENT_INDEX_STATE_DIR = $stateRoot
    $env:AGENT_INDEX_DATA_DIR = $stateRoot
    $env:AGENT_INDEX_RUN_DIR = $runRoot
    $env:AGENT_INDEX_LOG_DIR = $logsRoot
    $env:AGENT_INDEX_CACHE_DIR = $cacheRoot
    $env:AGENT_INDEX_CONFIG_ROOT = Join-Path $Root 'config'
    $env:AGENT_INDEX_CONFIG = Join-Path $env:AGENT_INDEX_CONFIG_ROOT 'config.yaml'
    $env:AGENT_INDEX_ROUTING_DIR = Join-Path $runRoot 'zdd'
    $env:AGENT_INDEX_HOST = '127.0.0.1'
    $env:AGENT_INDEX_PORT = '0'
    $env:AGENT_INDEX_ENGINE_HOME = Join-Path $Root 'engine'
    $env:AGENT_INDEX_ENGINE_HOST = '127.0.0.1'
    $env:AGENT_INDEX_ENGINE_PORT = '0'
    $env:AGENT_INDEX_ENGINE_MODE = 'external'
    $env:AGENT_INDEX_BACKUP_DIR = Join-Path $Root 'backups'
    $env:AGENT_INDEX_BACKUP_MOUNT_ROOT = $Root
    $env:AGENT_INDEX_INSTALLATION_ID = "$MarketplaceId/agent-index"
    $env:XDG_CACHE_HOME = $cacheRoot
    Remove-Item Env:AGENT_INDEX_ENDPOINT -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
    Set-Location -LiteralPath $Root
    [IO.Directory]::SetCurrentDirectory($Root)
}
else {
    [Console]::Error.WriteLine(
        "[agent-index] installation context blocks invocation: " +
        "status=$ResolutionStatus reason=$ResolutionReason."
    )
    exit 126
}

function Get-ManagementPython {
    foreach ($candidate in @('python', 'python3', 'py')) {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $found) { continue }
        if ($candidate -eq 'py') {
            $resolved = @(& $found.Source -3 -c 'import sys; print(sys.executable)' 2>$null)
            if ($LASTEXITCODE -eq 0 -and $resolved.Count -gt 0) {
                return ("$($resolved[-1])").Trim()
            }
        } else {
            return $found.Source
        }
    }
    return $null
}

if (
    $ActualMode -ceq 'namespaced' -and
    $Command -ceq 'engine' -and
    $InvocationArgs.Count -ge 2 -and
    [string]$InvocationArgs[1] -in @('start', 'run')
) {
    [Console]::Error.WriteLine(
        '[agent-index] the installation-cell exemplar does not provision or ' +
        'start the heavy embedding engine.'
    )
    exit 2
}

if (
    $ActualMode -ceq 'namespaced' -and
    $Command -in @('start', 'serve')
) {
    [Console]::Error.WriteLine(
        '[agent-index] public start/serve is unavailable for an active ' +
        'namespaced installation.'
    )
    exit 126
}

if (
    $ActualMode -ceq 'namespaced' -and
    $Command -ceq 'deploy' -and
    (
        -not $env:AGENT_INDEX_CELL_TRANSACTION -or
        -not $env:AGENT_INDEX_CELL_TRANSACTION_TOKEN -or
        -not $env:AGENT_INDEX_CELL_TRANSACTION_ID
    )
) {
    [Console]::Error.WriteLine(
        '[agent-index] namespaced deploy/recovery requires the owning cell transaction.'
    )
    exit 126
}

if ($Command -in @('__cell-bootstrap', '__cell-service-ensure')) {
    if ($ActualMode -cne 'namespaced') { exit 10 }
    if (
        $ResolutionStatus -cne 'ready' -or
        $ResolutionReason -cne 'namespaced-active'
    ) {
        exit 0
    }
    $cellRuntime = Join-Path $PayloadRoot 'scripts\cell-runtime.py'
    if (-not (Test-Path -LiteralPath $cellRuntime -PathType Leaf)) { exit 126 }
    $cellPython = Get-ManagementPython
    if (-not $cellPython) { exit 126 }
    if ($Command -eq '__cell-bootstrap') {
        $arguments = @(
            "`"$cellRuntime`"", 'bootstrap',
            '--context', "`"$Context`"",
            '--expected-marketplace-id', "`"$MarketplaceId`"",
            '--durable-home', "`"$contextDurableHome`""
        )
        Start-Process -FilePath $cellPython `
            -WorkingDirectory $PayloadRoot `
            -ArgumentList (@('-I', '-X', 'utf8') + $arguments) `
            -WindowStyle Hidden | Out-Null
        exit 0
    }
    & $cellPython -I -X utf8 $cellRuntime service-ensure-kick `
        --context $Context `
        --expected-marketplace-id $MarketplaceId `
        --durable-home $contextDurableHome
    exit $LASTEXITCODE
}

function Get-ConfiguredRole {
    $role = if ($env:AGENT_INDEX_ROLE) { $env:AGENT_INDEX_ROLE.Trim().ToLowerInvariant() } else { '' }
    if ($role -in @('host', 'client')) { return $role }
    $machine = if ($env:AGENT_INDEX_MACHINE) {
        $env:AGENT_INDEX_MACHINE
    } else {
        [Environment]::MachineName
    }
    $roleResolver = Join-Path $PSScriptRoot 'resolve-activation-role.py'
    $resolverArgs = @('-E', '-X', 'utf8', $roleResolver, '--machine', $machine)
    if ($env:AGENT_INDEX_CONFIG_DATA_B64) {
        $resolverArgs += @('--data-b64', $env:AGENT_INDEX_CONFIG_DATA_B64)
    } elseif ($env:AGENT_INDEX_EFFECTIVE_CONFIG) {
        $resolverArgs += @('--config', $env:AGENT_INDEX_EFFECTIVE_CONFIG)
    }
    if ($resolverArgs.Count -gt 6) {
        $resolved = (& $GatePython.Source @resolverArgs 2>$null | Select-Object -Last 1)
        $role = ("$resolved").Trim().ToLowerInvariant()
        if ($role -in @('host', 'client')) { return $role }
    }
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

function Test-RuntimeOrigin(
    [string]$Interpreter,
    [string]$ExpectedVersionsRoot = ''
) {
    if (-not $Interpreter) { return $false }
    $slot = Split-Path -Parent (Split-Path -Parent $Interpreter)
    if (-not (Test-Path -LiteralPath $slot -PathType Container)) {
        return $false
    }
    $comparison = if ($env:OS -ceq 'Windows_NT') {
        [StringComparison]::OrdinalIgnoreCase
    } else {
        [StringComparison]::Ordinal
    }
    $slotRoot = (Resolve-Path -LiteralPath $slot).Path.TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
    if ($ExpectedVersionsRoot) {
        if (-not (Test-Path -LiteralPath $ExpectedVersionsRoot -PathType Container)) {
            return $false
        }
        $versionsRoot = (
            Resolve-Path -LiteralPath $ExpectedVersionsRoot
        ).Path.TrimEnd(
            [IO.Path]::DirectorySeparatorChar,
            [IO.Path]::AltDirectorySeparatorChar
        )
        if (-not $slotRoot.StartsWith(
            $versionsRoot + [IO.Path]::DirectorySeparatorChar,
            $comparison
        )) {
            return $false
        }
    }
    $previousPreference = $ErrorActionPreference
    $previousLocation = Get-Location
    $previousCwd = [IO.Directory]::GetCurrentDirectory()
    try {
        $ErrorActionPreference = 'Continue'
        Set-Location -LiteralPath $slot
        [IO.Directory]::SetCurrentDirectory($slot)
        $originOutput = @(
            & $Interpreter -I -X utf8 -c (
                'from pathlib import Path; import agent_index; ' +
                'print(Path(agent_index.__file__).resolve())'
            ) 2>$null
        )
        if ($LASTEXITCODE -ne 0 -or $originOutput.Count -eq 0) {
            return $false
        }
        $origin = [IO.Path]::GetFullPath(("$($originOutput[-1])").Trim())
        $prefix = $slotRoot + [IO.Path]::DirectorySeparatorChar
        return (
            (Test-Path -LiteralPath $origin -PathType Leaf) -and
            $origin.StartsWith($prefix, $comparison)
        )
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $previousPreference
        Set-Location -LiteralPath $previousLocation
        [IO.Directory]::SetCurrentDirectory($previousCwd)
    }
}

function Resolve-ReadyRuntime {
    if ($ActualMode -ceq 'namespaced') {
        $cellRuntime = Join-Path $PayloadRoot 'scripts\cell-runtime.py'
        if (-not (Test-Path -LiteralPath $cellRuntime -PathType Leaf)) {
            [Console]::Error.WriteLine(
                '[agent-index] installation-cell runtime validator is unavailable.'
            )
            exit 126
        }
        $cellPython = Get-ManagementPython
        if (-not $cellPython) {
            [Console]::Error.WriteLine(
                '[agent-index] Python is unavailable for installation-cell validation.'
            )
            exit 126
        }
        $validationJson = @(
            & $cellPython -I -X utf8 $cellRuntime launch-validate `
                --context $Context `
                --expected-marketplace-id $MarketplaceId `
                --durable-home $contextDurableHome `
                --command $Command
        )
        if ($LASTEXITCODE -ne 0) {
            [Console]::Error.WriteLine(
                '[agent-index] selected installation runtime failed operative validation.'
            )
            exit 126
        }
        try {
            $validation = ($validationJson -join "`n") | ConvertFrom-Json
        } catch {
            [Console]::Error.WriteLine(
                '[agent-index] selected installation runtime validation was malformed.'
            )
            exit 126
        }
        if (-not $env:AGENT_INDEX_CELL_START_TOKEN) {
            Remove-Item Env:AGENT_INDEX_CELL_LOCK_TOKEN -ErrorAction SilentlyContinue
            Remove-Item Env:AGENT_INDEX_CELL_LOCK_ROOT -ErrorAction SilentlyContinue
        }
        $selected = [string]$validation.interpreter
        if (Test-RuntimeOrigin `
            -Interpreter $selected `
            -ExpectedVersionsRoot (Join-Path $Root 'versions')) {
            $runtimeVersion = [string]$validation.runtimeVersion
            if ($runtimeVersion) {
                $env:AGENT_INDEX_RUNTIME_VERSION = $runtimeVersion
            }
            return $selected
        }
        Remove-Item Env:AGENT_INDEX_RUNTIME_VERSION -ErrorAction SilentlyContinue
        return $null
    }
    $AgentRtPy = $null
    if (Test-Path -LiteralPath $Resolver -PathType Leaf) {
        $env:AGENT_RT_ROOT = $Root
        . $Resolver
    }
    if ($AgentRtPy) {
        $previous = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $ready = Test-RuntimeOrigin $AgentRtPy
        $ErrorActionPreference = $previous
        if ($ready) { return $AgentRtPy }
    }
    Remove-Item Env:AGENT_INDEX_RUNTIME_VERSION -ErrorAction SilentlyContinue
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
    if ($ActualMode -ceq 'namespaced') {
        if (
            $ResolutionStatus -cne 'ready' -or
            $ResolutionReason -cne 'namespaced-active'
        ) {
            [Console]::Error.WriteLine(
                '[agent-index] deactivation-pending installation cannot ' +
                'provision a new runtime.'
            )
            return 126
        }
        $installer = Join-Path $PayloadRoot 'scripts\install.ps1'
        if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
            return 127
        }
        [Console]::Error.WriteLine(
            '[agent-index] provisioning the active installation cell after ' +
            'explicit setup/configuration.'
        )
        [Console]::Error.WriteLine(
            '::agent-provisioning:: plugin=agent-index eta_seconds=120 reason=setup'
        )
        $priorNoEngine = $env:AGENT_INDEX_NO_ENGINE_DEPS
        $priorRole = $env:AGENT_INDEX_ROLE
        $env:AGENT_INDEX_NO_ENGINE_DEPS = '1'
        $provisionRole = if ($SetupRole) { $SetupRole } else { Get-ConfiguredRole }
        if ($provisionRole) { $env:AGENT_INDEX_ROLE = $provisionRole }
        try {
            & $hostExe -NoProfile -ExecutionPolicy Bypass -File $installer `
                -Action cell-provision `
                -Context $Context `
                -ExpectedMarketplaceId $MarketplaceId 2>&1 |
                ForEach-Object { [Console]::Error.WriteLine($_) }
            $rc = $LASTEXITCODE
        } finally {
            if ($null -eq $priorNoEngine) {
                Remove-Item Env:AGENT_INDEX_NO_ENGINE_DEPS -ErrorAction SilentlyContinue
            } else {
                $env:AGENT_INDEX_NO_ENGINE_DEPS = $priorNoEngine
            }
            if ($null -eq $priorRole) {
                Remove-Item Env:AGENT_INDEX_ROLE -ErrorAction SilentlyContinue
            } else {
                $env:AGENT_INDEX_ROLE = $priorRole
            }
        }
        return $rc
    }
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
        $provisionRole = if ($SetupRole) { $SetupRole } else { Get-ConfiguredRole }
        $env:AGENT_INDEX_ROLE = if ($provisionRole) { $provisionRole } else { 'client' }
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

$PlannedSetupRole = if ($Command -eq 'setup') { Get-SetupRole } else { $null }
$script:SetupRoleTemporary = $false
$script:SetupRoleHadEnvironment = Test-Path Env:AGENT_INDEX_ROLE
$script:SetupRolePrior = $env:AGENT_INDEX_ROLE
if ($PlannedSetupRole) {
    $env:AGENT_INDEX_ROLE = $PlannedSetupRole
    $script:SetupRoleTemporary = $true
}

function Restore-PlannedSetupRole {
    if (-not $script:SetupRoleTemporary) { return }
    if ($script:SetupRoleHadEnvironment) {
        $env:AGENT_INDEX_ROLE = $script:SetupRolePrior
    } else {
        Remove-Item Env:AGENT_INDEX_ROLE -ErrorAction SilentlyContinue
    }
    $script:SetupRoleTemporary = $false
}

$Python = Resolve-ReadyRuntime
$Role = Get-ConfiguredRole
$RuntimeState = Get-RuntimeState $Python
$SetupProvisioned = $false

switch ($Command) {
    '--version' {
        if ($Python) { & $Python -I -X utf8 -m agent_index @InvocationArgs; exit $LASTEXITCODE }
        Write-Output $PackageVersion
        exit 0
    }
    'version' {
        if ($Python) { & $Python -I -X utf8 -m agent_index @InvocationArgs; exit $LASTEXITCODE }
        Write-Output $PackageVersion
        exit 0
    }
    'status' {
        if (-not $Python) {
            if ($Role) { Write-RuntimeUnavailable $RuntimeState $Role }
            else { Write-SetupRequired $RuntimeState }
            exit 0
        }
        & $Python -I -X utf8 -m agent_index @InvocationArgs
        exit $LASTEXITCODE
    }
    'installer-readiness' {
        Write-Readiness 'configuration-empty' 'agent-index runtime activation is explicit; session startup does not provision packages or start services.'
        exit 0
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
        if ($Python) { & $Python -I -X utf8 -m agent_index @InvocationArgs; exit $LASTEXITCODE }
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
    if (Test-Path Env:AGENT_INDEX_FORWARDED) {
        Write-RuntimeUnavailable $RuntimeState $Role
        exit 1
    }
    if (Test-Path Env:AGENT_INDEX_NO_SELFPROVISION) {
        [Console]::Error.WriteLine('[agent-index] runtime is not ready and self-provisioning is disabled.')
        exit 1
    }
    $rc = Invoke-RuntimeProvision $PlannedSetupRole
    if ($rc -ne 0) {
        Restore-PlannedSetupRole
        exit $rc
    }
    if ($Command -eq 'setup' -and $PlannedSetupRole) {
        $SetupProvisioned = $true
    }
    $Python = Resolve-ReadyRuntime
    if (-not $Python) {
        Restore-PlannedSetupRole
        [Console]::Error.WriteLine('[agent-index] provisioning completed without an importable runtime.')
        exit 1
    }
}

if ($Command -eq 'setup') {
    $setupOutput = & $Python -I -X utf8 -m agent_index @InvocationArgs | Out-String
    $setupRc = $LASTEXITCODE
    Restore-PlannedSetupRole
    if ($setupOutput) { Write-Output $setupOutput.TrimEnd() }
    if ($setupRc -ne 0) { exit $setupRc }
    $configuredSetupRole = Get-ConfiguredRole
    if (
        -not $SetupProvisioned -or
        ($PlannedSetupRole -and $configuredSetupRole -ne $PlannedSetupRole)
    ) {
        $previousRebuild = $env:AGENT_INDEX_REBUILD_CURRENT
        $env:AGENT_INDEX_REBUILD_CURRENT = '1'
        try {
            $rebuildRc = Invoke-RuntimeProvision $configuredSetupRole
        } finally {
            if ($null -eq $previousRebuild) { Remove-Item Env:AGENT_INDEX_REBUILD_CURRENT -ErrorAction SilentlyContinue }
            else { $env:AGENT_INDEX_REBUILD_CURRENT = $previousRebuild }
        }
        exit $rebuildRc
    }
    exit 0
}

& $Python -I -X utf8 -m agent_index @InvocationArgs
exit $LASTEXITCODE
