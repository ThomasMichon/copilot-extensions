$ErrorActionPreference = 'Stop'

function Write-Effect {
    param([string]$Operation, $Detail)
    @{ operation = $Operation; detail = $Detail } |
        ConvertTo-Json -Depth 12 -Compress |
        Add-Content -LiteralPath (Join-Path $PSScriptRoot 'effects.jsonl') -Encoding UTF8
}

function New-ScheduledTaskAction {
    [CmdletBinding()]
    param([string]$Execute, [string]$Argument)
    [pscustomobject]@{ Execute = $Execute; Arguments = $Argument }
}

function New-ScheduledTaskTrigger {
    [CmdletBinding()]
    param([switch]$Once, [datetime]$At, [timespan]$RepetitionInterval)
    [pscustomobject]@{
        Once = [bool]$Once
        At = $At.ToString('o')
        IntervalSeconds = $RepetitionInterval.TotalSeconds
        Repetition = [pscustomobject]@{ StopAtDurationEnd = $true }
    }
}

function New-ScheduledTaskSettingsSet {
    [CmdletBinding()]
    param(
        [switch]$AllowStartIfOnBatteries,
        [switch]$DontStopIfGoingOnBatteries,
        [switch]$StartWhenAvailable,
        [timespan]$ExecutionTimeLimit,
        [string]$MultipleInstances
    )
    [pscustomobject]@{
        AllowStartIfOnBatteries = [bool]$AllowStartIfOnBatteries
        DontStopIfGoingOnBatteries = [bool]$DontStopIfGoingOnBatteries
        StartWhenAvailable = [bool]$StartWhenAvailable
        LimitSeconds = $ExecutionTimeLimit.TotalSeconds
        MultipleInstances = $MultipleInstances
    }
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
    $path = Join-Path $PSScriptRoot 'task.json'
    if (Test-Path -LiteralPath $path) {
        Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    }
}

function Write-Task {
    param([string]$Operation, [string]$TaskName, $Action, $Trigger, $Settings, $Principal)
    $task = @{
        TaskName = $TaskName
        Actions = @($Action)
        Trigger = $Trigger
        Settings = $Settings
        Principal = $Principal
    }
    Write-Effect $Operation $task
    $modePath = Join-Path $PSScriptRoot 'mode'
    $mode = if (Test-Path -LiteralPath $modePath) { Get-Content -LiteralPath $modePath -Raw } else { '' }
    if ($mode -eq 'fatal') { throw 'fixture fatal registration failure' }
    if ($mode -eq 'denied') { throw [UnauthorizedAccessException]::new('fixture access denied') }
    $task | ConvertTo-Json -Depth 12 |
        Set-Content -LiteralPath (Join-Path $PSScriptRoot 'task.json') -Encoding UTF8
}

function Register-ScheduledTask {
    [CmdletBinding()]
    param([string]$TaskName, $Action, $Trigger, $Settings, $Principal, [string]$Description)
    Write-Task 'register' $TaskName $Action $Trigger $Settings $Principal
}

function Set-ScheduledTask {
    [CmdletBinding()]
    param([string]$TaskName, $Action, $Trigger, $Settings, $Principal)
    Write-Task 'set' $TaskName $Action $Trigger $Settings $Principal
}

function Stop-ScheduledTask {
    [CmdletBinding()]
    param([string]$TaskName)
    Write-Effect 'stop' $TaskName
}

function Start-ScheduledTask { throw 'fixture refuses personal sync/prune execution' }
function Unregister-ScheduledTask { throw 'fixture refuses task removal' }

Export-ModuleMember -Function New-ScheduledTaskAction,New-ScheduledTaskTrigger,New-ScheduledTaskSettingsSet,New-ScheduledTaskPrincipal,Get-ScheduledTask,Register-ScheduledTask,Set-ScheduledTask,Stop-ScheduledTask,Start-ScheduledTask,Unregister-ScheduledTask
