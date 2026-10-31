# Installation driver contract: pull-requests-first-use-v1
$ErrorActionPreference = 'Stop'
$forward = @($args)
$mode = & (Join-Path $PSScriptRoot 'payload-mode.ps1')
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
    $ErrorActionPreference = 'Continue'
    & $AgentRtPy -m agent_pull_requests @forward
    exit $LASTEXITCODE
}
if ($mode -cne 'cell') { throw 'Native installation admission returned an unknown mode' }
$python = $null
foreach ($candidate in @('python3', 'python')) {
    $command = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($command -and $command.Source -notmatch 'WindowsApps') {
        & $command.Source -I -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>$null
        if ($LASTEXITCODE -eq 0) { $python = $command.Source; break }
    }
}
if (-not $python) { throw 'Governed invocation requires an existing Python 3.10+ interpreter' }
$ErrorActionPreference = 'Continue'
& $python -I (Join-Path $PSScriptRoot 'cell_invoke.py') -- @forward
exit $LASTEXITCODE
