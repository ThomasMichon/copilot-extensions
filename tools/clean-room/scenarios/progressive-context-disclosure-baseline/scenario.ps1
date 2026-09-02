Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'

if (-not $env:CR_SCENARIO_NAME) {
    $env:CR_SCENARIO_NAME = 'progressive-context-disclosure-baseline'
}
$cleanRoomLib = if ($env:CR_LIB) {
    $env:CR_LIB
}
else {
    Join-Path $PSScriptRoot '..\..\lib\clean-room-lib.ps1'
}
. $cleanRoomLib

cr_init
cr_meta 'role' 'progressive-context-tier-p-windows'
phase 0 'validate frozen progressive-disclosure fixture'
envdump

$source = $env:CR_PARTNER_PATH
$fixture = Join-Path $PSScriptRoot 'fixture.py'
$python = Get-Command python3 -ErrorAction SilentlyContinue
if (-not $python) {
    $python = Get-Command python -ErrorAction SilentlyContinue
}

if (-not $source -or -not (Test-Path -LiteralPath $source -PathType Container)) {
    jam 'repo-config' 'the source checkout is not mounted at CR_PARTNER_PATH' `
        'pass -PartnerPath <copilot-extensions-checkout> to the Windows runner'
}
elseif (-not $python) {
    jam 'toolchain-python' 'python is unavailable in the Windows clean-room image' `
        'rebuild copilot-cleanroom:windows from Dockerfile.windows'
}
elseif (-not (Test-Path -LiteralPath $fixture -PathType Leaf)) {
    jam 'scenario-fixture' 'the progressive fixture driver is absent' `
        'restore fixture.py in the scenario directory'
}
else {
    $verify = capture 'verify-frozen-fixture' {
        & $python.Source $fixture verify --source $source
    }
    $matrix = capture 'verify-phase2-matrix' {
        & $python.Source $fixture verify-phase2
    }
    if ($verify -eq 0 -and $matrix -eq 0) {
        pass 'frozen inputs and all deterministic Phase 2 render cells are coherent'
    }
    else {
        jam 'scenario-fixture' 'progressive-disclosure fixture validation failed' `
            'repair the frozen fixture before running behavioral comparisons'
    }
}

cr_finalize
