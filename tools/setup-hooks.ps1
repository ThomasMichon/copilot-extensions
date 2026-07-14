<#
.SYNOPSIS
    Wire this checkout's git to the tracked hooks under tools/hooks.
.DESCRIPTION
    Points core.hooksPath at tools/hooks so the pre-commit / pre-push guards
    run. Safe to re-run; will not overwrite a non-standard core.hooksPath.
    Git does not auto-enable a committed hooks dir, so run this once per clone
    (and per worktree host that lacks it).
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

$current = (git config --local core.hooksPath 2>$null)
if ($current -eq 'tools/hooks') {
    Write-Host 'core.hooksPath already = tools/hooks'
    exit 0
}
if ($current) {
    Write-Warning "core.hooksPath already set to '$current' -- not overwriting."
    Write-Host '  To force: git config --local core.hooksPath tools/hooks'
    exit 0
}
git config --local core.hooksPath tools/hooks
Write-Host 'Set core.hooksPath = tools/hooks (pre-commit + pre-push guards active).'
