#Requires -Version 7.0
<#
.SYNOPSIS
    Stable launcher for the dispatch-supervised dtssh host companion.

.DESCRIPTION
    agent-dispatch runs this file from the activated Copilot plugin payload,
    because its registrar only accepts an identity-verified plugin root. The
    lifecycle logic itself ships inside the agent_ssh package
    (agent_ssh/dtssh_companion.ps1), so the installed runtime slot selected by
    `current-version` decides the behavior. An `install.ps1 update` therefore
    takes effect without refreshing the Copilot payload copy. When no
    installed runtime carries the logic, this launcher uses the payload's own
    package source.
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'health')]
    [string]$Action
)

$ErrorActionPreference = 'Stop'
$LogicName = 'dtssh_companion.ps1'

function Get-RuntimeCompanionLogic {
    $runtimeRoot = if ($env:AGENT_SSH_HOME) {
        $env:AGENT_SSH_HOME
    } elseif ($env:USERPROFILE) {
        Join-Path $env:USERPROFILE '.agent-ssh'
    } elseif ($env:HOME) {
        Join-Path $env:HOME '.agent-ssh'
    } else {
        return $null
    }
    $resolver = Join-Path $PSScriptRoot 'resolve-runtime.ps1'
    if (-not (Test-Path -LiteralPath $runtimeRoot -PathType Container) -or
        -not (Test-Path -LiteralPath $resolver -PathType Leaf)) {
        return $null
    }
    $previousRoot = $env:AGENT_RT_ROOT
    try {
        $env:AGENT_RT_ROOT = $runtimeRoot
        $AgentRtPy = $null
        . $resolver -TraceMode read-only
        if (-not $AgentRtPy) { return $null }
        $environment = Split-Path (Split-Path $AgentRtPy -Parent) -Parent
        $candidates = @(Join-Path $environment "Lib\site-packages\agent_ssh\$LogicName")
        $posixLib = Join-Path $environment 'lib'
        if (Test-Path -LiteralPath $posixLib -PathType Container) {
            $candidates += @(
                Get-ChildItem -LiteralPath $posixLib -Directory -Filter 'python*' |
                    ForEach-Object { Join-Path $_.FullName "site-packages/agent_ssh/$LogicName" }
            )
        }
        foreach ($candidate in $candidates) {
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
        }
        return $null
    } catch {
        return $null
    } finally {
        $env:AGENT_RT_ROOT = $previousRoot
    }
}

$logic = Get-RuntimeCompanionLogic
if (-not $logic) {
    $logic = Join-Path (Split-Path $PSScriptRoot -Parent) "src\agent_ssh\$LogicName"
}
if (-not (Test-Path -LiteralPath $logic -PathType Leaf)) {
    [Console]::Error.WriteLine("dtssh companion logic is missing: $logic")
    exit 126
}
# Run in-process so a supervisor that stops this PID stops the logic as well;
# the logic always ends with an explicit exit code.
$global:LASTEXITCODE = 0
& $logic $Action
exit $LASTEXITCODE
