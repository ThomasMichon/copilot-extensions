<#
.SYNOPSIS
    Default session setup script for repos without their own.

.DESCRIPTION
    Used by agent-worktrees when the anchor repo does not provide a
    tools/setup/setup.ps1.  Sets basic environment variables, displays
    a brief welcome banner, and launches the Copilot CLI.

    The launcher (launch-session.ps1) sets the working directory before
    calling this script. Context (project) resolves from CWD, git-like --
    no ambient WORKTREE_PROJECT is required.
#>
[CmdletBinding()]
param(
    [string]$Machine = $env:COMPUTERNAME,
    [switch]$Recovery,
    [Parameter(ValueFromRemainingArguments)]
    [string[]]$CopilotArgs
)

$ErrorActionPreference = 'Stop'

# ── Environment ──────────────────────────────────────────────────────────
# Resolve the project from CWD (git-like); fall back to the directory name if
# the CLI is unavailable (e.g. recovery mode).
$project = (agent-worktrees get project 2>$null | Select-Object -First 1)
if (-not $project) { $project = Split-Path -Leaf $PWD }
$env:WORKTREE_MACHINE = $Machine

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
