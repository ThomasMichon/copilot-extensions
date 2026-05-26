#Requires -Version 7.0
<#
.SYNOPSIS
    Shared utilities for Windows service installers.

.DESCRIPTION
    Dot-source this file from any Windows service install.ps1 to get
    standardized helpers for scheduled-task management, status reporting,
    directory operations, and config utility bridging.

    Usage from an installer:
        . "$PSScriptRoot\..\..\services\service-utils.ps1"   # shared service
        . "$PSScriptRoot\..\..\..\services\service-utils.ps1" # machine service
#>

Set-StrictMode -Version Latest

# Capture the repo root at load time so functions can locate repo-relative
# resources regardless of which installer dot-sources this file.
# service-utils.ps1 lives at <repo>/services/service-utils.ps1.
$script:_ServiceUtilsRepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

# ── Status output ────────────────────────────────────────────────────────

function Write-ServiceStatus {
    param(
        [string]$Symbol,
        [string]$Color,
        [string]$Message
    )
    Write-Host "  $Symbol " -ForegroundColor $Color -NoNewline
    Write-Host $Message
}

function Write-ServiceOk      { param([string]$Msg) Write-ServiceStatus '✓' 'Green'  $Msg }
function Write-ServiceChanged { param([string]$Msg) Write-ServiceStatus '→' 'Yellow' $Msg }
function Write-ServiceSkipped { param([string]$Msg) Write-ServiceStatus '○' 'Cyan'   $Msg }
function Write-ServiceErr     { param([string]$Msg) Write-ServiceStatus '✗' 'Red'    $Msg }

function Write-ServiceHeader {
    param([string]$Name)
    Write-Host ""
    Write-Host "═══ $Name " -ForegroundColor Cyan -NoNewline
    Write-Host ("═" * [Math]::Max(0, 56 - $Name.Length)) -ForegroundColor DarkCyan
}

# ── Scheduled task helpers ───────────────────────────────────────────────

function Get-ServiceTask {
    <#
    .SYNOPSIS
        Get a scheduled task by name, or $null if it doesn't exist.
    #>
    param([string]$TaskName)
    Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}

function Test-ServiceTaskRunning {
    param([string]$TaskName)
    $task = Get-ServiceTask $TaskName
    return ($null -ne $task -and $task.State -eq 'Running')
}

function Start-ServiceTask {
    param([string]$TaskName)
    $task = Get-ServiceTask $TaskName
    if (-not $task) {
        Write-ServiceErr "Scheduled task '$TaskName' not found — install first"
        return $false
    }
    if ($task.State -eq 'Running') {
        Write-ServiceOk "Already running"
        return $true
    }
    Start-ScheduledTask -TaskName $TaskName
    Write-ServiceChanged "Started '$TaskName'"
    return $true
}

function Stop-ServiceTask {
    param([string]$TaskName)
    $task = Get-ServiceTask $TaskName
    if (-not $task) {
        Write-ServiceSkipped "Scheduled task '$TaskName' not found"
        return $true
    }
    if ($task.State -ne 'Running') {
        Write-ServiceOk "Already stopped"
        return $true
    }
    Stop-ScheduledTask -TaskName $TaskName
    Write-ServiceChanged "Stopped '$TaskName'"
    return $true
}

function Unregister-ServiceTask {
    <#
    .SYNOPSIS
        Unregister a scheduled task. Stops it first if running.
    #>
    param([string]$TaskName)
    $task = Get-ServiceTask $TaskName
    if (-not $task) {
        Write-ServiceSkipped "Scheduled task '$TaskName' not found (already removed?)"
        return
    }
    if ($task.State -eq 'Running') {
        Stop-ScheduledTask -TaskName $TaskName
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-ServiceChanged "Removed scheduled task '$TaskName'"
}

# ── Elevation helpers ─────────────────────────────────────────────────────

function Test-Elevated {
    <#
    .SYNOPSIS
        Returns $true if the current process is running elevated (Administrator).
    #>
    ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Assert-Elevated {
    <#
    .SYNOPSIS
        Check for elevation and print an error if not elevated. Returns $false
        if the process is not elevated, $true otherwise.
    .DESCRIPTION
        Legacy gate-and-bail helper. Prefer Invoke-SelfElevated for service
        installers that should self-elevate via UAC.
    #>
    if (-not (Test-Elevated)) {
        Write-ServiceErr "This action requires an elevated (Administrator) terminal"
        return $false
    }
    return $true
}

function Invoke-SelfElevated {
    <#
    .SYNOPSIS
        Self-elevate the current script via UAC if not already running as admin.
    .DESCRIPTION
        Call at the top of any action that requires elevation. If already elevated,
        returns immediately and the caller continues normally. If not elevated,
        spawns an elevated child process that re-runs the same script with the
        same arguments, waits for completion, replays captured output, and exits
        with the child's exit code. The caller never resumes after self-elevation.

        The elevated child gets its own console window where interactive prompts
        (Read-Host, etc.) work normally. Output is tee'd to a temp file so the
        non-elevated parent can replay it for callers that capture stdout
        (e.g., worktree-manager services update).

        Status and other read-only actions should NOT call this — keep them
        non-elevated so automated checks (session boot, pre-launch) don't
        trigger UAC prompts.
    .PARAMETER ScriptPath
        Full path to the script to re-run elevated. Typically $PSCommandPath.
    .PARAMETER ArgumentList
        Arguments to pass to the elevated script (e.g., the action name).
    .EXAMPLE
        # At the top of an 'install' or 'update' action:
        Invoke-SelfElevated -ScriptPath $PSCommandPath -ArgumentList $Action
        # If we reach here, we're elevated — continue with the real work.
    #>
    param(
        [Parameter(Mandatory)][string]$ScriptPath,
        [string[]]$ArgumentList = @()
    )

    if (Test-Elevated) { return }

    Write-ServiceChanged "Requesting elevation via UAC..."

    # Pre-resolve paths in the caller's context (avoids $env:TEMP divergence
    # between non-elevated and elevated sessions — see elevation skill).
    $tmpDir = Join-Path $env:USERPROFILE '.aperture-labs\services\.tmp'
    if (-not (Test-Path $tmpDir)) {
        New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
    }
    $tag = "$PID-$(Get-Random)"
    $outputFile = Join-Path $tmpDir "elev-$tag.log"
    $wrapperFile = Join-Path $tmpDir "elev-$tag.ps1"

    # Build argument string for the inner invocation
    $quotedArgs = ($ArgumentList | ForEach-Object { "'$_'" }) -join ', '

    # The wrapper runs the real script as a child pwsh process so that
    # `exit N` from the script sets $LASTEXITCODE reliably (it's a native
    # exe boundary).  Output is tee'd to both the elevated console and
    # a file for the parent to replay.
    $escapedPath = $ScriptPath -replace "'", "''"
    @"
`$ErrorActionPreference = 'Continue'
& pwsh -NoProfile -ExecutionPolicy Bypass -Command "& '$escapedPath' $quotedArgs" 2>&1 ``
    | Tee-Object -FilePath '$outputFile'
exit `$LASTEXITCODE
"@ | Set-Content -Path $wrapperFile -Encoding UTF8

    try {
        $proc = Start-Process pwsh -Verb RunAs -ArgumentList @(
            '-ExecutionPolicy', 'Bypass',
            '-NoProfile',
            '-File', $wrapperFile
        ) -PassThru -ErrorAction Stop
    } catch {
        Write-ServiceErr "Elevation cancelled or failed"
        Remove-Item $wrapperFile -ErrorAction SilentlyContinue
        exit 1
    }

    $completed = $proc.WaitForExit(300000)   # 5-minute timeout
    $exitCode = if ($completed) { $proc.ExitCode } else { 1 }

    if (-not $completed) {
        Write-ServiceErr "Elevated process timed out"
    }

    # Replay captured output for callers that read stdout
    if (Test-Path $outputFile) {
        Get-Content $outputFile
        Remove-Item $outputFile -ErrorAction SilentlyContinue
    }
    Remove-Item $wrapperFile -ErrorAction SilentlyContinue

    exit $exitCode
}

# ── Directory helpers ────────────────────────────────────────────────────

function Ensure-InstallDir {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
        Write-ServiceChanged "Created $Path"
    }
}

function Resolve-ServicePassword {
    <#
    .SYNOPSIS
        Retrieve the Windows login password for the current machine, preferring
        the Aperture Vault. Falls back to interactive Read-Host if the vault is
        unavailable.
    .DESCRIPTION
        Scheduled tasks that run before login (ONSTART, /RL HIGHEST) require the
        user's plaintext password. This function tries Vault.psm1 first — entry
        "Aperture Science/Windows on <Machine>" — then falls back to a masked
        interactive prompt.

        The vault module is resolved relative to $PSScriptRoot (assumes we're
        dot-sourced from a service installer under the repo tree). If the module
        can't be found or the lookup fails, the fallback prompt still works.
    #>
    $vaultModule = Join-Path $script:_ServiceUtilsRepoRoot 'tools\vault\Vault.psm1'
    if (Test-Path $vaultModule) {
        try {
            Import-Module $vaultModule -ErrorAction Stop
            $machineName = (Get-Culture).TextInfo.ToTitleCase($env:COMPUTERNAME.ToLower())
            $pw = Get-VaultSecret "Aperture Science/Windows on $machineName" -Field password -ErrorAction Stop
            if ($pw) {
                Write-ServiceOk "Retrieved Windows password from vault"
                return $pw
            }
        } catch {
            Write-Warning "Vault lookup failed: $_"
        }
    }

    # Fallback: interactive prompt
    $secure = Read-Host "  Windows password for $env:USERDOMAIN\$env:USERNAME" -AsSecureString
    return [System.Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
}

function Remove-InstallDir {
    param(
        [string]$Path,
        [switch]$RemoveConfig,
        [string]$ConfigFile
    )
    if (-not (Test-Path $Path)) {
        Write-ServiceSkipped "Install directory not found (already removed?)"
        return
    }
    if (-not $RemoveConfig -and $ConfigFile) {
        $configPath = Join-Path $Path $ConfigFile
        if (Test-Path $configPath) {
            $backupDir = Join-Path $env:TEMP "service-config-backup"
            Ensure-InstallDir $backupDir
            $backupPath = Join-Path $backupDir (Split-Path $ConfigFile -Leaf)
            Copy-Item $configPath $backupPath -Force
            Write-ServiceSkipped "Config backed up to $backupPath (use -RemoveConfig to delete)"
        }
    }
    Remove-Item $Path -Recurse -Force
    Write-ServiceChanged "Removed $Path"
}

# ── Config utility bridge ────────────────────────────────────────────────

function Invoke-ServiceConfig {
    <#
    .SYNOPSIS
        Call the shared service-config.py utility.
    .PARAMETER Action
        One of: merge, drift, deploy, pull
    .PARAMETER ServiceDir
        Path to the service directory (contains config/ subdirectory).
    .PARAMETER Machine
        Machine name (e.g., lambda-core, borealis).
    .PARAMETER RuntimePath
        Path to the runtime config file (required for drift/deploy/pull).
    .PARAMETER Force
        Pass --force to deploy action.
    #>
    param(
        [Parameter(Mandatory)]
        [ValidateSet('merge','drift','deploy','pull')]
        [string]$Action,

        [Parameter(Mandatory)]
        [string]$ServiceDir,

        [Parameter(Mandatory)]
        [string]$Machine,

        [string]$RuntimePath,

        [switch]$Force
    )

    # Find service-config.py relative to this script or repo root
    $configUtil = $null
    foreach ($candidate in @(
        (Join-Path $PSScriptRoot 'service-config.py'),
        (Join-Path $PSScriptRoot '..\service-config.py'),
        (Join-Path $PSScriptRoot '..\..\services\service-config.py')
    )) {
        if (Test-Path $candidate) {
            $configUtil = Resolve-Path $candidate
            break
        }
    }

    if (-not $configUtil) {
        Write-ServiceErr "service-config.py not found"
        return 1
    }

    $args_ = @($configUtil, $Action, $ServiceDir, '--machine', $Machine)
    if ($RuntimePath) { $args_ += @('--runtime', $RuntimePath) }
    if ($Force)       { $args_ += '--force' }

    # Clear uv/venv env vars so the system Python uses its own stdlib,
    # not the worktree-manager's 3.11 paths (causes SRE module mismatch).
    $savedHome = $env:PYTHONHOME
    $savedPath = $env:PYTHONPATH
    $savedUvHome = $env:UV_INTERNAL__PYTHONHOME
    $env:PYTHONHOME = $null
    $env:PYTHONPATH = $null
    $env:UV_INTERNAL__PYTHONHOME = $null
    try {
        & python @args_
        return $LASTEXITCODE
    } finally {
        $env:PYTHONHOME = $savedHome
        $env:PYTHONPATH = $savedPath
        $env:UV_INTERNAL__PYTHONHOME = $savedUvHome
    }
}

# ── Status reporting ─────────────────────────────────────────────────────

function Get-ServiceStatusReport {
    <#
    .SYNOPSIS
        Build a status report for a scheduled-task-based service.
    .OUTPUTS
        PSCustomObject with Installed, Running, TaskRegistered properties.
    #>
    param(
        [string]$InstallDir,
        [string]$TaskName,
        [string]$CheckFile  # file to check in InstallDir to verify deployment
    )

    $installed = $false
    if ($CheckFile) {
        $installed = Test-Path (Join-Path $InstallDir $CheckFile)
    } else {
        $installed = Test-Path $InstallDir
    }

    $task = Get-ServiceTask $TaskName
    $registered = $null -ne $task
    $running = $registered -and $task.State -eq 'Running'

    return [PSCustomObject]@{
        Installed      = $installed
        TaskRegistered = $registered
        Running        = $running
        TaskState      = if ($task) { $task.State } else { 'NotRegistered' }
    }
}

function Show-ServiceStatus {
    <#
    .SYNOPSIS
        Print a formatted status block for a service.
    #>
    param(
        [string]$ServiceName,
        [string]$InstallDir,
        [string]$TaskName,
        [string]$CheckFile
    )

    Write-ServiceHeader "$ServiceName Status"

    $status = Get-ServiceStatusReport -InstallDir $InstallDir -TaskName $TaskName -CheckFile $CheckFile

    if ($status.Installed) {
        Write-ServiceOk "Installed at $InstallDir"
    } else {
        Write-ServiceErr "Not installed (expected at $InstallDir)"
    }

    if ($status.TaskRegistered) {
        Write-ServiceOk "Scheduled task '$TaskName' registered (state: $($status.TaskState))"
    } else {
        Write-ServiceErr "Scheduled task '$TaskName' not registered"
    }

    return $status
}

# ── Environment detection ────────────────────────────────────────────────

function Get-CurrentEnvironment {
    <#
    .SYNOPSIS
        Detect the current deployment environment identifier.
    .DESCRIPTION
        Returns a string like "lambda-core-windows", "borealis-wsl", or
        "wheatley" based on hostname and WSL detection.
    #>
    $hostname = ($env:COMPUTERNAME ?? (hostname)).ToLower()

    # Normalize known hostnames
    $machine = switch -Regex ($hostname) {
        'lambda.?core' { 'lambda-core' }
        'borealis'     { 'borealis' }
        'tmichon.?book2' { 'tmichon-book2' }
        'wheatley'     { 'wheatley' }
        'home.?ass'    { 'home-assistant' }
        default        { $hostname }
    }

    # WSL detection
    if ($env:WSL_DISTRO_NAME) {
        return "$machine-wsl"
    }

    # Windows detection (PowerShell is always Windows in this facility)
    if ($env:OS -eq 'Windows_NT') {
        return "$machine-windows"
    }

    # Native Linux (Wheatley, HA)
    return $machine
}

# ── Deployment target validation ─────────────────────────────────────────

function Assert-DeploymentTarget {
    <#
    .SYNOPSIS
        Check that the current environment is a valid deployment target.
    .DESCRIPTION
        Reads service.yaml from the service source directory, checks the
        deployments: map for the current environment. Returns $true if the
        environment is listed as 'full' or 'redirector'. Prints an error
        and returns $false otherwise.
    .PARAMETER ServiceYamlPath
        Path to the service.yaml file.
    #>
    param(
        [Parameter(Mandatory)]
        [string]$ServiceYamlPath
    )

    if (-not (Test-Path $ServiceYamlPath)) {
        Write-ServiceErr "service.yaml not found at $ServiceYamlPath"
        return $false
    }

    $env_ = Get-CurrentEnvironment

    # Simple YAML parse — look for our environment under deployments:
    $content = Get-Content $ServiceYamlPath -Raw
    $inDeployments = $false
    $foundEnv = $false
    $deployType = $null
    $allTargets = @()

    foreach ($line in ($content -split "`n")) {
        $trimmed = $line.TrimEnd()
        # Top-level key detection (no leading whitespace)
        if ($trimmed -match '^(\w)' -and $trimmed -notmatch '^\s') {
            $inDeployments = $trimmed -match '^deployments:'
            continue
        }
        if ($inDeployments) {
            # Environment key (2-space indent, ends with colon)
            if ($trimmed -match '^\s{2}(\S[^:]+):$') {
                $envKey = $Matches[1].Trim()
                $allTargets += $envKey
                if ($envKey -eq $env_) {
                    $foundEnv = $true
                }
            }
            # Type field under a matched environment
            if ($foundEnv -and -not $deployType -and $trimmed -match '^\s{4}type:\s*(.+)') {
                $deployType = $Matches[1].Trim()
            }
        }
    }

    if (-not $foundEnv) {
        $targetList = $allTargets -join ', '
        Write-ServiceErr "This service does not deploy to '$env_'"
        Write-ServiceErr "Valid targets: $targetList"
        return $false
    }

    if ($deployType -notin @('full', 'redirector')) {
        Write-ServiceErr "Environment '$env_' has deployment type '$deployType' — not deployable"
        return $false
    }

    Write-ServiceOk "Deployment target: $env_ ($deployType)"
    return $true
}

# ── Deploy manifest ──────────────────────────────────────────────────────

function Write-DeployManifest {
    <#
    .SYNOPSIS
        Write a deploy-manifest.json to the service install directory.
    .DESCRIPTION
        Records git provenance (commit, branch, dirty state), deployment
        timestamp, environment, and source paths. Call this as the FINAL
        step of a successful install or update — after code deploy, task
        registration, and restart have all succeeded.
    .PARAMETER InstallDir
        Path to the service install directory.
    .PARAMETER ServiceName
        Name of the service (matches service.yaml name field).
    .PARAMETER SourcePaths
        Array of repo-relative paths that contribute to this deployment.
    .PARAMETER InstallerPath
        Repo-relative path to the installer script.
    #>
    param(
        [Parameter(Mandatory)][string]$InstallDir,
        [Parameter(Mandatory)][string]$ServiceName,
        [Parameter(Mandatory)][string[]]$SourcePaths,
        [Parameter(Mandatory)][string]$InstallerPath
    )

    $env_ = Get-CurrentEnvironment
    $manifestPath = Join-Path $InstallDir 'deploy-manifest.json'

    # Find repo root — use the pre-resolved repo root from load time,
    # falling back to CWD-based detection for external callers.
    $repoRoot = $null
    if ($script:_ServiceUtilsRepoRoot -and (Test-Path (Join-Path $script:_ServiceUtilsRepoRoot '.git'))) {
        $repoRoot = $script:_ServiceUtilsRepoRoot
    } else {
        try {
            $repoRoot = (git rev-parse --show-toplevel 2>$null)
        } catch { }
    }

    $gitAvailable = $null -ne $repoRoot -and $repoRoot -ne ''
    $commit = $null
    $branch = $null
    $dirty = $false
    $dirtyFiles = @()

    if ($gitAvailable) {
        $commit = (git -C $repoRoot rev-parse HEAD 2>$null)
        $branch = (git -C $repoRoot rev-parse --abbrev-ref HEAD 2>$null)
        $gitAvailable = $LASTEXITCODE -eq 0

        if ($gitAvailable) {
            # Check for dirty files scoped to source paths
            $statusArgs = @('-C', $repoRoot, 'status', '--porcelain', '--untracked-files=all', '--')
            $statusArgs += $SourcePaths
            $statusOutput = git @statusArgs 2>$null
            if ($statusOutput) {
                $dirty = $true
                $dirtyFiles = @($statusOutput | ForEach-Object {
                    ($_ -replace '^...\s*', '').Trim()
                } | Where-Object { $_ })
            }
        }
    }

    $manifest = [ordered]@{
        schema_version = 1
        service        = $ServiceName
        environment    = $env_
        commit         = $commit
        branch         = $branch
        dirty          = $dirty
        dirty_files    = $dirtyFiles
        git_available  = $gitAvailable
        deployed_at    = (Get-Date -Format 'o')
        deployed_by    = $env_
        source_paths   = $SourcePaths
        installer_path = $InstallerPath
    }

    $manifest | ConvertTo-Json -Depth 4 | Set-Content -Path $manifestPath -Encoding UTF8
    Write-ServiceOk "Deploy manifest written to $manifestPath"
}

function Read-DeployManifest {
    <#
    .SYNOPSIS
        Read and return the deploy manifest from an install directory.
    .OUTPUTS
        PSCustomObject with manifest fields, or $null if not found.
    #>
    param(
        [Parameter(Mandatory)][string]$InstallDir
    )

    $manifestPath = Join-Path $InstallDir 'deploy-manifest.json'
    if (-not (Test-Path $manifestPath)) {
        return $null
    }

    return (Get-Content $manifestPath -Raw | ConvertFrom-Json)
}

function Test-ServiceStale {
    <#
    .SYNOPSIS
        Check whether a deployed service is stale relative to the repo.
    .DESCRIPTION
        Reads deploy-manifest.json from the install dir, compares the
        deployed commit against HEAD for the service's source_paths.
        Returns $true if there are newer commits (service needs update),
        $false if up to date, or $null if staleness cannot be determined
        (no manifest, no git info, git errors).
    .PARAMETER InstallDir
        Path to the service install directory.
    .OUTPUTS
        [Nullable[bool]] — $true = stale, $false = current, $null = unknown.
    #>
    param(
        [Parameter(Mandatory)][string]$InstallDir
    )

    $manifest = Read-DeployManifest -InstallDir $InstallDir
    if (-not $manifest) { return $null }
    if (-not $manifest.commit -or -not $manifest.source_paths) { return $null }

    try {
        $logArgs = @('log', '--oneline', "$($manifest.commit)..HEAD", '--')
        $logArgs += @($manifest.source_paths)
        $staleCommits = @(git @logArgs 2>$null | Where-Object { $_ })
        if ($LASTEXITCODE -ne 0) { return $null }
        return ($staleCommits.Count -gt 0)
    } catch {
        return $null
    }
}

function Show-DeployStatus {
    <#
    .SYNOPSIS
        Print formatted deployment provenance and staleness info.
    .DESCRIPTION
        Reads deploy-manifest.json from the install dir. Shows commit,
        branch, dirty state, and staleness (commits behind HEAD for
        the service's source paths). Integrates into status output.
    .PARAMETER InstallDir
        Path to the service install directory.
    #>
    param(
        [Parameter(Mandatory)][string]$InstallDir
    )

    $manifest = Read-DeployManifest -InstallDir $InstallDir
    if (-not $manifest) {
        Write-ServiceSkipped "No deploy manifest (deploy with updated installer to create one)"
        return
    }

    # Basic provenance
    $commitShort = if ($manifest.commit) { $manifest.commit.Substring(0, [Math]::Min(10, $manifest.commit.Length)) } else { 'unknown' }
    $branch = $manifest.branch ?? 'unknown'
    $deployedAt = $manifest.deployed_at ?? 'unknown'

    if ($manifest.dirty) {
        $dirtyCount = @($manifest.dirty_files).Count
        Write-ServiceChanged "Deployed from $branch @ $commitShort (DIRTY — $dirtyCount file(s) modified)"
    } else {
        Write-ServiceOk "Deployed from $branch @ $commitShort"
    }
    Write-ServiceOk "Deployed at $deployedAt"

    # Staleness check
    if (-not $manifest.git_available -or -not $manifest.commit) {
        Write-ServiceSkipped "Staleness: unknown (no git info in manifest)"
        return
    }

    try {
        $logArgs = @('log', '--oneline', "$($manifest.commit)..HEAD", '--')
        $logArgs += @($manifest.source_paths)
        $staleCommits = @(git @logArgs 2>$null | Where-Object { $_ })

        if ($staleCommits.Count -eq 0) {
            Write-ServiceOk "Up to date (no source changes since deploy)"
        } else {
            Write-ServiceChanged "Stale — $($staleCommits.Count) commit(s) behind HEAD:"
            $staleCommits | Select-Object -First 5 | ForEach-Object {
                Write-Host "    $_"
            }
            if ($staleCommits.Count -gt 5) {
                Write-Host "    ... and $($staleCommits.Count - 5) more"
            }
        }
    } catch {
        Write-ServiceSkipped "Staleness: could not check (git error)"
    }
}
