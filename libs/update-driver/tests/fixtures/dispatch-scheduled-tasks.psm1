$ErrorActionPreference = 'Stop'

function Write-Effect {
    param([string]$Operation, $Detail)
    @{ operation = $Operation; detail = $Detail } | ConvertTo-Json -Depth 12 -Compress |
        Add-Content -LiteralPath (Join-Path $PSScriptRoot 'effects.jsonl') -Encoding UTF8
}

function Task-Path {
    param([string]$Name)
    if ($Name -notin @('agent-dispatch', 'agent-dispatch-supervisor')) {
        throw "fixture refuses foreign task: $Name"
    }
    Join-Path $PSScriptRoot "$Name.json"
}

function New-ScheduledTaskAction {
    [CmdletBinding()]
    param([string]$Execute, [string]$Argument, [string]$WorkingDirectory)
    if ($WorkingDirectory -ne (Join-Path $env:USERPROFILE '.agent-dispatch') -or $Execute -ne 'conhost.exe') {
        throw 'fixture refuses foreign task action'
    }
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
        [switch]$StartWhenAvailable, [timespan]$ExecutionTimeLimit, [string]$MultipleInstances,
        [int]$RestartCount, [timespan]$RestartInterval
    )
    [pscustomobject]@{
        LimitSeconds = $ExecutionTimeLimit.TotalSeconds; MultipleInstances = $MultipleInstances
        RestartCount = $RestartCount; RestartSeconds = $RestartInterval.TotalSeconds
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
    if ($TaskName -eq 'agent-dispatch-supervisor-*') { return }
    $path = Task-Path $TaskName
    if (Test-Path -LiteralPath $path) { Get-Content -LiteralPath $path -Raw | ConvertFrom-Json }
}

function Register-ScheduledTask {
    [CmdletBinding()]
    param([string]$TaskName, $Action, $Trigger, $Settings, $Principal, [string]$Description, [switch]$Force)
    $path = Task-Path $TaskName
    Write-Effect 'register' $TaskName
    @{
        TaskName = $TaskName; Actions = @($Action); Triggers = @($Trigger)
        Settings = $Settings; Principal = $Principal; State = 'Ready'
    } | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $path -Encoding UTF8
}

function Set-TaskState {
    param([string]$Name, [string]$State, [string]$Operation)
    $path = Task-Path $Name
    $mode = Join-Path $PSScriptRoot 'mode'
    if ($Name -eq 'agent-dispatch-supervisor' -and $Operation -eq 'disable' -and
        (Test-Path -LiteralPath $mode) -and
        (Get-Content -LiteralPath $mode -Raw) -eq 'supervisor-disable-denied') {
        Write-Effect 'supervisor-disable-denied' $Name
        throw [UnauthorizedAccessException]::new('fixture supervisor disable denied')
    }
    if (-not (Test-Path -LiteralPath $path)) { throw "fixture task is absent: $Name" }
    $task = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    $task.State = $State
    $task | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $path -Encoding UTF8
    Write-Effect $Operation $Name
}

function Start-ScheduledTask {
    [CmdletBinding()]
    param([string]$TaskName)
    Set-TaskState $TaskName 'Running' 'start'
}

function Stop-ScheduledTask {
    [CmdletBinding()]
    param([string]$TaskName)
    Set-TaskState $TaskName 'Ready' 'stop'
}

function Enable-ScheduledTask {
    [CmdletBinding()]
    param([string]$TaskName)
    Set-TaskState $TaskName 'Ready' 'enable'
}

function Disable-ScheduledTask {
    [CmdletBinding()]
    param([string]$TaskName)
    Set-TaskState $TaskName 'Disabled' 'disable'
}

function Get-NetFirewallRule {
    [CmdletBinding()]
    param([string]$DisplayName)
    if ($DisplayName -ne 'agent-dispatch coordinator (WSL)') { throw 'fixture refuses foreign firewall lookup' }
    Write-Effect 'firewall-lookup' $DisplayName
}

function Remove-NetFirewallRule { throw 'fixture refuses firewall mutation' }

function Get-ItemProperty {
    [CmdletBinding()]
    param([string]$Path, [string]$Name)
    if ($Path -ne 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -or
        $Name -notin @('agent-dispatch', 'agent-dispatch-supervisor')) {
        throw 'fixture refuses foreign registry lookup'
    }
    Write-Effect 'registry-lookup' $Name
}

function New-ItemProperty { throw 'fixture refuses registry mutation or coordinator fallback' }
function Remove-ItemProperty { throw 'fixture refuses registry mutation' }
function Unregister-ScheduledTask { throw 'fixture refuses task removal' }

Export-ModuleMember -Function New-ScheduledTaskAction,New-ScheduledTaskTrigger,New-ScheduledTaskSettingsSet,New-ScheduledTaskPrincipal,Get-ScheduledTask,Register-ScheduledTask,Start-ScheduledTask,Stop-ScheduledTask,Enable-ScheduledTask,Disable-ScheduledTask,Get-NetFirewallRule,Remove-NetFirewallRule,Get-ItemProperty,New-ItemProperty,Remove-ItemProperty,Unregister-ScheduledTask
