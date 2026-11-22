$ErrorActionPreference = 'Stop'

function Write-Effect {
    param([string]$Operation, $Detail)
    @{ operation = $Operation; detail = $Detail } |
        ConvertTo-Json -Depth 12 -Compress |
        Add-Content -LiteralPath (Join-Path $PSScriptRoot 'effects.jsonl') -Encoding UTF8
}

function New-ScheduledTaskAction {
    [CmdletBinding()]
    param([string]$Execute, [string]$Argument, [string]$WorkingDirectory)
    [pscustomobject]@{ Execute = $Execute; Arguments = $Argument; WorkingDirectory = $WorkingDirectory }
}

function New-ScheduledTaskTrigger {
    [CmdletBinding()]
    param([switch]$AtStartup, [switch]$AtLogOn, [string]$User)
    [pscustomobject]@{ AtStartup = [bool]$AtStartup; AtLogOn = [bool]$AtLogOn; User = $User; Delay = '' }
}

function New-ScheduledTaskSettingsSet {
    [CmdletBinding()]
    param(
        [switch]$AllowStartIfOnBatteries, [switch]$DontStopIfGoingOnBatteries,
        [switch]$StartWhenAvailable, [timespan]$ExecutionTimeLimit, [string]$MultipleInstances
    )
    [pscustomobject]@{ LimitSeconds = $ExecutionTimeLimit.TotalSeconds; MultipleInstances = $MultipleInstances }
}

function New-ScheduledTaskPrincipal {
    [CmdletBinding()]
    param([string]$UserId, [string]$LogonType, [string]$RunLevel)
    [pscustomobject]@{ UserId = $UserId; LogonType = $LogonType; RunLevel = $RunLevel }
}

function Get-ScheduledTask {
    [CmdletBinding()]
    param([string]$TaskName)
    Write-Effect 'get' $TaskName
    $modePath = Join-Path $PSScriptRoot 'mode'
    $mode = if (Test-Path -LiteralPath $modePath) { Get-Content -LiteralPath $modePath -Raw } else { '' }
    $tampered = Join-Path $PSScriptRoot 'source-tampered'
    if ($mode -in @('repair-source-replaced', 'repair-source-removed') -and
        -not (Test-Path -LiteralPath $tampered)) {
        $original = Join-Path $env:COPILOT_PLUGIN_STAGED_FROM 'scripts\repair-scheduled-task.ps1'
        if ($mode -eq 'repair-source-replaced') {
            [IO.File]::WriteAllText($original, "throw 'mutable original repair executed'")
        } else {
            Remove-Item -LiteralPath $original
        }
        [IO.File]::WriteAllText($tampered, $mode)
    }
    $path = Join-Path $PSScriptRoot 'task.json'
    if (Test-Path -LiteralPath $path) { Get-Content -LiteralPath $path -Raw | ConvertFrom-Json }
}

function Get-ScheduledTaskInfo {
    [CmdletBinding()]
    param([string]$TaskName)
    $modePath = Join-Path $PSScriptRoot 'mode'
    $mode = if (Test-Path -LiteralPath $modePath) { Get-Content -LiteralPath $modePath -Raw } else { '' }
    [pscustomobject]@{ LastTaskResult = $(if ($mode -like 'repair-source-*') { 267011 } else { 0 }) }
}

function Write-Task {
    param([string]$Operation, [string]$TaskName, $Action, $Trigger, $Settings, $Principal)
    Write-Effect $Operation $TaskName
    $modePath = Join-Path $PSScriptRoot 'mode'
    if ((Test-Path -LiteralPath $modePath) -and (Get-Content -LiteralPath $modePath -Raw) -eq 'registration-denied') {
        throw [UnauthorizedAccessException]::new('fixture registration denied')
    }
    if (-not $Principal) { $Principal = [pscustomobject]@{ LogonType = 'Interactive' } }
    @{ TaskName = $TaskName; Actions = @($Action); Trigger = $Trigger; Settings = $Settings; Principal = $Principal } |
        ConvertTo-Json -Depth 12 |
        Set-Content -LiteralPath (Join-Path $PSScriptRoot 'task.json') -Encoding UTF8
}

function Register-ScheduledTask {
    [CmdletBinding()]
    param([string]$TaskName, $Action, $Trigger, $Settings, $Principal, [string]$Description, [switch]$Force)
    Write-Task 'register' $TaskName $Action $Trigger $Settings $Principal
}

function Set-ScheduledTask {
    [CmdletBinding()]
    param([string]$TaskName, $Action, $Trigger, $Settings, $Principal)
    Write-Task 'set' $TaskName $Action $Trigger $Settings $Principal
}

function Start-ScheduledTask {
    [CmdletBinding()]
    param([string]$TaskName)
    Write-Effect 'start' $TaskName
    $modePath = Join-Path $PSScriptRoot 'mode'
    if ((Test-Path -LiteralPath $modePath) -and (Get-Content -LiteralPath $modePath -Raw) -eq 'start-fatal') {
        throw 'fixture start failure'
    }
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot 'started'), 'synthetic service; no daemon launched')
}

function Invoke-RestMethod {
    [CmdletBinding()]
    param([string]$Uri, [int]$TimeoutSec)
    if ($Uri -ne 'http://127.0.0.1:9280/health') { throw 'fixture refuses unexpected network probe' }
    Write-Effect 'synthetic-health' $Uri
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'started'))) { throw 'synthetic service not started' }
    [pscustomobject]@{ service = 'agent-bridge'; fixture = $true }
}

function Get-NetTCPConnection {
    [CmdletBinding()]
    param([int]$LocalPort)
    Write-Effect 'synthetic-listener' $LocalPort
}

function Unregister-ScheduledTask { throw 'fixture refuses task removal' }
function Stop-ScheduledTask { throw 'fixture refuses stopping a service' }

Export-ModuleMember -Function New-ScheduledTaskAction,New-ScheduledTaskTrigger,New-ScheduledTaskSettingsSet,New-ScheduledTaskPrincipal,Get-ScheduledTask,Get-ScheduledTaskInfo,Register-ScheduledTask,Set-ScheduledTask,Start-ScheduledTask,Invoke-RestMethod,Get-NetTCPConnection,Unregister-ScheduledTask,Stop-ScheduledTask
