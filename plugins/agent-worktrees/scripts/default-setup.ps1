<#
.SYNOPSIS
    Default / normalized session setup script for repos.

.DESCRIPTION
    Used by agent-worktrees as the normalized launcher. Prepends any
    repo-provided session PATH directories, runs an optional repo setup hook
    (vault / MCP; context passed by argument, not ambient env), displays a
    brief welcome banner, and launches the Copilot CLI.

    A repo opts into this normalized flow by declaring a ``setup_hook`` in its
    ``.agent-worktrees/config.yaml``. When absent, this script is still used as
    the fallback launcher for repos without their own
    ``tools/setup/setup.ps1``.

    The launcher (launch-session.ps1) sets the working directory before
    calling this script. Context (project) resolves from CWD, git-like --
    no ambient WORKTREE_PROJECT is required.
#>
[CmdletBinding()]
param(
    [string]$Machine = $env:COMPUTERNAME,
    [switch]$Recovery,
    # Path to an optional repo setup hook (.ps1). Run before Copilot launches
    # (skipped in -Recovery). Receives -Machine; self-resolves paths via
    # `agent-worktrees get`. It must NOT launch Copilot itself.
    [string]$SetupHook,
    # OS-path-separator-joined directories to prepend to PATH before launch.
    [string]$SessionPath,
    # Path to an optional repo environment-priming script (.bat/.cmd/.ps1).
    # UNLIKE -SetupHook (a child process whose env is discarded), this script's
    # resulting environment is captured and imported into THIS process so the
    # Copilot launched below inherits it (e.g. an Office/SPO OpenEnlistment.bat
    # that sets build vars + PATH). Runs even in -Recovery -- the build env is
    # always needed.
    [string]$EnvScript,
    [Parameter(ValueFromRemainingArguments)]
    [string[]]$CopilotArgs
)

$ErrorActionPreference = 'Stop'

# ── --stdio (ACP) mode: keep human output off the JSON-RPC channel ────────
# In --stdio mode stdout is the ACP JSON-RPC stream (SSH merges Information into
# stdout), so redirect all Write-Host to stderr. The repo setup hook runs as a
# child process, so its output is redirected at invocation (below).
$script:StdioMode = ($CopilotArgs -contains '--stdio')
if ($script:StdioMode) {
    function global:Write-Host {
        param(
            [Parameter(Position = 0, ValueFromRemainingArguments)]
            [object[]]$Object,
            [switch]$NoNewline,
            [ConsoleColor]$ForegroundColor,
            [ConsoleColor]$BackgroundColor
        )
        $text = ($Object -join ' ')
        if ($NoNewline) { [Console]::Error.Write($text) } else { [Console]::Error.WriteLine($text) }
    }
}

# ── Session PATH prepend (generic; repo-provided dirs) ───────────────────
if ($SessionPath) {
    $dirs = $SessionPath.Split([IO.Path]::PathSeparator) | Where-Object { $_ }
    if ($dirs) {
        $env:PATH = ($dirs -join [IO.Path]::PathSeparator) + [IO.Path]::PathSeparator + $env:PATH
    }
}

# ── Enlistment env priming (repo env_script) ─────────────────────────────
# Run the repo's env-priming script in a child cmd, snapshot the resulting
# environment, and import it into THIS process so the Copilot exec below
# inherits the build environment. This is the whole point of env_script vs a
# setup hook (whose child-process env would be lost). Runs even in recovery.
# The script's own output is silenced (`>nul 2>&1`); only the `set` dump is
# captured, so nothing leaks onto the ACP stdout channel.
if ($EnvScript) {
    if (Test-Path -LiteralPath $EnvScript) {
        Write-Host "  Env:      $EnvScript" -ForegroundColor DarkGray
        $captured = & cmd.exe /c "call `"$EnvScript`" >nul 2>&1 && set" 2>$null
        foreach ($line in $captured) {
            $eq = $line.IndexOf('=')
            if ($eq -gt 0) {
                $name = $line.Substring(0, $eq)
                $value = $line.Substring($eq + 1)
                [Environment]::SetEnvironmentVariable($name, $value, 'Process')
            }
        }
    } else {
        Write-Warning "env_script not found: $EnvScript"
    }
}

# ── Environment ──────────────────────────────────────────────────────────
# Resolve the project from CWD (git-like); fall back to the directory name if
# the CLI is unavailable (e.g. recovery mode).
$project = (agent-worktrees get project 2>$null | Select-Object -First 1)
if (-not $project) { $project = Split-Path -Leaf $PWD }
$env:WORKTREE_MACHINE = $Machine

# ── Repo setup hook (vault / MCP; repo-specific) ─────────────────────────
# Runs before launch, context passed by argument. Skipped in recovery so a
# broken hook can never lock the operator out of a recovery session. A
# non-zero exit warns but does not abort the launch.
if ($SetupHook -and -not $Recovery) {
    if (Test-Path -LiteralPath $SetupHook) {
        Write-Host "  Setup:    $SetupHook" -ForegroundColor DarkGray
        if ($script:StdioMode) {
            # Keep the hook's stdout off the ACP channel.
            & pwsh.exe -NoProfile -NoLogo -File $SetupHook -Machine $Machine 2>&1 |
                ForEach-Object { [Console]::Error.WriteLine($_) }
        } else {
            & pwsh.exe -NoProfile -NoLogo -File $SetupHook -Machine $Machine
        }
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Setup hook exited with code $LASTEXITCODE; continuing to launch."
        }
    } else {
        Write-Warning "Setup hook not found: $SetupHook"
    }
}

# ── Welcome banner ───────────────────────────────────────────────────────
$branch = git branch --show-current 2>$null
if (-not $branch) { $branch = '(detached)' }
$dirty = git status --porcelain 2>$null
$status = if ($dirty) { 'dirty' } else { 'clean' }

Write-Host ''
Write-Host "  Project:  $project" -ForegroundColor Cyan
Write-Host "  Branch:   $branch ($status)"
Write-Host "  Machine:  $Machine"
Write-Host "  Path:     $PWD"
Write-Host ''

# ── Launch Copilot ───────────────────────────────────────────────────────
$copilotCmd = Get-Command copilot -ErrorAction SilentlyContinue
if (-not $copilotCmd) {
    $ghCmd = Get-Command gh -ErrorAction SilentlyContinue
    if ($ghCmd) {
        gh copilot @CopilotArgs
    } else {
        Write-Error 'Neither copilot nor gh found on PATH.'
        exit 1
    }
} else {
    copilot @CopilotArgs
}

exit $LASTEXITCODE
