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
  run   : (default) start a fresh container, run the scenario (to -Until), report.
  shell : drop into an interactive login shell in the container (start one if
          none is up). Use after `run -Then shell`, or standalone to poke a box.
  down  : remove the container for the selected variant.
  all   : build -> (auth if needed) -> run.

.PARAMETER Image     base | pristine (default base).
.PARAMETER Scenario  Scenario to run: a name under scenarios/ or a dir path
                     (default generic-single-plugin). The runner mounts the
                     scenario dir + shared lib/ read-only and runs scenario.sh.
.PARAMETER Until     Stop the scenario after this stage number, or 'all'
                     (default) for every stage.
.PARAMETER Then      After `run`: none | shell | down (default none).
.PARAMETER NpmRegistry
  Explicit npm feed for the image BUILD only (installs the Copilot CLI prereq).
  Empty = public registry.npmjs.org; never auto-detected from the host.
.PARAMETER UvIndex
  Opt-in uv-index fixture: point the deploy stage's uv at an internal index on a
  governed box (runtime analog of -NpmRegistry). Empty = off, so the governed uv
  jam surfaces. Also settable via $env:CR_UV_INDEX.

.EXAMPLE
  ./run.ps1                                   # base: full generic-single-plugin scenario
.EXAMPLE
  ./run.ps1 -Image pristine -Mode shell       # drop into a pristine fresh box
.EXAMPLE
  ./run.ps1 -Until 1 -Then shell              # install the plugin, then hand off
.EXAMPLE
  ./run.ps1 -UvIndex https://…/pypi/simple/   # opt-in uv-index fixture (governed box)
#>
[CmdletBinding()]
param(
    [ValidateSet('build','auth','run','shell','down','bridge-register','bridge-unregister','all')]
    [string]$Mode = 'run',
    [ValidateSet('base','pristine')]
    [string]$Image = 'base',
    # Scenario to run: a name under scenarios/ or an explicit dir path.
    [string]$Scenario = 'generic-single-plugin',
    # Stop the scenario after this stage number, or 'all' (default) for every stage.
    [string]$Until = 'all',
    [ValidateSet('none','shell','down')]
    [string]$Then = 'none',
    [string]$NpmRegistry = '',
    # Opt-in uv-index fixture: point the deploy stage's uv at an internal index
    # (governed box). Empty = off, so the governed uv jam surfaces. Runtime
    # analog of -NpmRegistry (which is build-time). Also $env:CR_UV_INDEX.
    [string]$UvIndex = '',
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

# --- Until validation: 'all' or a non-negative integer ------------------------
if ($Until -ne 'all' -and $Until -notmatch '^\d+$') {
    throw "-Until must be 'all' or a non-negative integer (got '$Until')"
}

# --- scenario resolution: an explicit dir, else scenarios/<name>/ -------------
if (Test-Path -PathType Container $Scenario) {
    $ScenarioDir  = (Resolve-Path $Scenario).Path
    $ScenarioName = Split-Path -Leaf $ScenarioDir
} elseif (Test-Path -PathType Container (Join-Path $Here "scenarios\$Scenario")) {
    $ScenarioDir  = Join-Path $Here "scenarios\$Scenario"
    $ScenarioName = $Scenario
} else {
    throw "unknown -Scenario '$Scenario' (not a dir, and no scenarios\$Scenario)"
}
if (-not (Test-Path (Join-Path $ScenarioDir 'scenario.sh'))) {
    throw "scenario '$ScenarioName' has no scenario.sh"
}
$LibDir = Join-Path $Here 'lib'

# --- image/tag/container naming (base keeps the legacy :authed tag) -----------
$Dockerfile = if ($Image -eq 'pristine') { 'Dockerfile.pristine' } else { 'Dockerfile' }
$BaseTag    = "copilot-cleanroom:$Image"
$AuthTag    = if ($Image -eq 'base') { 'copilot-cleanroom:authed' } else { "copilot-cleanroom:$Image-authed" }
$Container  = "cr-$Image"
$AgentName  = "cleanroom-$Image"   # agent-bridge agent + provider name for this box

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
    $scenDir = ($ScenarioDir) -replace '\\','/'
    $libDir  = ($LibDir)      -replace '\\','/'
    $res     = ($Results)     -replace '\\','/'
    docker run -d --name $Container `
        -v "${scenDir}:/home/operator/scenario:ro" `
        -v "${libDir}:/home/operator/lib:ro" `
        -v "${res}:/home/operator/out" `
        -e "CR_LIB=/home/operator/lib/clean-room-lib.sh" `
        -e "CR_SCENARIO_NAME=$ScenarioName" `
        -e "CR_MARKETPLACE_REPO=$MarketplaceRepo" `
        -e "CR_MARKETPLACE_NAME=$MarketplaceName" `
        -e "CR_PRIMARY_PLUGIN=$PrimaryPlugin" `
        -e "CR_EXPECT_DEPS=$ExpectDeps" `
        -e "CR_UV_INDEX=$UvIndex" `
        -e "CR_LOGDIR=/home/operator/cr-logs" `
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
    $untilArgs = @()
    if ($Until -ne 'all') { $untilArgs = @('-e', "CR_UNTIL=$Until") }
    Write-Host "== running clean-room scenario '$ScenarioName' ($Image, through stage $Until) ==" -ForegroundColor Cyan
    docker exec @untilArgs $Container /bin/bash -lc `
        'bash /home/operator/scenario/scenario.sh; rc=$?; cp -r $HOME/cr-logs /home/operator/out/ 2>/dev/null; exit $rc'
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

# Register the running container as an agent-bridge agent so you can drive the
# in-container Copilot with `agent-bridge send $AgentName "<prompt>"`. Uses the
# runtime provider API (a `docker exec ... copilot --acp --stdio` command agent);
# a static acp-agents.json cannot express a raw docker-exec transport.
function Invoke-BridgeRegister {
    Ensure-Container
    $py = (Get-Command python -ErrorAction SilentlyContinue) ?? (Get-Command python3 -ErrorAction SilentlyContinue)
    if (-not $py) { throw "python not found on PATH (needed to call the agent-bridge provider API)" }
    & $py.Source (Join-Path $Here 'bridge_register.py') register --container $Container --name $AgentName
    if ($LASTEXITCODE -ne 0) { throw "bridge registration failed" }
    Write-Host "drive it:  agent-bridge send $AgentName `"<prompt>`"" -ForegroundColor Green
}
function Invoke-BridgeUnregister {
    $py = (Get-Command python -ErrorAction SilentlyContinue) ?? (Get-Command python3 -ErrorAction SilentlyContinue)
    if (-not $py) { throw "python not found on PATH" }
    & $py.Source (Join-Path $Here 'bridge_register.py') unregister --name $AgentName
}

switch ($Mode) {
    'build' { Invoke-Build }
    'auth'  { Invoke-Auth }
    'run'   { Invoke-Run }
    'shell' { Invoke-Shell }
    'down'  { Invoke-Down }
    'bridge-register'   { Invoke-BridgeRegister }
    'bridge-unregister' { Invoke-BridgeUnregister }
    'all'   { Invoke-Build; Invoke-Run }
}
