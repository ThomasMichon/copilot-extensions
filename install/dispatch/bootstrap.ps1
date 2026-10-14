# install/dispatch/bootstrap.ps1 -- the STABLE, public install entry point
# for the agent-dispatch standalone package.
#
#   iex (irm https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/install/dispatch/bootstrap.ps1)
#
# This is the file any published URL, bookmarked command, or another
# plugin's hardcoded installer reference should point at -- see
# docs/patterns/standalone-service-layout.md's "public-install-surface
# indirection". It never moves and never changes shape. It does nothing but
# resolve and exec the CURRENT real bootstrap
# (services/dispatch/bootstrap.ps1 today); when that package moves to a new
# top-level location in a future reorganization, only $RealPathParts below
# needs updating -- every external caller's URL stays byte-for-byte the same.
#
# Every argument is forwarded verbatim to the real bootstrap script.

$ErrorActionPreference = 'Stop'

$RepoOwner = 'ThomasMichon'
$RepoName  = 'copilot-extensions'
$Ref       = if ($env:AGENT_DISPATCH_SERVICE_REF) { $env:AGENT_DISPATCH_SERVICE_REF } else { 'main' }

# <-- The ONLY line that should ever need updating when the real package moves.
$RealPathParts = @('services', 'dispatch', 'bootstrap.ps1')

$RealUrlPath = $RealPathParts -join '/'

# Prefer a local sibling copy when this proxy is itself running from a
# cloned checkout (a developer invoking it directly, or a test) rather than
# a bare `irm` fetch -- avoids a redundant network round-trip and lets this
# proxy be exercised offline.
$LocalReal = $null
if ($PSScriptRoot) {
    $candidate = Join-Path $PSScriptRoot ("..\..\" + ($RealPathParts -join '\'))
    if (Test-Path -LiteralPath $candidate) { $LocalReal = $candidate }
}

if ($LocalReal) {
    & $LocalReal @Args
    exit $LASTEXITCODE
}

$RealUrl = "https://raw.githubusercontent.com/$RepoOwner/$RepoName/$Ref/$RealUrlPath"
Write-Host "install/dispatch/bootstrap.ps1: fetching the real bootstrap ($RealUrlPath, $Ref)..."
$TempFile = Join-Path ([IO.Path]::GetTempPath()) "agent-dispatch-install-proxy-$PID.ps1"
try {
    Invoke-WebRequest -Uri $RealUrl -UseBasicParsing -OutFile $TempFile
    & $TempFile @Args
    exit $LASTEXITCODE
} finally {
    Remove-Item -LiteralPath $TempFile -Force -ErrorAction SilentlyContinue
}
