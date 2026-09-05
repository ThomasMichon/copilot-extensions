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
    [string]$DtsshVersion,
    [string]$HostKeyBackupRoot
)

$ErrorActionPreference = 'Stop'

# === install-contract:test-persistent-environment -- keep byte-identical across installers ===
function Get-CopilotPersistentEnvironmentVariable {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][ValidateSet('User', 'Machine')][string]$Target
    )
    $testMode = $env:COPILOT_EXTENSIONS_TEST_CONTAINED -eq '1' -or [bool]$env:PYTEST_CURRENT_TEST
    $effectiveTarget = if ($testMode) { 'Process' } else { $Target }
    return [Environment]::GetEnvironmentVariable($Name, $effectiveTarget)
}

function Set-CopilotPersistentEnvironmentVariable {
    param(
        [Parameter(Mandatory)][string]$Name,
        [AllowNull()][string]$Value,
        [Parameter(Mandatory)][ValidateSet('User', 'Machine')][string]$Target
    )
    $testMode = $env:COPILOT_EXTENSIONS_TEST_CONTAINED -eq '1' -or [bool]$env:PYTEST_CURRENT_TEST
    $effectiveTarget = if ($testMode) { 'Process' } else { $Target }
    [Environment]::SetEnvironmentVariable($Name, $Value, $effectiveTarget)
}
# === end install-contract:test-persistent-environment ===
$InstallRelease = 'https://raw.githubusercontent.com/bmiddha/devtunnel-ssh/main/scripts/install-release.ps1'
$InstallDir = Join-Path $env:LOCALAPPDATA 'agent-ssh-dtssh'
$DtsshDir = Join-Path $env:LOCALAPPDATA 'dtssh\bin'
$DtsshExe = Join-Path $DtsshDir 'dtssh.exe'
$OpenSSHDir = Join-Path $env:LOCALAPPDATA 'OpenSSH-Win64'
$ValidationOpenSSHDir = Join-Path $InstallDir 'validation-OpenSSH'
$InstallerSrc = $PSCommandPath
$InstallerDst = Join-Path $InstallDir 'install-host.ps1'
$LauncherSrc = Join-Path $PSScriptRoot 'dtssh-host-launcher.ps1'
$LauncherDst = Join-Path $InstallDir 'dtssh-host-launcher.ps1'
$StartupLnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'agent-ssh-dtssh-host.lnk'
$HostStateDir = Join-Path $env:LOCALAPPDATA 'dtssh\host'

function Resolve-DurableHostIdentityRoot {
    if ($HostKeyBackupRoot) {
        return [System.IO.Path]::GetFullPath(
            [Environment]::ExpandEnvironmentVariables($HostKeyBackupRoot)
        )
    }
    if ($env:AGENT_SSH_DTSSH_HOST_KEY_BACKUP_ROOT) {
        return [System.IO.Path]::GetFullPath(
            [Environment]::ExpandEnvironmentVariables(
                $env:AGENT_SSH_DTSSH_HOST_KEY_BACKUP_ROOT
            )
        )
    }
    if ($env:OneDriveCommercial -and (Test-Path -LiteralPath $env:OneDriveCommercial)) {
        return Join-Path $env:OneDriveCommercial '.agent-ssh\dtssh-host-identities'
    }
    return Join-Path $InstallDir 'host-identities'
}

function Get-HostIdentityDirectory {
    $safeAlias = [regex]::Replace(
        $Alias.ToLowerInvariant(),
        '[^a-z0-9._-]',
        '_'
    )
    if ([string]::IsNullOrWhiteSpace($safeAlias)) {
        throw 'The dtssh alias cannot produce an empty host-identity key.'
    }
    return Join-Path (Resolve-DurableHostIdentityRoot) $safeAlias
}

function Protect-PrivateKey {
    param([Parameter(Mandatory)][string]$Path)

    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $isDirectory = Test-Path -LiteralPath $Path -PathType Container
    $icacls = Join-Path $env:SystemRoot 'System32\icacls.exe'
    & $icacls $Path '/inheritance:r' | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Cannot disable inherited permissions on private host-key path $Path."
    }
    $otherIdentities = @((Get-Acl -LiteralPath $Path).Access |
        ForEach-Object {
            try {
                $_.IdentityReference.Translate(
                    [Security.Principal.SecurityIdentifier]
                ).Value
            } catch {
                $_.IdentityReference.Value
            }
        } |
        Where-Object { $_ -ne $identity } |
        Sort-Object -Unique)
    foreach ($otherIdentity in $otherIdentities) {
        & $icacls $Path '/remove:g' "*$otherIdentity" '/remove:d' "*$otherIdentity" |
            Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Cannot remove an unrelated permission from private host-key path $Path."
        }
    }
    $grant = if ($isDirectory) { "*${identity}:(OI)(CI)F" } else { "*${identity}:F" }
    & $icacls $Path '/grant:r' $grant | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Cannot grant the current user access to private host-key path $Path."
    }
}

function Protect-DurableHostIdentityDirectory {
    param([Parameter(Mandatory)][string]$Path)

    $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $owner = (Get-Acl -LiteralPath $Path).Owner
    try {
        $ownerSid = ([Security.Principal.NTAccount]$owner).Translate(
            [Security.Principal.SecurityIdentifier]
        ).Value
    } catch {
        $ownerSid = $owner
    }
    if ($ownerSid -ne $currentSid) {
        throw "The durable dtssh host-identity directory is not owned by the current user: $Path"
    }
    Protect-PrivateKey -Path $Path
}

function Get-PublicKeyCore {
    param([Parameter(Mandatory)][string]$Path)

    $parts = @((Get-Content -LiteralPath $Path -Raw).Trim() -split '\s+')
    if ($parts.Count -lt 2 -or -not $parts[0] -or -not $parts[1]) {
        throw "Invalid SSH public key: $Path"
    }
    return "$($parts[0]) $($parts[1])"
}

function Get-SshKeygenExe {
    foreach ($candidate in @(
        (Get-Command ssh-keygen.exe -ErrorAction SilentlyContinue).Source,
        (Join-Path $env:WINDIR 'System32\OpenSSH\ssh-keygen.exe'),
        (Join-Path $OpenSSHDir 'ssh-keygen.exe'),
        (Join-Path $ValidationOpenSSHDir 'ssh-keygen.exe')
    )) {
        if ($candidate -and (Test-Path $candidate)) { return $candidate }
    }
    return $null
}

function Get-ValidatedHostIdentity {
    param([Parameter(Mandatory)][string]$Directory)

    if (-not (Test-Path -LiteralPath $Directory -PathType Container)) {
        return @()
    }
    $privateKeys = @(Get-ChildItem -LiteralPath $Directory -File |
        Where-Object { $_.Name -match '^ssh_host_.+_key$' } |
        Sort-Object Name)
    $publicKeys = @(Get-ChildItem -LiteralPath $Directory -File |
        Where-Object { $_.Name -match '^ssh_host_.+_key\.pub$' } |
        Sort-Object Name)
    if ($privateKeys.Count -eq 0 -and $publicKeys.Count -eq 0) {
        return @()
    }
    if ($privateKeys.Count -eq 0 -or $privateKeys.Count -ne $publicKeys.Count) {
        throw "Incomplete dtssh host identity in $Directory."
    }

    $sshKeygen = Get-SshKeygenExe
    if (-not $sshKeygen) {
        throw 'ssh-keygen is required to validate the dtssh host identity.'
    }

    $pairs = @()
    foreach ($privateKey in $privateKeys) {
        $publicPath = "$($privateKey.FullName).pub"
        if (-not (Test-Path -LiteralPath $publicPath -PathType Leaf)) {
            throw "Missing public key for dtssh host identity file $($privateKey.Name)."
        }
        Protect-PrivateKey -Path $privateKey.FullName
        $derivedOutput = @(& $sshKeygen -y -f $privateKey.FullName 2>&1)
        $keygenExitCode = $LASTEXITCODE
        if ($keygenExitCode -ne 0) {
            $reason = ($derivedOutput | Out-String).Trim()
            throw "ssh-keygen could not read dtssh private host key $($privateKey.Name): $reason"
        }
        $derived = ($derivedOutput | Select-Object -First 1 | Out-String).Trim()
        if (-not $derived) {
            throw "ssh-keygen returned no public key for $($privateKey.Name)."
        }
        $derivedParts = @($derived -split '\s+')
        if ($derivedParts.Count -lt 2) {
            throw "ssh-keygen returned an invalid public key for $($privateKey.Name)."
        }
        $derivedCore = "$($derivedParts[0]) $($derivedParts[1])"
        if ($derivedCore -ne (Get-PublicKeyCore -Path $publicPath)) {
            throw "Mismatched dtssh host key pair: $($privateKey.Name)."
        }
        $pairs += [pscustomobject]@{
            Name = $privateKey.Name
            PrivatePath = $privateKey.FullName
            PublicPath = $publicPath
            PublicCore = $derivedCore
        }
    }
    return $pairs
}

function Assert-MatchingHostIdentity {
    param(
        [Parameter(Mandatory)][array]$LocalIdentity,
        [Parameter(Mandatory)][array]$BackupIdentity
    )

    if ($LocalIdentity.Count -ne $BackupIdentity.Count) {
        throw 'The local and durable dtssh host identities contain different key sets.'
    }
    $backupByName = @{}
    foreach ($pair in $BackupIdentity) { $backupByName[$pair.Name] = $pair.PublicCore }
    foreach ($pair in $LocalIdentity) {
        if (-not $backupByName.ContainsKey($pair.Name) -or
            $backupByName[$pair.Name] -ne $pair.PublicCore) {
            throw 'The local dtssh host identity differs from its durable backup.'
        }
    }
}

function Save-DurableHostIdentity {
    param(
        [Parameter(Mandatory)][array]$LocalIdentity,
        [Parameter(Mandatory)][string]$BackupDirectory
    )

    $root = Split-Path $BackupDirectory -Parent
    New-Item -ItemType Directory -Force -Path $root | Out-Null
    Protect-DurableHostIdentityDirectory -Path $root
    $staging = "$BackupDirectory.staging-$PID-$([guid]::NewGuid().ToString('N'))"
    try {
        New-Item -ItemType Directory -Path $staging | Out-Null
        Protect-PrivateKey -Path $staging
        foreach ($pair in $LocalIdentity) {
            $privateTarget = Join-Path $staging $pair.Name
            Copy-Item -LiteralPath $pair.PrivatePath -Destination $privateTarget
            Copy-Item -LiteralPath $pair.PublicPath -Destination "$privateTarget.pub"
            Protect-PrivateKey -Path $privateTarget
        }
        $stagedIdentity = @(Get-ValidatedHostIdentity -Directory $staging)
        Assert-MatchingHostIdentity `
            -LocalIdentity $LocalIdentity `
            -BackupIdentity $stagedIdentity
        if (Test-Path -LiteralPath $BackupDirectory -PathType Container) {
            Remove-Item -LiteralPath $BackupDirectory -Recurse -Force
        }
        Move-Item -LiteralPath $staging -Destination $BackupDirectory
        Write-Host "Backed up dtssh host identity to $BackupDirectory"
    } finally {
        if (Test-Path -LiteralPath $staging) {
            Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

function Restore-DurableHostIdentity {
    param([Parameter(Mandatory)][array]$BackupIdentity)

    New-Item -ItemType Directory -Force -Path $HostStateDir | Out-Null
    $stagedPairs = @()
    try {
        foreach ($pair in $BackupIdentity) {
            $suffix = ".restore-$PID-$([guid]::NewGuid().ToString('N'))"
            $privateStage = Join-Path $HostStateDir "$($pair.Name)$suffix"
            $publicStage = "$privateStage.pub"
            Copy-Item -LiteralPath $pair.PrivatePath -Destination $privateStage
            Copy-Item -LiteralPath $pair.PublicPath -Destination $publicStage
            Protect-PrivateKey -Path $privateStage
            $stagedPairs += [pscustomobject]@{
                Name = $pair.Name
                PrivatePath = $privateStage
                PublicPath = $publicStage
            }
        }
        foreach ($pair in $stagedPairs) {
            Move-Item -LiteralPath $pair.PrivatePath `
                -Destination (Join-Path $HostStateDir $pair.Name) -Force
            Move-Item -LiteralPath $pair.PublicPath `
                -Destination (Join-Path $HostStateDir "$($pair.Name).pub") -Force
        }
    } finally {
        foreach ($pair in $stagedPairs) {
            foreach ($stagedPath in @($pair.PrivatePath, $pair.PublicPath)) {
                if (Test-Path -LiteralPath $stagedPath) {
                    Remove-Item -LiteralPath $stagedPath -Force -ErrorAction SilentlyContinue
                }
            }
        }
    }
    $restoredIdentity = @(Get-ValidatedHostIdentity -Directory $HostStateDir)
    Assert-MatchingHostIdentity `
        -LocalIdentity $restoredIdentity `
        -BackupIdentity $BackupIdentity
    Write-Host "Restored dtssh host identity from $(Get-HostIdentityDirectory)"
}

function Sync-DtsshHostIdentity {
    param([switch]$RequireIdentity)

    $backupDirectory = Get-HostIdentityDirectory
    if (Test-Path -LiteralPath $backupDirectory -PathType Container) {
        Protect-DurableHostIdentityDirectory -Path (Split-Path $backupDirectory -Parent)
        Protect-DurableHostIdentityDirectory -Path $backupDirectory
    }
    $backupIdentity = @(Get-ValidatedHostIdentity -Directory $backupDirectory)
    try {
        $localIdentity = @(Get-ValidatedHostIdentity -Directory $HostStateDir)
    } catch {
        if ($backupIdentity.Count -eq 0) { throw }
        Get-ChildItem -LiteralPath $HostStateDir -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^ssh_host_.+_key(?:\.pub)?$' } |
            Remove-Item -Force
        Restore-DurableHostIdentity -BackupIdentity $backupIdentity
        return
    }

    if ($localIdentity.Count -eq 0 -and $backupIdentity.Count -eq 0) {
        if ($RequireIdentity) {
            throw 'dtssh did not create a host identity after launch.'
        }
        return
    }
    if ($localIdentity.Count -eq 0) {
        Restore-DurableHostIdentity -BackupIdentity $backupIdentity
        return
    }
    if ($backupIdentity.Count -eq 0) {
        Save-DurableHostIdentity `
            -LocalIdentity $localIdentity `
            -BackupDirectory $backupDirectory
        return
    }
    Assert-MatchingHostIdentity `
        -LocalIdentity $localIdentity `
        -BackupIdentity $backupIdentity
}

function Wait-DtsshHostIdentity {
    param([int]$TimeoutSeconds = 120)

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        try {
            Sync-DtsshHostIdentity -RequireIdentity
            return
        } catch {
            if ($_.Exception.Message -notmatch 'did not create|Incomplete dtssh host identity') {
                throw
            }
        }
        if (-not (Get-HostProcess)) {
            throw "dtssh stopped before creating its host identity; check $InstallDir\dtssh-host.log"
        }
        Start-Sleep -Seconds 2
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "dtssh did not create a complete host identity within $TimeoutSeconds seconds."
}

function Add-UserPath([string]$PathToAdd) {
    $userPath = Get-CopilotPersistentEnvironmentVariable -Name 'Path' -Target 'User'
    if ($userPath -notlike "*$PathToAdd*") {
        Set-CopilotPersistentEnvironmentVariable -Name 'Path' -Value "$PathToAdd;$userPath" -Target 'User'
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

function Install-HostIdentityValidationTools {
    if (Get-SshKeygenExe) { return }

    Write-Host 'Installing a private ssh-keygen for dtssh host-identity validation...'
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'ARM64' } else { 'Win64' }
    $asset = "OpenSSH-$arch.zip"
    $downloadDir = Join-Path $InstallDir 'downloads'
    New-Item -ItemType Directory -Force -Path $downloadDir | Out-Null
    $zip = Join-Path $downloadDir "validation-$asset"
    $release = Invoke-RestMethod 'https://api.github.com/repos/PowerShell/Win32-OpenSSH/releases/latest' -Headers @{ 'User-Agent' = 'agent-ssh-dtssh' }
    $url = ($release.assets | Where-Object { $_.name -eq $asset } | Select-Object -First 1).browser_download_url
    if (-not $url) { throw "Could not find $asset in latest Win32-OpenSSH release" }
    Invoke-WebRequest $url -OutFile $zip
    $staging = "$ValidationOpenSSHDir.staging-$PID"
    try {
        if (Test-Path -LiteralPath $staging) {
            Remove-Item -LiteralPath $staging -Recurse -Force
        }
        New-Item -ItemType Directory -Path $staging | Out-Null
        Expand-Archive $zip -DestinationPath $staging -Force
        $landed = Join-Path $staging "OpenSSH-$arch"
        if (-not (Test-Path -LiteralPath (Join-Path $landed 'ssh-keygen.exe'))) {
            throw 'The OpenSSH archive did not contain ssh-keygen.exe.'
        }
        if (Test-Path -LiteralPath $ValidationOpenSSHDir) {
            Remove-Item -LiteralPath $ValidationOpenSSHDir -Recurse -Force
        }
        Move-Item -LiteralPath $landed -Destination $ValidationOpenSSHDir
    } finally {
        if (Test-Path -LiteralPath $staging) {
            Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    if (-not (Get-SshKeygenExe)) {
        throw 'ssh-keygen.exe is not available after the validation-tool install'
    }
}

function Install-OpenSSHBinaries {
    $sshd = Get-SshdExe
    $sshKeygen = Get-SshKeygenExe
    if (-not $sshd -or -not $sshKeygen) {
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
        $sshKeygen = Get-SshKeygenExe
    }
    if (-not $sshd -or -not $sshKeygen) {
        throw 'sshd.exe and ssh-keygen.exe are required after the OpenSSH install'
    }
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
    $aliasPattern = [regex]::Escape($Alias)
    $portPattern = [regex]::Escape("$Port")
    $hosts = @(Get-CimInstance Win32_Process -Filter "Name='dtssh.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match '\bhost\b' })
    $exact = @($hosts |
        Where-Object {
            $_.CommandLine -match "(?:^|\s)--alias(?:\s+|=)`"?$aliasPattern(?:`"|\s|$)" -and
            $_.CommandLine -match "(?:^|\s)--port(?:\s+|=)`"?$portPattern(?:`"|\s|$)"
        })
    if ($exact.Count -gt 0) { return $exact }
    if ($hosts.Count -eq 1) { return $hosts }
    return @()
}

function Install-Shortcut {
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    Copy-Item $LauncherSrc $LauncherDst -Force
    if ([System.IO.Path]::GetFullPath($InstallerSrc) -ne
        [System.IO.Path]::GetFullPath($InstallerDst)) {
        Copy-Item $InstallerSrc $InstallerDst -Force
    }
    $pwsh = (Get-Command pwsh).Source
    $launcherArgs = @('-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-ExecutionPolicy', 'Bypass', '-File', "`"$InstallerDst`"", 'start', '-Alias', "`"$Alias`"", '-Port', "$Port")
    if ($Tunnel) { $launcherArgs += @('-Tunnel', "`"$Tunnel`"") }
    if ($User) { $launcherArgs += @('-User', "`"$User`"") }
    if ($HostKeyBackupRoot) {
        $launcherArgs += @('-HostKeyBackupRoot', "`"$HostKeyBackupRoot`"")
    }
    # Point the Startup shortcut at conhost --headless, not pwsh directly: a .lnk
    # whose TargetPath is pwsh.exe flashes a console window at logon even with
    # WindowStyle 7 / -WindowStyle Hidden, both of which the DefTerm handoff ignores.
    # conhost --headless gives the launcher a windowless console (matches the runtime
    # Start-HostLauncher path + the agent-bridge task pattern; windows-launch-hardening #786).
    $conhost = Join-Path $env:SystemRoot 'System32\conhost.exe'
    $ws = New-Object -ComObject WScript.Shell
    $shortcut = $ws.CreateShortcut($StartupLnk)
    $shortcut.TargetPath = $conhost
    $shortcut.Arguments = "--headless `"$pwsh`" " + ($launcherArgs -join ' ')
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
    # conhost --headless: -WindowStyle Hidden alone is ignored by the DefTerm
    # handoff and can flash a console (windows-launch-hardening #786).
    Start-Process -FilePath 'conhost.exe' `
        -ArgumentList (@('--headless', 'pwsh') + $args) `
        -WorkingDirectory $InstallDir `
        -WindowStyle Hidden
    # Poll instead of a single fixed-delay check: a binary that `Install-Dtssh`
    # just downloaded/replaced, or a cold tunnel negotiation, can take longer
    # than a blind 8s wait to report a running host process -- which produced a
    # false "did not stay running" warning even though the host (or its
    # independent watchdog) came up moments later (dotfiles#2070). Poll every 2s
    # up to 40s total and return as soon as the process is observed; only warn
    # once that whole window is exhausted.
    $deadline = (Get-Date).AddSeconds(40)
    do {
        if (Get-HostProcess) { Write-Host "dtssh host started for $Alias on port $Port"; return }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    Write-Warning "dtssh host did not stay running; check $InstallDir\dtssh-host.log"
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
        # Validation requires ssh-keygen. Provision it while the existing host
        # is still running so a rebuilt machine can restore before any outage.
        Install-HostIdentityValidationTools
        # Preserve or restore the host identity before stopping anything. This
        # keeps client pins stable even when dtssh recreates its state directory.
        Sync-DtsshHostIdentity
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
        # A fresh install creates its host key during first launch. Persist it
        # immediately so the next convergence can restore the same identity.
        if (-not $NoStart) { Wait-DtsshHostIdentity }
        Write-Host "Done. On clients: dtssh discover; ssh $Alias"
    }
    'uninstall' {
        Stop-Launcher
        Stop-HostLauncher
        if (Test-Path $StartupLnk) { Remove-Item $StartupLnk -Force }
        Write-Host "Removed launcher. Existing dtssh tunnel/client state is left intact."
    }
    'start' {
        Install-HostIdentityValidationTools
        Sync-DtsshHostIdentity
        Install-OpenSSHBinaries
        Start-HostLauncher
        Wait-DtsshHostIdentity
    }
    'stop' { Stop-Launcher; Stop-HostLauncher }
    'status' {
        if (Test-Path $DtsshExe) { Write-Host "dtssh: $(& $DtsshExe version 2>&1 | Select-Object -First 1)" } else { Write-Warning 'dtssh not installed' }
        $sshd = Get-SshdExe; if ($sshd) { Write-Host "sshd: $sshd" } else { Write-Warning 'sshd not found' }
        $running = @(Get-HostProcess); if ($running) { Write-Host "host running: $(@($running).ProcessId -join ',')" } else { Write-Warning 'host not running' }
        $sshdUp = $false
        $c = $null
        try {
            $c = [System.Net.Sockets.TcpClient]::new()
            $iar = $c.BeginConnect('127.0.0.1', $Port, $null, $null)
            if ($iar.AsyncWaitHandle.WaitOne(3000)) {
                $c.EndConnect($iar)
                if ($c.Connected) {
                    # Read the SSH banner, not just the TCP connect: a wedged sshd
                    # (pre-auth slots saturated past MaxStartups) still accepts TCP
                    # but never banners, so a connect-only check falsely reports
                    # healthy while remote reach is broken. Accumulate >= 4 bytes so
                    # a banner split across TCP segments isn't a false negative.
                    $s = $c.GetStream(); $s.ReadTimeout = 3000
                    $buf = [byte[]]::new(64); $got = 0
                    while ($got -lt 4) {
                        $n = $s.Read($buf, $got, $buf.Length - $got)
                        if ($n -le 0) { break }
                        $got += $n
                    }
                    $sshdUp = ($got -ge 4) -and [System.Text.Encoding]::ASCII.GetString($buf, 0, $got).StartsWith('SSH-')
                }
            }
        } catch { $sshdUp = $false } finally { if ($c) { $c.Dispose() } }
        if ($sshdUp) { Write-Host "sshd serving: 127.0.0.1:$Port (SSH banner OK)" }
        else {
            $est = @(Get-NetTCPConnection -LocalPort $Port -State Established -ErrorAction SilentlyContinue).Count
            Write-Warning "sshd NOT serving on :$Port -- accepts TCP but no SSH banner (dead sshd child #576, or a MaxStartups wedge: $est established pre-auth connection(s)). Remote reach is broken even if the tunnel shows connected; restart the host (install-host.ps1 stop; start)."
        }
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
        $backupDirectory = Get-HostIdentityDirectory
        if (Test-Path -LiteralPath $backupDirectory -PathType Container) {
            Write-Host "durable host identity: $backupDirectory"
        } else {
            Write-Host "durable host identity: pending first successful host launch ($backupDirectory)"
        }
    }
}
