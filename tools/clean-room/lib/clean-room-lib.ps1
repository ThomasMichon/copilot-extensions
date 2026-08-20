<#
  clean-room-lib.ps1 -- PowerShell port of the clean-room scenario helper API,
  the WINDOWS counterpart to lib/clean-room-lib.sh.

  A Windows scenario (scenario.ps1) dot-sources this and speaks the SAME helper
  vocabulary, so Windows and Linux scenarios report uniformly into the SAME
  cr-report.json shape (top-level scenario/ran_until_phase/passed/failed +
  env{} + jams[] + results[]). Consumers (verdict.sh, the downstream validators)
  read those keys identically regardless of which OS produced the report.

  Deliberately Windows-PowerShell-5.1 compatible (no ternary, no $IsWindows, no
  ?.) so it runs under the built-in `powershell.exe` inside a Server Core-based
  Windows container -- no pwsh 7 required in the image.

  Public API (mirrors the .sh lib):
    cr_init                       initialise accounting + logdir (call once, first)
    phase <n> <title>             start phase <n>; also the CR_UNTIL gate
    pass <msg> / fail <msg> / info <msg>
    capture <label> <scriptblock> run, tee to cr-logs\<label>.log, record exit
    envdump                       snapshot key tool presence/versions into env{}
    jam <category> <evidence> [hint]   classified failure (counts as a FAIL)
    cr_meta <key> <value>         scenario-specific top-level report field
    cr_finalize                   write cr-report.json + summary, then exit (0 iff no FAILs)

  Environment consumed (same names as the .sh lib):
    CR_REPORT   report path         (default $HOME\cr-report.json)
    CR_LOGDIR   per-label cmd logs  (default $HOME\cr-logs)
    CR_UNTIL    stop after phase n  (default 999 = all)
    CR_SCENARIO_NAME                (default 'unnamed')
#>

Set-StrictMode -Version Latest

if (-not $env:CR_REPORT) { $env:CR_REPORT = Join-Path $HOME 'cr-report.json' }
if (-not $env:CR_LOGDIR) { $env:CR_LOGDIR = Join-Path $HOME 'cr-logs' }
if (-not $env:CR_UNTIL) { $env:CR_UNTIL = '999' }
if (-not $env:CR_SCENARIO_NAME) { $env:CR_SCENARIO_NAME = 'unnamed' }

$script:CR_PASS = 0
$script:CR_FAIL = 0
$script:CR_RESULTS = New-Object System.Collections.ArrayList
$script:CR_JAMS = New-Object System.Collections.ArrayList
$script:CR_META = [ordered]@{}
$script:CR_ENV = [ordered]@{ path = ''; tools = @(); configs = @() }
$script:CR_CUR_PHASE = 0

function cr_init {
    New-Item -ItemType Directory -Force -Path $env:CR_LOGDIR | Out-Null
    $script:CR_PASS = 0; $script:CR_FAIL = 0
    $script:CR_RESULTS = New-Object System.Collections.ArrayList
    $script:CR_JAMS = New-Object System.Collections.ArrayList
    $script:CR_META = [ordered]@{}
    Write-Host ("### clean-room scenario: {0} (until phase {1}) ###" -f $env:CR_SCENARIO_NAME, $env:CR_UNTIL) -ForegroundColor White
}

function _cr_rec($kind, $msg) {
    switch ($kind) {
        'PASS' { $script:CR_PASS++; Write-Host ("  [PASS] " + $msg) -ForegroundColor Green }
        'FAIL' { $script:CR_FAIL++; Write-Host ("  [FAIL] " + $msg) -ForegroundColor Red }
        'INFO' { Write-Host ("  [INFO] " + $msg) -ForegroundColor Cyan }
    }
    [void]$script:CR_RESULTS.Add([ordered]@{ kind = $kind; phase = $script:CR_CUR_PHASE; msg = "$msg" })
}
function pass($msg) { _cr_rec 'PASS' $msg }
function fail($msg) { _cr_rec 'FAIL' $msg }
function info($msg) { _cr_rec 'INFO' $msg }

function phase($n, $title) {
    $u = 0; [void][int]::TryParse($env:CR_UNTIL, [ref]$u)
    if ($u -lt [int]$n) { cr_finalize }
    $script:CR_CUR_PHASE = [int]$n
    Write-Host ""
    Write-Host ("== Phase {0} -- {1} ==" -f $n, $title) -ForegroundColor White
}

function capture($label, [scriptblock]$block) {
    $log = Join-Path $env:CR_LOGDIR ("{0}.log" -f $label)
    $out = & $block 2>&1 | Out-String
    $rc = $LASTEXITCODE
    Set-Content -Path $log -Value $out -Encoding utf8
    Write-Host ("  (${label} exit=$rc, log=$log)")
    return $rc
}

function envdump {
    $tools = New-Object System.Collections.ArrayList
    foreach ($t in @('python', 'git', 'powershell', 'pwsh', 'node')) {
        $cmd = Get-Command $t -ErrorAction SilentlyContinue
        $where = ''; $ver = ''
        if ($cmd) {
            $where = $cmd.Source
            try {
                if ($t -eq 'powershell' -or $t -eq 'pwsh') {
                    # Windows PowerShell 5.1 has no `--version`; read the loaded version.
                    $ver = & $t -NoProfile -Command '$PSVersionTable.PSVersion.ToString()' 2>&1 | Select-Object -First 1
                }
                else {
                    $ver = (& $t --version 2>&1 | Select-Object -First 1)
                }
            }
            catch { $ver = '' }
        }
        [void]$tools.Add([ordered]@{ tool = $t; path = "$where"; version = "$ver" })
    }
    $script:CR_ENV = [ordered]@{ path = "$env:PATH"; tools = @($tools); configs = @() }
    info "envdump: PATH + tool presence captured"
}

function jam($category, $evidence, $hint = '') {
    $script:CR_FAIL++
    $h = ''
    if ($hint) { $h = " -- hint: $hint" }
    Write-Host ("  [JAM:$category] $evidence$h") -ForegroundColor Red
    [void]$script:CR_RESULTS.Add([ordered]@{ kind = 'FAIL'; phase = $script:CR_CUR_PHASE; msg = "jam[$category]: $evidence" })
    [void]$script:CR_JAMS.Add([ordered]@{ category = "$category"; phase = $script:CR_CUR_PHASE; evidence = "$evidence"; hint = "$hint" })
}

function cr_meta($key, $value) { $script:CR_META[[string]$key] = "$value" }

function cr_finalize {
    Write-Host ""
    Write-Host ("== Summary ==") -ForegroundColor White
    Write-Host ("  {0} passed, {1} failed (scenario={2}, ran through phase {3})" -f `
            $script:CR_PASS, $script:CR_FAIL, $env:CR_SCENARIO_NAME, $env:CR_UNTIL) -ForegroundColor White
    if ($script:CR_JAMS.Count -gt 0) { Write-Host ("  {0} jam(s) classified" -f $script:CR_JAMS.Count) -ForegroundColor Red }

    $report = [ordered]@{
        scenario        = "$env:CR_SCENARIO_NAME"
        copilot_version = ''
        ran_until_phase = [int]$env:CR_UNTIL
    }
    foreach ($k in $script:CR_META.Keys) { $report[$k] = $script:CR_META[$k] }
    $report['passed'] = $script:CR_PASS
    $report['failed'] = $script:CR_FAIL
    $report['env'] = $script:CR_ENV
    $report['jams'] = @($script:CR_JAMS)
    $report['results'] = @($script:CR_RESULTS)

    $json = $report | ConvertTo-Json -Depth 8
    Set-Content -Path $env:CR_REPORT -Value $json -Encoding utf8
    Write-Host ("  report: {0}`n  logs:   {1}" -f $env:CR_REPORT, $env:CR_LOGDIR)
    if ($script:CR_FAIL -eq 0) { exit 0 } else { exit 1 }
}
