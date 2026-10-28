$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
$OutputEncoding = [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$forwardArgs = @($args)
$globalSurface = (Split-Path -Leaf $PSScriptRoot) -ceq 'bin'
$module = 'agent_dispatch'
if ($globalSurface) {
    $legacyPolicyRoot = Join-Path $PSScriptRoot '..'
    $legacyRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
    $pointer = Join-Path $legacyRoot 'payload-dir'
    $payloadRoot = if ([IO.File]::Exists($pointer)) { [IO.File]::ReadAllText($pointer).Trim() } else { '' }
    if ((-not $payloadRoot -or -not [IO.File]::Exists((Join-Path $payloadRoot 'snapshot-provenance.json'))) -and
        $env:AGENT_DISPATCH_PAYLOAD_ROOT -and
        [IO.File]::Exists((Join-Path $env:AGENT_DISPATCH_PAYLOAD_ROOT 'plugin.json'))) {
        $payloadRoot = $env:AGENT_DISPATCH_PAYLOAD_ROOT
    }
    if ($env:AGENT_DISPATCH_COMMAND_MODULE) {
        if ($env:AGENT_DISPATCH_COMMAND_MODULE -cnotin @('agent_dispatch', 'agent_dispatch.board_cli')) {
            [Console]::Error.WriteLine('[agent-dispatch] unsupported command module.')
            exit 126
        }
        $module = $env:AGENT_DISPATCH_COMMAND_MODULE
    }
} else {
    $payloadRoot = $env:AGENT_DISPATCH_PAYLOAD_ROOT
    $ownRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
    if (-not $payloadRoot -or -not [string]::Equals($payloadRoot, $ownRoot, [StringComparison]::OrdinalIgnoreCase)) {
        [Console]::Error.WriteLine('[agent-dispatch] owning payload root is unavailable.')
        exit 126
    }
    $legacyRoot = if ($env:AGENT_DISPATCH_INSTALL_DIR) { $env:AGENT_DISPATCH_INSTALL_DIR } else {
        Join-Path $env:USERPROFILE '.agent-dispatch' # marketplace-isolation: allow legacy compatibility root
    }
    $legacyPolicyRoot = $legacyRoot
}
$helper = Join-Path $PSScriptRoot 'installation-context\installation-context.ps1'
$resolver = Join-Path $PSScriptRoot 'resolve-runtime.ps1'
$resolverCapability = $resolver
if ($globalSurface -and -not [IO.File]::Exists($resolver) -and $payloadRoot) {
    $resolverCapability = Join-Path $payloadRoot 'scripts\resolve-runtime.ps1'
}
foreach ($support in @($helper, $resolverCapability)) {
    if (-not [IO.File]::Exists($support)) {
        [Console]::Error.WriteLine("[agent-dispatch] required runtime admission support is unavailable: $support")
        exit 126
    }
}
if (-not (Select-String -LiteralPath $resolverCapability -SimpleMatch '# Trace contract: normal/read-only/ready-v1' -Quiet)) {
    [Console]::Error.WriteLine('[agent-dispatch] read-only runtime resolver is unavailable; update the owning installation.')
    exit 126
}
function Stop-Context([string]$Reason) {
    [Console]::Error.WriteLine("[agent-dispatch] installation context blocks invocation: $Reason")
    exit 126
}
function Read-Decision([hashtable]$Arguments) {
    $global:LASTEXITCODE = 1
    $response = @(& $helper @Arguments)
    if ($LASTEXITCODE -ne 0) { Stop-Context ($response -join ' ') }
    try { return ($response -join "`n") | ConvertFrom-Json -ErrorAction Stop }
    catch { Stop-Context "malformed decision: $($_.Exception.Message)" }
}
function Test-LegacyInvocationAllowed($Decision) {
    return (($Decision.status -ceq 'ready' -and $Decision.actualMode -ceq 'legacy' -and
             $Decision.desiredMode -ceq 'legacy') -or
        ($Decision.status -ceq 'provenance-blocked' -and -not $env:COPILOT_EXTENSIONS_CONTEXT -and
         $Decision.policy.enabled -is [bool] -and -not $Decision.policy.enabled -and
         $null -eq $Decision.legacy.tombstone -and $Decision.legacy.disposition -ceq 'active'))
}
$context = ''
$runtimeRoot = $legacyRoot
$statusArgs = @{ Action = 'status'; PayloadRoot = $payloadRoot; PluginId = 'agent-dispatch'; LegacyRoot = $legacyRoot }
if ($env:COPILOT_EXTENSIONS_CONTEXT) {
    $statusArgs.Context = $env:COPILOT_EXTENSIONS_CONTEXT
    $durableHome = $env:COPILOT_EXTENSIONS_CONTEXT
    1..5 | ForEach-Object { $durableHome = Split-Path -Parent $durableHome }
    $statusArgs.DurableHome = $durableHome
}
$resolution = Read-Decision $statusArgs
$installGeneration = [string]$resolution.installGeneration
if (Test-LegacyInvocationAllowed $resolution) {
    if ($env:COPILOT_EXTENSIONS_CONTEXT) { Stop-Context 'requested context is not active' }
    $runtimeRoot = [string]$resolution.legacy.root
} elseif ($resolution.actualMode -ceq 'namespaced' -and
    (($resolution.status -ceq 'ready' -and $resolution.reason -ceq 'namespaced-active') -or
     $resolution.status -ceq 'deactivation-required')) {
    $runtimeRoot = [string]$resolution.runtimeRoot
    $context = [string]$resolution.context
    if (-not $context) { Stop-Context 'active context is incomplete' }
    $durableHome = $context
    1..5 | ForEach-Object { $durableHome = Split-Path -Parent $durableHome }
    $validation = Read-Decision @{
        Action = 'validate'; Context = $context; DurableHome = $durableHome
        ExpectedPluginId = 'agent-dispatch'; ExpectedPayloadRoot = $payloadRoot
    }
    if ([string]$validation.generation -cne $installGeneration -or [string]$validation.pluginRoot -cne $runtimeRoot) {
        Stop-Context 'generation does not match governance'
    }
} else { Stop-Context ($resolution | ConvertTo-Json -Compress -Depth 20) }
if (-not $runtimeRoot -or -not [IO.Path]::IsPathRooted($runtimeRoot)) { Stop-Context 'canonical runtime root is missing' }
function Resolve-Runtime([string]$TraceMode = 'ready') {
    $AgentRtPy = $null
    $env:AGENT_RT_ROOT = $runtimeRoot
    if (-not [IO.File]::Exists($resolver)) { return $null }
    . $resolver -TraceMode $TraceMode
    return $AgentRtPy
}
function Invoke-Runtime([string]$Python) {
    $env:AGENT_DISPATCH_INSTALL_DIR = $runtimeRoot
    if ($context) { $env:COPILOT_EXTENSIONS_CONTEXT = $context } else {
        Remove-Item Env:COPILOT_EXTENSIONS_CONTEXT -ErrorAction SilentlyContinue
    }
    $nativeArgs = $forwardArgs
    $argumentMode = Get-Variable PSNativeCommandArgumentPassing -ValueOnly -ErrorAction SilentlyContinue
    if (-not $argumentMode -or $argumentMode -eq 'Legacy') {
        $nativeArgs = @($forwardArgs | ForEach-Object {
            '"' + [regex]::Replace([regex]::Replace([string]$_, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
        })
    }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Python -m $module @nativeArgs } finally { $ErrorActionPreference = $previous }
    exit $LASTEXITCODE
}
$python = Resolve-Runtime
if ($python) { Invoke-Runtime $python }
if ($env:AGENT_DISPATCH_NO_SELFPROVISION) {
    [Console]::Error.WriteLine('[agent-dispatch] runtime not provisioned (AGENT_DISPATCH_NO_SELFPROVISION set).')
    exit 1
}
function Assert-AutomaticRuntimeReconcile {
    $actionHelper = Join-Path $payloadRoot 'scripts\action_controls.py'
    $nativeHelper = Join-Path $payloadRoot 'scripts\versioned_runtime_native.ps1'
    foreach ($support in @($actionHelper, $nativeHelper)) {
        if (-not [IO.File]::Exists($support)) {
            [Console]::Error.WriteLine("[agent-dispatch] automatic-runtime-reconcile prerequisite unavailable: $support")
            $script:ProvisionFailureCode = 126
            exit 126
        }
    }
    . $nativeHelper
    $policyPython = $null
    foreach ($candidate in @('python3', 'python', 'py')) {
        $command = Get-Command $candidate -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $command -or $command.Source -match 'WindowsApps') { continue }
        if ($candidate -eq 'py') {
            $probeError = ''
            $probe = Invoke-Hidden -FilePath $command.Source -ArgList @('-3', '-I', '-B', '-c', 'import sys; print(sys.executable)') -ErrorOutput ([ref]$probeError)
            if ($LASTEXITCODE -ne 0) { continue }
            $policyPython = $probe.Trim()
        } else { $policyPython = $command.Source }
        if ([IO.Path]::IsPathRooted($policyPython) -and [IO.File]::Exists($policyPython)) { break }
        $policyPython = $null
    }
    if (-not $policyPython) {
        [Console]::Error.WriteLine('[agent-dispatch] automatic-runtime-reconcile prerequisite unavailable: absolute Python interpreter is missing.')
        $script:ProvisionFailureCode = 126
        exit 126
    }
    $diagnostic = ''
    try {
        $policyRoots = @($runtimeRoot)
        $legacySelection = Get-Variable -Name legacyPolicyRoot -ValueOnly -ErrorAction SilentlyContinue
        $selectedContext = Get-Variable -Name context -ValueOnly -ErrorAction SilentlyContinue
        # Keep governance's root authoritative, without erasing the selected spelling's ancestry.
        if ($legacySelection -and -not $selectedContext -and $legacySelection -cne $runtimeRoot) {
            $policyRoots = @([string]$legacySelection, $runtimeRoot)
        }
        $globalEntry = Get-Variable -Name globalSurface -ValueOnly -ErrorAction SilentlyContinue
        $mode = Get-Variable -Name resolution -ValueOnly -ErrorAction SilentlyContinue
        if ($globalEntry -and -not $selectedContext -and $mode -and $mode.environment.homeRealPath) {
            $defaultRoot = Join-Path ([string]$mode.environment.homeRealPath) '.agent-dispatch'
            $comparison = if ([IO.Path]::DirectorySeparatorChar -eq '/') {
                [StringComparison]::Ordinal
            } else { [StringComparison]::OrdinalIgnoreCase }
            if ([string]::Equals($runtimeRoot, $defaultRoot, $comparison)) {
                $selectedHome = if ([IO.Path]::DirectorySeparatorChar -eq '/') {
                    $env:HOME
                } else { $env:USERPROFILE }
                $defaultSpelling = Join-Path $selectedHome '.agent-dispatch'
                if ($defaultSpelling -cne $runtimeRoot) {
                    $policyRoots = @($defaultSpelling) + $policyRoots
                }
            }
        }
        foreach ($policyRoot in $policyRoots) {
            $pathRoot = [IO.Path]::GetPathRoot($policyRoot)
            $unixRoot = [IO.Path]::DirectorySeparatorChar -eq '/' -and $pathRoot -ceq '/'
            if (-not $pathRoot -or (-not $unixRoot -and
                ($pathRoot.Length -le 1 -or $pathRoot.EndsWith(':')))) {
                throw 'selected installation root must be absolute'
            }
            $response = Invoke-Hidden -FilePath $policyPython -ArgList @(
                '-I', '-B', $actionHelper, '--root', $policyRoot,
                '--action', 'automatic-runtime-reconcile', '--automatic'
            ) -ErrorOutput ([ref]$diagnostic)
            $code = $LASTEXITCODE
            if ($diagnostic) { [Console]::Error.Write($diagnostic) }
            if ($code -ne 0) {
                [Console]::Error.WriteLine("[agent-dispatch] automatic-runtime-reconcile refused (exit $code): $response")
                if ($code -eq 3) { $script:ProvisionFailureCode = 3; exit 3 }
                $script:ProvisionFailureCode = 126
                exit 126
            }
            $decision = $response | ConvertFrom-Json -ErrorAction Stop
            if ($decision.ok -isnot [bool] -or -not $decision.ok) {
                throw "policy did not admit the action: $response"
            }
        }
    } catch {
        [Console]::Error.WriteLine("[agent-dispatch] automatic-runtime-reconcile policy check failed: $($_.Exception.Message)")
        $script:ProvisionFailureCode = 126
        exit 126
    }
}
$script:ColdRuntimeEffects = $false
function Assert-Publication {
    if ($script:ColdRuntimeEffects) { Assert-AutomaticRuntimeReconcile }
    $publicationArgs = @{
        Action = 'probe-plugin-publication'; PayloadRoot = $payloadRoot
        PluginId = 'agent-dispatch'; LegacyRoot = $runtimeRoot
    }
    if ($context) { $publicationArgs.Context = $context; $publicationArgs.DurableHome = $durableHome }
    try {
        $global:LASTEXITCODE = 3
        $response = @(& $helper @publicationArgs)
        $code = $LASTEXITCODE
        $decision = ($response -join "`n") | ConvertFrom-Json -ErrorAction Stop
        $selected = if ($context) { [string]$decision.runtimeRoot } else { [string]$decision.legacy.root }
        if ($code -ne 0 -or $decision.deployment.allowPluginPublication -isnot [bool] -or
            -not $decision.deployment.allowPluginPublication -or $selected -cne $runtimeRoot -or
            [string]$decision.deployment.path -cne (Join-Path $runtimeRoot 'runtime-deployment.json') -or
            (-not $context -and -not (Test-LegacyInvocationAllowed $decision))) {
            throw ($response -join ' ')
        }
        if ($context) {
            [Console]::Error.WriteLine('[agent-dispatch] installation-cell first-use provisioning is unavailable; use explicit installation management for this context.')
            $script:ProvisionFailureCode = 126; exit 126
        }
        $defaultRoot = Join-Path $resolution.environment.homeRealPath '.agent-dispatch'
        if (-not [string]::Equals($runtimeRoot, $defaultRoot, [StringComparison]::OrdinalIgnoreCase)) {
            [Console]::Error.WriteLine('[agent-dispatch] scoped first-use provisioning is unavailable; use explicit installation management for this root.')
            $script:ProvisionFailureCode = 126; exit 126
        }
        $publicationArgs.Action = 'probe-legacy'
        $global:LASTEXITCODE = 3
        $response = @(& $helper @publicationArgs)
        $code = $LASTEXITCODE
        $legacy = ($response -join "`n") | ConvertFrom-Json -ErrorAction Stop
        if ($code -ne 0 -or $legacy.allowMutation -isnot [bool] -or -not $legacy.allowMutation -or
            [string]$legacy.legacy.root -cne $runtimeRoot -or
            -not (Test-LegacyInvocationAllowed $legacy)) { throw ($response -join ' ') }
    } catch {
        [Console]::Error.WriteLine(
            "[agent-dispatch] Runtime publication refused: $($_.Exception.Message) Use explicit installation management to install or repair this runtime."
        )
        $script:ProvisionFailureCode = 3
        exit 3
    }
}
Assert-Publication
function Select-ProvisionSource {
    $sourcePinned = $false
    $sourcePresent = $false
    $sourcePayloadPath = Join-Path $runtimeRoot 'payload-dir'
    $sourcePayload = if ([IO.File]::Exists($sourcePayloadPath)) { [IO.File]::ReadAllText($sourcePayloadPath).Trim() } else { '' }
    foreach ($marker in @('stamped-version', 'current-version', 'last-known-good')) {
        $path = Join-Path $runtimeRoot $marker
        $selected = if ([IO.File]::Exists($path)) { [IO.File]::ReadAllText($path).Trim() } else { '' }
        if ($selected -cmatch '[+.]source\.') {
            $sourcePresent = $true
            $expected = Join-Path (Join-Path $runtimeRoot 'snapshots') $selected
            if ([string]::Equals($sourcePayload, $expected, [StringComparison]::OrdinalIgnoreCase)) {
                $sourcePinned = $true
                break
            }
        }
    }
    if ($sourcePresent -and -not $sourcePinned) {
        [Console]::Error.WriteLine('Selected source snapshot is unavailable; refusing a different provisioning source.')
        $script:ProvisionFailureCode = 126; exit 126
    }
    if (-not $sourcePinned) {
        $sourceSlots = @(Get-ChildItem -LiteralPath (Join-Path $runtimeRoot 'versions') -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -cmatch '[+.]source\.' })
        if ($sourceSlots.Count) {
            [Console]::Error.WriteLine('Source runtime recovery requires an exact source selector; refusing upstream provisioning.')
            $script:ProvisionFailureCode = 126; exit 126
        }
    }
    $installer = if (($globalSurface -or $sourcePinned) -and $sourcePayload) {
        Join-Path $sourcePayload 'scripts\install.ps1'
    } elseif ($payloadRoot) {
        Join-Path $payloadRoot 'scripts\install.ps1'
    } else { '' }
    if ($sourcePinned -and -not [IO.File]::Exists($installer)) {
        [Console]::Error.WriteLine('Selected source snapshot is unavailable; refusing a different provisioning source.')
        $script:ProvisionFailureCode = 126; exit 126
    }
        return @{ SourcePinned = $sourcePinned; Installer = $installer }
}
$provisionSource = Select-ProvisionSource
$python = Resolve-Runtime normal
if ($python) { Invoke-Runtime $python }
$script:ColdRuntimeEffects = $true
Assert-AutomaticRuntimeReconcile
[void][IO.Directory]::CreateDirectory($runtimeRoot)
$statusPath = Join-Path $runtimeRoot '.provision-status'
$lock = $null
while (-not $lock) {
    try { $lock = [IO.File]::Open((Join-Path $runtimeRoot '.provision.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
    catch [IO.IOException] {
        $code = $_.Exception.HResult -band 0xffff
        $posixContention = [Environment]::OSVersion.Platform -eq [PlatformID]::Unix -and $code -eq 11
        if ($code -notin @(32, 33) -and -not $posixContention) { throw }
        Start-Sleep -Milliseconds 200
    }
}
$rc = 1
$script:ProvisionAttemptStarted = $false
$script:ProvisionFailureCode = 1
function Invoke-Installer([string]$Path) {
    Assert-AutomaticRuntimeReconcile
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = 1
    try {
        & $hostExe -NoProfile -ExecutionPolicy Bypass -File $Path provision -InstallDir $runtimeRoot 2>&1 |
            ForEach-Object { [Console]::Error.WriteLine($_) }
        return $LASTEXITCODE
    } finally { $ErrorActionPreference = $previous }
}
try {
    $python = Resolve-Runtime
    if (-not $python) {
        Assert-AutomaticRuntimeReconcile
        Assert-Publication
        $provisionSource = Select-ProvisionSource
        [Console]::Error.WriteLine('[agent-dispatch] runtime not provisioned -- provisioning on first use (may take ~30-120s). Do not kill; extend your timeout.')
        [Console]::Error.WriteLine("::agent-provisioning:: plugin=agent-dispatch eta_seconds=120 reason=first-use status=$statusPath")
        $script:ProvisionFailureCode = 1
        [IO.File]::WriteAllText($statusPath, 'provisioning')
        $script:ProvisionAttemptStarted = $true
        $env:AGENT_DISPATCH_INSTALL_DIR = $runtimeRoot
        $hostExe = (Get-Process -Id $PID).Path
        if ($globalSurface -and -not $provisionSource.SourcePinned -and -not $env:AGENT_DISPATCH_PROVISION_OFFLINE) {
            $bootFile = Join-Path ([IO.Path]::GetTempPath()) "agent-dispatch-bootstrap-$PID.ps1"
            try {
                Assert-AutomaticRuntimeReconcile
                [Console]::Error.WriteLine('[agent-dispatch] fetching upstream installer (install/dispatch/bootstrap.ps1, main)...')
                Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/install/dispatch/bootstrap.ps1' -UseBasicParsing -TimeoutSec 20 -OutFile $bootFile
                Assert-Publication
                $provisionSource = Select-ProvisionSource
                if (-not $provisionSource.SourcePinned) {
                    $rc = Invoke-Installer $bootFile
                }
            } catch {
                [Console]::Error.WriteLine("[agent-dispatch] upstream installer fetch/run failed ($($_.Exception.Message)); falling back to the locally stamped snapshot.")
            } finally { Remove-Item -LiteralPath $bootFile -Force -ErrorAction SilentlyContinue }
        }
        if ($rc -eq 3) {
            [Console]::Error.WriteLine('[agent-dispatch] upstream provision refused an action or governance check (exit 3); no local snapshot fallback attempted. Retry after the inhibition or governance refusal is resolved.')
        }
        if ($rc -ne 0 -and $rc -ne 3) {
            Assert-Publication
            $provisionSource = Select-ProvisionSource
            if ($provisionSource.Installer -and [IO.File]::Exists($provisionSource.Installer)) {
                $rc = Invoke-Installer $provisionSource.Installer
            } else {
                [Console]::Error.WriteLine('[agent-dispatch] cannot self-provision: local snapshot installer is unavailable; re-enable/update the owning plugin.')
                $rc = 127
            }
        }
        if ($rc -eq 0) { $python = Resolve-Runtime }
        if ($rc -eq 0 -and -not $python) { $rc = 1 }
    }
} finally {
    try {
        # Only this lock holder's successfully published attempt may be settled here.
        if ($script:ProvisionAttemptStarted) {
            $terminalCode = if ($script:ProvisionFailureCode -ne 1) { $script:ProvisionFailureCode } else { $rc }
            $terminalStatus = if ($terminalCode -eq 0 -and $python) { 'ready' } else {
                if ($terminalCode -eq 0) { $terminalCode = 1 }
                "failed rc=$terminalCode"
            }
            try { [IO.File]::WriteAllText($statusPath, $terminalStatus) }
            catch {
                [Console]::Error.WriteLine("[agent-dispatch] could not settle owned provision status ($terminalStatus): $($_.Exception.Message)")
                if ($terminalCode -eq 0) { $rc = 1; $python = $null }
            }
        }
    } finally { $lock.Dispose() }
}
if ($python) { Invoke-Runtime $python }
[Console]::Error.WriteLine("[agent-dispatch] provisioning did not yield a runtime (rc=$rc); see the installer diagnostics above.")
exit $rc
