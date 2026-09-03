Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'

if (-not $env:CR_SCENARIO_NAME) {
    $env:CR_SCENARIO_NAME = 'agent-index-installation-cells'
}
$cleanRoomLib = if ($env:CR_LIB) {
    $env:CR_LIB
}
else {
    Join-Path $PSScriptRoot '..\..\lib\clean-room-lib.ps1'
}
. $cleanRoomLib

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
$python = Get-Command python3 -ErrorAction SilentlyContinue
if (-not $python) {
    $python = Get-Command python -ErrorAction SilentlyContinue
}
if (-not $python) {
    jam 'toolchain-python' 'python3 is unavailable' 'install Python 3'
    cr_finalize
}
pass 'mounted source and portable scenario driver are present'

$selectedBuildMode = (& $python.Source -X utf8 $driver build-mode | Select-Object -Last 1)
if ($LASTEXITCODE -ne 0 -or -not $selectedBuildMode) {
    jam 'repo-config' 'Agent Index build mode is invalid' 'set CR_AGENT_INDEX_BUILD_MODE to full or smoke'
    cr_finalize
}
$selectedBuildMode = ("$selectedBuildMode").Trim().ToLowerInvariant()
$env:CR_AGENT_INDEX_BUILD_MODE = $selectedBuildMode
cr_meta 'build_mode' $selectedBuildMode
cr_meta 'acceptance_mode' $(if ($selectedBuildMode -eq 'smoke') { 'diagnostic-only' } else { 'full' })

$titles = @{
    1 = 'default and false policy remain legacy and non-activating'
    2 = 'two active Agent Index cells provision and serve independently'
    3 = 'cell A updates and cuts over while cell B is unchanged'
    4 = 'current management rolls back and recovers cutover crashes with one owned PID'
    5 = 'public deploy plus foreign and cross-cell control fail closed; shutdown is isolated'
}
$cleanupFailed = $false
$interrupted = $false
trap [System.Management.Automation.PipelineStoppedException] {
    $script:interrupted = $true
    break
}
try {
    foreach ($stage in 1..5) {
        phase $stage $titles[$stage]
        $rc = capture "stage-$stage" { & $python.Source -X utf8 $driver $stage }
        if ($rc -eq 0) {
            pass $titles[$stage]
        }
        else {
            $log = Join-Path $env:CR_LOGDIR "stage-$stage.log"
            if (
                (Test-Path -LiteralPath $log) -and
                (Select-String -Path $log -Pattern 'HandshakeFailure|Failed to fetch|No solution found|No matching distribution|certificate|TLS|SSL|package index' -Quiet)
            ) {
                jam 'toolchain-uv' "stage $stage could not resolve the lightweight Python dependencies; see cr-logs/stage-$stage.log" 'pass -UvIndex with an available package index or use a base interpreter that supports CR_AGENT_INDEX_BUILD_MODE=smoke'
            }
            else {
                jam 'install-contract' "stage $stage failed; see cr-logs/stage-$stage.log" 'inspect the deterministic dual-cell lifecycle evidence'
            }
            break
        }
    }
}
finally {
    $cleanupRc = capture 'cleanup' { & $python.Source -X utf8 $driver cleanup }
    if ($cleanupRc -eq 0) {
        pass 'all recorded Agent Index services stopped gracefully'
    }
    else {
        $cleanupFailed = $true
    }
}
if ($cleanupFailed) {
    jam 'install-contract' 'ownership-checked Agent Index cleanup failed; see cr-logs/cleanup.log' 'inspect the recorded PID and endpoint evidence'
}
if ($interrupted) {
    jam 'install-contract' 'scenario execution was interrupted after ownership-checked cleanup' 'rerun the full acceptance scenario'
}
if ($selectedBuildMode -eq 'smoke') {
    jam 'repo-config' 'smoke mode completed as a diagnostic and is not an acceptance result' 'rerun with CR_AGENT_INDEX_BUILD_MODE=full'
}

cr_finalize
