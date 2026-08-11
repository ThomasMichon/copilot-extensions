<#
.SYNOPSIS
  Drive the copilot-extensions clean-room install-flow validation (Docker).

.DESCRIPTION
  Builds a "fresh machine" image and drives a PERSISTENT container: it runs the
  in-container validate.sh driver (optionally stopping after a chosen phase) and
  can drop you into an INTERACTIVE SHELL in the same box for headed `copilot`
  smoke tests -- Copilot CLI does not fully enable every feature in `-p`/ACP, so
  the rig automates what it can and hands off for the rest.

  Auth is AUTOMATIC by default: the runner grabs a Copilot token from the host
  `gh` (COPILOT_GITHUB_TOKEN) and injects it into the container, so there is NO
  interactive device-code step and no need to pre-build an ":authed" image. The
  selected gh account must have Copilot entitlement. Use -NoToken to fall back to
  the one-time device-code login committed to a cached ":authed" image.

  Two image variants:
    base     -- stock toolchain present (git, python, node, uv). The default;
                today's Layer-0 plugin-install check.
    pristine -- the harshest fresh INTERNAL machine: Copilot + git ONLY (no
                python, no uv, no pip, no ~/.local/bin, no feed governance).
                Forces the harness to provision its own toolchain, so uv/venv/
                pip-feed jams SURFACE instead of being hidden.

  Feed policy: the runner does NOT auto-forward the host's npm config into the
  build -- silently inheriting the host feed biases the fresh-machine experiment.
  Pass -NpmRegistry explicitly only to install the Copilot CLI prereq on a
  governed box where public npm is TLS-blocked (a build-time given, not part of
  the experiment).

.PARAMETER Mode
  build : build the selected image only.
  auth  : one-time device-code `copilot` login, committed to the cached :authed
          image for that variant.
  run   : (default) start a fresh container, run validate.sh (to -Until), report.
  shell : drop into an interactive login shell in the container (start one if
          none is up). Use after `run -Then shell`, or standalone to poke a box.
  down  : remove the container for the selected variant.
  all   : build -> (auth if needed) -> run.

.PARAMETER Image     base | pristine (default base).
.PARAMETER Until     Stop validate.sh after this phase number 0-6 (default 6 = all).
.PARAMETER Then      After `run`: none | shell | down (default none).
.PARAMETER NpmRegistry
  Explicit npm feed for the image BUILD only (installs the Copilot CLI prereq).
  Empty = public registry.npmjs.org; never auto-detected from the host.

.EXAMPLE
  ./run.ps1                                   # base: full validate against :authed
.EXAMPLE
  ./run.ps1 -Image pristine -Mode shell       # drop into a pristine fresh box
.EXAMPLE
  ./run.ps1 -Until 1 -Then shell              # install the plugin, then hand off
#>
[CmdletBinding()]
param(
    [ValidateSet('build','auth','run','shell','down','all')]
    [string]$Mode = 'run',
    [ValidateSet('base','pristine')]
    [string]$Image = 'base',
    [ValidateRange(0,6)]
    [int]$Until = 6,
    [ValidateSet('none','shell','down')]
    [string]$Then = 'none',
    [string]$NpmRegistry = '',
    # Auth: by default the runner injects a Copilot token grabbed from the host
    # `gh` (COPILOT_GITHUB_TOKEN) so NO interactive device-code login is needed.
    # -TokenAccount picks which gh account (must have Copilot entitlement);
    # empty = the active account. Set -NoToken to force the device-code :authed
    # image path instead.
    [string]$TokenAccount = '',
    [switch]$NoToken,
    [string]$MarketplaceRepo = 'ThomasMichon/copilot-extensions',
    [string]$MarketplaceName = 'copilot-extensions',
    [string]$PrimaryPlugin   = 'agent-codespaces',
    [string]$ExpectDeps      = 'agent-bridge agent-worktrees',
    # Where run artifacts (report + logs) land. Defaults to a MACHINE-LOCAL dir
    # OUTSIDE any repo checkout -- run artifacts are per-run state and must never
    # be written into the (possibly anchor) repo tree.
    [string]$ResultsDir = ''
)
$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path

# --- image/tag/container naming (base keeps the legacy :authed tag) -----------
$Dockerfile = if ($Image -eq 'pristine') { 'Dockerfile.pristine' } else { 'Dockerfile' }
$BaseTag    = "copilot-cleanroom:$Image"
$AuthTag    = if ($Image -eq 'base') { 'copilot-cleanroom:authed' } else { "copilot-cleanroom:$Image-authed" }
$Container  = "cr-$Image"

if (-not $ResultsDir) { $ResultsDir = $env:CR_RESULTS_DIR }
if (-not $ResultsDir) {
    $root = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME '.local\state' }
    $ResultsDir = Join-Path $root ("copilot-cleanroom\runs\" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
}
$Results = $ResultsDir

function Test-Image($tag) { return [bool](docker images -q $tag 2>$null) }
function Test-Running($name) { return [bool](docker ps -q -f "name=^$name$" 2>$null) }

function Invoke-Build {
    Write-Host "== building $Image image ($BaseTag) from $Dockerfile ==" -ForegroundColor Cyan
    # No host-config auto-forward: pass a feed ONLY when explicitly requested
    # (installs the Copilot CLI prereq on a governed box). Public by default.
    $reg = if ($NpmRegistry) { $NpmRegistry } elseif ($env:CR_NPM_REGISTRY) { $env:CR_NPM_REGISTRY } else { 'https://registry.npmjs.org/' }
    Write-Host "   npm registry (build-time, Copilot install only): $reg" -ForegroundColor DarkGray
    docker build --build-arg "NPM_REGISTRY=$reg" -f (Join-Path $Here $Dockerfile) -t $BaseTag $Here
    if ($LASTEXITCODE -ne 0) {
        Write-Host "docker build failed. On a governed box the public npm feed is TLS-blocked;" -ForegroundColor Yellow
        Write-Host "re-run with -NpmRegistry https://<your-internal-npm-feed>/ to install the Copilot CLI." -ForegroundColor Yellow
        throw "docker build failed"
    }
}

function Invoke-Auth {
    if (-not (Test-Image $BaseTag)) { Invoke-Build }
    Write-Host "== one-time device-code login ($AuthTag) ==" -ForegroundColor Cyan
    Write-Host "An interactive Copilot session opens. Run '/login' if not prompted," -ForegroundColor Yellow
    Write-Host "authorize the device code in your browser, then '/exit'." -ForegroundColor Yellow
    docker rm -f cr-auth 2>$null | Out-Null
    docker run -it --name cr-auth --entrypoint /bin/bash $BaseTag -lc 'copilot; echo "--- login session ended ---"'
    Write-Host "== committing authed image ($AuthTag) ==" -ForegroundColor Cyan
    docker commit cr-auth $AuthTag | Out-Null
    docker rm -f cr-auth | Out-Null
    Write-Host "cached $AuthTag" -ForegroundColor Green
}

# Resolve a Copilot token from the host (unless -NoToken). Precedence:
#   $env:COPILOT_GITHUB_TOKEN  >  gh auth token [--user $TokenAccount]
# The CLI accepts COPILOT_GITHUB_TOKEN / GH_TOKEN / GITHUB_TOKEN; we use the
# first. Returns $null when none is available (caller falls back to :authed).
function Resolve-CopilotToken {
    if ($NoToken) { return $null }
    if ($env:COPILOT_GITHUB_TOKEN) { return $env:COPILOT_GITHUB_TOKEN }
    $ghArgs = @('auth','token')
    if ($TokenAccount) { $ghArgs += @('--user', $TokenAccount) }
    $t = (& gh @ghArgs 2>$null | Select-Object -First 1)
    if ($t) { return $t.Trim() }
    return $null
}

# Start a FRESH persistent container (idle), with the scenario + out dir mounted
# and CR_* in its environment (inherited by every `docker exec`).
#
# Auth: prefer a host-grabbed Copilot token injected as COPILOT_GITHUB_TOKEN
# (no interactive step; runs against the plain unauthed image). Fall back to the
# committed device-code :authed image only when no token is available.
function Start-Container {
    $token = Resolve-CopilotToken
    $tokenArgs = @()
    if ($token) {
        if (-not (Test-Image $BaseTag)) { Invoke-Build }
        $img = $BaseTag
        $acct = if ($TokenAccount) { $TokenAccount } else { 'active gh account' }
        Write-Host "auth: injecting COPILOT_GITHUB_TOKEN from host gh ($acct) -- no device-code needed" -ForegroundColor DarkGray
        # Pass the value through the runner's env (name-only -e) so the token is
        # not on the docker CLI args; the container keeps its own copy for exec.
        $env:COPILOT_GITHUB_TOKEN = $token
        $tokenArgs = @('-e', 'COPILOT_GITHUB_TOKEN')
    } else {
        if (-not (Test-Image $AuthTag)) {
            Write-Host "no host token and no $AuthTag image -- running device-code auth." -ForegroundColor Yellow
            Invoke-Auth
        }
        $img = $AuthTag
    }
    docker rm -f $Container 2>$null | Out-Null
    New-Item -ItemType Directory -Force -Path $Results | Out-Null
    $validate = ($Here + '/validate.sh') -replace '\\','/'
    $res      = ($Results) -replace '\\','/'
    docker run -d --name $Container `
        -v "${validate}:/home/operator/validate.sh:ro" `
        -v "${res}:/home/operator/out" `
        -e "CR_MARKETPLACE_REPO=$MarketplaceRepo" `
        -e "CR_MARKETPLACE_NAME=$MarketplaceName" `
        -e "CR_PRIMARY_PLUGIN=$PrimaryPlugin" `
        -e "CR_EXPECT_DEPS=$ExpectDeps" `
        -e "CR_REPORT=/home/operator/out/cr-report.json" `
        @tokenArgs `
        --entrypoint sleep $img infinity | Out-Null
    if ($token) { Remove-Item Env:\COPILOT_GITHUB_TOKEN -ErrorAction SilentlyContinue }
    Write-Host "container $Container up (results -> $Results)" -ForegroundColor DarkGray
}

function Ensure-Container {
    if (-not (Test-Running $Container)) { Start-Container }
}

function Invoke-Run {
    Start-Container
    Write-Host "== running clean-room validation ($Image, through phase $Until) ==" -ForegroundColor Cyan
    docker exec -e "CR_UNTIL=$Until" $Container /bin/bash -lc `
        'cp -r $HOME/cr-logs /home/operator/out/ 2>/dev/null; bash /home/operator/validate.sh; rc=$?; cp -r $HOME/cr-logs /home/operator/out/ 2>/dev/null; exit $rc'
    $rc = $LASTEXITCODE
    Write-Host "`n== report ==" -ForegroundColor Cyan
    if (Test-Path (Join-Path $Results 'cr-report.json')) {
        Get-Content (Join-Path $Results 'cr-report.json') -Raw | Write-Host
    }
    Write-Host "results dir: $Results"
    switch ($Then) {
        'shell' { Invoke-Shell }
        'down'  { Invoke-Down }
    }
    if ($Then -ne 'shell') { exit $rc }
}

function Invoke-Shell {
    Ensure-Container
    Write-Host "== entering $Container (interactive login shell; 'exit' to leave, container stays up) ==" -ForegroundColor Cyan
    Write-Host "   run './run.ps1 -Image $Image -Mode down' to remove it." -ForegroundColor DarkGray
    docker exec -it $Container /bin/bash -l
}

function Invoke-Down {
    docker rm -f $Container 2>$null | Out-Null
    Write-Host "removed $Container" -ForegroundColor Green
}

switch ($Mode) {
    'build' { Invoke-Build }
    'auth'  { Invoke-Auth }
    'run'   { Invoke-Run }
    'shell' { Invoke-Shell }
    'down'  { Invoke-Down }
    'all'   { Invoke-Build; Invoke-Run }
}
