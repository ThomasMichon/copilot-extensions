# bootstrap.ps1 — fetch and install the copilot-extensions agent-dispatch
# coordinator, with NO Copilot CLI and NO marketplace involved.
#
# This is the real implementation. The STABLE, public URL any published
# install command / bookmarked reference should use instead is the proxy at
# install/dispatch/bootstrap.ps1 (see
# docs/patterns/standalone-service-layout.md's "public-install-surface
# indirection") -- it never changes even if this file's own location moves
# in a future reorganization:
#
#   iex (irm https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/install/dispatch/bootstrap.ps1)
#
# This is delivered OUTSIDE the plugin pipe, mirroring
# worktree-manager/bootstrap.ps1's exact shape: it does not require any
# Copilot plugin to be installed, and does not go through `copilot plugin`.
# It fetches the repository payload, then hands off to the EXISTING,
# already-mature `plugins/agent-dispatch/scripts/install.ps1` -- the same
# versioned-venv + binstub + Scheduled-Task coordinator installer every
# plugin-delivered agent-dispatch host already uses. Nothing about that
# installer is reimplemented here; this script only supplies the
# Copilot-independent fetch step install.ps1 otherwise gets from the
# marketplace payload.
#
# Usage:
#   .\bootstrap.ps1 install        # venv + binstub + coordinator service
#   .\bootstrap.ps1 -Action update  # idempotent refresh (downgrade-guarded)
#   .\bootstrap.ps1 -Action status
#
# Every install.ps1 parameter (-InstallDir, -NoService, -NoSupervisor,
# -Purge, -Force) passes straight through via @Args.
#
# Prerequisites are auto-provisioned: git is installed best-effort via
# winget when missing, otherwise the payload is fetched as a GitHub tarball
# so a bare machine still bootstraps (install.ps1 itself auto-provisions uv
# the same way its plugin-delivered path already does). The git source
# (repo + ref) is taken from $env:AGENT_DISPATCH_SERVICE_REPO /
# $env:AGENT_DISPATCH_SERVICE_REF when set, otherwise the canonical defaults
# below. Relocate the staging root with $env:AGENT_DISPATCH_SERVICE_ROOT.

$ErrorActionPreference = 'Stop'

$Root    = if ($env:AGENT_DISPATCH_SERVICE_ROOT) { $env:AGENT_DISPATCH_SERVICE_ROOT } else { Join-Path $env:USERPROFILE '.agent-dispatch-service' }
$Staging = Join-Path $Root 'staging'

$Repo = if ($env:AGENT_DISPATCH_SERVICE_REPO) { $env:AGENT_DISPATCH_SERVICE_REPO } else { 'https://github.com/ThomasMichon/copilot-extensions.git' }
$Ref  = if ($env:AGENT_DISPATCH_SERVICE_REF)  { $env:AGENT_DISPATCH_SERVICE_REF }  else { 'main' }

Write-Host 'copilot-extensions agent-dispatch - standalone bootstrap'

# -- Prerequisites (auto-provision; restart-aware; user-local first) --------
# git is installed best-effort via winget when missing, else we fetch the
# payload as a GitHub tarball so the bootstrap never dead-ends without git.
# (uv provisioning is install.ps1's own job, same as every plugin-delivered
# host -- not duplicated here.)

$HaveGit = [bool](Get-Command git -ErrorAction SilentlyContinue)
if (-not $HaveGit) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host '  git not found - attempting winget install (may prompt for elevation)...'
        try {
            winget install --id Git.Git -e --source winget `
                --accept-source-agreements --accept-package-agreements | Out-Null
        } catch { }
        $gitCmd = Join-Path $env:ProgramFiles 'Git\cmd'
        if (Test-Path (Join-Path $gitCmd 'git.exe')) { $env:PATH = "$gitCmd;$env:PATH" }
        $HaveGit = [bool](Get-Command git -ErrorAction SilentlyContinue)
    }
    if (-not $HaveGit) { Write-Host '  git unavailable - will fetch the payload as a tarball (no git).' }
}

# -- Fetch the payload (git clone/fetch when available, else GitHub tarball) -
if ($HaveGit) {
    New-Item -ItemType Directory -Force -Path $Staging | Out-Null
    if (Test-Path (Join-Path $Staging '.git')) {
        git -C $Staging fetch --depth 1 $Repo $Ref | Out-Null
        git -C $Staging checkout -q FETCH_HEAD
    } else {
        if (Test-Path $Staging) { Remove-Item -Recurse -Force $Staging }
        git clone --depth 1 --branch $Ref $Repo $Staging | Out-Null
    }
    $PayloadParent = $Staging
} else {
    # Derive the codeload tarball URL from the (GitHub) source; a non-GitHub
    # source genuinely needs git.
    $TarUrl = $null
    $m = [regex]::Match($Repo, 'github\.com[/:]+([^/]+)/([^/]+?)(?:\.git)?/?$')
    if ($m.Success) { $TarUrl = "https://codeload.github.com/$($m.Groups[1].Value)/$($m.Groups[2].Value)/tar.gz/$Ref" }
    if (-not $TarUrl) { throw "git is required for a non-GitHub source ($Repo). Install git and re-run." }
    Write-Host '  fetching payload tarball (no git)...'
    New-Item -ItemType Directory -Force -Path $Staging | Out-Null
    $Tar = Join-Path $Staging 'payload.tar.gz'
    Invoke-WebRequest -Uri $TarUrl -OutFile $Tar -UseBasicParsing
    $Extract = Join-Path $Staging 'extract'
    if (Test-Path $Extract) { Remove-Item -Recurse -Force $Extract }
    New-Item -ItemType Directory -Force -Path $Extract | Out-Null
    tar -xzf $Tar -C $Extract   # bsdtar ships with Windows 10 1803+
    $PayloadParent = (Get-ChildItem $Extract -Directory | Select-Object -First 1).FullName
    Remove-Item $Tar -Force
}

# Hand off to the existing, already-mature coordinator installer. install.ps1
# resolves its own payload (src/, libs/, pyproject.toml) relative to itself,
# so running it from this freshly fetched checkout works exactly like
# running it from an installed marketplace payload.
& (Join-Path $PayloadParent 'plugins\agent-dispatch\scripts\install.ps1') @Args
