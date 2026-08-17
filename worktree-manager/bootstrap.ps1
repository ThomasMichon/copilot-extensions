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
# Prerequisites are auto-provisioned: uv is installed user-local (no admin) when
# missing; git is installed best-effort where a package manager exists, otherwise
# the payload is fetched as a GitHub tarball so a bare machine still bootstraps.
# The git source (repo + ref) is taken from the user-level source config
# ($Root/config.toml [source]) when present -- set it with `worktree-manager
# source set` to track a fork / canary branch -- otherwise the canonical defaults
# below. Relocate the install root with $env:WORKTREE_MANAGER_ROOT.

$ErrorActionPreference = 'Stop'

$Root    = if ($env:WORKTREE_MANAGER_ROOT) { $env:WORKTREE_MANAGER_ROOT } else { Join-Path $env:USERPROFILE '.worktree-manager' }
$Staging = Join-Path $Root 'staging'
$Config  = Join-Path $Root 'config.toml'

# Source (repo + ref): user-level config file [source] overrides, else defaults.
$Repo = 'https://github.com/ThomasMichon/copilot-extensions.git'
$Ref  = 'main'
if (Test-Path $Config) {
    $cfg = Get-Content $Config -Raw
    # Isolate the [source] table (up to the next [table] header or EOF).
    $sec = [regex]::Match($cfg, '(?ms)^\s*\[source\]\s*(.*?)(?=^\s*\[|\z)')
    if ($sec.Success) {
        $body  = $sec.Groups[1].Value
        $mRepo = [regex]::Match($body, '(?m)^\s*repo\s*=\s*"(.*?)"')
        $mRef  = [regex]::Match($body, '(?m)^\s*ref\s*=\s*"(.*?)"')
        if ($mRepo.Success) { $Repo = $mRepo.Groups[1].Value }
        if ($mRef.Success)  { $Ref  = $mRef.Groups[1].Value }
    }
}

Write-Host 'copilot-extensions Worktree Manager - bootstrap'

# -- Prerequisites (auto-provision; restart-aware; user-local first) ----------
# The one-liner must take a *bare* machine into the Manager (vision installer
# one-line-bootstrap). uv is auto-installed (user-local, no admin). git is
# installed best-effort where a package manager exists, else we fetch the payload
# as a GitHub tarball so the bootstrap never dead-ends without git.

$LocalBin = Join-Path $env:USERPROFILE '.local\bin'

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host '  uv not found - installing (user-local, no admin)...'
    try { Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression }
    catch { throw "uv install failed: $_" }
    # uv installs into ~\.local\bin; amend THIS session's PATH so we can continue
    # without a restart (the installer persists PATH for future shells).
    if (Test-Path (Join-Path $LocalBin 'uv.exe')) { $env:PATH = "$LocalBin;$env:PATH" }
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Warning "uv was installed but is not on PATH yet. Restart your shell and re-run the bootstrap."
        exit 1
    }
}

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

# -- Fetch the payload (git clone/fetch when available, else GitHub tarball) ---
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

Push-Location (Join-Path $PayloadParent 'worktree-manager')
try {
    # Version-install the fetched payload (idempotent, version-gated): publishes
    # the versions/<ver> slot + current-version marker + ~/.local/bin binstub.
    uv run --quiet python -m worktree_manager self-install --apply
    # Then run whatever the caller asked for through the freshly-installed app.
    uv run --quiet python -m worktree_manager @args
} finally {
    Pop-Location
}

if (($env:PATH -split ';') -notcontains $LocalBin) {
    Write-Warning "Add '$LocalBin' to your PATH (uv and the worktree-manager binstub live there), then restart your shell."
}
