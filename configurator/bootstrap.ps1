# bootstrap.ps1 — fetch, version-install, and launch the copilot-extensions Configurator.
#
#   iex (irm https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/configurator/bootstrap.ps1)
#
# This is delivered OUTSIDE the plugin pipe: it does not require any Copilot
# plugin to be installed, and does not go through `copilot plugin`. It fetches
# the Configurator payload, installs it under the SAME versioning convention as
# the harness's other installers — an immutable ~/.configurator/versions/<ver>
# slot, a plain-text ~/.configurator/current-version marker, and a
# ~/.local/bin/configurator binstub — then launches it. Re-running is
# version-gated (a no-op when already current).
#
# Phase 0 assumes git + uv are already present; automatic prerequisite
# provisioning lands in Phase 2 (issue #355). Override the fetched ref with
# $env:CONFIGURATOR_REF and the install root with $env:CONFIGURATOR_ROOT.

$ErrorActionPreference = 'Stop'

$Repo    = 'https://github.com/ThomasMichon/copilot-extensions.git'
$Ref     = if ($env:CONFIGURATOR_REF) { $env:CONFIGURATOR_REF } else { 'main' }
$Root    = if ($env:CONFIGURATOR_ROOT) { $env:CONFIGURATOR_ROOT } else { Join-Path $env:USERPROFILE '.configurator' }
$Staging = Join-Path $Root 'staging'

Write-Host 'copilot-extensions Configurator - bootstrap'

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

Push-Location (Join-Path $Staging 'configurator')
try {
    # Version-install the fetched payload (idempotent, version-gated): publishes
    # the versions/<ver> slot + current-version marker + ~/.local/bin binstub.
    uv run --quiet python -m configurator self-install --apply
    # Then run whatever the caller asked for through the freshly-installed app.
    uv run --quiet python -m configurator @args
} finally {
    Pop-Location
}
