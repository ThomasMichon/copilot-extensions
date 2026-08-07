#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Preview the Worktree Picker (and a chosen pivot/tab) from WORKTREE SOURCE, in
    an isolated sandbox -- without publishing, installing, or touching the active
    deployment.

.DESCRIPTION
    Stands up a throwaway "isolated test deployment" of the picker stack and
    captures a headless screenshot of a chosen tab:

      * a sandbox state root via the AGENT_HOME override (relocates the whole
        ~/.agent-* tree -- install dir, project dirs, pivots dir, agent-codespaces
        leases -- to a temp folder). AGENT_HOME deliberately does NOT relocate the
        real home, so gh/ssh/git auth still resolve from ~/ and live data (e.g.
        `agent-codespaces pool`) works;
      * the WORKTREE builds of the agent-* plugins on PATH (their .venv console
        scripts), so the capture exercises your uncommitted changes;
      * the shipped pivot manifests seeded into the sandbox pivots dir, so every
        contributed tab (CodeSpaces, Bridges, Containers, Tasks) appears.

    It then runs `agent-worktrees picker screenshot --pivot <tab> --wait` and
    prints (or writes) the capture. Nothing here mutates the live runtime.

.PARAMETER Pivot
    The pivot (top tab) to capture, by label (case-insensitive). Default: CodeSpaces.

.PARAMETER Format
    Capture format: text (plain grid, default), ansi (colour), or svg.

.PARAMETER Wait
    Seconds to wait for a registered pivot's background `list` to load so the
    capture shows real rows. Default: 40.

.PARAMETER Out
    Write the capture to this file instead of stdout.

.PARAMETER Project
    A project name for the picker to resolve its (sandbox, empty) worktree
    tracking dir. Any valid name works; default: preview.

.PARAMETER Live
    Capture the multi-machine SSH source instead of the local-only source.

.PARAMETER Keep
    Keep the sandbox dir (printed) instead of deleting it after capture.

.EXAMPLE
    pwsh -File scripts/preview-picker.ps1
    # captures the CodeSpaces tab from worktree source, live data, to stdout

.EXAMPLE
    pwsh -File scripts/preview-picker.ps1 -Pivot Bridges -Format ansi -Out bridges.ansi
#>
[CmdletBinding()]
param(
    [string]$Pivot = "CodeSpaces",
    [ValidateSet("text", "ansi", "svg")][string]$Format = "text",
    [double]$Wait = 40,
    [string]$Out,
    [string]$Project = "preview",
    [switch]$Live,
    [switch]$Interactive,
    [switch]$Keep
)

$ErrorActionPreference = "Stop"

# --- Locate the copilot-extensions checkout that owns this script --------------
# scripts/preview-picker.ps1 -> plugins/agent-worktrees/scripts -> repo root is 3 up.
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$awPlugin = Join-Path $repoRoot "plugins\agent-worktrees"
$csPlugin = Join-Path $repoRoot "plugins\agent-codespaces"

function Resolve-VenvScripts([string]$plugin) {
    $scripts = Join-Path $plugin ".venv\Scripts"
    $py = Join-Path $scripts "python.exe"
    if (-not (Test-Path $py)) {
        Write-Error @"
No worktree venv for '$plugin'.
Create it once so the preview runs your worktree code:
    cd '$plugin'
    uv venv .venv
    uv pip install --python .venv\Scripts\python.exe -e ".[dev]"
"@
    }
    return $scripts
}

$awScripts = Resolve-VenvScripts $awPlugin
$csScripts = Resolve-VenvScripts $csPlugin

# --- Build the isolated sandbox ------------------------------------------------
$sandbox = Join-Path ([System.IO.Path]::GetTempPath()) ("agent-picker-preview-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$pivotsDir = Join-Path $sandbox ".agent-worktrees\pivots"
New-Item -ItemType Directory -Force -Path $pivotsDir | Out-Null

# Seed every shipped pivot manifest so the full tab set renders.
$seeded = 0
Get-ChildItem (Join-Path $repoRoot "plugins\*\pivots\*.json") -ErrorAction SilentlyContinue | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $pivotsDir $_.Name) -Force
    $seeded++
}

Write-Host "Sandbox AGENT_HOME : $sandbox"
Write-Host "Worktree binaries  : $awScripts ; $csScripts"
Write-Host "Seeded pivots      : $seeded manifest(s)"
if (-not $Interactive) {
    Write-Host "Capturing pivot    : $Pivot  (format=$Format, wait=${Wait}s)"
}
Write-Host ""

if ($Interactive) {
    # Launch the DRAFT picker interactively (Textual TUI) in a NEW window, so you
    # can drive it by keyboard. Uses `picker mock` -- real data, but every
    # mutating action is simulated (no side effects). The window's env is scoped
    # to the sandbox + worktree binaries; the sandbox is kept for the session.
    $awExe = Join-Path $awScripts "agent-worktrees.exe"
    $childPath = "$awScripts;$csScripts;" + '$env:PATH'
    $child = @"
`$env:AGENT_HOME = '$sandbox'
`$env:WORKTREE_PROJECT = '$Project'
`$env:PATH = '$childPath'
Write-Host 'Draft picker (mock) -- sandbox AGENT_HOME=$sandbox' -ForegroundColor Cyan
Write-Host 'Arrow to the CODESPACES tab. Ctrl+C / q to exit. Mutating actions are simulated.' -ForegroundColor DarkGray
& '$awExe' picker mock --local
"@
    Start-Process -FilePath "pwsh.exe" `
        -ArgumentList "-NoExit", "-NoProfile", "-Command", $child `
        -WorkingDirectory $awPlugin
    Write-Host "Launched draft picker (mock) in a new window." -ForegroundColor Green
    Write-Host "Sandbox kept at: $sandbox  (delete when done)"
    return
}

try {
    # Sandbox root + worktree binaries on PATH for THIS process only (does not
    # touch the parent session or the live deployment). AGENT_HOME leaves the
    # real ~/ (gh/ssh/git auth) untouched, so live data still resolves.
    $env:AGENT_HOME = $sandbox
    $env:WORKTREE_PROJECT = $Project
    $env:PATH = "$awScripts;$csScripts;$env:PATH"

    $awArgs = @("picker", "screenshot", "--pivot", $Pivot, "--wait", "$Wait", "--format", $Format)
    if ($Live) { $awArgs += "--live" }
    if ($Out) { $awArgs += @("--out", $Out) }

    & (Join-Path $awScripts "agent-worktrees.exe") @awArgs
}
finally {
    if ($Keep) {
        Write-Host ""
        Write-Host "Sandbox kept at: $sandbox"
    }
    else {
        Remove-Item -Recurse -Force $sandbox -ErrorAction SilentlyContinue
    }
}
