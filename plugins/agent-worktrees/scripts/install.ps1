<#
.SYNOPSIS
    Worktree Session Manager - standardized installer interface.

.DESCRIPTION
    Manages the worktree session infrastructure lifecycle: install, uninstall,
    start, stop, status, update-config, update.

    Shared runtime (venv, package, wrappers) lives at ~/.agent-worktrees/.
    Per-project config and state lives at ~/.{project}/ (default: ~/.aperture-labs/).
    Binstubs go to ~/.local/bin/.

    Run from the repo root:
      pwsh -File plugins\agent-worktrees\scripts\install.ps1 install
      pwsh -File plugins\agent-worktrees\scripts\install.ps1 status

.PARAMETER Action
    Lifecycle action to perform.

.PARAMETER RemoveConfig
    On uninstall: also delete project config and worktree session metadata.

.PARAMETER Force
    Overwrite config without drift confirmation.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'uninstall', 'start', 'stop', 'status', 'update-config', 'update')]
    [string]$Action = 'status',

    [switch]$RemoveConfig,
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# -- Load shared utilities ------------------------------------------------

. (Join-Path $PSScriptRoot 'service-utils.ps1')

# -- Metadata -------------------------------------------------------------

$ServiceName     = 'Worktree Manager'
$ProjectName     = 'aperture-labs'
$InstallDir      = Join-Path $env:USERPROFILE '.agent-worktrees'
$ProjectDir      = Join-Path $env:USERPROFILE ".$ProjectName"
$BinDir          = Join-Path $InstallDir 'bin'
$WorktreesDir    = Join-Path $ProjectDir 'worktrees'
$LocalBin        = Join-Path $env:USERPROFILE '.local\bin'
$ScriptDir       = Split-Path -Parent $MyInvocation.MyCommand.Path
$PluginDir       = (Resolve-Path (Join-Path $ScriptDir '..'))
$ServiceYamlPath = Join-Path $ScriptDir 'service.yaml'

# RepoDir: the aperture-labs repo checkout. Try to detect from existing
# config, then fall back to common locations, then CWD.
$RepoDir = $null
$configPath_ = Join-Path $ProjectDir 'config.yaml'
if (Test-Path $configPath_) {
    try {
        $cfgLines = Get-Content $configPath_ -Raw
        if ($cfgLines -match 'anchor:\s*(.+)') {
            $candidate = $Matches[1].Trim()
            if (Test-Path $candidate) { $RepoDir = $candidate }
        }
    } catch { }
}
if (-not $RepoDir) {
    foreach ($candidate in @(
        (Join-Path (Split-Path $env:USERPROFILE) 'Src\aperture-labs'),
        (Join-Path $env:USERPROFILE 'Src\aperture-labs'),
        'D:\Src\aperture-labs'
    )) {
        if (Test-Path (Join-Path $candidate '.git')) { $RepoDir = $candidate; break }
    }
}
if (-not $RepoDir -and (Test-Path (Join-Path (Get-Location) '.git'))) {
    $RepoDir = (Get-Location).Path
}

$DeploySourcePaths = @('plugins/agent-worktrees/')
$InstallerRelPath  = 'plugins/agent-worktrees/scripts/install.ps1'


# Python runtime paths (shared across projects)
$LibDir   = Join-Path $InstallDir 'lib'
$VenvDir  = Join-Path $InstallDir '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'

# -- Machine detection ----------------------------------------------------

$HostnameMap = @{
    'BOREALIS'      = 'borealis'
    'LAMBDA-CORE'   = 'lambda-core'
    'TMICHON-BOOK2' = 'tmichon-book2'
}

function Resolve-Machine {
    $hostname = $env:COMPUTERNAME
    if ($HostnameMap.ContainsKey($hostname)) {
        return $HostnameMap[$hostname]
    }
    $name = Read-Host "Cannot auto-detect machine from hostname '$hostname'. Enter machine name"
    return $name
}

# -- Helpers --------------------------------------------------------------

function Test-ScriptSyntax {
    <# Validate PowerShell script syntax. Returns $true if valid. #>
    param([string]$Path)
    $tokens = $null; $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors) | Out-Null
    if ($errors.Count -gt 0) {
        Write-ServiceErr "Syntax errors in $(Split-Path $Path -Leaf):"
        $errors | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
        return $false
    }
    return $true
}

function Deploy-Package {
    <# Copy the agent_worktrees Python package to ~/.agent-worktrees/lib/. #>
    $src = Join-Path $PluginDir 'src\agent_worktrees'
    $dst = Join-Path $LibDir 'agent_worktrees'

    if (-not (Test-Path $src)) {
        Write-ServiceErr "Package source not found: $src"
        return $false
    }

    # Clean previous deployment
    if (Test-Path $dst) {
        Remove-Item $dst -Recurse -Force
    }

    New-Item -ItemType Directory -Path (Split-Path $dst) -Force | Out-Null
    Copy-Item $src $dst -Recurse

    # Stamp build info so --version reflects this deployment
    $buildInfoPath = Join-Path $dst '_build_info.py'
    $ts = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    $commit = ''
    $branch = ''
    try {
        $commit = (git -C (Split-Path $PluginDir -Parent | Split-Path -Parent) rev-parse HEAD 2>$null)
        $branch = (git -C (Split-Path $PluginDir -Parent | Split-Path -Parent) rev-parse --abbrev-ref HEAD 2>$null)
    } catch { }
    if (-not $commit) { $commit = 'unknown' }
    if (-not $branch) { $branch = 'unknown' }
    $srcNorm = ($PluginDir -replace '\\', '/')
    $buildContent = @"
`"`"`"Build provenance -- auto-generated at deploy time. Do not edit.`"`"`"

from __future__ import annotations

BUILD_INFO: dict[str, str] = {
    "version": "1.0.0",
    "commit": "$commit",
    "branch": "$branch",
    "build_timestamp": "$ts",
    "source": "$srcNorm",
}
"@
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($buildInfoPath, $buildContent, $utf8NoBom)

    Write-ServiceOk "Package deployed to $dst"
    return $true
}

function Deploy-Venv {
    <# Create venv and install pyyaml via uv. #>

    # Skip venv creation if python.exe already exists (may be locked by
    # a running session). Only create when missing.
    if (-not (Test-Path $VenvPython)) {
        $args_ = @('venv', $VenvDir, '--python', '3.11', '--allow-existing')
        $result = & uv @args_ 2>&1
        if ($LASTEXITCODE -ne 0) {
            # Fallback: try without version constraint
            $args_ = @('venv', $VenvDir, '--allow-existing')
            $result = & uv @args_ 2>&1
            if ($LASTEXITCODE -ne 0) {
                Write-ServiceErr "Failed to create venv: $result"
                return $false
            }
        }
        Write-ServiceOk "Venv created at $VenvDir"
    } else {
        Write-ServiceSkipped "Venv already exists at $VenvDir"
    }

    # Ensure pyvenv.cfg exists (uv can sometimes omit it)
    $pyvenvCfg = Join-Path $VenvDir 'pyvenv.cfg'
    if (-not (Test-Path $pyvenvCfg)) {
        $basePrefix = & $VenvPython -c "import sys; print(sys.base_prefix)" 2>$null
        if ($basePrefix) {
            @"
home = $basePrefix\Scripts
implementation = CPython
include-system-site-packages = false
prompt = .venv
"@ | Set-Content -Path $pyvenvCfg
            Write-ServiceChanged "Created missing pyvenv.cfg"
        }
    }

    # Install pyyaml
    $result = & uv pip install --python $VenvPython pyyaml 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-ServiceErr "Failed to install pyyaml: $result"
        return $false
    }

    Write-ServiceOk "Venv packages OK"
    return $true
}

function Deploy-Wrappers {
    <# Copy the static launch wrappers and bootstrap scripts to ~/.agent-worktrees/bin/. #>
    Ensure-InstallDir $BinDir

    foreach ($wrapper in @('launch-session.cmd', 'launch-session.ps1')) {
        $src = Join-Path $PluginDir "bin\$wrapper"
        $dst = Join-Path $BinDir $wrapper
        if (-not (Test-Path $src)) {
            Write-ServiceErr "Wrapper source not found: $src"
            return $false
        }
        Copy-Item $src $dst -Force
        Write-ServiceOk "Wrapper: $wrapper"
    }

    # Deploy bootstrap-check scripts (called by sessionStart hook)
    foreach ($script in @('bootstrap-check.ps1', 'bootstrap-check.sh')) {
        $src = Join-Path $ScriptDir $script
        $dst = Join-Path $BinDir $script
        if (Test-Path $src) {
            Copy-Item $src $dst -Force
            Write-ServiceOk "Bootstrap: $script"
        }
    }

    return $true
}

function Deploy-Binstub {
    <# Generate the project-specific binstub in ~/.local/bin/. #>
    Ensure-InstallDir $LocalBin

    $content = @"
@echo off
set "WORKTREE_PROJECT=$ProjectName"
"%USERPROFILE%\.agent-worktrees\bin\launch-session.cmd" %*
"@
    $dst = Join-Path $LocalBin "$ProjectName.cmd"
    Set-Content -Path $dst -Value $content -NoNewline
    Write-ServiceOk "Binstub: $dst"
}


function Deploy-Config {
    <# Write config.yaml to the project dir if missing (or Force). Returns $true if written. #>
    param([string]$Machine)

    $configPath = Join-Path $ProjectDir 'config.yaml'
    if ((Test-Path $configPath) -and -not $Force) {
        Write-ServiceSkipped "Config exists at $configPath (use -Force to overwrite)"
        return $false
    }

    if (-not $RepoDir) {
        Write-ServiceSkipped "Config generation skipped (no repo detected — set CWD to the repo or create config.yaml manually)"
        return $false
    }

    $srcRoot = Split-Path -Parent $RepoDir
    $worktreeRoot = Join-Path (Join-Path $srcRoot '.worktrees') $ProjectName

    @"
# ~/.$ProjectName/config.yaml
# Machine-local configuration for $ProjectName worktree management.

srcroot: $srcRoot
machine: $Machine
platform: windows
repo_name: $ProjectName

repos:
  ${ProjectName}:
    anchor: $RepoDir
    worktree_root: $worktreeRoot
    default_branch: master
    remote: origin
"@ | Set-Content -Path $configPath
    Write-ServiceChanged "Written config: $configPath"
    return $true
}

function Deploy-PsmuxConfig {
    <# Deploy psmux.conf to ~/.psmux.conf with drift detection. #>
    $src = Join-Path $PluginDir 'terminal\psmux.conf'
    $dst = Join-Path $env:USERPROFILE '.psmux.conf'

    if (-not (Test-Path $src)) {
        Write-ServiceWarn "psmux.conf template not found at $src"
        return
    }

    if ((Test-Path $dst) -and -not $Force) {
        $srcHash = (Get-FileHash $src -Algorithm SHA256).Hash
        $dstHash = (Get-FileHash $dst -Algorithm SHA256).Hash
        if ($srcHash -eq $dstHash) {
            Write-ServiceSkipped "psmux config up to date"
            return
        }
        Write-ServiceChanged "psmux config drift detected - updating"
    }

    Copy-Item $src $dst -Force
    if (Test-Path $dst) {
        Write-ServiceChanged "psmux config deployed to $dst"
    } else {
        Write-ServiceWarn "Failed to deploy psmux config"
    }
}

function Deploy-Icon {
    if (-not $RepoDir) { return }
    foreach ($icon in @('aperture-science.ico', 'aperture-science-wsl.ico')) {
        $iconSrc = Join-Path $RepoDir "home-assistant\media\$icon"
        $iconDst = Join-Path $InstallDir $icon
        if (Test-Path $iconSrc) {
            Copy-Item $iconSrc $iconDst -Force
        }
    }
    Write-ServiceOk "Icons deployed"
}

function Build-TerminalFragment {
    <# Generate a Windows Terminal fragment JSON with local + remote SSH profiles. #>
    param([string]$Machine)

    # Start with the local profiles (always present on Windows machines)
    $profiles = @(
        @{
            guid            = '{e8ba8d13-cc41-5a92-b5dd-5e4a5418e9a0}'
            name            = 'Aperture Labs'
            commandline     = "cmd /c `"%USERPROFILE%\.local\bin\aperture-labs.cmd`""
            icon            = "%USERPROFILE%\.aperture-labs\aperture-science.ico"
            startingDirectory = "%USERPROFILE%"
            colorScheme     = 'Aperture Science'
            hidden          = $false
        },
        @{
            guid            = '{fd1e4088-c416-5daa-b87c-a6546fa1cc25}'
            name            = 'Aperture Labs (WSL)'
            commandline     = "wsl.exe bash -lc aperture-labs"
            icon            = "%USERPROFILE%\.aperture-labs\aperture-science-wsl.ico"
            startingDirectory = "%USERPROFILE%"
            colorScheme     = 'Aperture Science'
            hidden          = $false
        }
    )

    # Discover additional adopted projects and generate profiles for each
    $knownProjects = @('aperture-labs')  # already has hardcoded profiles above
    Get-ChildItem -Path $env:USERPROFILE -Directory -Filter '.*' -ErrorAction SilentlyContinue | ForEach-Object {
        $cfgPath = Join-Path $_.FullName 'config.yaml'
        if (-not (Test-Path $cfgPath)) { return }
        $projName = $_.Name.TrimStart('.')
        if ($projName -in $knownProjects) { return }
        # Validate: must have repos: key and a matching binstub
        $binstub = Join-Path $LocalBin "$projName.cmd"
        if (-not (Test-Path $binstub)) { return }
        $cfgRaw = Get-Content $cfgPath -Raw -ErrorAction SilentlyContinue
        if (-not $cfgRaw -or $cfgRaw -notmatch 'repos:') { return }

        # Generate stable GUID from project name
        $guidBytes = [System.Text.Encoding]::UTF8.GetBytes("agent-worktrees-local:$projName")
        $hash = [System.Security.Cryptography.SHA256]::Create().ComputeHash($guidBytes)
        $guid = [guid]::new(
            [BitConverter]::ToInt32($hash, 0),
            [BitConverter]::ToInt16($hash, 4),
            [BitConverter]::ToInt16($hash, 6),
            $hash[8], $hash[9], $hash[10], $hash[11],
            $hash[12], $hash[13], $hash[14], $hash[15]
        )

        # Title-case the project name for display
        $displayName = ($projName -replace '-', ' ') -replace '(^| )(.)', { $_.Value.ToUpper() }

        $profiles += @{
            guid            = "{$guid}"
            name            = $displayName
            commandline     = "cmd /c `"%USERPROFILE%\.local\bin\$projName.cmd`""
            icon            = "%USERPROFILE%\.agent-worktrees\aperture-science.ico"
            startingDirectory = "%USERPROFILE%"
            colorScheme     = 'Aperture Science'
            hidden          = $false
        }
        $knownProjects += $projName
    }

    # Load machines.yaml for remote SSH profiles
    $machinesYaml = Join-Path $RepoDir 'machines.yaml'
    if (Test-Path $machinesYaml) {
        try {
            $raw = & $VenvPython -c "import yaml, json, sys; data = yaml.safe_load(open(sys.argv[1], encoding='utf-8')); print(json.dumps(data))" $machinesYaml 2>$null
            $machinesData = $raw | ConvertFrom-Json
            if ($machinesData.machines) {
                foreach ($prop in $machinesData.machines.PSObject.Properties) {
                    $key = $prop.Name
                    $entry = $prop.Value
                    if ($key -eq $Machine) { continue }  # skip self
                    if (-not $entry.ssh -or -not $entry.ssh.ready) { continue }

                    foreach ($env in $entry.ssh.environments) {
                        $alias = $env.alias
                        $envLabel = switch ($env.name) {
                            'windows' { 'Windows' }
                            'wsl'     { 'WSL' }
                            'linux'   { 'Linux' }
                            default   { $env.name }
                        }
                        $profileName = "$($entry.display_name) ($envLabel)"

                        $cmdline = "ssh $alias"

                        # Stable GUID seed: machine key + env name (not alias)
                        $guidBytes = [System.Text.Encoding]::UTF8.GetBytes("aperture-labs-ssh-$key-$($env.name)")
                        $hash = [System.Security.Cryptography.SHA256]::Create().ComputeHash($guidBytes)
                        $guid = [guid]::new(
                            [BitConverter]::ToInt32($hash, 0),
                            [BitConverter]::ToInt16($hash, 4),
                            [BitConverter]::ToInt16($hash, 6),
                            $hash[8], $hash[9], $hash[10], $hash[11],
                            $hash[12], $hash[13], $hash[14], $hash[15]
                        )

                        $profiles += @{
                            guid            = "{$guid}"
                            name            = $profileName
                            commandline     = $cmdline
                            icon            = "%USERPROFILE%\.aperture-labs\aperture-science.ico"
                            startingDirectory = "%USERPROFILE%"
                            colorScheme     = 'Aperture Science'
                            hidden          = $false
                        }

                        # "Aperture Labs (Machine + Env)" profile - SSH + launch binstub
                        $binstubCmd = if ($env.shell -eq 'pwsh') { 'aperture-labs.cmd' } else { 'aperture-labs' }
                        $alCmdline = "ssh -t $alias $binstubCmd"
                        $alLabel = if ($envLabel -eq 'Linux') { $entry.display_name } else { "$($entry.display_name) $envLabel" }
                        $alProfileName = "Aperture Labs ($alLabel)"

                        # Stable GUID seed: machine key + env name (not alias)
                        $alGuidBytes = [System.Text.Encoding]::UTF8.GetBytes("aperture-labs-launch-$key-$($env.name)")
                        $alHash = [System.Security.Cryptography.SHA256]::Create().ComputeHash($alGuidBytes)
                        $alGuid = [guid]::new(
                            [BitConverter]::ToInt32($alHash, 0),
                            [BitConverter]::ToInt16($alHash, 4),
                            [BitConverter]::ToInt16($alHash, 6),
                            $alHash[8], $alHash[9], $alHash[10], $alHash[11],
                            $alHash[12], $alHash[13], $alHash[14], $alHash[15]
                        )

                        $profiles += @{
                            guid            = "{$alGuid}"
                            name            = $alProfileName
                            commandline     = $alCmdline
                            icon            = "%USERPROFILE%\.aperture-labs\aperture-science.ico"
                            startingDirectory = "%USERPROFILE%"
                            colorScheme     = 'Aperture Science'
                            hidden          = $false
                        }
                    }
                }
            }
        } catch {
            Write-ServiceWarn "Could not parse machines.yaml for terminal profiles: $_"
        }
    }

    $colorScheme = @{
        name            = 'Aperture Science'
        background      = '#0C0C0C'
        foreground      = '#E8DFD0'
        cursorColor     = '#F6A821'
        selectionBackground = '#3A3A5C'
        black           = '#0C0C0C'
        red             = '#E24C3E'
        green           = '#6EA667'
        yellow          = '#F6A821'
        blue            = '#3B8EEA'
        purple          = '#9B6BC4'
        cyan            = '#4EC9B0'
        white           = '#D4D4D4'
        brightBlack     = '#3A3A3A'
        brightRed       = '#F44747'
        brightGreen     = '#B5CEA8'
        brightYellow    = '#FFD700'
        brightBlue      = '#6CB6FF'
        brightPurple    = '#D4BFFF'
        brightCyan      = '#7EECD8'
        brightWhite     = '#F0F0F0'
    }

    $fragment = @{
        profiles = $profiles
        schemes  = @($colorScheme)
    }

    return ($fragment | ConvertTo-Json -Depth 5)
}

function Deploy-Shortcuts {
    <# Deploy Windows Terminal fragment (with remote SSH profiles) and create .lnk shortcuts. #>
    param([string]$Machine)

    # Deploy WT fragment - profiles appear in Terminal dropdown automatically
    $fragmentDir = Join-Path $env:LOCALAPPDATA 'Microsoft\Windows Terminal\Fragments\ApertureLabs'
    if (-not (Test-Path $fragmentDir)) {
        New-Item -ItemType Directory -Path $fragmentDir -Force | Out-Null
    }

    # Generate the fragment dynamically from machines.yaml + static local profiles
    $fragmentDst = Join-Path $fragmentDir 'aperture-labs.json'
    $fragment = Build-TerminalFragment -Machine $Machine
    $fragment | Set-Content $fragmentDst -Encoding UTF8
    Write-ServiceOk "Windows Terminal profiles deployed (fragment with remote SSH profiles)"

    # Create .lnk shortcuts that launch the WT profiles (pinnable, proper taskbar grouping)
    $shell = New-Object -ComObject WScript.Shell
    $wtExe = "$env:LOCALAPPDATA\Microsoft\WindowsApps\wt.exe"

    $lnkPath = Join-Path $LocalBin 'Aperture Labs.lnk'
    $lnk = $shell.CreateShortcut($lnkPath)
    $lnk.TargetPath = $wtExe
    $lnk.Arguments = '-p "Aperture Labs"'
    $lnk.WorkingDirectory = "%USERPROFILE%"
    $lnk.Description = "Aperture Labs - Worktree Session Manager"
    $lnk.IconLocation = "$InstallDir\aperture-science.ico, 0"
    $lnk.Save()

    $lnkPath = Join-Path $LocalBin 'Aperture Labs (WSL).lnk'
    $lnk = $shell.CreateShortcut($lnkPath)
    $lnk.TargetPath = $wtExe
    $lnk.Arguments = '-p "Aperture Labs (WSL)"'
    $lnk.WorkingDirectory = "%USERPROFILE%"
    $lnk.Description = "Aperture Labs - Worktree Session Manager (WSL)"
    $lnk.IconLocation = "$InstallDir\aperture-science-wsl.ico, 0"
    $lnk.Save()

    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($shell) | Out-Null

    # Deploy tool binstubs
    foreach ($stub in @('agent-worktrees.cmd')) {
        $src = Join-Path $PluginDir "bin\$stub"
        $dst = Join-Path $LocalBin $stub
        if (Test-Path $src) {
            Copy-Item $src $dst -Force
        }
    }
    Write-ServiceOk "Shortcuts deployed to $LocalBin (targeting wt.exe profiles)"
}

function Deploy-CopilotPlugin {
    <# Install the agent-worktrees Copilot CLI plugin if copilot is available. #>
    # In the plugin layout, plugin.json is at the plugin root
    $pluginJsonPath = Join-Path $PluginDir 'plugin.json'
    if (-not (Test-Path $pluginJsonPath)) {
        Write-ServiceWarn "Copilot plugin.json not found at $pluginJsonPath"
        return
    }

    if (-not (Get-Command copilot -ErrorAction SilentlyContinue)) {
        Write-ServiceWarn "Copilot CLI not found — skipping plugin install"
        return
    }

    # Check if already installed and current
    $installed = copilot plugin list 2>$null
    if ($installed -match 'agent-worktrees') {
        copilot plugin install $PluginDir 2>$null | Out-Null
        Write-ServiceOk "Copilot plugin updated"
    } else {
        copilot plugin install $PluginDir 2>$null | Out-Null
        Write-ServiceChanged "Copilot plugin installed (agent-worktrees)"
    }
}

function Ensure-CopilotExperimental {
    <# Ensure experimental: true in Copilot CLI settings.json.
       The CLI gates extension loading on this flag — COPILOT_FEATURE_FLAGS
       alone is not sufficient. Both are required. #>
    $settingsFile = Join-Path $env:USERPROFILE '.copilot\settings.json'
    if (-not (Test-Path $settingsFile)) { return }

    try {
        $raw = Get-Content $settingsFile -Raw
        $settings = $raw | ConvertFrom-Json -AsHashtable
    } catch {
        Write-ServiceWarn "Could not parse $settingsFile — skipping"
        return
    }

    if ($settings.ContainsKey('experimental') -and $settings['experimental'] -eq $true) {
        Write-ServiceOk "Copilot experimental mode enabled"
        return
    }

    $settings['experimental'] = $true
    $settings | ConvertTo-Json -Depth 10 | Set-Content $settingsFile -Encoding utf8NoBOM
    Write-ServiceChanged "Copilot experimental mode enabled (required for extensions)"
}

function Deploy-GitHooksPath {
    <# Ensure core.hooksPath points to tools/hooks in the anchor repo. #>
    if (-not $RepoDir) { return }
    $current = git --no-pager -C $RepoDir config --local core.hooksPath 2>$null
    if ($current -eq 'tools/hooks') {
        Write-ServiceOk "Git hooksPath = tools/hooks"
        return
    }
    if ($current -and $current -ne 'tools/hooks') {
        Write-ServiceWarn "Git core.hooksPath already set to '$current' - not overwriting"
        Write-Host "    To update manually: git -C $RepoDir config --local core.hooksPath tools/hooks"
        return
    }
    git -C $RepoDir config --local core.hooksPath tools/hooks
    Write-ServiceChanged "Set git core.hooksPath = tools/hooks"
}

function Test-PathIncludes {
    param([string]$Dir)
    $pathDirs = $env:PATH -split ';'
    return ($pathDirs -contains $Dir)
}

function Assert-PathIncludes {
    param([string]$Dir)
    if (-not (Test-PathIncludes $Dir)) {
        Write-ServiceErr "$Dir is not on PATH"
        Write-Host "    Add it: [Environment]::SetEnvironmentVariable('PATH', `$env:PATH + ';$Dir', 'User')"
    } else {
        Write-ServiceOk "$Dir is on PATH"
    }
}

function Remove-Binstub {
    foreach ($stub in @("$ProjectName.cmd", 'mark-session-complete.cmd', 'agent-worktrees.cmd')) {
        $path = Join-Path $LocalBin $stub
        if (Test-Path $path) {
            Remove-Item $path -Force
            Write-ServiceChanged "Removed binstub: $path"
        }
    }
}

# -- Actions --------------------------------------------------------------

switch ($Action) {
    'install' {
        Write-ServiceHeader "Installing $ServiceName"

        if (-not (Assert-DeploymentTarget $ServiceYamlPath)) { exit 1 }

        $machine = Resolve-Machine
        Write-Host "  Machine: $machine"
        if ($RepoDir) {
            Write-Host "  Repo:    $RepoDir"
        } else {
            Write-Host "  Repo:    (not detected — repo-dependent features will be skipped)"
        }

        # Prereq checks
        $missingPrereqs = @()
        try { git --version 2>&1 | Out-Null } catch { $missingPrereqs += 'git' }
        try { uv --version 2>&1 | Out-Null } catch { $missingPrereqs += 'uv' }
        if ($missingPrereqs.Count -gt 0) {
            Write-ServiceErr "Missing prerequisites: $($missingPrereqs -join ', ')"
            exit 1
        }

        # Optional: psmux terminal multiplexer for session persistence
        if (-not (Get-Command psmux -ErrorAction SilentlyContinue)) {
            Write-Host "  Installing psmux (terminal multiplexer)..."
            & winget install --id marlocarlo.psmux --accept-source-agreements --accept-package-agreements 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) {
                Write-ServiceOk "psmux installed"
            } else {
                Write-ServiceWarn "psmux install failed - sessions will launch without multiplexing"
            }
        } else {
            Write-ServiceOk "psmux available"
        }

        # Create directory structure
        foreach ($dir in @($InstallDir, $BinDir, $ProjectDir, $WorktreesDir, $LocalBin)) {
            Ensure-InstallDir $dir
        }

        Deploy-Config -Machine $machine | Out-Null
        if (-not (Deploy-Package)) { exit 1 }
        if (-not (Deploy-Venv)) { exit 1 }
        if (-not (Deploy-Wrappers)) { exit 1 }
        Deploy-Binstub
        if ($RepoDir) { Deploy-Icon }
        Deploy-Shortcuts -Machine $machine
        Deploy-PsmuxConfig
        if ($RepoDir) { Deploy-GitHooksPath }
        Deploy-CopilotPlugin
        Ensure-CopilotExperimental
        Assert-PathIncludes $LocalBin

        # Deploy machine.instructions.md + AGENTS.md from machines.yaml
        if ($RepoDir) {
            try {
                $env:PYTHONUTF8 = '1'
                $env:PYTHONPATH = $LibDir
                $env:WORKTREE_PROJECT = $ProjectName
                & $VenvPython -m agent_worktrees deploy-instructions --machine $machine 2>&1 | ForEach-Object { Write-Host "  $_" }
            } catch {
                Write-ServiceWarn "Instruction file deployment skipped: $_"
            }
        } else {
            Write-ServiceSkipped "Instruction deployment skipped (no repo detected)"
        }

        Write-DeployManifest -InstallDir $InstallDir -ServiceName 'worktree-sessions' `
            -SourcePaths $DeploySourcePaths -InstallerPath $InstallerRelPath

        # Add runtime + plugin_source fields to manifest
        $manifestPath = Join-Path $InstallDir 'deploy-manifest.json'
        $m = Get-Content $manifestPath -Raw | ConvertFrom-Json
        $m | Add-Member -NotePropertyName 'runtime' -NotePropertyValue 'python' -Force
        $m | Add-Member -NotePropertyName 'plugin_source' -NotePropertyValue $PluginDir.ToString() -Force
        $m | ConvertTo-Json -Depth 4 | Set-Content $manifestPath -Encoding UTF8

        Write-Host ""
        Write-ServiceOk "Installation complete"
        Write-Host "  Runtime dir: $InstallDir"
        Write-Host "  Project dir: $ProjectDir"
        Write-Host "  Runtime:     Python ($VenvPython)"
        Write-Host "  Usage:       aperture-labs"
    }

    'uninstall' {
        Write-ServiceHeader "Uninstalling $ServiceName"

        Remove-Binstub

        # Remove Windows Terminal fragment
        $fragmentDir = Join-Path $env:LOCALAPPDATA 'Microsoft\Windows Terminal\Fragments\ApertureLabs'
        if (Test-Path $fragmentDir) {
            Remove-Item $fragmentDir -Recurse -Force
            Write-ServiceChanged "Removed Windows Terminal profiles (fragment)"
        }

        # Remove psmux config
        $psmuxConf = Join-Path $env:USERPROFILE '.psmux.conf'
        if (Test-Path $psmuxConf) {
            Remove-Item $psmuxConf -Force
            Write-ServiceChanged "Removed psmux config ($psmuxConf)"
        }

        # Remove shortcuts
        foreach ($lnk in @('Aperture Labs.lnk', 'Aperture Labs (WSL).lnk')) {
            $lnkPath = Join-Path $LocalBin $lnk
            if (Test-Path $lnkPath) { Remove-Item $lnkPath -Force }
        }
        Write-ServiceChanged "Removed shortcuts"

        # Remove Python runtime (venv + package)
        if (Test-Path $VenvDir) {
            Remove-Item $VenvDir -Recurse -Force
            Write-ServiceChanged "Removed venv: $VenvDir"
        }
        if (Test-Path $LibDir) {
            Remove-Item $LibDir -Recurse -Force
            Write-ServiceChanged "Removed package: $LibDir"
        }

        # Remove wrappers
        foreach ($wrapper in @('launch-session.cmd', 'launch-session.ps1')) {
            $path = Join-Path $BinDir $wrapper
            if (Test-Path $path) { Remove-Item $path -Force }
        }
        Write-ServiceChanged "Removed wrappers from $BinDir"

        if ($RemoveConfig) {
            if (Test-Path $ProjectDir) {
                Remove-Item $ProjectDir -Recurse -Force
                Write-ServiceChanged "Removed project dir $ProjectDir (config + session metadata)"
            }
            if (Test-Path $InstallDir) {
                Remove-Item $InstallDir -Recurse -Force
                Write-ServiceChanged "Removed runtime dir $InstallDir"
            }
        } else {
            $manifestPath = Join-Path $InstallDir 'deploy-manifest.json'
            if (Test-Path $manifestPath) {
                Remove-Item $manifestPath -Force
            }
            Write-ServiceSkipped "Config and session metadata preserved at $ProjectDir"
            Write-Host "    Use -RemoveConfig to delete everything"
        }

        Write-ServiceOk "Uninstall complete"
    }

    'start' {
        Write-ServiceHeader "Starting $ServiceName"
        Write-ServiceSkipped "Not a daemon - invoke with: aperture-labs"
    }

    'stop' {
        Write-ServiceHeader "Stopping $ServiceName"
        Write-ServiceSkipped "Not a daemon - Ctrl+C or close the terminal to end a session"
    }

    'status' {
        Write-ServiceHeader "$ServiceName Status"

        # Venv
        if (Test-Path $VenvPython) {
            Write-ServiceOk "Venv Python: $VenvPython"
        } else {
            Write-ServiceErr "Venv Python missing: $VenvPython"
        }

        # Package
        $pkgDir = Join-Path $LibDir 'agent_worktrees'
        if (Test-Path $pkgDir) {
            Write-ServiceOk "Package deployed: $pkgDir"
        } else {
            Write-ServiceErr "Package missing: $pkgDir"
        }

        # Wrapper
        foreach ($wrapper in @('launch-session.cmd', 'launch-session.ps1')) {
            $wrapperPath = Join-Path $BinDir $wrapper
            if (Test-Path $wrapperPath) {
                Write-ServiceOk "$wrapper deployed"
            } else {
                Write-ServiceErr "$wrapper missing"
            }
        }

        # Binstub
        $binstub = Join-Path $LocalBin "$ProjectName.cmd"
        if (Test-Path $binstub) {
            Write-ServiceOk "Binstub installed at $binstub"
        } else {
            Write-ServiceErr "Binstub missing at $binstub"
        }

        # Config (project dir)
        $configPath = Join-Path $ProjectDir 'config.yaml'
        if (Test-Path $configPath) {
            Write-ServiceOk "Config at $configPath"
        } else {
            Write-ServiceErr "Config missing at $configPath"
        }

        Assert-PathIncludes $LocalBin

        # Git hooks
        if ($RepoDir) {
            $hooksPath = git --no-pager -C $RepoDir config --local core.hooksPath 2>$null
        if ($hooksPath -eq 'tools/hooks') {
            Write-ServiceOk "Git hooksPath = tools/hooks"
        } elseif ($hooksPath) {
            Write-ServiceWarn "Git hooksPath = $hooksPath (expected tools/hooks)"
        } else {
            Write-ServiceErr "Git core.hooksPath not set - run 'update' to configure"
        }
        } else {
            Write-ServiceSkipped "Git hooks check skipped (no repo detected)"
        }

        # Windows Terminal fragment
        $fragmentPath = Join-Path $env:LOCALAPPDATA 'Microsoft\Windows Terminal\Fragments\ApertureLabs\aperture-labs.json'
        if (Test-Path $fragmentPath) {
            Write-ServiceOk "Windows Terminal profiles installed"
        } else {
            Write-ServiceErr "Windows Terminal fragment missing"
        }

        # psmux config
        $psmuxConf = Join-Path $env:USERPROFILE '.psmux.conf'
        if (Test-Path $psmuxConf) {
            Write-ServiceOk "psmux config at $psmuxConf"
        } else {
            Write-ServiceWarn "psmux config missing - run 'update' to deploy"
        }

        # Active worktree sessions
        if (Test-Path $WorktreesDir) {
            $sessions = @(Get-ChildItem $WorktreesDir -Filter '*.yaml' -ErrorAction SilentlyContinue)
            $active = @($sessions | ForEach-Object {
                $content = Get-Content $_.FullName -Raw
                if ($content -match 'status:\s*active') { $_ }
            })
            Write-ServiceOk "$($active.Count) active worktree(s), $($sessions.Count) total"
        }

        # Deploy provenance
        Show-DeployStatus -InstallDir $InstallDir
    }

    'update-config' {
        Write-ServiceHeader "Updating $ServiceName Config"

        $configPath = Join-Path $ProjectDir 'config.yaml'
        if (-not (Test-Path $configPath)) {
            Write-ServiceErr "Config not found - run 'install' first"
            exit 1
        }

        if ($Force) {
            $machine = Resolve-Machine
            Deploy-Config -Machine $machine
        } else {
            Write-ServiceSkipped "Config is machine-generated - use -Force to regenerate"
            Write-Host "    Current: $configPath"
        }
    }

    'update' {
        Write-ServiceHeader "Updating $ServiceName"

        if (-not (Assert-DeploymentTarget $ServiceYamlPath)) { exit 1 }

        if (-not (Test-Path $BinDir)) {
            Write-ServiceErr "Not installed - run 'install' first"
            exit 1
        }

        if (-not (Deploy-Package)) { exit 1 }
        if (-not (Deploy-Venv)) { exit 1 }
        if (-not (Deploy-Wrappers)) { exit 1 }
        Deploy-Binstub
        if ($RepoDir) { Deploy-Icon }
        # Detect machine for terminal profile generation
        $updateMachine = Resolve-Machine
        $configPath = Join-Path $ProjectDir 'config.yaml'
        if (Test-Path $configPath) {
            try {
                $cfgRaw = & $VenvPython -c "import yaml, json, sys; data = yaml.safe_load(open(sys.argv[1], encoding='utf-8')); print(json.dumps(data))" $configPath 2>$null
                $cfgObj = $cfgRaw | ConvertFrom-Json
                if ($cfgObj.machine) { $updateMachine = $cfgObj.machine }
            } catch { }
        }
        Deploy-Shortcuts -Machine $updateMachine
        Deploy-PsmuxConfig
        if ($RepoDir) { Deploy-GitHooksPath }
        Deploy-CopilotPlugin
        Ensure-CopilotExperimental

        # Deploy machine.instructions.md + AGENTS.md from machines.yaml
        if ($RepoDir) {
            try {
                $env:PYTHONUTF8 = '1'
                $env:PYTHONPATH = $LibDir
                $env:WORKTREE_PROJECT = $ProjectName
                & $VenvPython -m agent_worktrees deploy-instructions --machine $updateMachine 2>&1 | ForEach-Object { Write-Host "  $_" }
            } catch {
                Write-ServiceWarn "Instruction file deployment skipped: $_"
            }
        } else {
            Write-ServiceSkipped "Instruction deployment skipped (no repo detected)"
        }

        Write-DeployManifest -InstallDir $InstallDir -ServiceName 'worktree-sessions' `
            -SourcePaths $DeploySourcePaths -InstallerPath $InstallerRelPath

        # Add runtime + plugin_source fields to manifest
        $manifestPath = Join-Path $InstallDir 'deploy-manifest.json'
        $m = Get-Content $manifestPath -Raw | ConvertFrom-Json
        $m | Add-Member -NotePropertyName 'runtime' -NotePropertyValue 'python' -Force
        $m | Add-Member -NotePropertyName 'plugin_source' -NotePropertyValue $PluginDir.ToString() -Force
        $m | ConvertTo-Json -Depth 4 | Set-Content $manifestPath -Encoding UTF8

        Write-ServiceOk "Update complete"
    }
}
