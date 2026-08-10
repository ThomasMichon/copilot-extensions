# bootstrap.ps1 — fetch and launch the copilot-extensions Configurator.
#
#   iex (irm https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/configurator/bootstrap.ps1)
#
# This is delivered OUTSIDE the plugin pipe: it does not require any Copilot
# plugin to be installed, and does not go through `copilot plugin`. It fetches
# the Configurator payload and runs it.
#
# Phase 0 assumes git + uv are already present; automatic prerequisite
# provisioning (installing Python/uv/etc. and prompting for restarts) lands in
# Phase 2 (issue #355). Override the fetched ref with $env:CONFIGURATOR_REF.

$ErrorActionPreference = 'Stop'

$Repo = 'https://github.com/ThomasMichon/copilot-extensions.git'
$Ref  = if ($env:CONFIGURATOR_REF) { $env:CONFIGURATOR_REF } else { 'main' }
$Root = Join-Path $env:USERPROFILE '.copilot-extensions-configurator'
$Src  = Join-Path $Root 'src'

Write-Host 'copilot-extensions Configurator - bootstrap'

foreach ($tool in 'git', 'uv') {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "$tool is required for the Phase 0 bootstrap (automatic prerequisite install lands in Phase 2 / issue #355). Install $tool and re-run."
    }
}

New-Item -ItemType Directory -Force -Path $Root | Out-Null
if (Test-Path (Join-Path $Src '.git')) {
    git -C $Src fetch --depth 1 origin $Ref | Out-Null
    git -C $Src checkout -q FETCH_HEAD
} else {
    git clone --depth 1 --branch $Ref $Repo $Src | Out-Null
}

Push-Location (Join-Path $Src 'configurator')
try {
    uv run --quiet python -m configurator @args
} finally {
    Pop-Location
}
