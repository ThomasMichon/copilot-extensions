$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
$OutputEncoding = [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)

$forwardArgs = [string[]]@($args)
$payloadRoot = [Environment]::GetEnvironmentVariable('AGENT_CONTAINERS_PAYLOAD_ROOT', 'Process')
if (-not $payloadRoot -or -not (Test-Path -LiteralPath $payloadRoot -PathType Container)) {
    [Console]::Error.WriteLine('[agent-containers] owning payload root is unavailable.')
    exit 126
}
$payloadRoot = (Resolve-Path -LiteralPath $payloadRoot).Path
$scriptDir = Join-Path $payloadRoot 'scripts'
$modeRunner = Join-Path $scriptDir 'installation-context\installation-context.ps1'
$portableModeRunner = Join-Path $scriptDir 'installation-context\installation_context.py'
$bootstrapHelper = Join-Path $scriptDir 'bootstrap-python.ps1'
$runtimeResolver = Join-Path $scriptDir 'resolve-runtime.ps1'
$installer = Join-Path $scriptDir 'init.ps1'
$legacyRoot = if ($env:AGENT_CONTAINERS_HOME) {
    $env:AGENT_CONTAINERS_HOME
} else {
    Join-Path $env:USERPROFILE '.agent-containers' # marketplace-isolation: allow legacy compatibility root
}
if (
    -not (Test-Path -LiteralPath $modeRunner -PathType Leaf) -or
    -not (Test-Path -LiteralPath $runtimeResolver -PathType Leaf) -or
    -not (Test-Path -LiteralPath $installer -PathType Leaf)
) {
    [Console]::Error.WriteLine(
        '[agent-containers] installation-context runtime support is unavailable.'
    )
    exit 126
}
if (-not (Select-String -LiteralPath $runtimeResolver -SimpleMatch '# Trace contract: normal/read-only/ready-v1' -Quiet)) {
    [Console]::Error.WriteLine('[agent-containers] read-only runtime resolver is unavailable; update the owning installation.')
    exit 126
}

$hostExe = (Get-Process -Id $PID).Path
if (-not $hostExe) {
    [Console]::Error.WriteLine('[agent-containers] PowerShell host executable is unavailable.')
    exit 126
}

$runtimeRoot = $legacyRoot
$context = ''
$userHomePath = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }
$copilotHome = Join-Path $userHomePath '.copilot'
$durableHome = Join-Path $userHomePath '.copilot-extensions'
if ($env:COPILOT_EXTENSIONS_CONTEXT) {
    $durableHome = $env:COPILOT_EXTENSIONS_CONTEXT
    1..5 | ForEach-Object { $durableHome = Split-Path -Parent $durableHome }
}
if (-not (Test-Path -LiteralPath $bootstrapHelper -PathType Leaf) -or
    -not (Test-Path -LiteralPath $portableModeRunner -PathType Leaf)) {
    [Console]::Error.WriteLine('[agent-containers] payload Python evaluator is unavailable; update the owning plugin.')
    exit 126
}
. $bootstrapHelper
$bootstrap = Resolve-EvaluatorBootstrapPython
if ($bootstrap.Status -ceq 'unavailable') {
    [Console]::Error.WriteLine(
        '[agent-containers] Python 3.10+ must be available as python on PATH. ' +
        'Run the harness Python prerequisite setup, or install the standalone prerequisite, then retry.'
    )
    exit 126
}
if ($bootstrap.Status -cne 'selected') {
    if ($null -ne $bootstrap.Guard) { $bootstrap.Guard.Dispose() }
    throw 'Evaluator bootstrap returned an invalid selection status.'
}
$nativePathSemantics = @($legacyRoot, $durableHome) | Where-Object {
    $_.Contains('%') -or
    ($_ -match '^[A-Za-z]:(?![\\/])') -or ($_ -match '^[\\/](?![\\/])')
}

function Invoke-ContextRead {
    param([string[]]$NativeArguments, [string[]]$PortableArguments)
    $previousEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $global:LASTEXITCODE = 126
        if ($nativePathSemantics) {
            & $hostExe @NativeArguments
        } else {
            & $bootstrap.Python -I -S -B -X utf8 $portableModeRunner @PortableArguments
        }
        $script:ContextReadCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousEap
    }
}

try {
    $statusArgs = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $modeRunner,
        'status',
        '-PayloadRoot', $payloadRoot,
        '-PluginId', 'agent-containers',
        '-LegacyRoot', $legacyRoot
    )
    $portableStatusArgs = @(
        'status', '--copilot-home', $copilotHome,
        '--payload-root', $payloadRoot, '--plugin-id', 'agent-containers',
        '--legacy-root', $legacyRoot
    )
    if ($env:COPILOT_EXTENSIONS_CONTEXT) {
        $statusArgs += @('-Context', $env:COPILOT_EXTENSIONS_CONTEXT)
        $statusArgs += @('-DurableHome', $durableHome)
        $portableStatusArgs += @('--context', $env:COPILOT_EXTENSIONS_CONTEXT, '--durable-home', $durableHome)
    }
    $resolutionJson = @(Invoke-ContextRead -NativeArguments $statusArgs -PortableArguments $portableStatusArgs)
    if ($ContextReadCode -ne 0) {
        [Console]::Error.WriteLine(
            '[agent-containers] installation context could not be resolved.'
        )
        exit 126
    }
    try {
        $resolution = ($resolutionJson -join "`n") | ConvertFrom-Json
    } catch {
        [Console]::Error.WriteLine(
            '[agent-containers] installation context returned malformed status.'
        )
        exit 126
    }

    function Test-LegacyInvocationAllowed($Decision) {
        return (($Decision.status -ceq 'ready' -and $Decision.actualMode -ceq 'legacy' -and
                 $Decision.desiredMode -ceq 'legacy') -or
            ($Decision.status -ceq 'provenance-blocked' -and -not $env:COPILOT_EXTENSIONS_CONTEXT -and
             $Decision.policy.enabled -is [bool] -and -not $Decision.policy.enabled -and
             $null -eq $Decision.legacy.tombstone -and $Decision.legacy.disposition -ceq 'active'))
    }

    $resolutionStatus = [string]$resolution.status
    $resolutionReason = [string]$resolution.reason
    $actualMode = [string]$resolution.actualMode
    $installGeneration = [string]$resolution.installGeneration

    if (Test-LegacyInvocationAllowed $resolution) {
        if ($env:COPILOT_EXTENSIONS_CONTEXT) {
            [Console]::Error.WriteLine(
                '[agent-containers] requested installation context is not active.'
            )
            exit 126
        }
    }
    elseif (
        (
            $resolutionStatus -ceq 'ready' -and
            $resolutionReason -ceq 'namespaced-active'
        ) -or
        $resolutionStatus -ceq 'deactivation-required'
    ) {
        if ($actualMode -cne 'namespaced') {
            [Console]::Error.WriteLine(
                "[agent-containers] installation context blocks invocation: " +
                "status=$resolutionStatus reason=$resolutionReason."
            )
            exit 126
        }
        $runtimeRoot = [string]$resolution.runtimeRoot
        $context = [string]$resolution.context
        if (-not $runtimeRoot -or -not $context) {
            [Console]::Error.WriteLine(
                '[agent-containers] active installation context is incomplete.'
            )
            exit 126
        }
        $validationDurableHome = $context
        1..5 | ForEach-Object {
            $validationDurableHome = Split-Path -Parent $validationDurableHome
        }
        $validationArgs = @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $modeRunner,
            'validate',
            '-Context', $context,
            '-DurableHome', $validationDurableHome,
            '-ExpectedPluginId', 'agent-containers',
            '-ExpectedPayloadRoot', $payloadRoot
        )
        $portableValidationArgs = @(
            'validate', '--copilot-home', $copilotHome,
            '--context', $context, '--durable-home', $validationDurableHome,
            '--expected-plugin-id', 'agent-containers', '--expected-payload-root', $payloadRoot
        )
        $validationJson = @(Invoke-ContextRead -NativeArguments $validationArgs -PortableArguments $portableValidationArgs)
        if ($ContextReadCode -ne 0) {
            [Console]::Error.WriteLine(
                '[agent-containers] installation context validation failed.'
            )
            exit 126
        }
        try {
            $validatedContext = ($validationJson -join "`n") | ConvertFrom-Json
        } catch {
            [Console]::Error.WriteLine(
                '[agent-containers] installation context validation was malformed.'
            )
            exit 126
        }
        if ([string]$validatedContext.generation -cne $installGeneration -or
            [string]$validatedContext.pluginRoot -cne $runtimeRoot) {
            [Console]::Error.WriteLine(
                '[agent-containers] installation context generation does not match governance.'
            )
            exit 126
        }
    }
    else {
        [Console]::Error.WriteLine(
            "[agent-containers] installation context blocks invocation: " +
            "status=$resolutionStatus reason=$resolutionReason."
        )
        exit 126
    }

    if (-not $context) { $runtimeRoot = [string]$resolution.legacy.root }
    if (-not $runtimeRoot -or -not [IO.Path]::IsPathRooted($runtimeRoot)) {
        [Console]::Error.WriteLine('[agent-containers] installation context is missing the canonical runtime root.')
        exit 126
    }
} finally {
    if ($null -ne $bootstrap.Guard) { $bootstrap.Guard.Dispose() }
}

function Resolve-RuntimePython([string]$TraceMode = 'ready') {
    $script:AgentRtPy = $null
    $env:AGENT_RT_ROOT = $runtimeRoot
    try {
        . $runtimeResolver -TraceMode $TraceMode
    } catch {
        [Console]::Error.WriteLine("[agent-containers] runtime resolver failed; update the owning installation: $($_.Exception.Message)")
        exit 126
    }
    return $AgentRtPy
}

function Invoke-Runtime {
    param([Parameter(Mandatory)][string]$Python)
    $env:AGENT_CONTAINERS_HOME = $runtimeRoot
    $env:AGENT_CONTAINERS_STATE_DIR = $runtimeRoot
    $env:AGENT_CONTAINERS_PAYLOAD_ROOT = $payloadRoot
    if ($context) {
        $env:COPILOT_EXTENSIONS_CONTEXT = $context
    } else {
        Remove-Item Env:COPILOT_EXTENSIONS_CONTEXT -ErrorAction SilentlyContinue
    }
    $argumentMode = Get-Variable PSNativeCommandArgumentPassing -ValueOnly -ErrorAction SilentlyContinue
    $previousEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        if (-not $argumentMode -or $argumentMode -eq 'Legacy') {
            # Match Codespaces' lossless forwarding across legacy native argument passing.
            $json = ConvertTo-Json -InputObject @($forwardArgs) -Compress
            $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
            $runtimeBootstrap = "import base64,json,runpy,sys;sys.argv=['agent_containers']+json.loads(base64.b64decode(sys.argv[1]));runpy.run_module('agent_containers',run_name='__main__',alter_sys=True)"
            & $Python -c $runtimeBootstrap $encoded
        } else {
            & $Python -m agent_containers @forwardArgs
        }
    } finally {
        $ErrorActionPreference = $previousEap
    }
    exit $LASTEXITCODE
}

$runtimePython = Resolve-RuntimePython
if ($runtimePython) {
    Invoke-Runtime -Python $runtimePython
}

if ($env:AGENT_CONTAINERS_NO_SELFPROVISION) {
    [Console]::Error.WriteLine(
        '[agent-containers] runtime not provisioned (AGENT_CONTAINERS_NO_SELFPROVISION set).'
    )
    exit 1
}

function Assert-RuntimePublication {
    $publicationArgs = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $modeRunner,
        'probe-plugin-publication', '-PayloadRoot', $payloadRoot,
        '-PluginId', 'agent-containers', '-LegacyRoot', $runtimeRoot
    )
    if ($context) {
        $publicationArgs += @('-Context', $context, '-DurableHome', $validationDurableHome)
    }
    try {
        $global:LASTEXITCODE = 3
        $response = @(& $hostExe @publicationArgs)
        $code = $LASTEXITCODE
        $decision = ($response -join "`n") | ConvertFrom-Json -ErrorAction Stop
        $selected = if ($context) { [string]$decision.runtimeRoot } else { [string]$decision.legacy.root }
        if ($code -ne 0 -or $decision.deployment.allowPluginPublication -isnot [bool] -or
            -not $decision.deployment.allowPluginPublication -or $selected -cne $runtimeRoot -or
            [string]$decision.deployment.path -cne (Join-Path $runtimeRoot 'runtime-deployment.json')) {
            throw ($response -join ' ')
        }
        if ($context) {
            if ($decision.status -cnotin @('ready', 'deactivation-required') -or
                $decision.actualMode -cne 'namespaced' -or [string]$decision.context -cne $context -or
                [string]$decision.installGeneration -cne $installGeneration) {
                throw 'Decision no longer admits the validated installation context.'
            }
        } else {
            if (-not (Test-LegacyInvocationAllowed $decision)) { throw ($response -join ' ') }
            $legacyArgs = @(
                '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $modeRunner,
                'probe-legacy', '-PayloadRoot', $payloadRoot,
                '-PluginId', 'agent-containers', '-LegacyRoot', $runtimeRoot
            )
            $global:LASTEXITCODE = 3
            $response = @(& $hostExe @legacyArgs)
            $code = $LASTEXITCODE
            $legacy = ($response -join "`n") | ConvertFrom-Json -ErrorAction Stop
            if ($code -ne 0 -or $legacy.allowMutation -isnot [bool] -or -not $legacy.allowMutation -or
                [string]$legacy.legacy.root -cne $runtimeRoot -or
                -not (Test-LegacyInvocationAllowed $legacy)) { throw ($response -join ' ') }
        }
    } catch {
        [Console]::Error.WriteLine(
            "[agent-containers] Runtime publication refused: $($_.Exception.Message) Use explicit installation management to install or repair this runtime."
        )
        exit 3
    }
}
Assert-RuntimePublication
$runtimePython = Resolve-RuntimePython normal
if ($runtimePython) { Invoke-Runtime -Python $runtimePython }
[void][IO.Directory]::CreateDirectory($runtimeRoot)
$statusPath = Join-Path $runtimeRoot '.provision-status'
$_lockPath = Join-Path $runtimeRoot '.provision.lock'
$_lock = $null
while (-not $_lock) {
    try {
        $_lock = [IO.File]::Open(
            $_lockPath,
            [IO.FileMode]::OpenOrCreate,
            [IO.FileAccess]::ReadWrite,
            [IO.FileShare]::None
        )
    } catch [IO.IOException] {
        $code = $_.Exception.HResult -band 0xffff
        $posixContention = [Environment]::OSVersion.Platform -eq [PlatformID]::Unix -and $code -eq 11
        if ($code -notin @(32, 33) -and -not $posixContention) { throw }
        Start-Sleep -Milliseconds 200
    }
}

$_provisionedPy = $null
$_provisionRc = 0
try {
    $_provisionedPy = Resolve-RuntimePython
    if (-not $_provisionedPy) {
        Assert-RuntimePublication
        [Console]::Error.WriteLine(
            '[agent-containers] runtime not provisioned -- provisioning on first use (may take ~30-120s: acquires uv + builds a venv). Do not kill; extend your timeout.'
        )
        [Console]::Error.WriteLine(
            "::agent-provisioning:: plugin=agent-containers eta_seconds=120 reason=first-use status=$statusPath"
        )
        try {
            "provisioning $((Get-Date).ToUniversalTime().ToString('s'))Z" |
                Set-Content -LiteralPath $statusPath -Encoding utf8
        } catch {}

        $env:AGENT_CONTAINERS_HOME = $runtimeRoot
        $env:AGENT_CONTAINERS_STATE_DIR = $runtimeRoot
        if ($context) { $env:COPILOT_EXTENSIONS_CONTEXT = $context } else {
            Remove-Item Env:COPILOT_EXTENSIONS_CONTEXT -ErrorAction SilentlyContinue
        }
        $previousEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $global:LASTEXITCODE = 1
        try {
            & $hostExe -NoProfile -ExecutionPolicy Bypass -File $installer provision -InstallDir $runtimeRoot 2>&1 |
                ForEach-Object { [Console]::Error.WriteLine($_) }
            $_provisionRc = $LASTEXITCODE
        } finally { $ErrorActionPreference = $previousEap }
        if ($_provisionRc -eq 0) {
            $_provisionedPy = Resolve-RuntimePython
        }
    }
} finally {
    if ($_lock) { $_lock.Dispose() }
}
if ($_provisionRc -ne 0) {
    try {
        "failed rc=$_provisionRc $((Get-Date).ToUniversalTime().ToString('s'))Z" |
            Set-Content -LiteralPath $statusPath -Encoding utf8
    } catch {}
    exit $_provisionRc
}
if ($_provisionedPy) {
    try {
        "ready $((Get-Date).ToUniversalTime().ToString('s'))Z" |
            Set-Content -LiteralPath $statusPath -Encoding utf8
    } catch {}
    Invoke-Runtime -Python $_provisionedPy
}
try {
    "failed rc=1 $((Get-Date).ToUniversalTime().ToString('s'))Z" |
        Set-Content -LiteralPath $statusPath -Encoding utf8
} catch {}
[Console]::Error.WriteLine('[agent-containers] provisioning completed without a resolvable runtime.')
exit 1
