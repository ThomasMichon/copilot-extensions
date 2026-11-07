function Get-WindowsScheduledTaskPolicy {
    if (-not (Test-Path -LiteralPath (Join-Path $InstallDir 'config.yaml'))) { return 'preserve' }
    $policy = & $LinkPython -I -B -c 'import sys; from pathlib import Path; from agent_logger.scheduling import windows_scheduled_task; print(windows_scheduled_task(Path(sys.argv[1])))' $InstallDir
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read installation-local windows_scheduled_task policy' }
    $policy = ("$policy").Trim()
    if ($policy -notin @('preserve', 'enabled', 'absent')) { throw 'Invalid windows_scheduled_task policy result' }
    return $policy
}

function Get-LoggerScheduledTask {
    # Enumerating with Stop distinguishes a missing name from a failed query.
    $tasks = @(Get-ScheduledTask -ErrorAction Stop | Where-Object { $_.TaskName -eq $TaskName })
    if ($tasks.Count -gt 1 -or ($tasks.Count -eq 1 -and $tasks[0].TaskPath -ne '\')) {
        throw 'Ambiguous Logger scheduled task; left unchanged'
    }
    if ($tasks.Count -eq 1) { return $tasks[0] }
}

function Assert-LoggerScheduledTaskOwned {
    param($Task)
    $actions = @($Task.Actions)
    $launcher = [IO.Path]::ChangeExtension($TaskLauncher, '.py')
    if ($actions.Count -ne 1 -or
        -not [IO.Path]::IsPathRooted("$($actions[0].Execute)") -or
        $actions[0].Execute -notmatch '(?i)(^|[\\/])pythonw\.exe$' -or
        "$($actions[0].Arguments)" -notmatch ('^-I "' + [regex]::Escape($launcher) + '" "[^"]+[\\/](?:pwsh|powershell)\.exe" "' + [regex]::Escape($TaskLauncher) + '"$')) {
        throw 'Logger scheduled task action is foreign or unsupported; left unchanged'
    }
    if ($Task.State -eq 'Disabled' -or -not $Task.Settings.Enabled) {
        throw 'Logger scheduled task is disabled; retain its independent hold before task-only retirement or scheduling changes'
    }
}

function Remove-LoggerScheduledTaskOnly {
    $task = Get-LoggerScheduledTask
    if (-not $task) { return }
    Assert-LoggerScheduledTaskOwned $task
    if ($DryRun) { Write-Host "[dry-run] would retire task registration: $TaskName"; return }
    Unregister-ScheduledTask -TaskName $TaskName -TaskPath '\' -Confirm:$false -ErrorAction Stop
    if (Get-LoggerScheduledTask) { throw 'Logger scheduled task removal not confirmed' }
}

function Sync-LoggerScheduledTask {
    param([string]$LifecycleAction = 'install')
    switch (Get-WindowsScheduledTaskPolicy) {
        'preserve' { Write-Ok 'Windows scheduling preserved; no task created or rewritten' }
        'absent' { Remove-LoggerScheduledTaskOnly }
        'enabled' {
            $task = Get-LoggerScheduledTask
            if ($task) { Assert-LoggerScheduledTaskOwned $task }
            if ($task -and $LifecycleAction -eq 'update') {
                Update-SyncTaskBinding
            } else {
                Register-SyncTask
            }
        }
    }
}
