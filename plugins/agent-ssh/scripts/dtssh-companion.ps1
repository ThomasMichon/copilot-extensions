#Requires -Version 7.0
<#
.SYNOPSIS
    Dispatch-supervised lifecycle for the durable dtssh host launcher.

.DESCRIPTION
    agent-dispatch runs this file from the identity-verified plugin payload it
    registered, so the selected installation always runs its own companion
    logic and a payload update reaches it through Dispatch's companion
    generation cutover. A healthy host started by another owner (for example
    restore-host) is adopted rather than restarted. `stop` releases such an
    adoption without stopping the host, so a generation cutover keeps the
    live transport and its relayed sessions; the next generation adopts it.
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'health')]
    [string]$Action
)

$ErrorActionPreference = 'Stop'
$ConfigVersion = 1

function Get-CompanionConfigPath {
    if ($env:AGENT_SSH_DTSSH_COMPANION_CONFIG) {
        return [IO.Path]::GetFullPath(
            [Environment]::ExpandEnvironmentVariables(
                $env:AGENT_SSH_DTSSH_COMPANION_CONFIG
            )
        )
    }
    if (-not $env:LOCALAPPDATA) {
        throw 'LOCALAPPDATA is unavailable'
    }
    return Join-Path $env:LOCALAPPDATA 'agent-ssh-dtssh\dispatch-companion.json'
}

function Get-CompanionConfig {
    $path = Get-CompanionConfigPath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "dtssh companion config is missing: $path"
    }
    try {
        $config = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    } catch {
        throw "dtssh companion config is unreadable: $_"
    }
    if ($config.schema_version -ne $ConfigVersion) {
        throw "dtssh companion config needs schema_version $ConfigVersion"
    }
    if ([string]::IsNullOrWhiteSpace([string]$config.alias)) {
        throw 'dtssh companion config needs a non-empty alias'
    }
    if (($config.port -as [int]) -le 0) {
        throw 'dtssh companion config needs a positive port'
    }
    return [pscustomobject]@{
        Path = $path
        InstallRoot = Split-Path $path -Parent
        Alias = [string]$config.alias
        Port = [int]$config.port
        Tunnel = if ($config.PSObject.Properties.Name -contains 'tunnel' -and $config.tunnel) { [string]$config.tunnel } else { $null }
        User = if ($config.PSObject.Properties.Name -contains 'user' -and $config.user) { [string]$config.user } else { $null }
        HostKeyBackupRoot = if (
            $config.PSObject.Properties.Name -contains 'host_key_backup_root' -and
            $config.host_key_backup_root
        ) { [string]$config.host_key_backup_root } else { $null }
    }
}

function Get-InstallHostScript {
    $config = Get-CompanionConfig
    $script = Join-Path $config.InstallRoot 'install-host.ps1'
    if (-not (Test-Path -LiteralPath $script -PathType Leaf)) {
        throw "dtssh install-host script is missing: $script"
    }
    return @{ Config = $config; Script = $script }
}

function Get-InstallHostArgs {
    param(
        [Parameter(Mandatory)]$Config,
        [Parameter(Mandatory)][string]$Verb,
        [switch]$ForegroundLauncher
    )

    $args = @(
        $Verb,
        '-Alias', $Config.Alias,
        '-Port', "$($Config.Port)"
    )
    if ($Config.HostKeyBackupRoot) {
        $args += @('-HostKeyBackupRoot', $Config.HostKeyBackupRoot)
    }
    if ($Config.Tunnel) { $args += @('-Tunnel', $Config.Tunnel) }
    if ($Config.User) { $args += @('-User', $Config.User) }
    if ($ForegroundLauncher) { $args += '-ForegroundLauncher' }
    return $args
}

function Invoke-InstallHost {
    param(
        [Parameter(Mandatory)]$Install,
        [Parameter(Mandatory)][string]$Verb,
        [switch]$ForegroundLauncher,
        [switch]$Capture
    )

    $pwsh = (Get-Command pwsh -ErrorAction Stop).Source
    $argv = @(
        '-NoProfile',
        '-NonInteractive',
        '-ExecutionPolicy',
        'Bypass',
        '-File',
        $Install.Script
    ) + @(Get-InstallHostArgs -Config $Install.Config -Verb $Verb -ForegroundLauncher:$ForegroundLauncher)
    if ($Capture) {
        return @(& $pwsh @argv *>&1) | Out-String
    }
    # Child output must not join the returned exit code (it would make a
    # successful 'stop' look like a failure and skip the following 'start').
    & $pwsh @argv | Out-Host
    return $LASTEXITCODE
}

function Test-HealthyStatusText {
    param([AllowEmptyString()][string]$Output)

    if ([string]::IsNullOrWhiteSpace($Output)) {
        return $false
    }

    foreach ($marker in @(
        'host not running',
        'NOT serving',
        'watchdog not running',
        'dispatch companion launch config missing',
        'durable host identity: pending'
    )) {
        if ($Output.IndexOf($marker, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
            return $false
        }
    }
    return $Output -notmatch 'tunnel .*:\s*0 host connection\(s\)'
}

function Get-StatusProcessIds {
    param(
        [AllowEmptyString()][string]$Output,
        [Parameter(Mandatory)][string]$Label
    )

    $pattern = '(?im)^\s*' + [regex]::Escape($Label) + ':\s*([0-9][0-9,\s]*)$'
    $match = [regex]::Match([string]$Output, $pattern)
    if (-not $match.Success) { return @() }
    return @(
        $match.Groups[1].Value -split '[,\s]+' |
            Where-Object { $_ -match '^[0-9]+$' } |
            ForEach-Object { [int]$_ }
    )
}

function Get-AdoptionPath {
    param([Parameter(Mandatory)]$Config)
    return Join-Path $Config.InstallRoot 'dispatch-companion.adoption.json'
}

function Get-ProcessStartTicks {
    param([Parameter(Mandatory)][int]$ProcessId)
    try {
        return [string](Get-Process -Id $ProcessId -ErrorAction Stop).StartTime.ToUniversalTime().Ticks
    } catch {
        return $null
    }
}

function Read-Adoption {
    param([Parameter(Mandatory)]$Config)
    $path = Get-AdoptionPath $Config
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    try {
        $record = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    } catch {
        return $null
    }
    if ($record.schema_version -ne 1 -or ($record.companion_pid -as [int]) -le 0) { return $null }
    return $record
}

function Test-OwnAdoption {
    param([Parameter(Mandatory)]$Config, [Parameter(Mandatory)][string]$StartTicks)
    $record = Read-Adoption $Config
    return $null -ne $record -and
        [int]$record.companion_pid -eq $PID -and
        [string]$record.companion_start -ceq $StartTicks
}

function Write-Adoption {
    param([Parameter(Mandatory)]$Config, [Parameter(Mandatory)][int[]]$ProcessIds, [Parameter(Mandatory)][string]$StartTicks)
    $path = Get-AdoptionPath $Config
    $temporary = "$path.$PID.tmp"
    [ordered]@{
        schema_version = 1
        companion_pid = $PID
        companion_start = $StartTicks
        adopted_pids = @($ProcessIds)
    } | ConvertTo-Json -Compress | Set-Content -LiteralPath $temporary -Encoding utf8NoBOM
    Move-Item -LiteralPath $temporary -Destination $path -Force
}

function Wait-AdoptedHost {
    param([Parameter(Mandatory)]$Config, [Parameter(Mandatory)][int[]]$ProcessIds)

    $interval = 2
    if ($env:AGENT_SSH_DTSSH_COMPANION_ADOPT_POLL_SECONDS) {
        $interval = [Math]::Max(0.1, [double]$env:AGENT_SSH_DTSSH_COMPANION_ADOPT_POLL_SECONDS)
    }
    $startTicks = Get-ProcessStartTicks $PID
    Write-Adoption -Config $Config -ProcessIds $ProcessIds -StartTicks $startTicks
    try {
        # Wait while the adopted host lives and this generation still holds the
        # adoption; `stop` releases it so a cutover never stops the host.
        while (
            @(Get-Process -Id $ProcessIds -ErrorAction SilentlyContinue).Count -gt 0 -and
            (Test-OwnAdoption -Config $Config -StartTicks $startTicks)
        ) {
            Start-Sleep -Milliseconds ([int]($interval * 1000))
        }
    } finally {
        if (Test-OwnAdoption -Config $Config -StartTicks $startTicks) {
            Remove-Item -LiteralPath (Get-AdoptionPath $Config) -Force -ErrorAction SilentlyContinue
        }
    }
}

function Invoke-ReleaseAdoption {
    param([Parameter(Mandatory)]$Config)
    # Returns $true when a live adopting companion was released. A stale record
    # (its companion is gone or the PID was reused) is discarded instead.
    $record = Read-Adoption $Config
    $path = Get-AdoptionPath $Config
    if ($null -eq $record) {
        Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
        return $false
    }
    $companionPid = [int]$record.companion_pid
    $live = (Get-ProcessStartTicks $companionPid) -ceq [string]$record.companion_start
    Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    if (-not $live) { return $false }
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while ([DateTime]::UtcNow -lt $deadline -and $null -ne (Get-ProcessStartTicks $companionPid)) {
        Start-Sleep -Milliseconds 100
    }
    [Console]::Out.WriteLine(
        "released adopted dtssh host ($(@($record.adopted_pids) -join ',')); host left running"
    )
    return $true
}

switch ($Action) {
    'start' {
        $install = Get-InstallHostScript
        # A healthy host (for example one restored by restore-host) is adopted,
        # not restarted: restarting it drops live relayed sessions and the
        # replacement can come up unable to serve them. Stay running while the
        # adopted watchdog/host lives so the supervisor revives us when it ends.
        $status = Invoke-InstallHost -Install $install -Verb 'status' -Capture
        if (Test-HealthyStatusText $status) {
            $adopted = @(Get-StatusProcessIds $status 'watchdog running')
            if (-not $adopted) { $adopted = @(Get-StatusProcessIds $status 'host running') }
            if ($adopted) {
                [Console]::Out.WriteLine("adopted healthy dtssh host ($($adopted -join ','))")
                Wait-AdoptedHost -Config $install.Config -ProcessIds $adopted
                exit 0
            }
        }
        $stopCode = Invoke-InstallHost -Install $install -Verb 'stop'
        if ($stopCode -ne 0) { exit $stopCode }
        # Win32 OpenSSH re-exec descriptors belong to the old SSH process chain,
        # not this explicitly new daemon root. Preserve every other variable.
        $descriptorState = 'Env:C28FC6F98A2C44ABBBD89D6A3037D0D9_POSIX_FD_STATE'
        if (Test-Path -LiteralPath $descriptorState) {
            Remove-Item -LiteralPath $descriptorState -ErrorAction Stop
        }
        exit (Invoke-InstallHost -Install $install -Verb 'start' -ForegroundLauncher)
    }
    'stop' {
        $install = Get-InstallHostScript
        # The companion never stops a host it adopted: releasing the adoption
        # ends this generation and leaves the live transport for the next one.
        if (Invoke-ReleaseAdoption $install.Config) { exit 0 }
        exit (Invoke-InstallHost -Install $install -Verb 'stop')
    }
    'health' {
        $install = Get-InstallHostScript
        $output = Invoke-InstallHost -Install $install -Verb 'status' -Capture
        $healthy = Test-HealthyStatusText $output
        $detail = if ($healthy) {
            'dtssh host launcher is healthy'
        } else {
            (($output -split "`r?`n") | Where-Object { $_.Trim() } | Select-Object -First 1)
        }
        [Console]::Out.WriteLine(
            ([ordered]@{
                schema_version = 1
                healthy = $healthy
                detail = if ($detail) { $detail } else { 'dtssh host launcher is unhealthy' }
            } | ConvertTo-Json -Compress)
        )
    }
}
exit 0
