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

$BootstrapClock = [Diagnostics.Stopwatch]::StartNew()
function Write-BootstrapProgress(
    [string]$Message,
    [ValidateSet('running', 'warning', 'failed')][string]$State = 'running'
) {
    $stamp = [DateTime]::UtcNow.ToString('HH:mm:ss.fffZ', [Globalization.CultureInfo]::InvariantCulture)
    $elapsed = $BootstrapClock.Elapsed.TotalSeconds.ToString('F1', [Globalization.CultureInfo]::InvariantCulture)
    foreach ($line in [regex]::Split($Message, '\r\n|\n|\r')) {
        [Console]::Error.WriteLine("[$stamp] update/bootstrap $State $line [elapsed ${elapsed}s]")
    }
}

function Resolve-BootstrapExecutable([string]$Path) {
    $root = [IO.Path]::GetPathRoot($Path)
    if (-not $root) { throw 'Bootstrap executable must have an absolute path.' }
    $remaining = @($Path.Substring($root.Length) -split '[\\/]' | Where-Object { $_ })
    $current = $root
    $links = 0
    $ordinaryFile = $false
    while ($remaining.Count) {
        $component = $remaining[0]
        $remaining = @($remaining | Select-Object -Skip 1)
        if ($component -eq '.') { continue }
        if ($component -eq '..') {
            $parent = [IO.Path]::GetDirectoryName($current)
            if ($parent) { $current = $parent }
            continue
        }
        $candidate = [IO.Path]::Combine($current, $component)
        $item = Get-Item -LiteralPath $candidate -Force -ErrorAction Stop
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            $ordinaryFile = $false
            $links++
            $targets = @($item.Target)
            if ($links -gt 40 -or $targets.Count -ne 1 -or -not $targets[0]) {
                throw 'Bootstrap executable has an unsupported link or excessive indirection.'
            }
            $target = [string]$targets[0]
            if (-not [IO.Path]::IsPathRooted($target)) { $target = [IO.Path]::Combine($current, $target) }
            $current = [IO.Path]::GetPathRoot($target)
            $remaining = @($target.Substring($current.Length) -split '[\\/]' |
                Where-Object { $_ }) + $remaining
            continue
        }
        if ([bool]$item.PSIsContainer -ne [bool]$remaining.Count) {
            throw 'Bootstrap executable does not identify a regular file.'
        }
        $ordinaryFile = -not $item.PSIsContainer
        $current = $candidate
    }
    if (-not $ordinaryFile) { throw 'Bootstrap executable does not identify a regular file.' }
    return $current
}

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

$installerArgs = @()
$commandArgs = @($args)
$RecoveryStaging = $null
if ($commandArgs.Count -and $commandArgs[0] -ceq '--recover-for-repair') {
    $installerArgs += '--recover-for-repair'
    $commandArgs = @($commandArgs | Select-Object -Skip 1)
    if ($commandArgs.Count -and $commandArgs[0] -ceq '--json') {
        $installerArgs += '--json'
        $commandArgs = @($commandArgs | Select-Object -Skip 1)
    }
    if ($commandArgs.Count) { throw 'Repair recovery accepts only --json.' }
    $RecoveryStaging = Join-Path ([IO.Path]::GetTempPath()) ('manager-repair.' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $RecoveryStaging | Out-Null
    $Staging = Join-Path $RecoveryStaging 'source'
}
if ($commandArgs.Count -and $commandArgs[0] -ceq '--repair-update') {
    if ($commandArgs.Count -lt 4 -or $commandArgs[3] -cne 'update') {
        throw 'Incomplete or invalid update bootstrap source/command.'
    }
    $Repo = $commandArgs[1]
    $Ref = $commandArgs[2]
    $installerArgs += '--repair-update'
    $commandArgs = @($commandArgs | Select-Object -Skip 3)
}
try {
Write-BootstrapProgress 'Worktree Manager bootstrap'

# -- Prerequisites (auto-provision; restart-aware; user-local first) ----------
# The one-liner must take a *bare* machine into the Manager (vision installer
# one-line-bootstrap). uv is auto-installed (user-local, no admin). git is
# installed best-effort where a package manager exists, else we fetch the payload
# as a GitHub tarball so the bootstrap never dead-ends without git.

$LocalBin = Join-Path $env:USERPROFILE '.local\bin'

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-BootstrapProgress 'uv not found - installing (user-local, no admin)...'
    try { Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression }
    catch { throw "uv install failed: $_" }
    # uv installs into ~\.local\bin; amend THIS session's PATH so we can continue
    # without a restart (the installer persists PATH for future shells).
    if (Test-Path (Join-Path $LocalBin 'uv.exe')) { $env:PATH = "$LocalBin;$env:PATH" }
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-BootstrapProgress 'uv was installed but is not on PATH yet. Restart your shell and re-run the bootstrap.' warning
        exit 1
    }
}
$UvCommand = Get-Command uv -CommandType Application,ExternalScript -ErrorAction Stop |
    Select-Object -First 1
$Uv = Resolve-BootstrapExecutable $UvCommand.Source

$HaveGit = [bool](Get-Command git -ErrorAction SilentlyContinue)
if (-not $HaveGit) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-BootstrapProgress 'git not found - attempting winget install (may prompt for elevation)...'
        try {
            winget install --id Git.Git -e --source winget `
                --accept-source-agreements --accept-package-agreements | Out-Null
        } catch { }
        $gitCmd = Join-Path $env:ProgramFiles 'Git\cmd'
        if (Test-Path (Join-Path $gitCmd 'git.exe')) { $env:PATH = "$gitCmd;$env:PATH" }
        $HaveGit = [bool](Get-Command git -ErrorAction SilentlyContinue)
    }
    if (-not $HaveGit) { Write-BootstrapProgress 'git unavailable - will fetch the payload as a tarball (no git).' }
}

# -- Fetch the payload (git clone/fetch when available, else GitHub tarball) ---
if ($HaveGit) {
    New-Item -ItemType Directory -Force -Path $Staging | Out-Null
    if (-not (Test-Path (Join-Path $Staging '.git'))) {
        if (Test-Path $Staging) { Remove-Item -Recurse -Force $Staging }
        git init --quiet -- $Staging
        if ($LASTEXITCODE -ne 0) {
            Write-BootstrapProgress "git init failed (exit $LASTEXITCODE)." failed
            exit $LASTEXITCODE
        }
    }
    git -C $Staging fetch --depth 1 -- $Repo $Ref | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-BootstrapProgress "git fetch of $Repo@$Ref failed (exit $LASTEXITCODE)." failed
        exit $LASTEXITCODE
    }
    git -C $Staging -c core.hooksPath= checkout -q --detach FETCH_HEAD
    if ($LASTEXITCODE -ne 0) {
        Write-BootstrapProgress "git checkout of $Repo@$Ref failed (exit $LASTEXITCODE)." failed
        exit $LASTEXITCODE
    }
    $PayloadParent = $Staging
} else {
    # Derive the codeload tarball URL from the (GitHub) source; a non-GitHub
    # source genuinely needs git.
    $TarUrl = $null
    $m = [regex]::Match($Repo, 'github\.com[/:]+([^/]+)/([^/]+?)(?:\.git)?/?$')
    if ($m.Success) { $TarUrl = "https://codeload.github.com/$($m.Groups[1].Value)/$($m.Groups[2].Value)/tar.gz/$Ref" }
    if (-not $TarUrl) { throw "git is required for a non-GitHub source ($Repo). Install git and re-run." }
    Write-BootstrapProgress 'fetching payload tarball (no git)...'
    New-Item -ItemType Directory -Force -Path $Staging | Out-Null
    $Tar = Join-Path $Staging 'payload.tar.gz'
    Invoke-WebRequest -Uri $TarUrl -OutFile $Tar -UseBasicParsing
    $Extract = Join-Path $Staging 'extract'
    if (Test-Path $Extract) { Remove-Item -Recurse -Force $Extract }
    New-Item -ItemType Directory -Force -Path $Extract | Out-Null
    tar -xzf $Tar -C $Extract   # bsdtar ships with Windows 10 1803+
    if ($LASTEXITCODE -ne 0) {
        Write-BootstrapProgress "Extracting the payload tarball failed (exit $LASTEXITCODE)." failed
        exit $LASTEXITCODE
    }
    $PayloadParent = (Get-ChildItem $Extract -Directory | Select-Object -First 1).FullName
    Remove-Item $Tar -Force
}

Push-Location (Join-Path $PayloadParent 'worktree-manager')
try {
    $Python = $null
    try { $Python = & $Uv python find --no-project '>=3.11' 2>$null }
    catch { $Python = $null }
    if ($LASTEXITCODE -ne 0 -or -not $Python) {
        & $Uv python install 3.11
        if ($LASTEXITCODE -ne 0) { throw 'Manager base Python acquisition failed.' }
        $Python = & $Uv python find --no-project '>=3.11'
    }
    if ($LASTEXITCODE -ne 0 -or -not $Python) { throw 'Manager needs Python 3.11 or newer.' }
    if ($commandArgs.Count) {
        & $Python -I -S -B -X utf8 (Join-Path $PWD 'scripts\runtime_install.py') --root $Root --payload $PWD @installerArgs -- @commandArgs
    } else {
        & $Python -I -S -B -X utf8 (Join-Path $PWD 'scripts\runtime_install.py') --root $Root --payload $PWD @installerArgs
    }
    $commandExit = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($commandExit -ne 0) {
    Write-BootstrapProgress "Manager runtime command failed ($commandExit)." failed
}
if ($commandExit -eq 0 -and ($env:PATH -split ';') -notcontains $LocalBin) {
    Write-BootstrapProgress "Add '$LocalBin' to your PATH (uv and the worktree-manager binstub live there), then restart your shell." warning
}
exit $commandExit
} finally {
    if ($RecoveryStaging) { Remove-Item -LiteralPath $RecoveryStaging -Recurse -Force }
}
