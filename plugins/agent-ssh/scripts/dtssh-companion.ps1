#Requires -Version 7.0
<#
.SYNOPSIS
    Dispatch-supervised lifecycle for the durable dtssh host launcher.
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
    & $pwsh @argv
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

switch ($Action) {
    'start' {
        $install = Get-InstallHostScript
        $stopCode = Invoke-InstallHost -Install $install -Verb 'stop'
        if ($stopCode -ne 0) { exit $stopCode }
        exit (Invoke-InstallHost -Install $install -Verb 'start' -ForegroundLauncher)
    }
    'stop' {
        $install = Get-InstallHostScript
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
