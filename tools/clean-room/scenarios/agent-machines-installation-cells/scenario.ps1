Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'

if (-not $env:CR_SCENARIO_NAME) {
    $env:CR_SCENARIO_NAME = 'agent-machines-installation-cells'
}
. $env:CR_LIB

cr_init
phase 0 'source and clean fixture boundary'
envdump
if (-not $env:CR_PARTNER_PATH -and $env:CR_HARNESS_MOUNT) {
    $env:CR_PARTNER_PATH = $env:CR_HARNESS_MOUNT
}
if (-not $env:CR_PARTNER_PATH -or -not (Test-Path -LiteralPath $env:CR_PARTNER_PATH -PathType Container)) {
    jam 'repo-config' 'CR_PARTNER_PATH does not name the mounted source tree' 'pass -PartnerPath to the Windows clean-room runner'
    cr_finalize
}
$driver = Join-Path $PSScriptRoot 'scenario.py'
if (-not (Test-Path -LiteralPath $driver -PathType Leaf)) {
    jam 'drop-structural' 'scenario driver is absent' 'restore scenario.py'
    cr_finalize
}
pass 'mounted source and scenario driver are present'

$titles = @{
    1 = 'operative eligibility and full cell-provision locking hold'
    2 = 'two active Agent Machines cells install and run independently'
    3 = 'one cell updates without changing its peer'
    4 = 'historical owned slot rolls back without changing its peer'
    5 = 'blocked governance states fail closed without legacy fallback'
}
foreach ($stage in 1..5) {
    phase $stage $titles[$stage]
    $rc = capture "stage-$stage" { & python $driver $stage }
    if ($rc -eq 0) {
        pass $titles[$stage]
    }
    else {
        $log = Join-Path $env:CR_LOGDIR "stage-$stage.log"
        if (
            (Test-Path -LiteralPath $log) -and
            (Select-String -Path $log -Pattern 'HandshakeFailure|Failed to fetch|No solution found.*pyyaml' -Quiet)
        ) {
            jam 'toolchain-uv' "stage $stage could not resolve Python dependencies; see cr-logs/stage-$stage.log" 'pass -UvIndex with an available package index'
        }
        else {
            jam 'install-contract' "stage $stage failed; see cr-logs/stage-$stage.log" 'inspect the deterministic lifecycle evidence'
        }
        break
    }
}

cr_finalize
