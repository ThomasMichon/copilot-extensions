function Assert-DispatchOrdinaryPath {
    param([Parameter(Mandatory)][string]$Path)
    $current = [IO.Path]::GetFullPath($Path)
    while ($current) {
        try {
            $attributes = [IO.File]::GetAttributes($current)
            if ($attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Dispatch installation policy paths must not be linked'
            }
        } catch [IO.FileNotFoundException] {
        } catch [IO.DirectoryNotFoundException] {
        }
        $current = [IO.Path]::GetDirectoryName($current)
    }
}

function Read-DispatchInstallationPolicy {
    param([Parameter(Mandatory)][string]$Root, [string]$Config = '')
    $result = @{ windows_scheduled_task = 'preserve'; start_coordinator = $true; start_supervisor = $true }
    $explicitConfig = [bool]$Config
    if (-not $Config) { $Config = Join-Path $Root 'installation.json' }
    Assert-DispatchOrdinaryPath $Root
    Assert-DispatchOrdinaryPath $Config
    try { $item = Get-Item -LiteralPath $Config -Force -ErrorAction Stop }
    catch [System.Management.Automation.ItemNotFoundException] {
        if ($explicitConfig) { throw 'Explicit Dispatch installation policy is missing' }
        return $result
    }
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.Length -gt 65536) {
        throw 'Dispatch installation policy must be a bounded ordinary file'
    }
    $document = [IO.File]::ReadAllText($item.FullName) | ConvertFrom-Json -ErrorAction Stop
    if (@($document.PSObject.Properties.Name | Where-Object { $_ -notin @('version', 'installation') }).Count -or
        $document.version -ne 1 -or
        ($document.version -isnot [int] -and $document.version -isnot [long])) {
        throw 'Unsupported Dispatch installation policy'
    }
    $installation = $document.installation
    if (@($installation.PSObject.Properties.Name | Where-Object { $_ -notin @('root', 'local') }).Count -or
        -not [IO.Path]::IsPathRooted($installation.root) -or
        -not [IO.Path]::GetFullPath($installation.root).TrimEnd('\').Equals(
            [IO.Path]::GetFullPath($Root).TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Dispatch installation policy belongs to another installation'
    }
    foreach ($property in $installation.local.PSObject.Properties) {
        if (-not $result.ContainsKey($property.Name)) { throw "Unknown Dispatch local policy: $($property.Name)" }
        if ($property.Name -eq 'windows_scheduled_task') {
            if ($property.Value -isnot [string] -or $property.Value -cnotin @('preserve', 'enabled', 'absent')) {
                throw 'windows_scheduled_task must be preserve, enabled, or absent'
            }
        } elseif ($property.Value -isnot [bool]) {
            throw "$($property.Name) must be a boolean"
        }
        $result[$property.Name] = $property.Value
    }
    return $result
}

function Get-DispatchNativeTask {
    param([Parameter(Mandatory)][string]$Name, [switch]$Required, [string]$Root = '')
    if (-not (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue)) {
        if ($Required) { throw 'ScheduledTasks query capability is required for explicit scheduling changes' }
        if ($Root) {
            $priorOwner = $false
            try {
                [void][IO.File]::GetAttributes((Join-Path $Root 'serve-service.ps1'))
                $priorOwner = $true
            } catch [IO.FileNotFoundException] {
            } catch [IO.DirectoryNotFoundException] {
            }
            if ($priorOwner) {
                throw 'Prior native scheduling intent cannot be observed without ScheduledTasks; no direct start was attempted'
            }
        }
        return $null
    }
    # Avoid an administrative inventory read on the ordinary startup path.
    # Only the provider's structured, exact-name not-found result means absent.
    try {
        $matches = @(Get-ScheduledTask -TaskName $Name -ErrorAction Stop |
            Where-Object { $_.TaskName -eq $Name })
    } catch {
        if ($_.CategoryInfo.Category -ne 'ObjectNotFound' -or
            $_.FullyQualifiedErrorId -notmatch '^CmdletizationQuery_NotFound_TaskName,') {
            throw
        }
        $matches = @()
    }
    if ($matches.Count -gt 1) { throw "Ambiguous Scheduled Task name: $Name" }
    if (-not $matches.Count) { return $null }
    $nativeTask = $matches[0]
    if ($nativeTask.State -notin @('Ready', 'Running', 'Disabled', 'Queued')) {
        throw "Scheduled Task state is unconfirmed: $Name"
    }
    return $nativeTask
}

function Assert-DispatchOwnedTask {
    param(
        [Parameter(Mandatory)]$NativeTask,
        [Parameter(Mandatory)][string]$Root,
        [Parameter(Mandatory)][string]$Coordinator,
        [Parameter(Mandatory)][string]$Supervisor
    )
    $name = $NativeTask.TaskName
    if ($NativeTask.TaskPath -ne '\') { throw "Foreign Scheduled Task folder: $name" }
    $launcher = ''
    $expectedEnvironment = ''
    if ($name -eq $Coordinator) {
        $launcher = Join-Path $Root 'serve-service.ps1'
    } elseif ($name -eq $Supervisor) {
        $launcher = Join-Path $Root 'supervise-service.ps1'
        $expectedEnvironment = Join-Path $Root 'supervisor.env'
    } elseif ($name.StartsWith("$Supervisor-", [StringComparison]::OrdinalIgnoreCase)) {
        $profile = $name.Substring($Supervisor.Length + 1)
        if ($profile -notmatch '^[A-Za-z0-9_-]+$') { throw 'Unsafe supervisor profile task name' }
        $launcher = Join-Path $Root 'supervise-service.ps1'
        $expectedEnvironment = Join-Path $Root "supervisors\$profile.env"
    } else {
        throw "Task is outside the declared coordinator/supervisor family: $name"
    }
    Assert-DispatchOrdinaryPath $launcher
    $taskActions = @($NativeTask.Actions)
    if ($taskActions.Count -ne 1) { throw "Ambiguous Scheduled Task action: $name" }
    $nativeAction = $taskActions[0]
    $expected = "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`""
    $allowed = @($expected)
    if ($expectedEnvironment) { $allowed += "$expected -EnvFile `"$expectedEnvironment`"" }
    if ($nativeAction.Execute -notin @('conhost.exe', (Join-Path $env:SystemRoot 'System32\conhost.exe')) -or
        $nativeAction.Arguments -notin $allowed -or
        -not [IO.Path]::GetFullPath($nativeAction.WorkingDirectory).TrimEnd('\').Equals(
            [IO.Path]::GetFullPath($Root).TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)) {
        throw "Foreign Scheduled Task action: $name"
    }
}

function Remove-DispatchScheduledTaskOnly {
    param(
        [Parameter(Mandatory)][string]$Root,
        [Parameter(Mandatory)][string]$Coordinator,
        [Parameter(Mandatory)][string]$Supervisor,
        [Parameter(Mandatory)][string[]]$Names,
        [Parameter(Mandatory)]$Policy
    )
    if ($Policy.windows_scheduled_task -ne 'absent') { throw 'task-retire requires explicit absent scheduling intent' }
    if (-not (Get-Command Unregister-ScheduledTask -ErrorAction SilentlyContinue)) {
        throw 'ScheduledTasks removal capability is unavailable'
    }
    # Validate the complete requested set before the first removal.
    $selected = @()
    foreach ($name in $Names) {
        # Validate even a missing name; absence is not ownership.
        if ($name -ne $Coordinator -and $name -ne $Supervisor -and
            ($name -notmatch ('^' + [regex]::Escape($Supervisor) + '-[A-Za-z0-9_-]+$'))) {
            throw "Task is outside the declared coordinator/supervisor family: $name"
        }
        $nativeTask = Get-DispatchNativeTask -Name $name -Required
        if (-not $nativeTask) { continue }
        Assert-DispatchOwnedTask $nativeTask $Root $Coordinator $Supervisor
        $startAllowed = if ($name -eq $Coordinator) { $Policy.start_coordinator } else { $Policy.start_supervisor }
        if ($nativeTask.State -eq 'Disabled' -and $startAllowed) {
            throw "Retiring disabled task '$name' requires durable disabled start intent in installation.json"
        }
        if ($nativeTask.State -eq 'Disabled') {
            $durable = Read-DispatchInstallationPolicy -Root $Root -Config (Join-Path $Root 'installation.json')
            $durableAllowed = if ($name -eq $Coordinator) { $durable.start_coordinator } else { $durable.start_supervisor }
            if ($durableAllowed) { throw 'Disabled native intent must remain in the durable installation policy' }
        }
        $selected += $nativeTask
    }
    foreach ($nativeTask in $selected) {
        $current = Get-DispatchNativeTask -Name $nativeTask.TaskName -Required
        if (-not $current) { continue }
        Assert-DispatchOwnedTask $current $Root $Coordinator $Supervisor
        if ($current.State -ne $nativeTask.State -or
            ($current.Actions | ConvertTo-Json -Compress) -cne ($nativeTask.Actions | ConvertTo-Json -Compress)) {
            throw 'Scheduled Task changed before retirement'
        }
        Unregister-ScheduledTask -TaskName $current.TaskName -TaskPath '\' -Confirm:$false -ErrorAction Stop
        if (Get-DispatchNativeTask -Name $current.TaskName -Required) {
            throw "Scheduled Task removal is unconfirmed: $($current.TaskName)"
        }
    }
}

function Get-DispatchSchedulingPolicy {
    $config = $env:AGENT_DISPATCH_INSTALLATION_CONFIG
    return Read-DispatchInstallationPolicy -Root $InstallDir -Config $config
}

function Register-DispatchExistingLauncherTasks {
    param([string]$Root, [string]$Coordinator, [string]$Supervisor, $Policy)
    if ($Policy.windows_scheduled_task -ne 'enabled') { throw 'Scheduled activation requires enabled intent' }
    foreach ($name in @($Coordinator, $Supervisor)) {
        $allowed = if ($name -eq $Coordinator) { $Policy.start_coordinator } else { $Policy.start_supervisor }
        if (-not $allowed) { continue }
        $existing = Get-DispatchNativeTask $name -Required
        if ($existing) {
            continue
        }
        if (-not (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)) {
            throw 'ScheduledTasks registration capability is unavailable'
        }
        $filename = if ($name -eq $Coordinator) { 'serve-service.ps1' } else { 'supervise-service.ps1' }
        $launcher = Join-Path $Root $filename
        $item = Get-Item -LiteralPath $launcher -Force -ErrorAction Stop
        if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Scheduled activation requires an ordinary existing owner launcher'
        }
        $nativeAction = New-ScheduledTaskAction -Execute 'conhost.exe' -WorkingDirectory $Root `
            -Argument "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`""
        $trigger = New-ScheduledTaskTrigger -AtLogOn
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
        $principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
            -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $name -TaskPath '\' -Action $nativeAction -Trigger $trigger `
            -Settings $settings -Principal $principal -ErrorAction Stop | Out-Null
        $registered = Get-DispatchNativeTask $name -Required
        if (-not $registered) { throw 'Scheduled activation registration is unconfirmed' }
        Assert-DispatchOwnedTask $registered $Root $Coordinator $Supervisor
    }
}

function Test-DispatchStartIntent {
    param([ValidateSet('coordinator', 'supervisor')][string]$Kind, [string]$Name)
    $policy = Get-DispatchSchedulingPolicy
    if (-not $policy["start_$Kind"]) { return $false }
    $nativeTask = Get-DispatchNativeTask -Name $Name -Root $InstallDir
    if ($nativeTask -and $nativeTask.State -eq 'Disabled') { return $false }
    return $true
}

function Get-DispatchSelectedPython {
    $bootstrap = Get-BootstrapPython
    if (-not $bootstrap) { throw 'Selected runtime resolution requires the existing bootstrap interpreter' }
    $probe = @'
import importlib.util,json,sys
from pathlib import Path
path=Path(sys.argv[1])
spec=importlib.util.spec_from_file_location("_dispatch_selected_runtime",path)
module=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=module
spec.loader.exec_module(module)
root=Path(sys.argv[2])
version=module.current_version(root)
if not version or not module.is_complete(root,version):
    raise RuntimeError("The selected Dispatch runtime is not complete")
python=module.slot_python(root,version)
if python is None:
    raise RuntimeError("The selected Dispatch interpreter is unavailable")
print(json.dumps(str(python)))
'@
    $diagnostic = ''
    $output = Invoke-Hidden -FilePath $bootstrap -ArgList @(
        '-I', '-B', '-c', $probe, (Join-Path $ScriptDir 'versioned_runtime.py'), $InstallDir
    ) -ErrorOutput ([ref]$diagnostic)
    if ($LASTEXITCODE -ne 0) { throw "Selected runtime resolution failed: $diagnostic" }
    $selected = $output | ConvertFrom-Json -ErrorAction Stop
    if (-not [IO.Path]::IsPathRooted($selected) -or -not [IO.File]::Exists($selected)) {
        throw 'Selected runtime resolution did not return an installed interpreter'
    }
    return $selected
}

function Test-DispatchSelectedSource {
    param([string]$Python)
    $diagnostic = ''
    $null = Invoke-Hidden -FilePath $Python -ArgList @(
        '-I', '-B', '-c', 'from agent_dispatch.source_start import require_selected_source; require_selected_source()'
    ) -ErrorOutput ([ref]$diagnostic)
    return ($LASTEXITCODE -eq 0)
}

function Invoke-DispatchCoordinatorEnsure {
    Assert-DispatchActions @('coordinator-replacement')
    if (-not (Test-DispatchStartIntent -Kind coordinator -Name $TaskName)) { return }
    $selected = Get-DispatchSelectedPython
    & $selected -I -B -m agent_dispatch --local deploy --json
    if ($LASTEXITCODE -ne 0) { throw 'Task-independent coordinator readiness is unconfirmed' }
}

function Invoke-DispatchSupervisorEnsure {
    param([string]$Name, [string]$EnvFile, [string]$Launcher)
    if (-not (Test-DispatchStartIntent -Kind supervisor -Name $Name)) {
        Invoke-SupervisorAudit -Event selection -Reason start-intent-disabled -Name $Name -EnvFile $EnvFile -Outcome skipped
        return
    }
    $mode = Get-SupervisorMode -EnvFile $EnvFile
    $selected = Get-DispatchSelectedPython
    if (Test-DispatchSelectedSource -Python $selected) {
        & $selected -I -B -m agent_dispatch __source-start
        if ($LASTEXITCODE -ne 0) { throw 'Selected Source application readiness is unconfirmed' }
        return
    }
    if ($mode -ne 'serve' -and -not (Test-SupervisorLabelsConfigured -EnvFile $EnvFile)) { return }
    Assert-DispatchActions @('supervisor-reconcile')
    # Probe the actual selected interpreter without invoking downlevel CLI syntax.
    & $selected -I -B -c "import importlib,sys; m=importlib.import_module('agent_dispatch.supervisor_lane_status'); sys.exit(0 if getattr(m,'CAPABILITY',None)=='classic-completed-cycle-v1' else 78)"
    if ($LASTEXITCODE -ne 0) { throw 'Selected runtime does not support completed-cycle supervisor readiness' }
    Start-DispatchDetachedLogonLauncher -Launcher $Launcher -LauncherArguments @('-EnvFile', $EnvFile) -Ensure -Python $selected
}
