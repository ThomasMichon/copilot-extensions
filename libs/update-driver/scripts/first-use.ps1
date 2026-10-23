param(
    [Parameter(Mandatory)][string]$Root,
    [Parameter(Mandatory)][string]$Payload,
    [Parameter(Mandatory)][string]$RuntimeRoot,
    [Parameter(Mandatory)][string]$Project,
    [ValidateSet('launch', 'owner')][string]$Mode = 'launch',
    [string]$ExpectedSource,
    [string]$ReceiptLog,
    [ValidateSet('default', 'client-only')][string]$ServiceMode = 'default'
)
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$Mode = $Mode.ToLowerInvariant()
$ServiceMode = $ServiceMode.ToLowerInvariant()
if (Test-Path Env:UPDATE_DRIVER_WITNESS) {
    throw 'Nested first-use submission is unsupported; retain the current installation owner.'
}
if ($env:OS -ne 'Windows_NT' -or $PSVersionTable.PSEdition -ne 'Desktop' -or $PSVersionTable.PSVersion -lt [version]'5.1') {
    throw 'Native first-use admission requires stock Windows PowerShell 5.1.'
}
$foundation = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
. (Join-Path $foundation 'src\update_driver\foundation.ps1')
$shell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
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
if ($env:AGENT_VAULT_NO_SELFPROVISION -or (Test-Path Env:AGENT_VAULT_PINNED_CONTEXT)) {
    throw 'Owning plugin first-use provisioning is disabled for this invocation.'
}
$invocation = [IO.File]::ReadAllText((Join-Path $Payload 'payload-invocation.json')) | ConvertFrom-Json
$identity = [IO.File]::ReadAllText((Join-Path $Payload 'plugin.json')) | ConvertFrom-Json
$installer = if ($null -eq $invocation.PSObject.Properties['installer']) {
    'install'
} else { $invocation.installer }
$recipe = @{
    schema = 'copilot-extensions.payload-invocation'
    command = 'agent-vault'
    noSelfProvisionEnv = 'AGENT_VAULT_NO_SELFPROVISION'
    provisionMode = 'direct'
    installationContext = 'required'
}
foreach ($field in $recipe.GetEnumerator()) {
    $value = $invocation.PSObject.Properties[$field.Key]
    if ($null -eq $value -or $value.Value -isnot [string] -or $value.Value -cne $field.Value) {
        throw 'Owning payload does not declare the fixed first-use provision recipe.'
    }
}
if ($identity.name -isnot [string] -or $identity.name -cne 'agent-vault' -or
    ($invocation.version -isnot [int] -and $invocation.version -isnot [long]) -or
    $invocation.version -ne 2 -or $installer -isnot [string] -or $installer -cne 'install') {
    throw 'Owning payload does not declare the fixed first-use provision recipe.'
}
$probe = @(
    '-NoLogo', '-NoProfile', '-NonInteractive', '-File',
    (Join-Path $Payload 'scripts\installation-context\installation-context.ps1'),
    'probe-plugin-publication', '-PayloadRoot', $Payload,
    '-PluginId', 'agent-vault', '-LegacyRoot', $RuntimeRoot,
    '-ProjectRoot', $Project
)
$previousPayload = $env:COPILOT_PLUGIN_ROOT
try {
    $env:COPILOT_PLUGIN_ROOT = $Payload
    $document = @(& $shell @probe)
    $code = $LASTEXITCODE
    if ($code -ne 0) { throw 'Owning installation refused first-use publication.' }
    $sourceDocument = @(& $shell -NoLogo -NoProfile -NonInteractive -File `
        (Join-Path $Payload 'scripts\installation-context\installation-context.ps1') `
        resolve -PayloadRoot $Payload -PluginId agent-vault -ProjectRoot $Project)
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
    $worker = Start-NativeDaemon -Script $PSCommandPath -Arguments @(
        $Root, $Payload, $RuntimeRoot, $Project, 'owner', $source, $log, $ServiceMode
    ) -WorkingDirectory $Project -LogPath $log
    try {
        # This is launch acknowledgement, never a durable ordinary-request receipt.
        [Console]::Out.WriteLine((@{
            launched = $true; log = $log; pid = $worker.Id
            created = $worker.StartTime.ToUniversalTime().ToFileTimeUtc()
        } | ConvertTo-Json -Compress))
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
    & $python @submit 2>&1 |
        ForEach-Object {
            try {
                [IO.File]::AppendAllText($ReceiptLog, ([string]$_ + "`n"), (New-Object Text.UTF8Encoding($false)))
            } catch { throw 'First-use receipt diagnostics could not be persisted.' }
        }
    $code = $LASTEXITCODE
} finally { $ErrorActionPreference = $previousEap }
exit $code
