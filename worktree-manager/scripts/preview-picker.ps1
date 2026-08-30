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
    An adopted project name. By default the Manager selects its first project.

.PARAMETER Live
    Capture the multi-machine SSH source instead of the local-only source.

.PARAMETER Keep
    Keep the sandbox dir (printed) instead of deleting it after capture.

.EXAMPLE
    pwsh -File scripts/preview-picker.ps1 -Project my-project
    # captures the CodeSpaces tab from worktree source, live data, to stdout

.EXAMPLE
    pwsh -File scripts/preview-picker.ps1 -Project my-project -Pivot Bridges -Format ansi -Out bridges.ansi
#>
[CmdletBinding()]
param(
    [string]$Pivot = "CodeSpaces",
    [ValidateSet("text", "ansi", "svg")][string]$Format = "text",
    [double]$Wait = 40,
    [string]$Out,
    [Parameter(Mandatory=$true)][string]$Project,
    [switch]$Live,
    [switch]$Interactive,
    [switch]$Keep
)

$ErrorActionPreference = "Stop"

# --- Locate the copilot-extensions checkout that owns this script --------------
# scripts/preview-picker.ps1 -> worktree-manager/scripts -> repo root is 2 up.
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$manager = Join-Path $repoRoot "worktree-manager"
$awPlugin = Join-Path $repoRoot "plugins\agent-worktrees"

function Resolve-VenvScripts([string]$plugin, [switch]$Optional) {
    $scripts = Join-Path $plugin ".venv\Scripts"
    $py = Join-Path $scripts "python.exe"
    if (-not (Test-Path $py)) {
        if ($Optional) {
            Write-Warning "No worktree venv for '$plugin'; skipping its pivot manifests."
            return $null
        }
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
$managerScripts = Resolve-VenvScripts $manager
$awScripts = Resolve-VenvScripts $awPlugin
$previewPath = @($managerScripts, $awScripts)
$engineArgv = @((Join-Path $awScripts "python.exe"), "-m", "agent_worktrees")
$engineArgvJson = $engineArgv | ConvertTo-Json -Compress
$engineArgvLiteral = $engineArgvJson.Replace("'", "''")

# --- Build the isolated sandbox ------------------------------------------------
$sandbox = Join-Path ([System.IO.Path]::GetTempPath()) ("agent-picker-preview-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$pivotsDir = Join-Path $sandbox ".agent-worktrees\pivots"
$pluginsDir = Join-Path $sandbox "plugins"
New-Item -ItemType Directory -Force -Path $pivotsDir | Out-Null
New-Item -ItemType Directory -Force -Path $pluginsDir | Out-Null

# Seed pivots only when their contributing worktree runtime is built, so a
# preview never silently executes an ambient installed provider.
$seeded = 0
$seededLabels = @("Worktrees")
Get-ChildItem (Join-Path $repoRoot "plugins\*\pivots\*.json") -ErrorAction SilentlyContinue | ForEach-Object {
    $plugin = $_.Directory.Parent.FullName
    $scripts = Resolve-VenvScripts $plugin -Optional
    if (-not $scripts) { return }
    if ($scripts -notin $previewPath) { $previewPath += $scripts }
    Copy-Item $_.FullName (Join-Path $pivotsDir $_.Name) -Force
    $manifest = Get-Content $_.FullName -Raw | ConvertFrom-Json
    $seededLabels += [string]$manifest.label
    $seeded++
}
if (-not $Interactive -and $Pivot -notin $seededLabels) {
    Write-Error "Pivot '$Pivot' is not available from a built worktree provider. Build its plugin .venv first."
}

Write-Host "Sandbox AGENT_HOME : $sandbox"
Write-Host "Worktree binaries  : $($previewPath -join ' ; ')"
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
    $python = Join-Path $managerScripts "python.exe"
    $child = @"
`$env:AGENT_HOME = '$sandbox'
`$env:AGENT_WORKTREES_PLUGINS_DIR = '$pluginsDir'
`$env:WORKTREE_MANAGER_PICKER_NO_PIVOT_MATERIALIZE = '1'
`$env:WORKTREE_PROJECT = '$Project'
`$env:WORKTREE_MANAGER_AGENT_WORKTREES_SRC = '$(Join-Path $awPlugin "src")'
`$env:WORKTREE_MANAGER_ENGINE_ARGV = '$engineArgvLiteral'
`$env:PATH = '$($previewPath -join ';');' + `$env:PATH
Write-Host 'Draft picker (mock) -- sandbox AGENT_HOME=$sandbox' -ForegroundColor Cyan
Write-Host 'Arrow to the CODESPACES tab. Ctrl+C / q to exit. Mutating actions are simulated.' -ForegroundColor DarkGray
`$pickerArgs = @('-m', 'worktree_manager', 'picker', 'mock')
if ('$Project') { `$pickerArgs += '$Project' }
`$pickerArgs += '--local'
& '$python' @pickerArgs
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
    $env:AGENT_WORKTREES_PLUGINS_DIR = $pluginsDir
    $env:WORKTREE_MANAGER_PICKER_NO_PIVOT_MATERIALIZE = "1"
    $env:WORKTREE_PROJECT = $Project
    $env:WORKTREE_MANAGER_AGENT_WORKTREES_SRC = Join-Path $awPlugin "src"
    $env:WORKTREE_MANAGER_ENGINE_ARGV = $engineArgvJson
    $env:PATH = "$($previewPath -join ';');$env:PATH"

    $managerArgs = @("-m", "worktree_manager", "picker", "screenshot")
    if ($Project) { $managerArgs += $Project }
    $managerArgs += @("--pivot", $Pivot, "--wait", "$Wait", "--format", $Format)
    if ($Live) { $managerArgs += "--live" }
    if ($Out) { $managerArgs += @("--out", $Out) }

    & (Join-Path $managerScripts "python.exe") @managerArgs
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
