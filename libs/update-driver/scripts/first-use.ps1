# Installation driver lifecycle contract: vault-stamp-v1
# Installation driver lifecycle contract: logger-install-v1
# Installation driver lifecycle contract: ssh-command-v1
# Installation driver lifecycle contract: logger-command-v1
param(
    [Parameter(Mandatory)][string]$Root,
    [Parameter(Mandatory)][string]$Payload,
    [Parameter(Mandatory)][string]$RuntimeRoot,
    [Parameter(Mandatory)][string]$Project,
    [ValidateSet('launch', 'owner')][string]$Mode = 'launch',
    [string]$ExpectedSource,
    [string]$ReceiptLog,
    [ValidateSet('default', 'client-only')][string]$ServiceMode = 'default',
    [ValidateSet('provision', 'stamp', 'stamp-provision', 'install')][string]$InstallerAction = 'provision',
    [switch]$Wait
)
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$Mode = $Mode.ToLowerInvariant()
$ServiceMode = $ServiceMode.ToLowerInvariant()
$InstallerAction = $InstallerAction.ToLowerInvariant()
if ($InstallerAction -cne 'provision' -and $ServiceMode -cne 'default') {
    throw 'Client-only selection requires the provision action.'
}
if (Test-Path Env:UPDATE_DRIVER_WITNESS) {
    throw 'Nested first-use submission is unsupported; retain the current installation owner.'
}
if ($env:OS -ne 'Windows_NT' -or $PSVersionTable.PSEdition -ne 'Desktop' -or $PSVersionTable.PSVersion -lt [version]'5.1') {
    throw 'Native first-use admission requires stock Windows PowerShell 5.1.'
}
$foundation = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
. (Join-Path $foundation 'src\update_driver\foundation.ps1')
foreach ($path in @($Payload, $Project)) {
    $null = [CopilotExtensions.NativeFoundationState]::CheckPath($path, $true, $false)
}
$null = [CopilotExtensions.NativeFoundationState]::CheckPath($RuntimeRoot, $true, $true)
foreach ($path in @($Payload, $RuntimeRoot)) {
    if ([StringComparer]::OrdinalIgnoreCase.Equals($Root, $path) -or
        $Root.StartsWith($path.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $path.StartsWith($Root.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Execution root must be separate from owning payload and installation.'
    }
}
$invocation = [IO.File]::ReadAllText((Join-Path $Payload 'payload-invocation.json')) | ConvertFrom-Json
$identity = [IO.File]::ReadAllText((Join-Path $Payload 'plugin.json')) | ConvertFrom-Json
$plugin = $identity.name
switch -CaseSensitive ($plugin) {
    'agent-vault' { $prefix = 'AGENT_VAULT' }
    'agent-pull-requests' { $prefix = 'AGENT_PULL_REQUESTS' }
    'agent-ssh' { $prefix = 'AGENT_SSH' }
    'agent-codespaces' { $prefix = 'AGENT_CODESPACES' }
    'agent-logger' { $prefix = 'AGENT_LOGGER' }
    'agent-bridge' { $prefix = 'AGENT_BRIDGE' }
    default { throw 'Owning payload does not declare the fixed first-use provision recipe.' }
}
if ($InstallerAction -ceq 'stamp-provision' -and $plugin -cne 'agent-ssh') {
    throw 'Stamp-to-selected-snapshot provisioning requires SSH.'
}
if ($InstallerAction -ceq 'install' -and $plugin -cne 'agent-logger') {
    throw 'Fixed install action requires Logger.'
}
if ($plugin -ceq 'agent-logger') {
    foreach ($helper in @(
        'command_binding.py', 'command_lifecycle.py', 'installer_engine.py',
        'installation-context\installation_context.py'
    )) {
        if (-not [IO.File]::Exists((Join-Path $Payload "scripts\$helper"))) {
            throw 'Logger first use requires its complete materialized installation and lifecycle helpers.'
        }
    }
}
if ($plugin -ceq 'agent-bridge') {
    foreach ($helper in @(
        'installer-engine.ps1', 'installer-engine.sh',
        'installer_engine.py', 'installer_engine_process.py', 'runtime_launcher.py',
        'installer_wheel_inputs.py', 'installer_wheel_snapshot.py', 'installer_wheel_acquisition.py',
        'installer_dependency_policy.py', 'installer_dependency_config.py',
        'installation-context\installation_context.py',
        'installation-context\installation-context.ps1', 'installation-context\installation-context.sh',
        'install.ps1', 'install.sh', 'repair-scheduled-task.ps1',
        'versioned_runtime_native.ps1', 'versioned_runtime.py',
        'versioned_runtime_construction.py', 'versioned_runtime_source.py',
        'versioned_runtime_publication.py', 'versioned_runtime_leases.py'
    )) {
        if (-not [IO.File]::Exists((Join-Path $Payload "scripts\$helper"))) {
            throw 'Bridge first use requires its complete materialized installation helpers.'
        }
    }
}
if ($plugin -cin @('agent-ssh', 'agent-codespaces', 'agent-logger', 'agent-bridge')) {
    $label = @{ 'agent-ssh' = 'SSH'; 'agent-codespaces' = 'CodeSpaces'; 'agent-logger' = 'Logger'; 'agent-bridge' = 'Bridge' }[$plugin]
    $sshDeclaration = @{
        legacyRuntimeRoot = '.' + $plugin
        payloadRootEnv = $prefix + '_PAYLOAD_ROOT'
    }
    if ($plugin -cne 'agent-logger') { $sshDeclaration.module = $plugin.Replace('-', '_') }
    foreach ($field in $sshDeclaration.GetEnumerator()) {
        $value = $invocation.PSObject.Properties[$field.Key]
        if ($null -eq $value -or $value.Value -isnot [string] -or $value.Value -cne $field.Value) {
            throw "$label first use requires its declared native runtime gate."
        }
    }
    if ($invocation.payloadDispatcher -isnot [PSCustomObject] -or
        $invocation.payloadDispatcher.posix -isnot [string] -or
        $invocation.payloadDispatcher.windows -isnot [string] -or
        $invocation.payloadDispatcher.posix -cne 'scripts/runtime-gate.sh' -or
        $invocation.payloadDispatcher.windows -cne 'scripts/runtime-gate.ps1' -or
        @($invocation.payloadDispatcher.PSObject.Properties).Count -ne 2) {
        throw "$label first use requires its declared native runtime gate."
    }
}
if ($plugin -cin @('agent-codespaces', 'agent-logger', 'agent-bridge') -and (
    (Test-Path ("Env:" + $prefix + '_INSTALLATION_ID')) -or
    $RuntimeRoot -ine (Join-Path $env:USERPROFILE ('.' + $plugin)) -or
    -not (
        ([IO.Path]::GetDirectoryName([IO.Path]::GetDirectoryName($Payload)) -ieq
            (Join-Path $env:USERPROFILE '.copilot\installed-plugins') -and
            [IO.Path]::GetFileName($Payload) -ceq $plugin) -or
        ($plugin -ceq 'agent-logger' -and $InstallerAction -ceq 'provision' -and
            [IO.Path]::GetDirectoryName($Payload) -ieq (Join-Path $RuntimeRoot 'snapshots'))
    ))) {
    throw "$label first use requires its canonical installed payload and default legacy root."
}
if ($plugin -cin @('agent-pull-requests', 'agent-ssh', 'agent-logger')) {
    $label = @{ 'agent-ssh' = 'SSH'; 'agent-logger' = 'Logger'; 'agent-pull-requests' = 'Pull Requests' }[$plugin]
    $installed = [IO.Path]::GetDirectoryName([IO.Path]::GetDirectoryName($Payload)) -ieq
        (Join-Path $env:USERPROFILE '.copilot\installed-plugins') -and
        ($plugin -ceq 'agent-pull-requests' -or [IO.Path]::GetFileName($Payload) -ceq $plugin)
    $snapshot = [IO.Path]::GetDirectoryName($Payload) -ieq (Join-Path $RuntimeRoot 'snapshots')
    if ($RuntimeRoot -ine (Join-Path $env:USERPROFILE ('.' + $plugin)) -or
        -not ($installed -or $snapshot)) {
        throw "$label first use requires its canonical installed payload or selected legacy snapshot."
    }
    if ($snapshot) {
        $receiptPath = Join-Path $Payload 'snapshot-provenance.json'
        $null = [CopilotExtensions.NativeFoundationState]::CheckPath($receiptPath, $false, $false)
        $receipt = [IO.File]::ReadAllText($receiptPath) | ConvertFrom-Json
        if ($receipt.schema -cne 'copilot-extensions.legacy-snapshot-source') {
            throw "$label first use requires canonical selected snapshot provenance."
        }
    }
}
if ($ServiceMode -ceq 'client-only' -and $plugin -cne 'agent-vault') {
    throw 'Client-only selection is unsupported by the owning installer.'
}
$noSelf = $prefix + '_NO_SELFPROVISION'
if ([Environment]::GetEnvironmentVariable($noSelf) -or
    ($plugin -ceq 'agent-pull-requests' -and (Test-Path ("Env:" + $noSelf))) -or
    (Test-Path ("Env:" + $prefix + '_PINNED_CONTEXT'))) {
    throw 'Owning plugin first-use provisioning is disabled for this invocation.'
}
$installer = if ($null -eq $invocation.PSObject.Properties['installer']) {
    'install'
} else { $invocation.installer }
$recipe = @{
    schema = 'copilot-extensions.payload-invocation'
    noSelfProvisionEnv = $noSelf
    installationContext = 'required'
}
if ($plugin -ceq 'agent-logger') {
    $loggerCommands = @(
        @('agent-logger', 'agent_logger'),
        @('collate-session', 'agent_logger.segmenter.collate'),
        @('read-session-digest', 'agent_logger.segmenter.read_digest'),
        @('prepare-session-log', 'agent_logger.segmenter.prepare_log'),
        @('ramp-up-session', 'agent_logger.segmenter.ramp_up'),
        @('session-sync', 'agent_logger.sync.engine')
    )
    if ($invocation.plugin -isnot [string] -or $invocation.plugin -cne $plugin -or
        $invocation.installerLifecycle -isnot [bool] -or -not $invocation.installerLifecycle -or
        $null -ne $invocation.PSObject.Properties['command'] -or
        $null -ne $invocation.PSObject.Properties['module'] -or
        $null -ne $invocation.PSObject.Properties['provisionMode'] -or
        $invocation.commands -isnot [array] -or $invocation.commands.Count -ne $loggerCommands.Count) {
        throw 'Logger first use requires its fixed six-command lifecycle declaration.'
    }
    for ($index = 0; $index -lt $loggerCommands.Count; $index++) {
        $item = $invocation.commands[$index]
        if ($item -isnot [PSCustomObject] -or
            $item.command -isnot [string] -or $item.command -cne $loggerCommands[$index][0] -or
            $item.module -isnot [string] -or $item.module -cne $loggerCommands[$index][1]) {
            throw 'Logger first use requires its fixed six-command lifecycle declaration.'
        }
    }
} else { $recipe.command = $plugin }
$provisionMode = $invocation.PSObject.Properties['provisionMode']
if ($plugin -cin @('agent-ssh', 'agent-codespaces')) {
    if ($null -ne $provisionMode -and
        ($provisionMode.Value -isnot [string] -or $provisionMode.Value -cne 'snapshot')) {
        throw 'Owning payload does not declare the fixed first-use provision recipe.'
    }
} elseif ($plugin -cne 'agent-logger' -and ($null -eq $provisionMode -or
    $provisionMode.Value -isnot [string] -or $provisionMode.Value -cne 'direct')) {
    throw 'Owning payload does not declare the fixed first-use provision recipe.'
}
foreach ($field in $recipe.GetEnumerator()) {
    $value = $invocation.PSObject.Properties[$field.Key]
    if ($null -eq $value -or $value.Value -isnot [string] -or $value.Value -cne $field.Value) {
        throw 'Owning payload does not declare the fixed first-use provision recipe.'
    }
}
if ($identity.name -isnot [string] -or
    ($invocation.version -isnot [int] -and $invocation.version -isnot [long]) -or
    $invocation.version -ne 2 -or $installer -isnot [string] -or $installer -cne 'install') {
    throw 'Owning payload does not declare the fixed first-use provision recipe.'
}
# Admission is already in stock PowerShell; additional interpreter startups exceed hook budgets.
$modeRunner = Join-Path $Payload 'scripts\installation-context\installation-context.ps1'
$previousPayload = $env:COPILOT_PLUGIN_ROOT
try {
    $env:COPILOT_PLUGIN_ROOT = $Payload
    $document = @(& $modeRunner -Action probe-plugin-publication `
        -PayloadRoot $Payload -PluginId $plugin -LegacyRoot $RuntimeRoot -ProjectRoot $Project)
    $code = $LASTEXITCODE
    if ($code -ne 0) { throw 'Owning installation refused first-use publication.' }
    $sourceDocument = @(& $modeRunner -Action resolve `
        -PayloadRoot $Payload -PluginId $plugin -ProjectRoot $Project)
    if ($LASTEXITCODE -ne 0) { throw 'Owning payload provenance could not be resolved.' }
} finally {
    if ($null -eq $previousPayload) {
        Remove-Item Env:COPILOT_PLUGIN_ROOT -ErrorAction SilentlyContinue
    } else { $env:COPILOT_PLUGIN_ROOT = $previousPayload }
}
if ($code -ne 0) { throw 'Owning installation refused first-use publication.' }
$admission = ($document -join "`n") | ConvertFrom-Json
$provenance = ($sourceDocument -join "`n") | ConvertFrom-Json
$selectedRoot = if ($admission.runtimeRoot) { $admission.runtimeRoot } else { $admission.legacy.root }
if ($admission.status -cne 'ready' -or $admission.actualMode -cne 'legacy' -or
    $admission.deployment.allowPluginPublication -isnot [bool] -or
    -not $admission.deployment.allowPluginPublication -or
    [string]$selectedRoot -cne $RuntimeRoot -or
    [string]$provenance.sourceFingerprint -cnotmatch '\Asha256:[0-9a-f]{64}\z' -or
    [string]$provenance.marketplaceId -cne [string]$admission.marketplaceId) {
    throw 'Owning first-use publication admission is incomplete or names another installation.'
}
$source = [string]$provenance.sourceFingerprint
if ($ExpectedSource -and $ExpectedSource -cne $source) {
    throw 'Owning source changed after native first-use admission.'
}

if ($Mode -ceq 'launch') {
    [CopilotExtensions.NativeFoundationState]::CheckExecutionRoot($Root, $foundation)
    . (Join-Path $foundation 'src\update_driver\directory.ps1')
    . (Join-Path ([IO.Path]::GetDirectoryName($foundation)) 'agent-procutil\src\agent_procutil\native_daemon.ps1')
    New-NativeExecutionDirectory -Root $Root
    $log = Join-Path $Root ('first-use-' + [Guid]::NewGuid().ToString('N') + '.log')
    [IO.File]::Open($log, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read).Dispose()
    $worker = Start-NativeDaemon -Script $PSCommandPath -Arguments @(
        $Root, $Payload, $RuntimeRoot, $Project, 'owner', $source, $log, $ServiceMode, $InstallerAction
    ) -WorkingDirectory $Project -LogPath $log
    try {
        $acknowledgement = @{
            launched = $true; log = $log; pid = $worker.Id
            created = $worker.StartTime.ToUniversalTime().ToFileTimeUtc()
        } | ConvertTo-Json -Compress
        if ($Wait) {
            [Console]::Error.WriteLine("[installation-driver] launch acknowledgement: $acknowledgement")
            $worker.WaitForExit()
            $code = $worker.ExitCode
            Get-Content -LiteralPath $log -Encoding UTF8 | ForEach-Object { [Console]::Error.WriteLine($_) }
            exit $code
        }
        # This is launch acknowledgement, never a durable ordinary-request receipt.
        [Console]::Out.WriteLine($acknowledgement)
    } finally { $worker.Dispose() }
    exit 0
}

$producer = Start-NativeFoundationProducer -Root $Root -Plugin $foundation
try {
    $null = $producer.Handle
    if (-not $producer.WaitForExit(1200000)) {
        throw 'Foundation readiness timed out; inspect its durable progress before retrying.'
    }
    if ($producer.ExitCode -ne 0) { throw 'Foundation construction failed; inspect its durable progress.' }
} finally { $producer.Dispose() }
$progress = Get-NativeFoundationProgress -Root $Root | ConvertFrom-Json
if (-not $progress -or $progress.state -cne 'completed') {
    throw 'Foundation did not publish a completed operation.'
}
$target = Join-Path $Root 'foundation'
$selector = Join-Path $target 'current-version'
$null = [CopilotExtensions.NativeFoundationState]::CheckPath($selector, $false, $false)
if ((Get-Item -LiteralPath $selector).Length -gt 128) { throw 'Malformed foundation selector.' }
$selected = [IO.File]::ReadAllText($selector).Trim()
if ($selected -cne ('foundation-' + $progress.source_hash)) {
    throw 'Selected foundation differs from its completed construction.'
}
$slot = Join-Path $target ('versions\' + $selected)
$python = Join-Path $slot 'Scripts\python.exe'
$launcher = Join-Path $slot '.runtime-bootstrap\runtime_launcher.py'
foreach ($path in @($python, $launcher, (Join-Path $slot '.install-complete.json'))) {
    $null = [CopilotExtensions.NativeFoundationState]::CheckPath($path, $false, $false)
}
if (-not $ReceiptLog -or [IO.Path]::GetDirectoryName($ReceiptLog) -cne $Root -or
    [IO.Path]::GetFileName($ReceiptLog) -cnotmatch '\Afirst-use-[0-9a-f]{32}\.log\z') {
    throw 'Native first-use receipt log does not belong to this execution root.'
}
$null = [CopilotExtensions.NativeFoundationState]::CheckPath($ReceiptLog, $false, $true)
$previousEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    $submit = @(
        '-I', '-S', '-B', '-X', 'utf8', $launcher, '-m', 'update_driver.first_use', 'submit',
        '--payload', $Payload, '--runtime-root', $RuntimeRoot, '--project', $Project,
        '--root', $Root, '--expected-source', $source, '--wait'
    )
    if ($ServiceMode -ceq 'client-only') { $submit += '--client-only' }
    if ($InstallerAction -cne 'provision') { $submit += @('--installer-action', $InstallerAction) }
    & $python @submit 2>&1 |
        ForEach-Object {
            try {
                [IO.File]::AppendAllText($ReceiptLog, ([string]$_ + "`n"), (New-Object Text.UTF8Encoding($false)))
            } catch { throw 'First-use receipt diagnostics could not be persisted.' }
        }
    $code = $LASTEXITCODE
} finally { $ErrorActionPreference = $previousEap }
exit $code
