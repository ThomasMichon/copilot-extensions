$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
$OutputEncoding = [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)

$forwardArgs = [string[]]@($args)
$payloadRoot = [Environment]::GetEnvironmentVariable('AGENT_CODESPACES_PAYLOAD_ROOT', 'Process')
if (-not $payloadRoot -or -not (Test-Path -LiteralPath $payloadRoot -PathType Container)) {
    [Console]::Error.WriteLine('[agent-codespaces] owning payload root is unavailable.')
    exit 126
}
$payloadRoot = (Resolve-Path -LiteralPath $payloadRoot).Path
$scriptDir = Join-Path $payloadRoot 'scripts'
$modeRunner = Join-Path $scriptDir 'installation-context\installation-context.ps1'
$portableModeRunner = Join-Path $scriptDir 'installation-context\installation_context.py'
$bootstrapHelper = Join-Path $scriptDir 'bootstrap-python.ps1'
$runtimeResolver = Join-Path $scriptDir 'resolve-runtime.ps1'
$installer = Join-Path $scriptDir 'install.ps1'
$legacyRoot = if ($env:AGENT_CODESPACES_HOME) {
    $env:AGENT_CODESPACES_HOME
} else {
    Join-Path $env:USERPROFILE '.agent-codespaces' # marketplace-isolation: allow legacy compatibility root
}
if (
    -not (Test-Path -LiteralPath $modeRunner -PathType Leaf) -or
    -not (Test-Path -LiteralPath $runtimeResolver -PathType Leaf) -or
    -not (Test-Path -LiteralPath $installer -PathType Leaf)
) {
    [Console]::Error.WriteLine(
        '[agent-codespaces] installation-context runtime support is unavailable.'
    )
    exit 126
}
if (-not (Select-String -LiteralPath $runtimeResolver -SimpleMatch '# Trace contract: normal/read-only/ready-v1' -Quiet)) {
    [Console]::Error.WriteLine('[agent-codespaces] read-only runtime resolver is unavailable. Re-enable/update the plugin, then retry.')
    exit 126
}

$hostExe = (Get-Process -Id $PID).Path
if (-not $hostExe) {
    [Console]::Error.WriteLine('[agent-codespaces] PowerShell host executable is unavailable.')
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
    [Console]::Error.WriteLine('[agent-codespaces] payload Python evaluator is unavailable; update the owning plugin.')
    exit 126
}
. $bootstrapHelper
$bootstrap = Resolve-EvaluatorBootstrapPython
if ($bootstrap.Status -ceq 'unavailable') {
    [Console]::Error.WriteLine(
        '[agent-codespaces] Python 3.10+ must be available as python on PATH. ' +
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
        '-PluginId', 'agent-codespaces',
        '-LegacyRoot', $legacyRoot
    )
    $portableStatusArgs = @(
        'status', '--copilot-home', $copilotHome,
        '--payload-root', $payloadRoot, '--plugin-id', 'agent-codespaces',
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
            '[agent-codespaces] installation context could not be resolved.'
        )
        exit 126
    }
    try {
        $resolution = ($resolutionJson -join "`n") | ConvertFrom-Json
    } catch {
        [Console]::Error.WriteLine(
            '[agent-codespaces] installation context returned malformed status.'
        )
        exit 126
    }

    $resolutionStatus = [string]$resolution.status
    $resolutionReason = [string]$resolution.reason
    $actualMode = [string]$resolution.actualMode
    $desiredMode = [string]$resolution.desiredMode
    $installGeneration = [string]$resolution.installGeneration

    if (
        $resolutionStatus -ceq 'ready' -and
        $actualMode -ceq 'legacy' -and
        $desiredMode -ceq 'legacy'
    ) {
        if ($env:COPILOT_EXTENSIONS_CONTEXT) {
            [Console]::Error.WriteLine(
                '[agent-codespaces] requested installation context is not active.'
            )
            exit 126
        }
    }
    elseif (
        -not $env:COPILOT_EXTENSIONS_CONTEXT -and
        $resolutionStatus -ceq 'provenance-blocked' -and
        $resolution.policy.enabled -is [bool] -and
        -not $resolution.policy.enabled -and
        $null -eq $resolution.legacy.tombstone -and
        [string]$resolution.legacy.disposition -ceq 'active'
    ) {
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
                "[agent-codespaces] installation context blocks invocation: " +
                "status=$resolutionStatus reason=$resolutionReason."
            )
            exit 126
        }
        $runtimeRoot = [string]$resolution.runtimeRoot
        $context = [string]$resolution.context
        if (-not $runtimeRoot -or -not $context) {
            [Console]::Error.WriteLine(
                '[agent-codespaces] active installation context is incomplete.'
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
            '-ExpectedPluginId', 'agent-codespaces',
            '-ExpectedPayloadRoot', $payloadRoot
        )
        $portableValidationArgs = @(
            'validate', '--copilot-home', $copilotHome,
            '--context', $context, '--durable-home', $validationDurableHome,
            '--expected-plugin-id', 'agent-codespaces', '--expected-payload-root', $payloadRoot
        )
        $validationJson = @(Invoke-ContextRead -NativeArguments $validationArgs -PortableArguments $portableValidationArgs)
        if ($ContextReadCode -ne 0) {
            [Console]::Error.WriteLine(
                '[agent-codespaces] installation context validation failed.'
            )
            exit 126
        }
        try {
            $validatedContext = ($validationJson -join "`n") | ConvertFrom-Json
        } catch {
            [Console]::Error.WriteLine(
                '[agent-codespaces] installation context validation was malformed.'
            )
            exit 126
        }
        if ([string]$validatedContext.generation -cne $installGeneration) {
            [Console]::Error.WriteLine(
                '[agent-codespaces] installation context generation does not match governance.'
            )
            exit 126
        }
    }
    else {
        [Console]::Error.WriteLine(
            "[agent-codespaces] installation context blocks invocation: " +
            "status=$resolutionStatus reason=$resolutionReason."
        )
        exit 126
    }

    if (-not $context) {
        $runtimeRoot = [string]$resolution.legacy.root
        if (-not $runtimeRoot -or -not [IO.Path]::IsPathRooted($runtimeRoot)) {
            [Console]::Error.WriteLine('[agent-codespaces] installation context is missing the canonical legacy root.')
            exit 126
        }
    }
} finally {
    if ($null -ne $bootstrap.Guard) { $bootstrap.Guard.Dispose() }
}

function Resolve-RuntimePython {
    param([string]$TraceMode = 'ready')
    $script:AgentRtPy = $null
    $env:AGENT_RT_ROOT = $runtimeRoot
    . $runtimeResolver -TraceMode $TraceMode
    return $AgentRtPy
}

function Set-RuntimeEnvironment {
    $env:AGENT_CODESPACES_HOME = $runtimeRoot
    $env:AGENT_CODESPACES_PAYLOAD_ROOT = $payloadRoot
    if ($context) {
        $env:COPILOT_EXTENSIONS_CONTEXT = $context
    } else {
        Remove-Item Env:COPILOT_EXTENSIONS_CONTEXT -ErrorAction SilentlyContinue
    }
}

function Invoke-Runtime {
    param([Parameter(Mandatory)][string]$Python)
    Set-RuntimeEnvironment
    # Relax EAP for just this forwarding call: the wrapped module's own
    # exit code is the only contract this dispatcher relays, so a
    # legitimate stderr diagnostic from a non-zero exit must never become
    # a terminating exception here (copilot-extensions#5494).
    $previousEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        if ($PSVersionTable.PSVersion -lt [version]'7.3' -or
            (Get-Variable PSNativeCommandArgumentPassing -ValueOnly -ErrorAction SilentlyContinue) -eq 'Legacy') {
            # Legacy native argument passing retokenizes embedded quotes.
            $json = ConvertTo-Json -InputObject @($forwardArgs) -Compress
            $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
            $bootstrap = "import base64,json,runpy,sys;sys.argv=['agent_codespaces']+json.loads(base64.b64decode(sys.argv[1]));runpy.run_module('agent_codespaces',run_name='__main__',alter_sys=True)"
            & $Python -c $bootstrap $encoded
        } else {
            & $Python -m agent_codespaces @forwardArgs
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

if ($env:AGENT_CODESPACES_NO_SELFPROVISION) {
    [Console]::Error.WriteLine(
        '[agent-codespaces] runtime not provisioned (AGENT_CODESPACES_NO_SELFPROVISION set).'
    )
    exit 1
}

$publicationArgs = @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $modeRunner,
    'probe-plugin-publication', '-PayloadRoot', $payloadRoot,
    '-PluginId', 'agent-codespaces', '-LegacyRoot', $runtimeRoot
)
if ($context) {
    $publicationArgs += @('-Context', $context, '-DurableHome', $validationDurableHome)
}
$publicationJson = @(& $hostExe @publicationArgs)
$publicationCode = $LASTEXITCODE
try {
    $publication = ($publicationJson -join "`n") | ConvertFrom-Json -ErrorAction Stop
    $publicationRoot = if ($context) {
        [string]$publication.runtimeRoot
    } else { [string]$publication.legacy.root }
    if ($publicationCode -ne 0 -or
        $publication.deployment.allowPluginPublication -isnot [bool] -or
        -not $publication.deployment.allowPluginPublication -or
        $publicationRoot -cne $runtimeRoot -or
        [string]$publication.deployment.path -cne
            (Join-Path $runtimeRoot 'runtime-deployment.json')) {
        throw ($publicationJson -join ' ')
    }
} catch {
    [Console]::Error.WriteLine(
        "[agent-codespaces] Runtime publication refused: $($_.Exception.Message) " +
        'Use explicit installation management to install or repair this runtime.'
    )
    exit 3
}
$runtimePython = Resolve-RuntimePython -TraceMode normal
if ($runtimePython) {
    Invoke-Runtime -Python $runtimePython
}
New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
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
    } catch {
        Start-Sleep -Milliseconds 200
    }
}

$_provisionedPy = $null
$_provisionRc = 0
try {
    $_provisionedPy = Resolve-RuntimePython
    if (-not $_provisionedPy) {
        [Console]::Error.WriteLine(
            '[agent-codespaces] runtime not provisioned -- provisioning on first use (may take ~30-120s: acquires uv + builds a venv). Do not kill; extend your timeout.'
        )
        [Console]::Error.WriteLine(
            "::agent-provisioning:: plugin=agent-codespaces eta_seconds=120 reason=first-use status=$statusPath"
        )
        try {
            "provisioning $((Get-Date).ToUniversalTime().ToString('s'))Z" |
                Set-Content -LiteralPath $statusPath -Encoding utf8
        } catch {}

        Set-RuntimeEnvironment
        $previousEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            & $hostExe -NoProfile -ExecutionPolicy Bypass -File $installer provision -InstallDir $runtimeRoot 2>&1 |
                ForEach-Object { [Console]::Error.WriteLine($_) }
        } finally {
            $ErrorActionPreference = $previousEap
        }
        $_provisionRc = $LASTEXITCODE
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
[Console]::Error.WriteLine('[agent-codespaces] provisioning completed without a resolvable runtime.')
exit 1
