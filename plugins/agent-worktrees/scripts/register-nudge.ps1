<#
    register-nudge -- sessionStart additionalContext hook (hooks.json). Parity of
    register-nudge.sh.

    First-run ONBOARDING nudge: when a session is in a git repo that is NOT a
    registered agent-worktrees project, emit a ONE-TIME (per repo)
    additionalContext nudge inviting `agent-worktrees register <name>`. Nudge
    ONLY -- it NEVER registers/adopts anything (install-vs-adopt boundary).

    Grace-window-cheap + resolver-free: pure PowerShell + a heuristic read of
    projects.yaml, so it works on a tools-half box. Fail-open: emits `{}` on ANY
    uncertainty, and writes only machine-local runtime state
    (~/.agent-worktrees/.register-nudged/), never the repo. PS5.1+.
#>
$ErrorActionPreference = 'SilentlyContinue'

function Emit-Empty { Write-Output '{}'; exit 0 }

# Only nudge when agent-worktrees is available to register with.
$binstub = Join-Path $env:USERPROFILE '.local\bin\agent-worktrees'
if (-not (Get-Command agent-worktrees -ErrorAction SilentlyContinue) -and -not (Test-Path $binstub)) { Emit-Empty }

# Must be inside a git work tree.
$top = (git rev-parse --show-toplevel 2>$null)
if (-not $top) { Emit-Empty }
$top = ("$top").Trim()
if (-not $top) { Emit-Empty }

# Derive the repo name: strip a `<repo>.worktrees/<id>` worktree suffix, then
# take the leaf.
$base = $top
$idx = $base.IndexOf('.worktrees/')
if ($idx -ge 0) { $base = $base.Substring(0, $idx) }
$name = Split-Path $base -Leaf
if (-not $name) { Emit-Empty }

# Already registered? (heuristic: the repo name is a project key in
# projects.yaml.) If so, stay silent.
$projects = Join-Path $env:USERPROFILE '.agent-worktrees\projects.yaml'
if (Test-Path $projects) {
    if (Select-String -Path $projects -Pattern "^\s+$([regex]::Escape($name)):\s*$" -Quiet) { Emit-Empty }
}

# Once-per-repo gating.
$markerDir = Join-Path $env:USERPROFILE '.agent-worktrees\.register-nudged'
$sha = [System.Security.Cryptography.SHA1]::Create()
$key = -join ($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($top)) | ForEach-Object { $_.ToString('x2') })
$marker = Join-Path $markerDir $key
if (Test-Path $marker) { Emit-Empty }
New-Item -ItemType Directory -Path $markerDir -Force | Out-Null
Set-Content -LiteralPath $marker -Value '' -NoNewline

$msg = "This repo ($name) is not a registered agent-worktrees project. To enable isolated, concurrent worktree sessions (create/finalize + the PR flow), register it once from the repo root: agent-worktrees register $name . This is an onboarding nudge only -- nothing has been registered, and agent-worktrees never auto-adopts a repo."

$obj = @{ additionalContext = $msg }
Write-Output ($obj | ConvertTo-Json -Compress)
exit 0
