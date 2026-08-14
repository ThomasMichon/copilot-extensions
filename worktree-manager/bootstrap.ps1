# bootstrap.ps1 — fetch, version-install, and launch the copilot-extensions Worktree Manager.
#
#   iex (irm https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/worktree-manager/bootstrap.ps1)
#
# This is delivered OUTSIDE the plugin pipe: it does not require any Copilot
# plugin to be installed, and does not go through `copilot plugin`. It fetches
# the Worktree Manager payload, installs it under the SAME versioning convention as
# the harness's other installers — an immutable ~/.worktree-manager/versions/<ver>
# slot, a plain-text ~/.worktree-manager/current-version marker, and a
# ~/.local/bin/worktree-manager binstub — then launches it. Re-running is
# version-gated (a no-op when already current).
#
# Phase 0 assumes git + uv are already present; automatic prerequisite
# provisioning lands in Phase 2 (issue #355). Override the fetched ref with
# $env:WORKTREE_MANAGER_REF, the git source (mirror/fork) with
# $env:WORKTREE_MANAGER_REPO, and the install root with $env:WORKTREE_MANAGER_ROOT.

$ErrorActionPreference = 'Stop'

$Repo    = if ($env:WORKTREE_MANAGER_REPO) { $env:WORKTREE_MANAGER_REPO } else { 'https://github.com/ThomasMichon/copilot-extensions.git' }
$Ref     = if ($env:WORKTREE_MANAGER_REF) { $env:WORKTREE_MANAGER_REF } else { 'main' }
$Root    = if ($env:WORKTREE_MANAGER_ROOT) { $env:WORKTREE_MANAGER_ROOT } else { Join-Path $env:USERPROFILE '.worktree-manager' }
$Staging = Join-Path $Root 'staging'

Write-Host 'copilot-extensions Worktree Manager - bootstrap'

foreach ($tool in 'git', 'uv') {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "$tool is required for the Phase 0 bootstrap (automatic prerequisite install lands in Phase 2 / issue #355). Install $tool and re-run."
    }
}

New-Item -ItemType Directory -Force -Path $Staging | Out-Null
if (Test-Path (Join-Path $Staging '.git')) {
    git -C $Staging fetch --depth 1 origin $Ref | Out-Null
    git -C $Staging checkout -q FETCH_HEAD
} else {
    git clone --depth 1 --branch $Ref $Repo $Staging | Out-Null
}

Push-Location (Join-Path $Staging 'worktree-manager')
try {
    # Version-install the fetched payload (idempotent, version-gated): publishes
    # the versions/<ver> slot + current-version marker + ~/.local/bin binstub.
    uv run --quiet python -m worktree_manager self-install --apply
    # Then run whatever the caller asked for through the freshly-installed app.
    uv run --quiet python -m worktree_manager @args
} finally {
    Pop-Location
}
