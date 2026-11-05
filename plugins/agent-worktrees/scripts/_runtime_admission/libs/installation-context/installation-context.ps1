[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Action,
    [string]$Distribution,
    [string]$SourceJson,
    [string]$SourceFile,
    [string]$MarketplaceKey,
    [string]$PluginId,
    [string]$PayloadRoot,
    [string]$CopilotHome,
    [string]$ProjectRoot,
    [string]$DurableHome,
    [string]$Context,
    [string]$ExpectedMarketplaceId,
    [string]$ExpectedPluginId,
    [string]$ExpectedPayloadRoot,
    [string]$ExpectedPayloadVersion,
    [string]$ExpectedCellRoot,
    [string]$PayloadVersion,
    [string]$PayloadOrigin,
    [string]$PayloadOriginReceipt,
    [object]$ExpectedNamespaceGeneration,
    [object]$ExpectedInstallGeneration,
    [object]$ExpectedActivationGeneration,
    [string]$ExpectedCurrentVersion,
    [switch]$ExpectCurrentAbsent,
    [string]$SnapshotId,
    [string]$RuntimeVersion,
    [string]$ReservationRoot,
    [object]$ExpectedReservationGeneration,
    [string]$ExpectedReservationSha256,
    [string]$NamespaceState = 'active',
    [string]$InstallState = 'active',
    [string]$ActivationMode,
    [string]$ActivationState,
    [string]$LegacyDisposition,
    [string]$LegacyRoot,
    [string]$LegacyProbeJson,
    [string]$LegacyProbeFile,
    [string]$PolicyPath,
    [switch]$WithContextValidation
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
$utf8NoBom = New-Object Text.UTF8Encoding($false)
$OutputEncoding = $utf8NoBom
[Console]::OutputEncoding = $utf8NoBom

function Fail([string]$Message) { throw $Message }

function ConvertTo-ExpectedGeneration($Value, [string]$ReceiptName) {
    if ($null -eq $Value) {
        Fail "Expected $ReceiptName generation must be a non-negative integer."
    }
    $text = [Convert]::ToString($Value, [Globalization.CultureInfo]::InvariantCulture)
    if ($text -notmatch '\A[0-9]+\z') {
        Fail "Expected $ReceiptName generation must be a non-negative integer."
    }
    [long]$parsed = 0
    if (-not [long]::TryParse(
        $text,
        [Globalization.NumberStyles]::None,
        [Globalization.CultureInfo]::InvariantCulture,
        [ref]$parsed
    )) {
        Fail "Expected $ReceiptName generation exceeds the portable signed 64-bit maximum."
    }
    return $text
}

function ConvertTo-InputPath([string]$Value) {
    return [IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($Value))
}

try {
    $request = @{}
    foreach ($name in $PSBoundParameters.Keys) {
        if ($name -cin @('Action', 'ExpectCurrentAbsent', 'WithContextValidation')) { continue }
        $request[$name] = $PSBoundParameters[$name]
    }
    $request['NamespaceState'] = $NamespaceState
    $request['InstallState'] = $InstallState
    $request['ExpectCurrentAbsent'] = [bool]$ExpectCurrentAbsent
    $request['WithContextValidation'] = [bool]$WithContextValidation

    foreach ($pair in @(
        @('ExpectedNamespaceGeneration', 'namespace.json'),
        @('ExpectedInstallGeneration', 'install.json'),
        @('ExpectedActivationGeneration', 'activation'),
        @('ExpectedReservationGeneration', 'reservation')
    )) {
        $required = (
            ($pair[0] -cin @('ExpectedNamespaceGeneration', 'ExpectedInstallGeneration') -and
                $Action -cin @('stamp', 'activation-cas', 'snapshot-stamp', 'slot-cutover', 'slot-release')) -or
            ($pair[0] -ceq 'ExpectedActivationGeneration' -and $Action -ceq 'activation-cas') -or
            ($pair[0] -ceq 'ExpectedReservationGeneration' -and $Action -ceq 'slot-release')
        )
        if ($required) {
            $request[$pair[0]] = ConvertTo-ExpectedGeneration (Get-Variable -Name $pair[0] -ValueOnly) $pair[1]
        }
    }

    if ($Action -ceq 'slot-release' -and -not $DurableHome) {
        Fail 'slot-release requires -DurableHome.'
    }
    $userHomePath = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }
    if ((-not $CopilotHome -or -not $DurableHome) -and -not $userHomePath) {
        Fail 'Cannot determine the user home.'
    }
    if (-not $CopilotHome) { $CopilotHome = Join-Path $userHomePath '.copilot' }
    if (-not $DurableHome) { $DurableHome = Join-Path $userHomePath '.copilot-extensions' }
    if (-not [IO.Path]::IsPathRooted($CopilotHome) -or
        -not [IO.Path]::IsPathRooted($DurableHome)) {
        Fail '-CopilotHome and -DurableHome must be absolute.'
    }
    $request['CopilotHome'] = ConvertTo-InputPath $CopilotHome
    $request['DurableHome'] = ConvertTo-InputPath $DurableHome
    $modeActions = @('status', 'deployment-status', 'probe-plugin-publication', 'probe-legacy')
    $sourceActions = @('source-id', 'resolve', 'stamp') + $modeActions
    $fileInputs = @('ProjectRoot')
    if ($Action -cin $sourceActions) { $fileInputs += 'SourceFile' }
    if ($Action -cin ($modeActions + @('activation-cas'))) { $fileInputs += 'LegacyProbeFile' }
    foreach ($name in $fileInputs) {
        if ($request[$name]) { $request[$name] = ConvertTo-InputPath $request[$name] }
    }
    # These inputs historically accept rooted drive-relative paths. Receipt
    # pointers, expectations and reservation identities retain stricter checks.
    $pointer = if ($Context) { $Context } else { $env:COPILOT_EXTENSIONS_CONTEXT }
    $rootedInputs = @()
    if ($Action -ceq 'stamp' -or ($Action -cin (@('resolve') + $modeActions) -and -not $pointer)) {
        $rootedInputs += 'PayloadRoot'
    }
    if ($Action -cin $modeActions) { $rootedInputs += @('LegacyRoot', 'PolicyPath') }
    if ($Action -ceq 'stamp') { $rootedInputs += 'PayloadOriginReceipt' }
    foreach ($name in $rootedInputs) {
        if ($request[$name]) {
            if (-not [IO.Path]::IsPathRooted([string]$request[$name])) {
                Fail "-$name must be absolute."
            }
            $request[$name] = ConvertTo-InputPath $request[$name]
        }
    }
    if ($request['ProjectRoot'] -and -not (Test-Path -LiteralPath $request['ProjectRoot'])) {
        Fail "Path does not exist: $($request['ProjectRoot'])"
    }
    $childEnvironment = @{}
    if ($env:COPILOT_PLUGIN_ROOT -and -not $pointer -and
        $Action -cin (@('resolve', 'stamp') + $modeActions)) {
        if (-not [IO.Path]::IsPathRooted($env:COPILOT_PLUGIN_ROOT)) {
            Fail 'COPILOT_PLUGIN_ROOT must be absolute.'
        }
        $childEnvironment['COPILOT_PLUGIN_ROOT'] = ConvertTo-InputPath $env:COPILOT_PLUGIN_ROOT
    }
    if ($Action -cin $modeActions -and $pointer) {
        if (-not [IO.Path]::IsPathRooted($pointer)) {
            Fail 'The installation-context receipt pointer must be absolute.'
        }
        $request['Context'] = ConvertTo-InputPath $pointer
    }
    $sdk = Join-Path $PSScriptRoot 'installation_context.py'
    if (-not (Test-Path -LiteralPath $sdk -PathType Leaf)) {
        Fail 'The owning payload installation_context.py SDK is unavailable.'
    }
    $packet = @{
        sdk = $sdk
        action = $Action
        parameters = $request
        supplied = @($PSBoundParameters.Keys)
        processDirectory = [Environment]::CurrentDirectory
        environment = $childEnvironment
    }
    & (Join-Path $PSScriptRoot 'shell-caller-owned.ps1') `
        -Worker (Join-Path $PSScriptRoot '_installation_context_worker.py') `
        -Request $packet -DiagnosticPrefix 'installation-context'
    exit $LASTEXITCODE
}
catch {
    [Console]::Error.WriteLine("installation-context: $($_.Exception.Message)")
    exit 1
}
