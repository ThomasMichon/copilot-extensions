#Requires -Version 7.0
<#
.SYNOPSIS
    Install, persist, and manage a dtssh host running as the interactive user.
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'update', 'uninstall', 'start', 'stop', 'status')]
    [string]$Action = 'status',
    [string]$Alias = "$(($env:COMPUTERNAME).ToLowerInvariant())",
    [int]$Port = 2222,
    [string]$Tunnel,
    [string]$User,
    [switch]$NoStart,
    [switch]$SkipLogin,
    [string]$DtsshVersion
)

$ErrorActionPreference = 'Stop'
$InstallRelease = 'https://raw.githubusercontent.com/bmiddha/devtunnel-ssh/main/scripts/install-release.ps1'
$InstallDir = Join-Path $env:LOCALAPPDATA 'agent-ssh-dtssh'
$DtsshDir = Join-Path $env:LOCALAPPDATA 'dtssh\bin'
$DtsshExe = Join-Path $DtsshDir 'dtssh.exe'
$OpenSSHDir = Join-Path $env:LOCALAPPDATA 'OpenSSH-Win64'
$LauncherSrc = Join-Path $PSScriptRoot 'dtssh-host-launcher.ps1'
$LauncherDst = Join-Path $InstallDir 'dtssh-host-launcher.ps1'
$StartupLnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'agent-ssh-dtssh-host.lnk'

function Add-UserPath([string]$PathToAdd) {
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($userPath -notlike "*$PathToAdd*") {
        [Environment]::SetEnvironmentVariable('Path', "$PathToAdd;$userPath", 'User')
    }
    if ($env:Path -notlike "*$PathToAdd*") { $env:Path = "$PathToAdd;$env:Path" }
}

function Install-Dtssh {
    <#
      Install dtssh, or -- on -Force (the `update` action) -- REPLACE an
      already-installed binary. Guarding the install on `-not Test-Path` alone
      made `update` a silent no-op that kept the old binary (dotfiles#850).

      The running host locks dtssh.exe, but Windows permits *renaming* a running
      image, so rename the current binary aside, let install-release.ps1 drop a
      fresh one, then clean up (or restore it on failure so the host is never
      bricked). The self-healing launcher relaunches the host child on the new
      binary after Start-HostLauncher. $DtsshVersion (script param) pins the
      release via install-release.ps1's $env:VERSION; unset = latest.
    #>
    param([switch]$Force)
    if (-not $Force -and (Test-Path $DtsshExe)) { Add-UserPath $DtsshDir; return }

    $asideName = $null
    if (Test-Path $DtsshExe) {
        Write-Host "Updating dtssh binary$(if ($DtsshVersion) { " to $DtsshVersion" })..."
        $asideName = "$DtsshExe.old-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        try { Rename-Item $DtsshExe $asideName -ErrorAction Stop } catch { $asideName = $null }
    } else {
        Write-Host "Installing dtssh..."
    }

    $ProgressPreference = 'SilentlyContinue'
    $hadVersion = Test-Path Env:\VERSION
    $prevVersion = if ($hadVersion) { $env:VERSION } else { $null }
    if ($DtsshVersion) { $env:VERSION = $DtsshVersion }
    try {
        Invoke-RestMethod $InstallRelease | Invoke-Expression | Out-Null
    } finally {
        if ($DtsshVersion) {
            if ($hadVersion) { $env:VERSION = $prevVersion } else { Remove-Item Env:\VERSION -ErrorAction SilentlyContinue }
        }
    }

    if (-not (Test-Path $DtsshExe)) {
        if ($asideName -and (Test-Path $asideName)) { Rename-Item $asideName $DtsshExe }
        throw "dtssh install did not produce $DtsshExe"
    }
    # New binary confirmed; drop the aside copy (best-effort -- may still be locked
    # by a host left running under -NoStart; a later update sweeps stale *.old-*).
    if ($asideName -and (Test-Path $asideName)) { Remove-Item $asideName -Force -ErrorAction SilentlyContinue }
    Add-UserPath $DtsshDir
}

function Get-SshdExe {
    foreach ($candidate in @(
        (Get-Command sshd.exe -ErrorAction SilentlyContinue).Source,
        (Join-Path $env:WINDIR 'System32\OpenSSH\sshd.exe'),
        (Join-Path $OpenSSHDir 'sshd.exe')
    )) {
        if ($candidate -and (Test-Path $candidate)) { return $candidate }
    }
    return $null
}

function Install-OpenSSHBinaries {
    $sshd = Get-SshdExe
    if (-not $sshd) {
        Write-Host "Installing no-admin Win32-OpenSSH binaries for dtssh's dedicated sshd..."
        $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'ARM64' } else { 'Win64' }
        $asset = "OpenSSH-$arch.zip"
        $downloadDir = Join-Path $InstallDir 'downloads'
        New-Item -ItemType Directory -Force -Path $downloadDir | Out-Null
        $zip = Join-Path $downloadDir $asset
        $release = Invoke-RestMethod 'https://api.github.com/repos/PowerShell/Win32-OpenSSH/releases/latest' -Headers @{ 'User-Agent' = 'agent-ssh-dtssh' }
        $url = ($release.assets | Where-Object { $_.name -eq $asset } | Select-Object -First 1).browser_download_url
        if (-not $url) { throw "Could not find $asset in latest Win32-OpenSSH release" }
        Invoke-WebRequest $url -OutFile $zip
        if (Test-Path $OpenSSHDir) { Remove-Item $OpenSSHDir -Recurse -Force }
        Expand-Archive $zip -DestinationPath $env:LOCALAPPDATA -Force
        $landed = Join-Path $env:LOCALAPPDATA "OpenSSH-$arch"
        if ($landed -ne $OpenSSHDir -and (Test-Path $landed)) { Rename-Item $landed $OpenSSHDir }
        $sshd = Get-SshdExe
    }
    if (-not $sshd) { throw 'sshd.exe is not available after install' }
    Add-UserPath (Split-Path $sshd -Parent)
}

function Get-DevTunnelExe {
    <#
      Resolve the devtunnel CLI. dtssh does NOT bundle devtunnel next to
      dtssh.exe (it downloads it to the WinGet package path), so never assume
      it sits in the dtssh bin dir. Prefer a sibling devtunnel.exe if present,
      else fall back to PATH. Returns $null if it cannot be found.
    #>
    param([string]$DtsshPath = $DtsshExe)
    $sibling = Join-Path (Split-Path $DtsshPath -Parent) 'devtunnel.exe'
    if (Test-Path $sibling) { return $sibling }
    $cmd = Get-Command devtunnel -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Test-DevTunnelLogin {
    # dtssh has no `login --status` subcommand; query the bundled devtunnel CLI.
    param([Parameter(Mandatory)][string]$DtsshPath)
    $devtunnel = Get-DevTunnelExe -DtsshPath $DtsshPath
    if (-not $devtunnel) { return $false }
    try {
        $json = & $devtunnel user show --json 2>$null | Out-String
        return ($json -match '"status"\s*:\s*"Logged in"')
    } catch { return $false }
}

function Assert-Login {
    if ($SkipLogin) { return }
    if (-not (Test-DevTunnelLogin -DtsshPath $DtsshExe)) {
        Write-Host "Starting dtssh login. Complete the Entra/WAM prompt, then return here."
        & $DtsshExe login
    }
}

function Get-HostProcess {
    Get-CimInstance Win32_Process -Filter "Name='dtssh.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match '\bhost\b' }
}

function Install-Shortcut {
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    Copy-Item $LauncherSrc $LauncherDst -Force
    $pwsh = (Get-Command pwsh).Source
    $launcherArgs = @('-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-ExecutionPolicy', 'Bypass', '-File', "`"$LauncherDst`"", '-Alias', $Alias, '-Port', "$Port")
    if ($Tunnel) { $launcherArgs += @('-Tunnel', $Tunnel) }
    if ($User) { $launcherArgs += @('-User', $User) }
    $ws = New-Object -ComObject WScript.Shell
    $shortcut = $ws.CreateShortcut($StartupLnk)
    $shortcut.TargetPath = $pwsh
    $shortcut.Arguments = ($launcherArgs -join ' ')
    $shortcut.WorkingDirectory = $InstallDir
    $shortcut.WindowStyle = 7
    $shortcut.Description = 'agent-ssh-dtssh host launcher'
    $shortcut.Save()
}

function Start-HostLauncher {
    if (Get-HostProcess) { Write-Host "dtssh host already running"; return }
    if (-not (Test-Path $LauncherDst)) { throw "Launcher not installed: $LauncherDst" }
    $args = @('-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-ExecutionPolicy', 'Bypass', '-File', "`"$LauncherDst`"", '-Alias', $Alias, '-Port', "$Port")
    if ($Tunnel) { $args += @('-Tunnel', $Tunnel) }
    if ($User) { $args += @('-User', $User) }
    Start-Process pwsh -WindowStyle Hidden -ArgumentList $args
    Start-Sleep -Seconds 8
    if (Get-HostProcess) { Write-Host "dtssh host started for $Alias on port $Port" } else { Write-Warning "dtssh host did not stay running; check $InstallDir\dtssh-host.log" }
}

function Stop-HostLauncher {
    $procs = @(Get-HostProcess)
    foreach ($proc in $procs) { Stop-Process -Id $proc.ProcessId -Force }
    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($listenerPid in @($listeners.OwningProcess | Sort-Object -Unique | Where-Object { $_ })) {
        $process = Get-Process -Id $listenerPid -ErrorAction SilentlyContinue
        if ($process -and $process.Name -eq 'sshd') { Stop-Process -Id $listenerPid -Force }
    }
}

function Stop-Launcher {
    <#
      Retire the self-healing watchdog launcher pwsh so (a) `update` reloads new
      launcher code instead of the old watchdog continuing to run, and (b) a box
      migrating off the LEGACY dotfiles host doesn't leave a competing watchdog
      fighting for the port/tunnel (dotfiles#401). Stops:
        - this plugin's launcher   (%LOCALAPPDATA%\agent-ssh-dtssh\dtssh-host-launcher.ps1)
        - the legacy dotfiles one  (%LOCALAPPDATA%\dtssh-service\dtssh-host-launcher.ps1)
      and removes the legacy `dtssh-host.lnk` Startup shortcut. Never touches the
      current process (matched by path, and $PID-guarded).
    #>
    $legacyLnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'dtssh-host.lnk'
    if (Test-Path $legacyLnk) {
        Remove-Item $legacyLnk -Force -ErrorAction SilentlyContinue
        Write-Host "Retired legacy Startup shortcut: dtssh-host.lnk"
    }
    $watchdogs = Get-CimInstance Win32_Process -Filter "Name='pwsh.exe'" -ErrorAction SilentlyContinue | Where-Object {
        $_.ProcessId -ne $PID -and $_.CommandLine -and (
            $_.CommandLine -like '*agent-ssh-dtssh\dtssh-host-launcher.ps1*' -or
            $_.CommandLine -like '*dtssh-service\dtssh-host-launcher.ps1*'
        )
    }
    foreach ($w in @($watchdogs)) {
        try {
            Stop-Process -Id $w.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Host "Stopped launcher watchdog (pid $($w.ProcessId))"
        } catch { }
    }
}

switch ($Action) {
    { $_ -in @('install', 'update') } {
        # Retire any legacy/own watchdog + host child FIRST: this frees the locked
        # dtssh.exe so `update` can replace the binary, prevents double-hosting on a
        # migration off the legacy dotfiles host (dotfiles#401), and makes `update`
        # reload new launcher code. No-op on a clean first install. Skipped under
        # -NoStart, where Install-Dtssh's rename-aside still lets the binary be
        # swapped under a still-running host.
        if (-not $NoStart) {
            Stop-Launcher
            Stop-HostLauncher
        }
        Install-Dtssh -Force:($Action -eq 'update')
        Install-OpenSSHBinaries
        Assert-Login
        Install-Shortcut
        if (-not $NoStart) { Start-HostLauncher }
        Write-Host "Done. On clients: dtssh discover; ssh $Alias"
    }
    'uninstall' {
        Stop-Launcher
        Stop-HostLauncher
        if (Test-Path $StartupLnk) { Remove-Item $StartupLnk -Force }
        Write-Host "Removed launcher. Existing dtssh tunnel/client state is left intact."
    }
    'start' { Start-HostLauncher }
    'stop' { Stop-Launcher; Stop-HostLauncher }
    'status' {
        if (Test-Path $DtsshExe) { Write-Host "dtssh: $(& $DtsshExe version 2>&1 | Select-Object -First 1)" } else { Write-Warning 'dtssh not installed' }
        $sshd = Get-SshdExe; if ($sshd) { Write-Host "sshd: $sshd" } else { Write-Warning 'sshd not found' }
        $running = @(Get-HostProcess); if ($running) { Write-Host "host running: $(@($running).ProcessId -join ',')" } else { Write-Warning 'host not running' }
        $wd = @(Get-CimInstance Win32_Process -Filter "Name='pwsh.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*agent-ssh-dtssh\dtssh-host-launcher.ps1*' })
        if ($wd) { Write-Host "watchdog running: $($wd.ProcessId -join ',')" } else { Write-Warning 'watchdog not running (host will not self-heal or restart on child exit)' }
        $rec = Join-Path $env:LOCALAPPDATA "dtssh\host\service-$Alias.tunnel"
        if (Test-Path $rec) {
            $tid = (Get-Content $rec -Raw -ErrorAction SilentlyContinue).Trim()
            if ($tid) {
                $devtunnel = Get-DevTunnelExe
                if ($devtunnel) {
                    $show = & $devtunnel show $tid 2>&1 | Out-String
                    if ($show -match 'Host connections\s*:\s*(\d+)') { Write-Host "tunnel ${tid}: $($Matches[1]) host connection(s)" } else { Write-Host "tunnel: $tid" }
                } else {
                    Write-Host "tunnel: $tid (devtunnel CLI not found; cannot report host connections)"
                }
            }
        }
        if (Test-Path $StartupLnk) { Write-Host "startup shortcut: $StartupLnk" } else { Write-Warning 'startup shortcut missing' }
    }
}
