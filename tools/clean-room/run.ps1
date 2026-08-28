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
    [ValidateSet('build','auth','run','eval','shell','down','bridge-register','bridge-unregister','all')]
    [string]$Mode = 'run',
    [ValidateSet('base','pristine')]
    [string]$Image = 'base',
    # Optional suffix to make the container + agent-bridge agent names UNIQUE, so
    # a second clean-room of the SAME image can run concurrently without the
    # `docker rm -f cr-<image>` in Start-Container clobbering another agent's box.
    # e.g. -Image base -NameSuffix agc -> container `cr-base-agc`, agent
    # `cleanroom-base-agc`. Empty = the legacy `cr-<image>` name. Docker-name-safe.
    [ValidatePattern('^$|^[a-zA-Z0-9][a-zA-Z0-9_.-]*$')]
    [string]$NameSuffix = '',
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
    [string]$ResultsDir = '',
    # Generic host->container value relay: names of HOST environment variables to
    # forward into the container (in addition to COPILOT_GITHUB_TOKEN). This is
    # the substrate seam a downstream harness uses to relay a host-minted testing
    # credential (e.g. an ADO/az bearer its own wrapper mints) into a live
    # scenario, without baking any product/tenant specifics into this public
    # runner. Only names that are actually set on the host are forwarded.
    [string[]]$PassEnv = @(),
    # Tier-E seam: bind a downstream harness tree (read-only) into the box at
    # /harness and export CR_HARNESS_MOUNT=/harness, so a name-ful eval scenario
    # can reach the operator's local plugins/skills (e.g. an in-repo `.ai/`
    # marketplace) without the public rig naming any repo. Empty = no harness
    # mount (the scenario's CR_HARNESS_MOUNT default applies). Host path; the
    # container path is fixed at /harness. Also $env:CR_HARNESS_MOUNT_HOST.
    [string]$HarnessMount = '',
    # Tier-E only: override the manifest's runs.count (how many times the driven
    # agent is run for flake aggregation). 0/empty = use the manifest (default 1).
    [int]$Runs = 0,
    # Tier-E only: skip the cheap in-box Tier-P precondition (a `<plugin> --version`
    # smoke check that a broken CLI surface doesn't waste an eval's credits). The
    # gate is ON by default because it is nearly free; pass this to force an eval.
    [switch]$SkipTierPGate,
    # --- Windows arm (formalized Windows-container flow) ---------------------
    # Run the WINDOWS clean-room arm (a Windows container running scenario.ps1)
    # instead of the default Linux arm. Requires a Windows-container engine on the
    # box this runs on. See scenario.ps1 + Dockerfile.windows.
    [ValidateSet('linux', 'windows')]
    [string]$Os = 'linux',
    # docker -H endpoint. Empty = the CLI default (honors $env:DOCKER_HOST). On a
    # Windows-only host reached via the non-elevated loopback-TCP broker, pass
    # tcp://127.0.0.1:2375 (or set $env:DOCKER_HOST).
    [string]$DockerEndpoint = '',
    # Windows arm: the partner harness tree to validate (mounted at C:\partner),
    # or -PartnerRepo to clone one on the host first. -PartnerPlugins are the
    # vendored plugins to structurally check.
    [string]$PartnerPath = '',
    [string]$PartnerRepo = '',
    [string]$PartnerName = 'partner-harness',
    [string]$PartnerPlugins = 'agent-bridge agent-codespaces'
)
$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path

# --- Until validation: 'all' or a non-negative integer ------------------------
if ($Until -ne 'all' -and $Until -notmatch '^\d+$') {
    throw "-Until must be 'all' or a non-negative integer (got '$Until')"
}

# =============================================================================
# Windows arm -- a self-contained branch that runs a Windows scenario (scenario.ps1)
# in a Windows container, harmonizing the clean-room across Linux and Windows.
# It does NOT touch the Linux auth/bridge/eval machinery below (which is
# ubuntu/agent-oriented and irrelevant to a deterministic Tier-P Windows scenario).
# Runs on a Windows-container host; the harness-side remote driver
# (transfer the drop to that host) lives in the consuming harness.
# =============================================================================
if ($Os -eq 'windows') {
    $dh = @()
    if ($DockerEndpoint) { $dh = @('-H', $DockerEndpoint) }
    $WinTag = 'copilot-cleanroom:windows'
    $WinDockerfile = Join-Path $Here 'Dockerfile.windows'

    # scenario resolution — a Windows scenario provides scenario.ps1
    if (Test-Path -PathType Container $Scenario) { $sdir = (Resolve-Path $Scenario).Path; $sname = Split-Path -Leaf $sdir }
    elseif (Test-Path -PathType Container (Join-Path $Here "scenarios\$Scenario")) { $sdir = Join-Path $Here "scenarios\$Scenario"; $sname = $Scenario }
    else { throw "unknown -Scenario '$Scenario'" }
    if (-not (Test-Path (Join-Path $sdir 'scenario.ps1'))) {
        throw "scenario '$sname' has no scenario.ps1 (the Windows arm needs a PowerShell scenario)"
    }
    $libDir = Join-Path $Here 'lib'
    if (-not $ResultsDir) { $ResultsDir = $env:CR_RESULTS_DIR }
    if (-not $ResultsDir) {
        $root = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME '.local\state' }
        $ResultsDir = Join-Path $root ("copilot-cleanroom\runs\win-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    }
    New-Item -ItemType Directory -Force -Path $ResultsDir | Out-Null

    function Test-WinImage { [bool](& docker @dh images -q $WinTag 2>$null) }
    if ($Mode -eq 'down') {
        & docker @dh rm -f "cr-windows" 2>$null | Out-Null
        Write-Host "windows: nothing persistent to tear down (scenarios run --rm)."; exit 0
    }
    if ($Mode -in @('build', 'all') -or -not (Test-WinImage)) {
        Write-Host "== building Windows image ($WinTag) from Dockerfile.windows ==" -ForegroundColor Cyan
        & docker @dh build --isolation=hyperv -f $WinDockerfile -t $WinTag $Here
        if ($LASTEXITCODE -ne 0) { throw "docker build (windows) failed" }
        if ($Mode -eq 'build') { exit 0 }
    }

    # partner tree: an explicit -PartnerPath, or clone -PartnerRepo on the host
    $partner = $PartnerPath
    $clonedPartner = $null
    if (-not $partner -and $PartnerRepo) {
        $clonedPartner = Join-Path $ResultsDir 'partner-src'
        Write-Host "== cloning $PartnerRepo -> $clonedPartner ==" -ForegroundColor Cyan
        & git clone --depth 1 $PartnerRepo $clonedPartner
        if ($LASTEXITCODE -ne 0) { throw "git clone of -PartnerRepo failed" }
        $partner = $clonedPartner
    }
    if (-not $partner -or -not (Test-Path -LiteralPath $partner)) {
        throw "the Windows scenario needs a partner tree: pass -PartnerPath <dir> or -PartnerRepo <url>"
    }
    $partner = (Resolve-Path -LiteralPath $partner).Path

    $untilArgs = @()
    if ($Until -ne 'all') { $untilArgs = @('-e', "CR_UNTIL=$Until") }

    Write-Host "== running Windows clean-room scenario '$sname' (through stage $Until) ==" -ForegroundColor Cyan
    & docker @dh run --rm --isolation=hyperv `
        -v "${sdir}:C:\scenario:ro" `
        -v "${libDir}:C:\lib:ro" `
        -v "${ResultsDir}:C:\out" `
        -v "${partner}:C:\partner:ro" `
        -e "CR_LIB=C:\lib\clean-room-lib.ps1" `
        -e "CR_PARTNER_PATH=C:\partner" `
        -e "CR_PARTNER_NAME=$PartnerName" `
        -e "CR_PARTNER_PLUGINS=$PartnerPlugins" `
        -e "CR_SCENARIO_NAME=$sname" `
        -e "CR_REPORT=C:\out\cr-report.json" `
        -e "CR_LOGDIR=C:\out\cr-logs" `
        @untilArgs `
        $WinTag powershell -NoProfile -ExecutionPolicy Bypass -File C:\scenario\scenario.ps1
    $rc = $LASTEXITCODE

    Write-Host "`n== report ==" -ForegroundColor Cyan
    $repPath = Join-Path $ResultsDir 'cr-report.json'
    if (Test-Path $repPath) { Get-Content $repPath -Raw | Write-Host }
    Write-Host "results dir: $ResultsDir"
    if ($clonedPartner) { Remove-Item -Recurse -Force $clonedPartner -ErrorAction SilentlyContinue }
    exit $rc
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
# A Tier-P scenario drives scenario.sh; a Tier-E scenario drives setup.sh (its
# starting state) + an agent eval. Require the one that matches what's present.
if (-not (Test-Path (Join-Path $ScenarioDir 'scenario.sh')) -and
    -not (Test-Path (Join-Path $ScenarioDir 'setup.sh'))) {
    throw "scenario '$ScenarioName' has neither scenario.sh (Tier-P) nor setup.sh (Tier-E)"
}
$LibDir = Join-Path $Here 'lib'
. (Join-Path $LibDir 'acp-command.ps1')
# Optional per-suite shared helpers: if the selected scenario's parent dir holds
# a `_lib/`, mount it read-only at /home/operator/scenario-lib and expose it as
# $CR_SCENARIO_LIB, so sibling scenarios in a suite can source shared phase
# helpers instead of each duplicating them. Opt-in: absent -> unchanged.
$ScenarioSharedLib = Join-Path (Split-Path -Parent $ScenarioDir) '_lib'
if (-not (Test-Path -PathType Container $ScenarioSharedLib)) { $ScenarioSharedLib = $null }

# --- image/tag/container naming (base keeps the legacy :authed tag) -----------
# $NameSuffix makes the CONTAINER + agent names unique (concurrent clean-rooms of
# the same image); the image/tag are shared (name-collision is only on the
# container, so a suffix never forces a rebuild).
$Dockerfile = if ($Image -eq 'pristine') { 'Dockerfile.pristine' } else { 'Dockerfile' }
$BaseTag    = "copilot-cleanroom:$Image"
$AuthTag    = if ($Image -eq 'base') { 'copilot-cleanroom:authed' } else { "copilot-cleanroom:$Image-authed" }
$NameTail   = if ($NameSuffix) { "-$NameSuffix" } else { '' }
$Container  = "cr-$Image$NameTail"
$AgentName  = "cleanroom-$Image$NameTail"   # legacy label (kept for logs)
$DriveAgent = "cleanroom:$Container"        # the namespaced agent-bridge address
# The in-container Copilot ACP command the cleanroom: provider resolves to. The
# eval path overrides this to add --plugin-dir for the scenario's plugins (a bare
# copilot --acp does not reliably load enabled plugins headless). Script-scoped so
# Invoke-Eval can set it before Invoke-BridgeRegister bakes it into the manifest.
$script:AcpCommand = 'copilot --acp --stdio --allow-all-tools'
$script:AcpCwd = ''
$script:BridgeContainerId = ''

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
    # Optional per-suite shared-lib mount (see scenario resolution above).
    $scenLibArgs = @()
    if ($ScenarioSharedLib) {
        $slib = ($ScenarioSharedLib) -replace '\\','/'
        $scenLibArgs = @(
            '-v', "${slib}:/home/operator/scenario-lib:ro",
            '-e', 'CR_SCENARIO_LIB=/home/operator/scenario-lib'
        )
    }
    # Generic host->container value relay: forward each requested host env var by
    # NAME (value stays in the runner's env, not on the docker CLI args). Only
    # names actually set on the host are forwarded; a missing one is skipped with
    # a warning so a downstream harness fails loud rather than silently unauthed.
    $passArgs = @()
    foreach ($name in $PassEnv) {
        if ([string]::IsNullOrWhiteSpace($name)) { continue }
        $val = [Environment]::GetEnvironmentVariable($name)
        if ([string]::IsNullOrEmpty($val)) {
            Write-Host "warn: -PassEnv '$name' is not set on the host -- not forwarding" -ForegroundColor Yellow
            continue
        }
        Set-Item -Path "Env:\$name" -Value $val
        $passArgs += @('-e', $name)
    }
    # Optional downstream-harness bind (Tier-E seam): mount a harness tree
    # read-only at /harness and expose CR_HARNESS_MOUNT so a name-ful eval
    # scenario reaches the operator's local plugins/skills. Host path from
    # -HarnessMount or $env:CR_HARNESS_MOUNT_HOST; the container path is fixed.
    $harnessArgs = @()
    $harnessHost = if ($HarnessMount) { $HarnessMount } else { $env:CR_HARNESS_MOUNT_HOST }
    if ($harnessHost) {
        if (-not (Test-Path -PathType Container $harnessHost)) {
            throw "-HarnessMount '$harnessHost' is not a directory"
        }
        $hm = ((Resolve-Path $harnessHost).Path) -replace '\\','/'
        $harnessArgs = @(
            '-v', "${hm}:/harness:ro",
            '-e', 'CR_HARNESS_MOUNT=/harness'
        )
        Write-Host "harness bind: $hm -> /harness (ro)  [CR_HARNESS_MOUNT=/harness]" -ForegroundColor DarkGray
    }
    docker run -d --name $Container `
        -v "${scenDir}:/home/operator/scenario:ro" `
        -v "${libDir}:/home/operator/lib:ro" `
        -v "${res}:/home/operator/out" `
        @scenLibArgs `
        @harnessArgs `
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
        @passArgs `
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
    Write-Host "   run './run.ps1 -Image $Image$(if($NameSuffix){" -NameSuffix $NameSuffix"}) -Mode down' to remove it." -ForegroundColor DarkGray
    docker exec -it $Container /bin/bash -l
}

function Invoke-Down {
    $containerId = $script:BridgeContainerId
    if (-not $containerId) {
        $containerId = (& docker inspect -f '{{.Id}}' $Container 2>$null | Out-String).Trim()
    }
    if (-not $containerId) {
        try {
            Invoke-BridgeUnregister | Out-Null
        } catch {
            throw "no container found and stale registration cleanup failed for ${Container}: $_"
        }
        Write-Host "removed $Container" -ForegroundColor Green
        return
    }
    docker rm -f $containerId 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "could not remove $Container ($containerId)"
    }
    try {
        Invoke-BridgeUnregister -ContainerId $containerId | Out-Null
    } catch {
        throw "removed $Container but could not unregister it from agent-bridge: $_"
    }
    Write-Host "removed $Container" -ForegroundColor Green
}

# Register the running container with agent-bridge so you can drive the
# in-container Copilot with `agent-bridge create cleanroom:<container> ...`. Uses
# the declarative `providers.d/` namespace-provider model (agent-bridge >= dev307;
# the old runtime provider POST API was retired, ce#582): bridge_register.py drops
# a `cleanroom` manifest and *is* the provider CLI the daemon shells out to.
function Invoke-BridgeRegister {
    Ensure-Container
    $py = (Get-Command python -ErrorAction SilentlyContinue) ?? (Get-Command python3 -ErrorAction SilentlyContinue)
    if (-not $py) { throw "python not found on PATH (needed to register the agent-bridge provider)" }
    $bridgeArgs = @('--acp-command', $script:AcpCommand)
    if ($script:AcpCwd) { $bridgeArgs += @('--acp-cwd', $script:AcpCwd) }
    $bridgeArgs += @('register', '--container', $Container, '--name', $AgentName)
    $response = (& $py.Source (Join-Path $Here 'bridge_register.py') @bridgeArgs | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw "bridge registration failed" }
    try {
        $registration = $response | ConvertFrom-Json
        $script:BridgeContainerId = [string]$registration.container_id
    } catch {
        throw "bridge registration returned invalid JSON"
    }
    if (-not $script:BridgeContainerId) {
        throw "bridge registration returned no container ID"
    }
    Write-Host $response
    Write-Host "drive it:  agent-bridge create $DriveAgent `"<prompt>`"" -ForegroundColor Green
}
function Invoke-BridgeUnregister([string]$ContainerId = '') {
    $py = (Get-Command python -ErrorAction SilentlyContinue) ?? (Get-Command python3 -ErrorAction SilentlyContinue)
    if (-not $py) { throw "python not found on PATH" }
    if (-not $ContainerId) { $ContainerId = $script:BridgeContainerId }
    if (-not $ContainerId) {
        $ContainerId = (& docker inspect -f '{{.Id}}' $Container 2>$null | Out-String).Trim()
    }
    $unregisterArgs = @('unregister', '--name', $AgentName, '--container', $Container)
    if ($ContainerId) {
        $unregisterArgs += @('--container-id', $ContainerId)
    } else {
        $unregisterArgs += @('--stale')
    }
    & $py.Source (Join-Path $Here 'bridge_register.py') @unregisterArgs
    if ($LASTEXITCODE -ne 0) { throw "bridge unregistration failed" }
}
# End any prior agent-bridge session for an agent so a fresh `create` isn't
# refused with "already has an active session" (a recreated box leaves the old
# session record behind). Idempotent + best-effort.
function Invoke-EndAgentSessions([string]$Agent) {
    $json = (& agent-bridge --json sessions 2>$null | Out-String)
    if (-not $json.Trim()) { return }
    try { $sessions = $json | ConvertFrom-Json } catch { return }
    foreach ($s in @($sessions)) {
        if ($s.agent_name -eq $Agent -and $s.session_id) {
            Write-Host "   (ending prior session $($s.session_id) for $Agent)" -ForegroundColor DarkGray
            & agent-bridge end $s.session_id --force 2>$null | Out-Null
        }
    }
}

# Short SHA-256 of a string (first 16 hex chars). Used to fingerprint the exact
# injected prompt so a Tier-E verdict is reproducible-in-context (the same prompt
# + same docs hash + same copilot_version should reproduce a verdict).
function Get-Sha256Short([string]$Text) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes(($Text ?? ''))
        return -join ($sha.ComputeHash($bytes)[0..7] | ForEach-Object { $_.ToString('x2') })
    } finally { $sha.Dispose() }
}

# Drive one agent turn with a wall-clock timeout. `agent-bridge create` has no
# --reply-timeout, so bound it host-side via a job: on timeout, stop the job and
# report the partial transcript so a hung agent is a FAIL, not an infinite wait.
# Returns @{ transcript; duration_s; timed_out }.
function Invoke-DriveWithTimeout([string]$Agent, [string]$PromptFile, [int]$TimeoutSec) {
    $t0 = Get-Date
    $job = Start-Job -ScriptBlock {
        param($a, $pf)
        & agent-bridge create $a --prompt-file $pf --expand all --no-color 2>&1 | Out-String
    } -ArgumentList $Agent, $PromptFile
    $timedOut = $false
    if ($TimeoutSec -gt 0) {
        if (-not (Wait-Job $job -Timeout $TimeoutSec)) { $timedOut = $true }
    } else {
        Wait-Job $job | Out-Null
    }
    $out = (Receive-Job $job 2>&1 | Out-String)
    if ($timedOut) {
        Stop-Job $job -ErrorAction SilentlyContinue
        $out += "`n[clean-room] TIMED OUT after ${TimeoutSec}s -- driven agent did not complete its turn.`n"
    }
    Remove-Job $job -Force -ErrorAction SilentlyContinue
    return @{ transcript = $out; duration_s = [int]((Get-Date) - $t0).TotalSeconds; timed_out = $timedOut }
}

# Tier-E (agent-driven eval): establish the scenario's starting state, drive the
# in-container Copilot over agent-bridge with a literal-mode + stated-purpose
# prompt, and capture the transcript(s) as judge evidence. The runner produces
# eval/ artifacts and prints the judge-packet path; it does NOT itself judge --
# that is the `validating-in-clean-room` skill's `clean-room-judge` handoff (keeps
# this shell runner free of any model/agent dependency). See TIER-E-EXECUTION.md.
function Invoke-Eval {
    # --- read the Tier-E manifest -------------------------------------------
    $manifestPath = Join-Path $ScenarioDir 'manifest.json'
    if (-not (Test-Path $manifestPath)) { throw "eval: scenario '$ScenarioName' has no manifest.json" }
    $manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
    if ($manifest.tier -ne 'E') {
        Write-Host "warn: scenario '$ScenarioName' is tier '$($manifest.tier)', not 'E' -- -Mode eval expects a Tier-E scenario." -ForegroundColor Yellow
    }
    # setup driver (starting state) + prompt + runs.count + post_check
    $setupRel = if ($manifest.starting_state -and $manifest.starting_state.setup) { $manifest.starting_state.setup } else { 'setup.sh' }
    if (-not (Test-Path (Join-Path $ScenarioDir $setupRel))) { throw "eval: setup driver '$setupRel' not found in scenario dir" }
    $prompt = $manifest.prompt
    if (-not $prompt) {
        $promptFile = Join-Path $ScenarioDir 'prompt.md'
        if (-not (Test-Path $promptFile)) { throw "eval: no 'prompt' in manifest and no prompt.md in scenario dir" }
        $prompt = Get-Content $promptFile -Raw
    }
    $runCount = if ($Runs -gt 0) { $Runs } elseif ($manifest.runs -and $manifest.runs.count) { [int]$manifest.runs.count } else { 1 }
    $perTurnTimeout = if ($manifest.runs -and $manifest.runs.per_turn_timeout_s) { [int]$manifest.runs.per_turn_timeout_s } else { 0 }
    $postCheck = $manifest.post_check
    if (-not $postCheck -and (Test-Path (Join-Path $ScenarioDir 'post_check.sh'))) { $postCheck = 'post_check.sh' }
    # ACP launch settings. A static cwd is known now; a setup-generated cwd file
    # is resolved after the setup driver runs.
    $acpCwd = ''
    $acpCwdFile = ''
    $acpPluginDirs = @()
    if ($manifest.eval -and $manifest.eval.acp_plugin_dirs) {
        $acpPluginDirs = @($manifest.eval.acp_plugin_dirs) | Where-Object { $_ }
        foreach ($acpPluginDir in $acpPluginDirs) {
            $acpPluginDir = [string]$acpPluginDir
            if (-not $acpPluginDir.StartsWith('/') -or $acpPluginDir -match "[`0`r`n`t]") {
                throw 'eval.acp_plugin_dirs entries must be absolute in-container POSIX paths'
            }
        }
    }
    if ($manifest.eval -and $manifest.eval.acp_cwd) {
        $acpCwd = [string]$manifest.eval.acp_cwd
        if (-not $acpCwd.StartsWith('/') -or $acpCwd -match "[`0`r`n`t]") {
            throw 'eval.acp_cwd must be an absolute in-container POSIX path'
        }
    }
    if ($manifest.eval -and $manifest.eval.acp_cwd_file) {
        $acpCwdFile = [string]$manifest.eval.acp_cwd_file
        if (-not $acpCwdFile.StartsWith('/') -or $acpCwdFile -match "[`0`r`n`t]") {
            throw 'eval.acp_cwd_file must be an absolute in-container POSIX path'
        }
    }
    if ($acpCwd -and $acpCwdFile) {
        throw 'eval.acp_cwd and eval.acp_cwd_file are mutually exclusive'
    }
    # Tier-P precondition: an in-box command that must exit 0 before we spend the
    # drive (a broken CLI surface would fail the eval for the wrong reason).
    # Explicit manifest.tier_p_precondition wins; else `<first installed plugin>
    # --version` (every runtime plugin exposes it).
    $installedPlugins = @()
    if ($manifest.starting_state -and $manifest.starting_state.installed_plugins) { $installedPlugins = @($manifest.starting_state.installed_plugins) }
    $tierPCmd = $manifest.tier_p_precondition
    if (-not $tierPCmd -and $installedPlugins.Count -gt 0) { $tierPCmd = "$($installedPlugins[0]) --version" }

    # --- literal-mode fixture (substrate-owned; injected, never in a plugin) --
    $litPath = Join-Path $LibDir 'literal-mode.md'
    if (-not (Test-Path $litPath)) { throw "eval: literal-mode fixture missing at $litPath" }
    $literal = Get-Content $litPath -Raw
    $fullPrompt = "$literal`n`n--- TASK ---`n`n$prompt"

    # --- 1) start box + 2) establish starting state --------------------------
    Start-Container
    Write-Host "== eval: establishing starting state ($setupRel) ==" -ForegroundColor Cyan
    docker exec $Container /bin/bash -lc `
        "bash /home/operator/scenario/$setupRel; rc=`$?; cp -r `$HOME/cr-logs /home/operator/out/ 2>/dev/null; exit `$rc"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "warn: setup driver exited $LASTEXITCODE -- the starting state may be incomplete (see cr-report.json)." -ForegroundColor Yellow
    }

    # Resolve and build the driven-agent command after setup. acp_cwd_file lets
    # setup publish a generated managed-worktree path without using a symlink.
    if ($acpCwdFile) {
        $acpCwd = (& docker exec $Container python3 -c @'
import pathlib, sys
p = pathlib.Path(sys.argv[1])
lines = p.read_text(encoding="utf-8").splitlines()
if len(lines) != 1 or not lines[0].startswith("/") or any(c in lines[0] for c in "\0\r\n\t"):
    raise SystemExit("invalid ACP cwd file")
cwd = pathlib.Path(lines[0])
if not cwd.is_dir():
    raise SystemExit("ACP cwd is not a directory")
print(cwd)
'@ $acpCwdFile | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or -not $acpCwd) {
            throw "eval: could not resolve a valid cwd from '$acpCwdFile'"
        }
    }
    $acp = New-CleanRoomAcpCommand -PluginDirs $acpPluginDirs
    $script:AcpCwd = $acpCwd
    if ($acpCwd) {
        $quotedCwd = ConvertTo-CleanRoomBashLiteral $acpCwd
        $acp = "cd -- $quotedCwd && $acp"
    }
    $script:AcpCommand = $acp
    Write-Host "eval: ACP command -> $acp" -ForegroundColor DarkGray

    # --- Tier-P precondition: cheap in-box smoke of the plugin CLI -----------
    # Don't spend an eval's credits on a broken CLI surface -- if `<plugin>
    # --version` (or the manifest's explicit command) fails, the eval would red
    # for the wrong reason. On by default; -SkipTierPGate forces past it.
    if (-not $SkipTierPGate -and $tierPCmd) {
        Write-Host "== eval: Tier-P precondition ($tierPCmd) ==" -ForegroundColor Cyan
        docker exec $Container /bin/bash -lc "$tierPCmd" | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "eval: Tier-P precondition '$tierPCmd' failed (exit $LASTEXITCODE) -- refusing to spend an eval on a broken CLI surface. Fix the plugin's *-solo Tier-P scenario first, or pass -SkipTierPGate to force."
        }
        Write-Host "   precondition OK" -ForegroundColor DarkGray
    }

    # --- 3) register the box as a bridge agent -------------------------------
    Invoke-BridgeRegister

    # --- eval/ artifacts -----------------------------------------------------
    $evalDir = Join-Path $Results 'eval'
    New-Item -ItemType Directory -Force -Path $evalDir | Out-Null
    Set-Content -Path (Join-Path $evalDir 'literal-mode.txt') -Value $literal -Encoding utf8
    $promptTxt = Join-Path $evalDir 'prompt.txt'
    Set-Content -Path $promptTxt -Value $fullPrompt -Encoding utf8

    # --- reproducibility fingerprints ---------------------------------------
    # A Tier-E verdict is only meaningful against the exact prompt + the docs the
    # agent could see. Fingerprint both so a verdict is reproducible-in-context
    # and a doc change that flips it is visible.
    $promptHash = Get-Sha256Short $fullPrompt
    $docsHash = (docker exec $Container /bin/bash -lc `
        'find $HOME/.copilot/installed-plugins -type f \( -name "*.md" -o -name "*.json" -o -name "*.sh" \) 2>/dev/null | sort | xargs sha256sum 2>/dev/null | sha256sum | cut -c1-16' `
        2>$null | Select-Object -First 1)

    # --- 4/5) drive N times + capture transcripts ----------------------------
    # Always CREATE a fresh session per run (never `send <agent>`, which resumes a
    # prior session -- a stale one whose recreated box gives HTTP 500). Pass the
    # prompt via --prompt-file (multi-line safe; no PowerShell argv mangling) and
    # --expand all so thoughts + tool calls land in the transcript for the judge.
    # Each turn is wall-clock bounded (runs.per_turn_timeout_s) so a hung agent is
    # a FAIL, not an infinite wait.
    Write-Host "== eval: driving '$DriveAgent' x$runCount (fresh session; literal-mode + stated purpose) ==" -ForegroundColor Cyan
    if ($perTurnTimeout -gt 0) { Write-Host "   per-turn timeout: ${perTurnTimeout}s" -ForegroundColor DarkGray }
    $runRecords = @()
    for ($n = 1; $n -le $runCount; $n++) {
        $runDir = if ($runCount -eq 1) { $evalDir } else { $d = Join-Path $evalDir "run-$n"; New-Item -ItemType Directory -Force -Path $d | Out-Null; $d }
        $transcriptPath = Join-Path $runDir 'transcript.txt'
        Write-Host "   -- run $n/$runCount --" -ForegroundColor DarkGray
        Invoke-EndAgentSessions $DriveAgent
        $drv = Invoke-DriveWithTimeout $DriveAgent $promptTxt $perTurnTimeout
        Set-Content -Path $transcriptPath -Value $drv.transcript -Encoding utf8
        $runRecords += [pscustomobject]@{
            n          = $n
            transcript = ($transcriptPath -replace [regex]::Escape($Results + '\'), '') -replace '\\','/'
            duration_s = $drv.duration_s
            timed_out  = $drv.timed_out
        }
        $tag = if ($drv.timed_out) { " -- TIMED OUT" } else { '' }
        $col = if ($drv.timed_out) { 'Yellow' } else { 'DarkGray' }
        Write-Host "      transcript -> $transcriptPath  ($($drv.duration_s)s)$tag" -ForegroundColor $col
    }

    # --- 6) optional programmatic post-check (ground-truth evidence) ---------
    if ($postCheck -and (Test-Path (Join-Path $ScenarioDir $postCheck))) {
        Write-Host "== eval: programmatic post-check ($postCheck) ==" -ForegroundColor Cyan
        docker exec $Container /bin/bash -lc `
            "bash /home/operator/scenario/$postCheck; cp -r `$HOME/cr-logs /home/operator/out/ 2>/dev/null" | Out-Null
    }

    # --- 7) unregister the bridge agent --------------------------------------
    $bridgeCleanupError = ''
    try {
        Invoke-BridgeUnregister 2>$null
    } catch {
        $bridgeCleanupError = "could not unregister $Container from agent-bridge: $_"
        Write-Warning $bridgeCleanupError
    }

    # --- write the eval run-manifest (judge packet index) --------------------
    $copilotVer = (docker exec $Container /bin/bash -lc 'copilot --version 2>/dev/null' 2>$null | Select-Object -First 1)
    $evalMeta = [ordered]@{
        scenario     = $ScenarioName
        tier         = 'E'
        family       = $manifest.family
        image        = $Image
        prompt       = 'eval/prompt.txt'
        literal_mode = 'eval/literal-mode.txt'
        runs         = $runRecords
        run_count    = $runCount
        aggregate_policy = if ($manifest.runs -and $manifest.runs.aggregate) { $manifest.runs.aggregate } else { 'unanimous' }
        copilot_version  = $copilotVer
        prompt_hash      = $promptHash
        docs_hash        = $docsHash
        per_turn_timeout_s = $perTurnTimeout
        tier_p_precondition = if ($SkipTierPGate) { "$tierPCmd (SKIPPED)" } else { $tierPCmd }
        bridge_cleanup_error = $bridgeCleanupError
        max_credits_note = "runs.max_credits is advisory: the agent-bridge `create` transport does not expose per-turn credits, so it cannot be hard-enforced from the runner (see TIER-E-EXECUTION.md)."
        report       = 'cr-report.json'
        cr_logs      = 'cr-logs/'
        judged       = $false
        note         = "Run clean-room-judge on this packet, then write cr-eval.json (see TIER-E-EXECUTION.md)."
    }
    $evalMeta | ConvertTo-Json -Depth 6 | Set-Content -Path (Join-Path $evalDir 'eval-run.json') -Encoding utf8

    # --- report the judge packet --------------------------------------------
    Write-Host "`n== eval complete ==" -ForegroundColor Green
    Write-Host "judge packet (hand to clean-room-judge via the validating-in-clean-room skill):" -ForegroundColor Cyan
    Write-Host "  expected outcome : $manifestPath (manifest.expected_outcome + prompt)"
    Write-Host "  transcript(s)    : $evalDir"
    Write-Host "  report + logs    : $Results"
    Write-Host "  run index        : $(Join-Path $evalDir 'eval-run.json')"
    Write-Host "results dir: $Results"
    switch ($Then) {
        'shell' { Invoke-Shell }
        'down'  { Invoke-Down }
    }
    if ($bridgeCleanupError) { throw $bridgeCleanupError }
}

switch ($Mode) {
    'build' { Invoke-Build }
    'auth'  { Invoke-Auth }
    'run'   { Invoke-Run }
    'eval'  { Invoke-Eval }
    'shell' { Invoke-Shell }
    'down'  { Invoke-Down }
    'bridge-register'   { Invoke-BridgeRegister }
    'bridge-unregister' { Invoke-BridgeUnregister }
    'all'   { Invoke-Build; Invoke-Run }
}
