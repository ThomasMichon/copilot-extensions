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
    [Parameter(ValueFromRemainingArguments)]
    [string[]]$CopilotArgs
)

$ErrorActionPreference = 'Stop'

# ── Session PATH prepend (generic; repo-provided dirs) ───────────────────
if ($SessionPath) {
    $dirs = $SessionPath.Split([IO.Path]::PathSeparator) | Where-Object { $_ }
    if ($dirs) {
        $env:PATH = ($dirs -join [IO.Path]::PathSeparator) + [IO.Path]::PathSeparator + $env:PATH
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
        & pwsh.exe -NoProfile -NoLogo -File $SetupHook -Machine $Machine
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
