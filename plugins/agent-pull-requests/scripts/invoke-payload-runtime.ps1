# Installation driver contract: pull-requests-first-use-v1
$ErrorActionPreference = 'Stop'
$forward = [string[]]@($args)
$mode = & (Join-Path $PSScriptRoot 'payload-mode.ps1')
$env:PYTHONUTF8 = '1'
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
$OutputEncoding = [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)

function Get-AdmittedRuntimePython([string]$Interpreter) {
    try {
        $supportRoot = Join-Path $PSScriptRoot '_bootstrap_python'
        . (Join-Path $supportRoot 'bootstrap-uv.ps1')
        $windowsCapture = [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT
        if ($windowsCapture) {
            . (Join-Path $supportRoot 'bootstrap-capture.ps1')
        } else {
            . (Join-Path $supportRoot 'versioned_runtime_native.ps1')
        }
        $capture = {
            param([string]$FilePath, [string[]]$ArgList, [hashtable]$Environment)
            if ($windowsCapture) {
                return Invoke-BootstrapOwnedCapture -FilePath $FilePath -ArgList $ArgList `
                    -Environment $Environment -TimeoutMilliseconds 10000 -CleanupMilliseconds 500
            }
            $diagnostic = ''
            $output = Invoke-Hidden -FilePath $FilePath -ArgList $ArgList -Environment $Environment `
                -InputText '' -ErrorOutput ([ref]$diagnostic) `
                -TimeoutMilliseconds 10000 -CleanupMilliseconds 500
            [pscustomobject]@{
                ExitCode = $LASTEXITCODE; Stdout = [string]$output; Stderr = $diagnostic
            }
        }
        $selected = Resolve-UvRuntimePython -ExpectedPython $Interpreter -InvokeCaptured $capture
        if ($selected.Status -cne 'selected') {
            throw 'Prepared UV prerequisites are unavailable; use explicit installation management.'
        }
        return $selected.Python
    } catch {
        [Console]::Error.WriteLine("[agent-pull-requests] application runtime admission failed: $($_.Exception.Message)")
        exit 126
    }
}

function Invoke-PythonEntry {
    param([string]$Python, [string]$Target, [switch]$ScriptEntry, [string[]]$Arguments)
    $argumentMode = Get-Variable PSNativeCommandArgumentPassing -ValueOnly -ErrorAction SilentlyContinue
    $ErrorActionPreference = 'Continue'
    if (-not $argumentMode -or $argumentMode -eq 'Legacy') {
        $argumentFile = $null
        try {
            $ErrorActionPreference = 'Stop'
            . (Join-Path $PSScriptRoot 'argument-transport.ps1')
            $json = ConvertTo-Json -InputObject @($Arguments) -Compress
            $argumentFile = New-PullRequestsArgumentFile $json
            $encodedPath = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($argumentFile))
            $encodedTarget = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Target))
            $entry = @'
import base64,json,os,runpy,sys
target,path=(base64.b64decode(value).decode('utf-8') for value in sys.argv[1:3])
try:
    with open(path,encoding='utf-8') as stream:
        arguments=json.load(stream)
    if not isinstance(arguments,list) or not all(isinstance(value,str) for value in arguments):
        raise ValueError('Argument transport requires an array of strings')
    os.unlink(path)
    os.rmdir(os.path.dirname(path))
except (OSError,ValueError) as error:
    print('Argument transport refused: '+str(error),file=sys.stderr)
    raise SystemExit(126)
if sys.argv[3]=='script':
    sys.argv=[target,'--']+arguments
    runpy.run_path(target,run_name='__main__')
else:
    sys.argv=[target]+arguments
    runpy.run_module(target,run_name='__main__',alter_sys=True)
'@
            $ErrorActionPreference = 'Continue'
            if ($ScriptEntry) {
                & $Python -I -c $entry $encodedTarget $encodedPath script
            } else {
                & $Python -c $entry $encodedTarget $encodedPath module
            }
            $invocationExit = $LASTEXITCODE
        } catch {
            [Console]::Error.WriteLine("[agent-pull-requests] argument transport refused: $($_.Exception.Message)")
            $invocationExit = 126
        } finally {
            if ($argumentFile) { Remove-PullRequestsArgumentFile $argumentFile }
        }
        exit $invocationExit
    } elseif ($ScriptEntry) {
        & $Python -I $Target -- @Arguments
    } else {
        & $Python -m $Target @Arguments
    }
    exit $LASTEXITCODE
}

if ($mode -ceq 'legacy') {
    $env:AGENT_RT_ROOT = Join-Path $env:USERPROFILE '.agent-pull-requests'
    $resolver = Join-Path $PSScriptRoot 'resolve-runtime.ps1'
    if (-not (Test-Path -LiteralPath $resolver -PathType Leaf) -or
        -not (Select-String -LiteralPath $resolver -SimpleMatch '# Trace contract: normal/read-only/ready-v1' -Quiet)) {
        throw 'Read-only runtime resolver is unavailable; update or repair the owning payload.'
    }
    . $resolver -TraceMode ready
    if (-not $AgentRtPy) {
        if (Test-Path 'env:AGENT_PULL_REQUESTS_NO_SELFPROVISION') {
            throw 'Runtime not provisioned (AGENT_PULL_REQUESTS_NO_SELFPROVISION set)'
        }
        if (Test-Path 'env:AGENT_PULL_REQUESTS_PINNED_CONTEXT') {
            throw 'Pinned invocation requires a prepared owning runtime; provision independently.'
        }
        $payload = Split-Path -Parent $PSScriptRoot
        $firstUse = Join-Path $payload 'libs\update-driver\scripts\first-use.ps1'
        if (-not (Test-Path -LiteralPath $firstUse -PathType Leaf)) {
            throw 'Packaged installation driver is unavailable; update or repair the owning payload.'
        }
        $location = Get-Location
        if ($location.Provider.Name -ne 'FileSystem') {
            throw 'Invocation requires a filesystem project location.'
        }
        $nativeShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $executionRoot = Join-Path $env:USERPROFILE '.copilot-extensions\update-driver'
        $ErrorActionPreference = 'Continue'
        & $nativeShell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $firstUse `
            -Root $executionRoot -Payload $payload -RuntimeRoot $env:AGENT_RT_ROOT `
            -Project $location.ProviderPath -Wait
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        $ErrorActionPreference = 'Stop'
        . $resolver -TraceMode ready
    }
    if (-not $AgentRtPy) { throw 'Owning installer returned without a resolvable runtime' }
    $AgentRtPy = Get-AdmittedRuntimePython $AgentRtPy
    Invoke-PythonEntry -Python $AgentRtPy -Target 'agent_pull_requests' -Arguments $forward
}
if ($mode -cne 'cell') { throw 'Native installation admission returned an unknown mode' }
try {
    $python = & (Join-Path $PSScriptRoot '_bootstrap_python\select.ps1') `
        -Operation Inspect -TimeoutMilliseconds 10000
    if (-not $python) { throw 'Prepared command bootstrap Python is unavailable.' }
} catch {
    [Console]::Error.WriteLine("[agent-pull-requests] command bootstrap Python admission failed: $($_.Exception.Message)")
    exit 126
}
Invoke-PythonEntry -Python $python -Target (Join-Path $PSScriptRoot 'cell_invoke.py') `
    -ScriptEntry -Arguments $forward
