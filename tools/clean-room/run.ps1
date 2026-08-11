<#
.SYNOPSIS
  Drive the copilot-extensions clean-room install-flow validation (Docker).

.DESCRIPTION
  Builds a credential-free Linux "fresh machine" image, captures a one-time
  device-code Copilot login into a cached ":authed" image, then runs the
  in-container validate.sh driver and copies its JSON report + logs back to the
  host. Iterating on validate.sh needs no rebuild -- it is bind-mounted at run.

.PARAMETER Mode
  build : build the credential-free base image only.
  auth  : launch the base image interactively for a one-time `copilot` device-code
          login, then `docker commit` to the cached :authed image. Do this once
          (re-run when the token expires).
  run   : run the driver against the :authed image (default).
  all   : build -> (auth if no :authed image) -> run.

.EXAMPLE
  ./run.ps1 -Mode all
#>
[CmdletBinding()]
param(
    [ValidateSet('build','auth','run','all')]
    [string]$Mode = 'run',
    [string]$MarketplaceRepo = 'ThomasMichon/copilot-extensions',
    [string]$MarketplaceName = 'copilot-extensions',
    [string]$PrimaryPlugin   = 'agent-codespaces',
    [string]$ExpectDeps      = 'agent-bridge agent-worktrees',
    [string]$BaseTag  = 'copilot-cleanroom:base',
    [string]$AuthTag  = 'copilot-cleanroom:authed'
)
$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Results = Join-Path $Here 'results'
New-Item -ItemType Directory -Force -Path $Results | Out-Null

function Test-Image($tag) { return [bool](docker images -q $tag 2>$null) }

function Invoke-Build {
    Write-Host "== building credential-free base image ($BaseTag) ==" -ForegroundColor Cyan
    # Governed machines block the public npm registry at the TLS layer; forward
    # the host's configured registry (e.g. an internal governed feed) so the
    # in-image `npm install -g @github/copilot` uses the same path the host does.
    $npmRegistry = (& npm config get registry 2>$null | Select-Object -First 1)
    if (-not $npmRegistry -or $npmRegistry -eq 'undefined' -or $npmRegistry -eq 'null') {
        $npmRegistry = 'https://registry.npmjs.org/'
    }
    Write-Host "   npm registry: $npmRegistry" -ForegroundColor DarkGray
    docker build --build-arg "NPM_REGISTRY=$npmRegistry" -t $BaseTag $Here
    if ($LASTEXITCODE -ne 0) { throw "docker build failed" }
}

function Invoke-Auth {
    if (-not (Test-Image $BaseTag)) { Invoke-Build }
    Write-Host "== one-time device-code login ==" -ForegroundColor Cyan
    Write-Host "An interactive Copilot session opens. Run '/login' if not prompted," -ForegroundColor Yellow
    Write-Host "authorize the device code in your browser, then '/exit'." -ForegroundColor Yellow
    docker rm -f cr-auth 2>$null | Out-Null
    # Interactive: the human completes the device-code flow, then exits.
    docker run -it --name cr-auth --entrypoint /bin/bash $BaseTag -lc 'copilot; echo "--- login session ended ---"'
    Write-Host "== committing authed image ($AuthTag) ==" -ForegroundColor Cyan
    docker commit cr-auth $AuthTag | Out-Null
    docker rm -f cr-auth | Out-Null
    Write-Host "cached $AuthTag" -ForegroundColor Green
}

function Invoke-Run {
    if (-not (Test-Image $AuthTag)) {
        Write-Host "no $AuthTag image yet -- running auth first." -ForegroundColor Yellow
        Invoke-Auth
    }
    Write-Host "== running clean-room validation ==" -ForegroundColor Cyan
    $validate = ($Here + '/validate.sh') -replace '\\','/'
    docker run --rm `
        -v "${validate}:/home/operator/validate.sh:ro" `
        -v "${Results}:/home/operator/out" `
        -e "CR_MARKETPLACE_REPO=$MarketplaceRepo" `
        -e "CR_MARKETPLACE_NAME=$MarketplaceName" `
        -e "CR_PRIMARY_PLUGIN=$PrimaryPlugin" `
        -e "CR_EXPECT_DEPS=$ExpectDeps" `
        -e "CR_REPORT=/home/operator/out/cr-report.json" `
        --entrypoint /bin/bash `
        $AuthTag -lc 'cp -r $HOME/cr-logs /home/operator/out/ 2>/dev/null; bash /home/operator/validate.sh; rc=$?; cp -r $HOME/cr-logs /home/operator/out/ 2>/dev/null; exit $rc'
    $rc = $LASTEXITCODE
    Write-Host "`n== report ==" -ForegroundColor Cyan
    if (Test-Path (Join-Path $Results 'cr-report.json')) {
        Get-Content (Join-Path $Results 'cr-report.json') -Raw | Write-Host
    }
    Write-Host "results dir: $Results"
    exit $rc
}

switch ($Mode) {
    'build' { Invoke-Build }
    'auth'  { Invoke-Auth }
    'run'   { Invoke-Run }
    'all'   { Invoke-Build; if (-not (Test-Image $AuthTag)) { Invoke-Auth }; Invoke-Run }
}
