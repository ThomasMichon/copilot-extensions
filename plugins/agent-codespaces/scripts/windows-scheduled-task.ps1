function Get-WindowsScheduledTaskPolicy {
    if (-not (Test-Path -LiteralPath (Join-Path $InstallDir 'config.yaml'))) { return 'preserve' }
    $policy = & $LinkPython -I -B -c 'import sys; from pathlib import Path; from agent_codespaces.scheduling import windows_scheduled_task; print(windows_scheduled_task(Path(sys.argv[1])))' $InstallDir
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read installation-local windows_scheduled_task policy' }
    $policy = ("$policy").Trim()
    if ($policy -notin @('preserve', 'enabled', 'absent')) { throw 'Invalid windows_scheduled_task policy result' }
    return $policy
}

function Get-OwnerScheduledTask {
    $tasks = @(Get-ScheduledTask -ErrorAction Stop | Where-Object { $_.TaskName -eq $OwnerTaskName })
    if ($tasks.Count -gt 1 -or ($tasks.Count -eq 1 -and $tasks[0].TaskPath -ne '\')) {
        throw 'Ambiguous Connection Owner scheduled task; left unchanged'
    }
    if ($tasks.Count -eq 1) { return $tasks[0] }
}

function Assert-OwnerScheduledTaskOwned {
    param($Task)
    $actions = @($Task.Actions)
    $stub = Join-Path $LocalBin 'agent-codespaces.ps1'
    if ($actions.Count -ne 1 -or
        $actions[0].Execute -ne 'conhost.exe' -or
        "$($actions[0].WorkingDirectory)" -ne $InstallDir -or
        "$($actions[0].Arguments)" -notmatch ('^--headless "[^"]+[\\/](?:pwsh|powershell)\.exe" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + [regex]::Escape($stub) + '" owner$')) {
        throw 'Connection Owner scheduled task action is foreign or unsupported; left unchanged'
    }
    if ($Task.State -eq 'Disabled' -or -not $Task.Settings.Enabled) {
        throw 'Connection Owner scheduled task is disabled; retain its independent hold before task-only retirement or scheduling changes'
    }
}

function Remove-OwnerScheduledTaskOnly {
    $task = Get-OwnerScheduledTask
    if (-not $task) { return }
    Assert-OwnerScheduledTaskOwned $task
    if ($DryRun) { Write-Host "[dry-run] would retire task registration: $OwnerTaskName"; return }
    Unregister-ScheduledTask -TaskName $OwnerTaskName -TaskPath '\' -Confirm:$false -ErrorAction Stop
    if (Get-OwnerScheduledTask) { throw 'Connection Owner scheduled task removal not confirmed' }
}
