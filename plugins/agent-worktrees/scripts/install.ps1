<#
.SYNOPSIS
    Worktree Session Manager - standardized installer interface.

.DESCRIPTION
    Manages the worktree session infrastructure lifecycle: install, uninstall,
    start, stop, status, update-config, update.

    Shared runtime (venv, package, wrappers) lives at ~/.agent-worktrees/.
    Per-project config and state lives at ~/.{project}/.
    Binstubs go to ~/.local/bin/.

    Run from the repo root:
      pwsh -File plugins\agent-worktrees\scripts\install.ps1 install
      pwsh -File plugins\agent-worktrees\scripts\install.ps1 install -ProjectName my-repo
      pwsh -File plugins\agent-worktrees\scripts\install.ps1 status

.PARAMETER Action
    Lifecycle action to perform.

.PARAMETER ProjectName
    Project name (e.g. 'my-project'). Defaults to: WORKTREE_PROJECT env var,
    then inferred from existing config, then basename of CWD repo.

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

    [string]$ProjectName,

    [switch]$RemoveConfig,
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# -- Load shared utilities ------------------------------------------------

. (Join-Path $PSScriptRoot 'service-utils.ps1')

# -- Metadata -------------------------------------------------------------

$ServiceName     = 'Worktree Manager'
$InstallDir      = Join-Path $env:USERPROFILE '.agent-worktrees'
$BinDir          = Join-Path $InstallDir 'bin'
$LocalBin        = Join-Path $env:USERPROFILE '.local\bin'
$ScriptDir       = Split-Path -Parent $MyInvocation.MyCommand.Path
$PluginDir       = (Resolve-Path (Join-Path $ScriptDir '..'))
$ServiceYamlPath = Join-Path $ScriptDir 'service.yaml'

# RepoDir: detect from existing config, then CWD.
$RepoDir = $null

# Infer project name: explicit parameter > env var > existing config > basename of CWD repo
if (-not $ProjectName) { $ProjectName = $env:WORKTREE_PROJECT }
if (-not $ProjectName) {
    # Try to infer from existing config directories (find any .{name}/config.yaml)
    if ((Get-Location).Path -match '[\\/]([^\\/]+)$') {
        $cwdName = $Matches[1]
        $candidateConf = Join-Path $env:USERPROFILE ".$cwdName\config.yaml"
        if (Test-Path $candidateConf) { $ProjectName = $cwdName }
    }
}
# Don't auto-adopt the CWD repo -- project association is explicit.
# Runtime installs fine without a project name.
$HasProject = [bool]$ProjectName

if ($HasProject) {
    $ProjectDir      = Join-Path $env:USERPROFILE ".$ProjectName"
    $WorktreesDir    = Join-Path $ProjectDir 'worktrees'

    # Detect repo dir from existing project config, then CWD
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
    if (-not $RepoDir -and (Test-Path (Join-Path (Get-Location) '.git'))) {
        $RepoDir = (Get-Location).Path
    }
} else {
    $ProjectDir   = $null
    $WorktreesDir = $null
}

$DeploySourcePaths = @('plugins/agent-worktrees/')
$InstallerRelPath  = 'plugins/agent-worktrees/scripts/install.ps1'


# Python runtime paths (shared across projects)
$LibDir   = Join-Path $InstallDir 'lib'
$VenvDir  = Join-Path $InstallDir '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'

# -- Projects registry ----------------------------------------------------

$ProjectsYamlPath = Join-Path $InstallDir 'projects.yaml'

function Read-ProjectsRegistry {
    <# Read projects.yaml and return hashtable. Returns empty projects hash if file missing. #>
    if (-not (Test-Path $ProjectsYamlPath)) {
        return @{ projects = @{} }
    }
    if (-not (Test-Path $VenvPython)) {
        # Can't parse YAML without Python — return empty
        return @{ projects = @{} }
    }
    try {
        $raw = & $VenvPython -c "import yaml, json, sys; data = yaml.safe_load(open(sys.argv[1], encoding='utf-8')); print(json.dumps(data))" $ProjectsYamlPath 2>$null
        $parsed = $raw | ConvertFrom-Json
        if (-not $parsed.projects) { $parsed | Add-Member -NotePropertyName 'projects' -NotePropertyValue @{} -Force }
        return $parsed
    } catch {
        return @{ projects = @{} }
    }
}

function Format-YamlValue {
    <# Format a scalar value for YAML output. #>
    param([object]$Val)
    if ($null -eq $Val) { return 'null' }
    if ($Val -is [bool]) { return if ($Val) { 'true' } else { 'false' } }
    if ($Val -is [string]) { return "`"$($Val -replace '\\', '\\')`"" }
    return "$Val"
}

function Write-YamlFields {
    <# Write fields of a dict/PSCustomObject at a given indent depth. #>
    param([object]$Entry, [int]$Indent = 4)
    $pad = ' ' * $Indent
    $fields = if ($Entry -is [hashtable]) {
        $Entry.GetEnumerator() | Sort-Object Name | ForEach-Object { [PSCustomObject]@{ Name = $_.Key; Value = $_.Value } }
    } elseif ($Entry -is [PSCustomObject]) {
        $Entry.PSObject.Properties
    } else { @() }

    $result = @()
    foreach ($field in $fields) {
        $val = $field.Value
        if ($val -is [hashtable] -or $val -is [PSCustomObject]) {
            $result += "${pad}$($field.Name):"
            $result += Write-YamlFields -Entry $val -Indent ($Indent + 2)
        } else {
            $result += "${pad}$($field.Name): $(Format-YamlValue $val)"
        }
    }
    return $result
}

function Write-ProjectsRegistry {
    <# Write the projects registry back to projects.yaml. #>
    param([object]$Registry)
    if (-not (Test-Path $VenvPython)) { return }
    Ensure-InstallDir (Split-Path $ProjectsYamlPath)

    # Build YAML content manually (simple structure, avoid Python dependency for writing)
    $lines = @("# ~/.agent-worktrees/projects.yaml", "# Registry of adopted repos for terminal profile generation.", "", "projects:")
    $projects = $Registry.projects
    if ($projects -is [PSCustomObject]) {
        foreach ($prop in $projects.PSObject.Properties) {
            $lines += "  $($prop.Name):"
            $lines += Write-YamlFields -Entry $prop.Value -Indent 4
        }
    } elseif ($projects -is [hashtable]) {
        foreach ($name in ($projects.Keys | Sort-Object)) {
            $lines += "  ${name}:"
            $lines += Write-YamlFields -Entry $projects[$name] -Indent 4
        }
    }
    $content = ($lines -join "`n") + "`n"
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($ProjectsYamlPath, $content, $utf8NoBom)
}

function Register-ProjectEntry {
    <# Add or update this project in the projects registry.
       Preserves existing WSL state when re-registering from Windows. #>
    $registry = Read-ProjectsRegistry

    $entry = @{
        config_dir     = "~/.${ProjectName}"
        anchor         = if ($RepoDir) { $RepoDir } else { '' }
        machines_yaml  = if ($RepoDir -and (Test-Path (Join-Path $RepoDir 'machines.yaml'))) { (Join-Path $RepoDir 'machines.yaml') } else { $null }
        default_branch = 'master'
        registered_at  = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    }

    # Try to read default_branch from existing config
    $cfgPath = Join-Path $ProjectDir 'config.yaml'
    if (Test-Path $cfgPath) {
        $cfgRaw = Get-Content $cfgPath -Raw
        if ($cfgRaw -match 'default_branch:\s*(\S+)') {
            $entry['default_branch'] = $Matches[1]
        }
    }

    # Preserve existing WSL state from previous registration
    $existingWsl = $null
    if ($registry.projects -is [PSCustomObject] -and $registry.projects.PSObject.Properties[$ProjectName]) {
        $existing = $registry.projects.$ProjectName
        if ($existing.PSObject.Properties['wsl'] -and $existing.wsl) {
            $existingWsl = $existing.wsl
        }
    } elseif ($registry.projects -is [hashtable] -and $registry.projects.ContainsKey($ProjectName)) {
        $existing = $registry.projects[$ProjectName]
        if ($existing -is [PSCustomObject] -and $existing.PSObject.Properties['wsl'] -and $existing.wsl) {
            $existingWsl = $existing.wsl
        }
    }
    if ($existingWsl) {
        $entry['wsl'] = $existingWsl
    }

    # Upsert into registry
    if ($registry.projects -is [PSCustomObject]) {
        # Convert to hashtable for mutation
        $ht = @{}
        foreach ($p in $registry.projects.PSObject.Properties) { $ht[$p.Name] = $p.Value }
        $ht[$ProjectName] = [PSCustomObject]$entry
        $registry = @{ projects = $ht }
    } elseif ($registry.projects -is [hashtable]) {
        $registry.projects[$ProjectName] = [PSCustomObject]$entry
    } else {
        $registry = @{ projects = @{ $ProjectName = [PSCustomObject]$entry } }
    }

    Write-ProjectsRegistry $registry
    Write-ServiceOk "Project '$ProjectName' registered in projects.yaml"
}

# -- Machine detection ----------------------------------------------------

$HostnameMap = @{
    # Add entries here if COMPUTERNAME differs from desired machine name.
    # If empty, the lowercase hostname is used as-is.
}

function Resolve-Machine {
    $hostname = $env:COMPUTERNAME
    if ($HostnameMap.ContainsKey($hostname)) {
        return $HostnameMap[$hostname]
    }
    # Unknown machine -- use lowercase hostname as machine name
    return $hostname.ToLower()
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
    $ver = '0.0.0'
    $pyproj = Join-Path $PluginDir 'pyproject.toml'
    if (Test-Path $pyproj) {
        $verLine = Select-String -Path $pyproj -Pattern '^\s*version\s*=' | Select-Object -First 1
        if ($verLine) { $ver = ($verLine.Line -replace '.*=\s*"([^"]+)".*','$1') }
    }
    $buildContent = @"
`"`"`"Build provenance -- auto-generated at deploy time. Do not edit.`"`"`"

from __future__ import annotations

BUILD_INFO: dict[str, str] = {
    "version": "$ver",
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

    # Deploy sessionStart hook scripts (bootstrap-check + project-hooks)
    foreach ($script in @('bootstrap-check.ps1', 'bootstrap-check.sh', 'project-hooks.ps1', 'project-hooks.sh')) {
        $src = Join-Path $ScriptDir $script
        $dst = Join-Path $BinDir $script
        if (Test-Path $src) {
            Copy-Item $src $dst -Force
            Write-ServiceOk "Hook: $script"
        }
    }

    return $true
}

function Deploy-Binstub {
    <# Generate the project-specific binstub in ~/.local/bin/.
       Routes through the Python CLI for subcommand dispatch.
       Falls back to launch-session.cmd if the venv is missing. #>
    Ensure-InstallDir $LocalBin

    $content = @"
@echo off
set "WORKTREE_PROJECT=$ProjectName"
set "_PY=%USERPROFILE%\.agent-worktrees\.venv\Scripts\python.exe"
if exist "%_PY%" (
    set "PYTHONPATH=%USERPROFILE%\.agent-worktrees\lib"
    set "PYTHONUTF8=1"
    "%_PY%" -m agent_worktrees %*
    exit /b %ERRORLEVEL%
)
rem Fallback: launch session directly (venv missing / recovery)
"%USERPROFILE%\.agent-worktrees\bin\launch-session.cmd" %*
exit /b %ERRORLEVEL%
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

# Helper: check if a WSL binstub actually exists on disk
function Test-WslBinstubExists {
    param(
        [string]$Name,
        [string]$Distro
    )
    try {
        if ($Distro) {
            & wsl.exe -d $Distro -- bash -c 'test -x "$HOME/.local/bin/$1"' _ $Name 2>$null
        } else {
            & wsl.exe -- bash -c 'test -x "$HOME/.local/bin/$1"' _ $Name 2>$null
        }
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

# Helper: detect the default WSL distro name
function Get-WslDefaultDistro {
    try {
        $line = & wsl.exe -l -q 2>&1 | Where-Object { $_ -match '\S' } | Select-Object -First 1
        $name = ($line -replace "`0", '').Trim()
        if ($name) { return $name }
    } catch {}
    return $null
}

function Build-TerminalFragment {
    <# Generate a Windows Terminal fragment JSON with local + remote SSH profiles
       for ALL registered projects in projects.yaml. #>
    param([string]$Machine)

    $profiles = @()

    # Helper: generate stable GUID from a seed string
    function New-StableGuid {
        param([string]$Seed)
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Seed)
        $hash = [System.Security.Cryptography.SHA256]::Create().ComputeHash($bytes)
        return [guid]::new(
            [BitConverter]::ToInt32($hash, 0),
            [BitConverter]::ToInt16($hash, 4),
            [BitConverter]::ToInt16($hash, 6),
            $hash[8], $hash[9], $hash[10], $hash[11],
            $hash[12], $hash[13], $hash[14], $hash[15]
        )
    }

    # Helper: title-case a slug ("my-project" -> "My Project")
    function Get-DisplayName {
        param([string]$Slug)
        return ($Slug -replace '-', ' ') -replace '(^| )(.)', { $_.Value.ToUpper() }
    }

    # Collect projects: start with current project, then add from registry
    $projectList = @()
    $registry = Read-ProjectsRegistry

    # Helper: extract WSL info from a registry entry
    function Get-WslInfo {
        param([object]$Entry)
        $wsl = $null
        if ($Entry -is [PSCustomObject] -and $Entry.PSObject.Properties['wsl']) {
            $wsl = $Entry.wsl
        } elseif ($Entry -is [hashtable] -and $Entry.ContainsKey('wsl')) {
            $wsl = $Entry['wsl']
        }
        if (-not $wsl) { return $null }
        $state = $null; $distro = $null
        if ($wsl -is [PSCustomObject]) {
            if ($wsl.PSObject.Properties['state']) { $state = $wsl.state }
            if ($wsl.PSObject.Properties['distro']) { $distro = $wsl.distro }
        } elseif ($wsl -is [hashtable]) {
            $state = $wsl['state']; $distro = $wsl['distro']
        }
        return @{ state = $state; distro = $distro }
    }

    # Ensure current project is always included (even if not yet in registry)
    $currentRegEntry = $null
    if ($registry.projects -is [PSCustomObject] -and $registry.projects.PSObject.Properties[$ProjectName]) {
        $currentRegEntry = $registry.projects.$ProjectName
    } elseif ($registry.projects -is [hashtable] -and $registry.projects.ContainsKey($ProjectName)) {
        $currentRegEntry = $registry.projects[$ProjectName]
    }
    $currentEntry = @{
        name          = $ProjectName
        anchor        = $RepoDir
        machines_yaml = if ($RepoDir -and (Test-Path (Join-Path $RepoDir 'machines.yaml'))) { Join-Path $RepoDir 'machines.yaml' } else { $null }
        wsl_info      = if ($currentRegEntry) { Get-WslInfo $currentRegEntry } else { $null }
    }
    $projectList += [PSCustomObject]$currentEntry

    # Add other registered projects
    $registeredNames = @($ProjectName)
    if ($registry.projects) {
        $projObj = $registry.projects
        $propList = if ($projObj -is [PSCustomObject]) { $projObj.PSObject.Properties } else { @() }
        foreach ($prop in $propList) {
            if ($prop.Name -in $registeredNames) { continue }
            $registeredNames += $prop.Name
            $e = $prop.Value
            $anchor = if ($e.PSObject.Properties['anchor']) { $e.anchor } else { $null }
            $my = if ($e.PSObject.Properties['machines_yaml']) { $e.machines_yaml } else { $null }
            $projectList += [PSCustomObject]@{
                name          = $prop.Name
                anchor        = $anchor
                machines_yaml = $my
                wsl_info      = Get-WslInfo $e
            }
        }
    }

    # Generate profiles for each project
    foreach ($proj in $projectList) {
        $pName = $proj.name
        $pDisplay = Get-DisplayName $pName
        $pAnchor = $proj.anchor
        $pMachinesYaml = $proj.machines_yaml
        $pWslInfo = $proj.wsl_info

        # Icon: prefer project-specific, fall back to agent-worktrees default
        $iconPath = "%USERPROFILE%\.${pName}\aperture-science.ico"
        if (-not (Test-Path (Join-Path $env:USERPROFILE ".$pName\aperture-science.ico"))) {
            $iconPath = "%USERPROFILE%\.agent-worktrees\aperture-science.ico"
        }

        # Local Windows profile
        $guid = New-StableGuid "${pName}-local-windows"
        $profiles += @{
            guid              = "{$guid}"
            name              = $pDisplay
            commandline       = "cmd /c `"%USERPROFILE%\.local\bin\${pName}.cmd`""
            icon              = $iconPath
            startingDirectory = "%USERPROFILE%"
            colorScheme       = 'Aperture Science'
            hidden            = $false
        }

        # Local WSL profile — only when the binstub actually exists in WSL
        $wslDistro = if ($pWslInfo) { $pWslInfo['distro'] } else { Get-WslDefaultDistro }
        if (Test-WslBinstubExists -Name $pName -Distro $wslDistro) {
            $wslIconPath = "%USERPROFILE%\.${pName}\aperture-science-wsl.ico"
            if (-not (Test-Path (Join-Path $env:USERPROFILE ".$pName\aperture-science-wsl.ico"))) {
                $wslIconPath = $iconPath
            }
            # Use distro-specific invocation when known
            $wslCmd = if ($wslDistro) {
                "wsl.exe -d $wslDistro -- bash -lc $pName"
            } else {
                "wsl.exe bash -lc $pName"
            }
            $wslLabel = "$pDisplay (WSL)"

            $guid = New-StableGuid "${pName}-local-wsl"
            $profiles += @{
                guid              = "{$guid}"
                name              = $wslLabel
                commandline       = $wslCmd
                icon              = $wslIconPath
                startingDirectory = "%USERPROFILE%"
                colorScheme       = 'Aperture Science'
                hidden            = $false
            }
        }

        # SSH profiles from this project's machines.yaml
        if ($pMachinesYaml -and (Test-Path $pMachinesYaml)) {
            try {
                $raw = & $VenvPython -c "import yaml, json, sys; data = yaml.safe_load(open(sys.argv[1], encoding='utf-8')); print(json.dumps(data))" $pMachinesYaml 2>$null
                $machinesData = $raw | ConvertFrom-Json
                if ($machinesData.machines) {
                    foreach ($mProp in $machinesData.machines.PSObject.Properties) {
                        $key = $mProp.Name
                        $mEntry = $mProp.Value
                        if ($key -eq $Machine) { continue }  # skip self
                        if (-not $mEntry.ssh -or -not $mEntry.ssh.ready) { continue }

                        foreach ($sshEnv in $mEntry.ssh.environments) {
                            $alias = $sshEnv.alias
                            $envLabel = switch ($sshEnv.name) {
                                'windows' { 'Windows' }
                                'wsl'     { 'WSL' }
                                'linux'   { 'Linux' }
                                default   { $sshEnv.name }
                            }

                            # Plain SSH profile
                            $sshGuid = New-StableGuid "${pName}-ssh-${key}-$($sshEnv.name)"
                            $profileName = if ($envLabel -eq 'WSL') { "$($mEntry.display_name) (WSL)" } else { $mEntry.display_name }
                            $profiles += @{
                                guid              = "{$sshGuid}"
                                name              = $profileName
                                commandline       = "ssh $alias"
                                icon              = $iconPath
                                startingDirectory = "%USERPROFILE%"
                                colorScheme       = 'Aperture Science'
                                hidden            = $false
                            }

                            # Launch-via-SSH profile
                            $binstubCmd = if ($sshEnv.shell -eq 'pwsh') { "${pName}.cmd" } else { $pName }
                            $launchCmdline = "ssh -t $alias $binstubCmd"
                            $launchLabel = if ($envLabel -eq 'WSL') { "$($mEntry.display_name) WSL" } else { $mEntry.display_name }
                            $launchProfileName = "$pDisplay ($launchLabel)"

                            $launchGuid = New-StableGuid "${pName}-launch-${key}-$($sshEnv.name)"
                            $profiles += @{
                                guid              = "{$launchGuid}"
                                name              = $launchProfileName
                                commandline       = $launchCmdline
                                icon              = $iconPath
                                startingDirectory = "%USERPROFILE%"
                                colorScheme       = 'Aperture Science'
                                hidden            = $false
                            }
                        }
                    }
                }
            } catch {
                Write-ServiceWarn "Could not parse machines.yaml for '$pName' terminal profiles: $_"
            }
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

function Sync-TerminalState {
    <# Synchronize WT settings.json and state.json after a fragment regeneration.

       When the fragment changes, two WT state files need cleanup:

       1. settings.json -- cached fragment-sourced profiles with stale GUIDs
          must be removed so they don't persist as ghost entries.

       2. state.json -- the generatedProfiles array tracks every profile GUID
          WT has ever seen from fragments.  If a GUID is in generatedProfiles
          but absent from both the fragment and settings.json, WT interprets
          this as "user intentionally deleted this profile" and hides it.
          We must remove stale GUIDs and newly-added GUIDs from this list so
          WT rediscovers them fresh on next launch. #>
    param(
        [string[]]$OldFragmentGuids = @(),
        [string[]]$NewFragmentGuids = @()
    )

    # --- state.json: generatedProfiles ---
    $statePath = Join-Path $env:LOCALAPPDATA 'Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\state.json'
    if (Test-Path $statePath) {
        try {
            $state = Get-Content $statePath -Raw | ConvertFrom-Json
            if ($state.generatedProfiles) {
                $genProfiles = @($state.generatedProfiles)
                $before = $genProfiles.Count

                # GUIDs to remove: stale (old but not new) + newly added (new but not old).
                # Unchanged GUIDs (in both) stay, preserving any user profile customizations.
                $staleGuids = @($OldFragmentGuids | Where-Object { $_ -notin $NewFragmentGuids })
                $newlyAdded = @($NewFragmentGuids | Where-Object { $_ -notin $OldFragmentGuids })
                $removeSet  = @(@($staleGuids) + @($newlyAdded) | Sort-Object -Unique)

                if ($removeSet.Count -gt 0) {
                    $state.generatedProfiles = @($genProfiles | Where-Object {
                        $_.ToLower() -notin $removeSet
                    })
                    $after = @($state.generatedProfiles).Count
                    if ($after -ne $before) {
                        $state | ConvertTo-Json -Depth 10 | Set-Content $statePath -Encoding UTF8
                        Write-ServiceChanged "Cleaned $($before - $after) GUID(s) from WT state.json generatedProfiles"
                    }
                }
            }
        } catch {
            Write-ServiceWarn "Could not update WT state.json: $_"
        }
    }

    # --- settings.json: stale cached profiles ---
    Clean-TerminalSettingsJson -NewFragmentGuids $NewFragmentGuids
}

function Clean-TerminalSettingsJson {
    <# Remove stale profiles and schemes from WT settings.json.

       Removes AgentWorktrees-sourced profiles whose GUID is not in the
       current fragment (stale from previous installs or unregistered
       projects). #>
    param(
        [string[]]$NewFragmentGuids = @()
    )

    $settingsPath = Join-Path $env:LOCALAPPDATA 'Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json'
    if (-not (Test-Path $settingsPath)) { return }

    try {
        $raw = Get-Content $settingsPath -Raw -ErrorAction Stop
        $json = $raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        Write-ServiceWarn "Could not parse WT settings.json for cleanup: $_"
        return
    }

    # If no GUIDs were passed, read them from the fragment on disk
    if ($NewFragmentGuids.Count -eq 0) {
        $fragmentPath = Join-Path $env:LOCALAPPDATA 'Microsoft\Windows Terminal\Fragments\AgentWorktrees\agent-worktrees.json'
        if (Test-Path $fragmentPath) {
            try {
                $frag = Get-Content $fragmentPath -Raw | ConvertFrom-Json
                $NewFragmentGuids = @($frag.profiles | ForEach-Object { $_.guid.ToLower() })
            } catch { }
        }
    }

    $changed = $false

    if ($json.profiles -and $json.profiles.list) {
        $before = $json.profiles.list.Count
        $json.profiles.list = @($json.profiles.list | Where-Object {
            if (-not $_.PSObject.Properties['source']) {
                # Manually-added (no source) -- remove if GUID matches current fragment
                $isOurs = ($_.PSObject.Properties['guid'] -and $_.guid.ToLower() -in $NewFragmentGuids)
                return -not $isOurs
            }

            # AgentWorktrees-sourced: remove if GUID is not in the current fragment
            if ($_.source -eq 'AgentWorktrees') {
                if ($_.PSObject.Properties['guid']) {
                    return ($_.guid.ToLower() -in $NewFragmentGuids)
                }
                return $false  # no GUID = orphan, remove
            }

            return $true
        })
        $removed = $before - $json.profiles.list.Count
        if ($removed -gt 0) {
            $changed = $true
            Write-ServiceChanged "Removed $removed stale profile(s) from WT settings.json"
        }
    }

    if ($changed) {
        $backup = "$settingsPath.wt-backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        Copy-Item $settingsPath $backup -Force
        $json | ConvertTo-Json -Depth 20 | Set-Content $settingsPath -Encoding UTF8
        Write-ServiceOk "WT settings.json cleaned (backup: $backup)"
    }
}

function Deploy-WslBinstub {
    <# Deploy a thin project binstub into WSL's ~/.local/bin/.
       The binstub launches via the agent-worktrees Python CLI if installed,
       or prints setup instructions if not.  Returns $true if deployed,
       $false if WSL is unavailable or deployment failed. #>

    # Check WSL availability
    try {
        $wslStatus = & wsl.exe --status 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-ServiceWarn "WSL not available - skipping binstub"
            return $false
        }
    } catch {
        Write-ServiceWarn "WSL not available - skipping binstub"
        return $false
    }

    # Detect default distro
    $distro = ''
    try {
        $distroLine = & wsl.exe -l -q 2>&1 | Where-Object { $_ -match '\S' } | Select-Object -First 1
        $distro = ($distroLine -replace "`0", '').Trim()
    } catch {}
    if (-not $distro) {
        Write-ServiceWarn "No WSL distro found - skipping binstub"
        return $false
    }

    # Generate thin launcher with helpful error when not yet installed
    $binstubScript = @"
#!/usr/bin/env bash
# Thin binstub for $ProjectName - deployed by agent-worktrees (Windows)
# Requires agent-worktrees to be installed in WSL via the copilot-extensions plugin.
export WORKTREE_PROJECT="$ProjectName"
_launcher="`$HOME/.agent-worktrees/bin/launch-session.sh"
if [[ -x "`$_launcher" ]]; then
    exec "`$_launcher" "`$@"
else
    echo "agent-worktrees is not installed in WSL." >&2
    echo "To set up:" >&2
    echo "  1. Install the copilot-extensions plugin in WSL" >&2
    echo "  2. Run: agent-worktrees install --project-name $ProjectName" >&2
    exit 1
fi
"@

    # Deploy to WSL via base64 to avoid quoting issues
    try {
        & wsl.exe -d $distro -- bash -c 'mkdir -p "$HOME/.local/bin"' 2>$null

        $cleanScript = $binstubScript -replace "`r", ""
        $b64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($cleanScript))
        & wsl.exe -d $distro -- bash -c 'echo "$1" | base64 -d > "$HOME/.local/bin/$2" && chmod +x "$HOME/.local/bin/$2"' _ $b64 $ProjectName

        if ($LASTEXITCODE -eq 0) {
            Write-ServiceOk "WSL binstub deployed to ~/.local/bin/$ProjectName ($distro)"

            # Record distro in projects registry (metadata only, not used for gating)
            $registry = Read-ProjectsRegistry
            $projEntry = $null
            if ($registry.projects -is [PSCustomObject] -and $registry.projects.PSObject.Properties[$ProjectName]) {
                $projEntry = $registry.projects.$ProjectName
            } elseif ($registry.projects -is [hashtable] -and $registry.projects.ContainsKey($ProjectName)) {
                $projEntry = $registry.projects[$ProjectName]
            }
            if ($projEntry) {
                $wslBlock = @{ distro = $distro }
                if ($projEntry -is [PSCustomObject]) {
                    $projEntry | Add-Member -NotePropertyName 'wsl' -NotePropertyValue ([PSCustomObject]$wslBlock) -Force
                } elseif ($projEntry -is [hashtable]) {
                    $projEntry['wsl'] = [PSCustomObject]$wslBlock
                }
                Write-ProjectsRegistry $registry
            }

            return $true
        } else {
            Write-ServiceWarn "Failed to deploy WSL binstub"
            return $false
        }
    } catch {
        Write-ServiceWarn "Failed to deploy WSL binstub: $_"
        return $false
    }
}

function Deploy-Shortcuts {
    <# Deploy Windows Terminal fragment (with remote SSH profiles) and create .lnk shortcuts.
       Handles WT state cleanup so new/changed profiles appear correctly on next WT launch. #>
    param([string]$Machine)

    # Deploy WT fragment - use a shared fragment directory for all projects
    $fragmentDir = Join-Path $env:LOCALAPPDATA 'Microsoft\Windows Terminal\Fragments\AgentWorktrees'
    if (-not (Test-Path $fragmentDir)) {
        New-Item -ItemType Directory -Path $fragmentDir -Force | Out-Null
    }

    # Collect GUIDs from existing fragment BEFORE any overwrites.
    # We need these to compute stale GUIDs for state cleanup later.
    $oldFragGuids = @()
    $fragmentDst = Join-Path $fragmentDir 'agent-worktrees.json'
    if (Test-Path $fragmentDst) {
        try {
            $oldFrag = Get-Content $fragmentDst -Raw | ConvertFrom-Json
            $oldFragGuids += @($oldFrag.profiles | ForEach-Object { $_.guid.ToLower() })
        } catch { }
    }
    $oldFragGuids = @($oldFragGuids | Sort-Object -Unique)

    # Generate the fragment dynamically from projects.yaml + machines.yaml
    $fragment = Build-TerminalFragment -Machine $Machine
    $newFragObj = $fragment | ConvertFrom-Json
    $newFragGuids = @($newFragObj.profiles | ForEach-Object { $_.guid.ToLower() })

    # Clean WT state BEFORE writing the new fragment to avoid a race where
    # WT reads the new fragment while stale GUIDs are still in state.json.
    Sync-TerminalState -OldFragmentGuids $oldFragGuids -NewFragmentGuids $newFragGuids

    # Write the new fragment
    $fragment | Set-Content $fragmentDst -Encoding UTF8
    Write-ServiceOk "Windows Terminal profiles deployed (fragment with all registered projects)"

    # Create .lnk shortcuts for each registered project
    $shell = New-Object -ComObject WScript.Shell
    $wtExe = "$env:LOCALAPPDATA\Microsoft\WindowsApps\wt.exe"

    $registry = Read-ProjectsRegistry
    $allProjects = @($ProjectName)
    if ($registry.projects -is [PSCustomObject]) {
        foreach ($p in $registry.projects.PSObject.Properties) {
            if ($p.Name -notin $allProjects) { $allProjects += $p.Name }
        }
    } elseif ($registry.projects -is [hashtable]) {
        foreach ($p in $registry.projects.Keys) {
            if ($p -notin $allProjects) { $allProjects += $p }
        }
    }

    foreach ($proj in $allProjects) {
        $displayName = ($proj -replace '-', ' ') -replace '(^| )(.)', { $_.Value.ToUpper() }

        $lnkPath = Join-Path $LocalBin "$displayName.lnk"
        $lnk = $shell.CreateShortcut($lnkPath)
        $lnk.TargetPath = $wtExe
        $lnk.Arguments = "-p `"$displayName`""
        $lnk.WorkingDirectory = "%USERPROFILE%"
        $lnk.Description = "$displayName - Worktree Session Manager"
        $lnk.IconLocation = "$InstallDir\aperture-science.ico, 0"
        $lnk.Save()

        # WSL shortcut — only when the binstub actually exists in WSL
        $projWslInfo = $null
        if ($registry.projects -is [PSCustomObject] -and $registry.projects.PSObject.Properties[$proj]) {
            $projEntry = $registry.projects.$proj
            if ($projEntry.PSObject.Properties['wsl'] -and $projEntry.wsl) {
                $projWslInfo = $projEntry.wsl
            }
        }
        $shortcutWslDistro = if ($projWslInfo -is [PSCustomObject] -and $projWslInfo.PSObject.Properties['distro']) { $projWslInfo.distro } else { $null }
        if (Test-WslBinstubExists -Name $proj -Distro $shortcutWslDistro) {
            $wslLabel = "$displayName (WSL)"
            $lnkPath = Join-Path $LocalBin "$wslLabel.lnk"
            $lnk = $shell.CreateShortcut($lnkPath)
            $lnk.TargetPath = $wtExe
            $lnk.Arguments = "-p `"$wslLabel`""
            $lnk.WorkingDirectory = "%USERPROFILE%"
            $lnk.Description = "$displayName - Worktree Session Manager (WSL)"
            $lnk.IconLocation = "$InstallDir\aperture-science-wsl.ico, 0"
            $lnk.Save()
        } else {
            # Remove stale WSL shortcut if it exists from a previous install
            foreach ($pattern in @("$displayName (WSL).lnk", "$displayName (WSL: *).lnk")) {
                Get-ChildItem -Path $LocalBin -Filter $pattern -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
            }
        }
    }

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
    <# Install agent-worktrees from the copilot-extensions marketplace.
       Ensures the marketplace is registered, installs or updates the plugin,
       then removes any stale _direct install.

       When the installer itself is running from the installed-plugins
       directory (i.e. invoked by cmd_update after it already ran
       'copilot plugin update'), skip the update call to avoid EBUSY
       errors from trying to replace files in our own working directory. #>

    if (-not (Get-Command copilot -ErrorAction SilentlyContinue)) {
        Write-ServiceWarn "Copilot CLI not found - skipping plugin install"
        return
    }

    # Detect if we are running from the installed plugin directory.
    # When cmd_update invokes us, it sets cwd to the plugin dir and
    # has already done the plugin update — re-running it would EBUSY
    # on Windows because copilot CLI tries to rmdir our own cwd.
    $installedPluginsDir = Join-Path $env:USERPROFILE '.copilot\installed-plugins'
    $runningFromInstalled = $PluginDir.Path -like "$installedPluginsDir*"

    # 1. Register marketplace if not present
    $marketplaces = (copilot plugin marketplace list 2>$null) -join "`n"
    if ($marketplaces -notmatch 'copilot-extensions') {
        $addOut = copilot plugin marketplace add ThomasMichon/copilot-extensions 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-ServiceWarn "Failed to register marketplace: $addOut"
            return
        }
        Write-ServiceChanged "Registered copilot-extensions marketplace"
    }

    # 2. Parse current plugin state
    $pluginList = copilot plugin list 2>$null
    $hasMarketplace = $false
    $hasDirect = $false
    foreach ($line in $pluginList) {
        if ($line -match 'agent-worktrees@copilot-extensions') {
            $hasMarketplace = $true
        } elseif ($line -match 'agent-worktrees' -and $line -notmatch '@') {
            $hasDirect = $true
        }
    }

    # 3. Install or update marketplace plugin
    if ($runningFromInstalled) {
        Write-ServiceOk "Copilot plugin updated (marketplace)"
    } elseif ($hasMarketplace) {
        $out = copilot plugin update agent-worktrees@copilot-extensions 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-ServiceWarn "Plugin update failed: $out"
        } else {
            Write-ServiceOk "Copilot plugin updated (marketplace)"
        }
    } else {
        $out = copilot plugin install agent-worktrees@copilot-extensions 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-ServiceWarn "Plugin install failed: $out"
            return
        }
        Write-ServiceChanged "Copilot plugin installed (agent-worktrees@copilot-extensions)"
    }

    # 4. Remove stale _direct install if marketplace is now present
    if ($hasDirect) {
        $verify = (copilot plugin list 2>$null) -join "`n"
        if ($verify -match 'agent-worktrees@copilot-extensions') {
            copilot plugin uninstall agent-worktrees 2>$null | Out-Null
            Write-ServiceChanged "Removed stale _direct plugin install"
        }
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

        $machine = Resolve-Machine
        Write-Host "  Machine: $machine"
        if ($HasProject) {
            Write-Host "  Project: $ProjectName"
            if ($RepoDir) { Write-Host "  Repo:    $RepoDir" }
        } else {
            Write-Host "  Project: (none - runtime only; pass -ProjectName to adopt a repo)"
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

        # Create directory structure (runtime dirs always; project dirs only if adopting)
        $runtimeDirs = @($InstallDir, $BinDir, $LocalBin)
        if ($HasProject) { $runtimeDirs += @($ProjectDir, $WorktreesDir) }
        foreach ($dir in $runtimeDirs) {
            Ensure-InstallDir $dir
        }

        # -- Shared runtime --
        if (-not (Deploy-Package)) { exit 1 }
        if (-not (Deploy-Venv)) { exit 1 }
        if (-not (Deploy-Wrappers)) { exit 1 }
        Deploy-CopilotPlugin
        Ensure-CopilotExperimental
        Assert-PathIncludes $LocalBin

        # -- Project-specific (only when adopting) --
        if ($HasProject) {
            Deploy-Config -Machine $machine | Out-Null
            Deploy-Binstub
            Register-ProjectEntry
            if ($RepoDir) { Deploy-Icon }
            Deploy-Shortcuts -Machine $machine
            Deploy-PsmuxConfig
            if ($RepoDir) { Deploy-GitHooksPath }

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
            }
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
        if ($HasProject) {
            Write-Host "  Project dir: $ProjectDir"
            Write-Host "  Usage:       $ProjectName"
        }
        Write-Host "  Runtime:     Python ($VenvPython)"
    }

    'uninstall' {
        Write-ServiceHeader "Uninstalling $ServiceName"

        Remove-Binstub

        # Remove Windows Terminal fragment
        $fragDir = Join-Path $env:LOCALAPPDATA 'Microsoft\Windows Terminal\Fragments\AgentWorktrees'
        if (Test-Path $fragDir) {
            Remove-Item $fragDir -Recurse -Force
            Write-ServiceChanged "Removed Windows Terminal fragment: $fragDir"
        }

        # Remove psmux config
        $psmuxConf = Join-Path $env:USERPROFILE '.psmux.conf'
        if (Test-Path $psmuxConf) {
            Remove-Item $psmuxConf -Force
            Write-ServiceChanged "Removed psmux config ($psmuxConf)"
        }

        # Remove shortcuts
        $displayName = ($ProjectName -replace '-', ' ') -replace '(^| )(.)', { $_.Value.ToUpper() }
        foreach ($lnk in @("$displayName.lnk", "$displayName (WSL).lnk")) {
            $lnkPath = Join-Path $LocalBin $lnk
            if (Test-Path $lnkPath) { Remove-Item $lnkPath -Force }
        }
        # Also remove distro-specific WSL shortcuts (e.g. "Aperture Labs (WSL: Ubuntu).lnk")
        Get-ChildItem -Path $LocalBin -Filter "$displayName (WSL: *).lnk" -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
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
        Write-ServiceSkipped "Not a daemon - invoke with: $ProjectName"
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
        $fragmentPath = Join-Path $env:LOCALAPPDATA 'Microsoft\Windows Terminal\Fragments\AgentWorktrees\agent-worktrees.json'
        if (Test-Path $fragmentPath) {
            Write-ServiceOk "Windows Terminal fragment installed"
        } else {
            Write-ServiceErr "Windows Terminal fragment missing"
        }

        # Check for stale settings.json entries
        $wtSettingsPath = Join-Path $env:LOCALAPPDATA 'Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json'
        if (Test-Path $wtSettingsPath) {
            try {
                $wtJson = Get-Content $wtSettingsPath -Raw | ConvertFrom-Json
                $fragPath = Join-Path $env:LOCALAPPDATA 'Microsoft\Windows Terminal\Fragments\AgentWorktrees\agent-worktrees.json'
                $fragGuids = @()
                if (Test-Path $fragPath) {
                    $frag = Get-Content $fragPath -Raw | ConvertFrom-Json
                    $fragGuids = @($frag.profiles | ForEach-Object { $_.guid.ToLower() })
                }
                $stale = @($wtJson.profiles.list | Where-Object {
                    $_.PSObject.Properties['source'] -and $_.source -eq 'AgentWorktrees' -and
                    $_.PSObject.Properties['guid'] -and $_.guid.ToLower() -notin $fragGuids
                })
                if ($stale.Count -gt 0) {
                    Write-ServiceWarn "WT settings.json has $($stale.Count) stale profile(s) - run 'update' to clean"
                } else {
                    Write-ServiceOk "WT settings.json clean"
                }
            } catch { }
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

        if (-not (Test-Path $BinDir)) {
            Write-ServiceErr "Not installed - run 'install' first"
            exit 1
        }

        # -- Shared runtime --
        if (-not (Deploy-Package)) { exit 1 }
        if (-not (Deploy-Venv)) { exit 1 }
        if (-not (Deploy-Wrappers)) { exit 1 }
        Deploy-CopilotPlugin
        Ensure-CopilotExperimental

        # -- Project-specific (only when a project is known) --
        if ($HasProject) {
            Deploy-Binstub
            Register-ProjectEntry
            if ($RepoDir) { Deploy-Icon }
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
            }
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
