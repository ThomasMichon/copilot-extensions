<#
  partner-harness-setup/scenario.ps1 -- the WINDOWS arm of the partner-harness
  setup gate, run INSIDE a disposable Windows container. The counterpart to
  scenario.sh: same intent (never publish a drop that breaks the partner's setup
  flow), same report contract, but validates the partner's *_ps1 (Windows-native)
  setup flow instead of *_sh.

  Layers:
    1. drop structure  -- each vendored plugin parses + is marketplace-listed +
       ships its installers; the partner's setup entrypoint + golden-path doc exist.
    2. read-only check -- (optional) the partner's `setup.ps1 <check>` runs without
       crashing IF a pwsh is present; otherwise deferred to the unittest suite.
    3. partner's own tests -- the partner's OWN *_ps1 setup/update suite passes
       (stdlib unittest; only python is required -- the lone pwsh test auto-skips).

  Name-free of any specific partner: everything is CR_PARTNER_* configurable.
  Windows-PowerShell-5.1 compatible (runs under the container's built-in
  powershell.exe -- no pwsh 7 required).
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'

if (-not $env:CR_SCENARIO_NAME) { $env:CR_SCENARIO_NAME = 'partner-harness-setup' }
$LibPath = $env:CR_LIB
if (-not $LibPath) { $LibPath = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'lib\clean-room-lib.ps1' }
. $LibPath

$PARTNER_NAME = if ($env:CR_PARTNER_NAME) { $env:CR_PARTNER_NAME } else { 'partner-harness' }
$PARTNER_PATH = $env:CR_PARTNER_PATH
$PARTNER_PLUGINS = if ($env:CR_PARTNER_PLUGINS) { $env:CR_PARTNER_PLUGINS } else { 'agent-bridge agent-codespaces' }
$PARTNER_MARKETPLACE = if ($env:CR_PARTNER_MARKETPLACE) { $env:CR_PARTNER_MARKETPLACE } else { '.github/plugin/marketplace.json' }
$PARTNER_GOLDEN_DOC = if ($env:CR_PARTNER_GOLDEN_DOC) { $env:CR_PARTNER_GOLDEN_DOC } else { 'docs/golden-path/setup.md' }
$PARTNER_SETUP_ENTRY = if ($env:CR_PARTNER_SETUP_ENTRY) { $env:CR_PARTNER_SETUP_ENTRY } else { 'setup.ps1' }
$PARTNER_SETUP_TESTS = if ($env:CR_PARTNER_SETUP_TESTS) { $env:CR_PARTNER_SETUP_TESTS } else { 'tests.test_setup_ps1 tests.test_update_ps1' }
$Plugins = @($PARTNER_PLUGINS -split '\s+' | Where-Object { $_ })
$TestMods = @($PARTNER_SETUP_TESTS -split '\s+' | Where-Object { $_ })

cr_init
cr_meta 'partner' $PARTNER_NAME
$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { $py = (Get-Command python3 -ErrorAction SilentlyContinue) }

# =========================================================================
phase 0 'environment + partner tree present'
envdump
info ("partner=$PARTNER_NAME plugins='$PARTNER_PLUGINS'")
info ("python: " + $(if ($py) { $py.Source } else { 'MISSING' }))
$ROOT = $PARTNER_PATH
if (-not $ROOT -or -not (Test-Path -LiteralPath $ROOT)) {
    jam 'repo-config' "no partner tree (set CR_PARTNER_PATH to a mounted tree)" "the sync gate mounts the drop at CR_PARTNER_PATH"
    cr_finalize
}
if (-not $py) {
    jam 'validator-env' "python not found in the container (needed to run the partner *_ps1 suite)" "use a Windows python image"
    cr_finalize
}
pass "partner tree present at $ROOT; python available"

# =========================================================================
phase 1 'drop structure (plugins parse, marketplace-listed, installers + golden-path present)'
$MktPath = Join-Path $ROOT $PARTNER_MARKETPLACE
$mkt = $null
if (-not (Test-Path -LiteralPath $MktPath)) {
    jam 'drop-structural' "marketplace manifest missing: $PARTNER_MARKETPLACE" "a drop must not remove the partner marketplace manifest"
}
else {
    try { $mkt = Get-Content -Raw -LiteralPath $MktPath | ConvertFrom-Json; pass "marketplace manifest present: $PARTNER_MARKETPLACE" }
    catch { jam 'drop-structural' "marketplace.json failed to parse: $($_.Exception.Message)" "the drop corrupted the manifest" }
}
foreach ($p in $Plugins) {
    $pdir = Join-Path $ROOT ("plugins/$p")
    if (-not (Test-Path -LiteralPath $pdir)) { jam 'drop-structural' "plugins/$p absent" "upstream may no longer ship it, or the re-blit failed"; continue }
    $pj = Join-Path $pdir 'plugin.json'
    if (-not (Test-Path -LiteralPath $pj)) { jam 'drop-structural' "plugins/$p/plugin.json missing" "the re-blitted plugin subtree is incomplete"; continue }
    try { $pjo = Get-Content -Raw -LiteralPath $pj | ConvertFrom-Json }
    catch { jam 'drop-structural' "plugins/$p/plugin.json failed to parse: $($_.Exception.Message)" "re-blit produced invalid JSON"; continue }
    if ($pjo.name -ne $p) { jam 'drop-structural' "plugins/$p/plugin.json declares name '$($pjo.name)'" "mismatched name breaks marketplace resolution"; continue }
    if (-not $pjo.version) { jam 'drop-structural' "plugins/$p/plugin.json has no version" "every plugin drop must carry a version"; continue }
    $miss = @()
    foreach ($inst in @('scripts/install.ps1', 'scripts/install.sh')) { if (-not (Test-Path -LiteralPath (Join-Path $pdir $inst))) { $miss += $inst } }
    if ($miss.Count -gt 0) { jam 'drop-structural' "plugins/$p missing installer(s): $($miss -join ', ')" "setup delegates to these installers"; continue }
    $listed = $null
    if ($mkt) { $listed = @($mkt.plugins) | Where-Object { $_.name -eq $p } | Select-Object -First 1 }
    if (-not $listed) { jam 'drop-structural' "plugins/$p not listed in $PARTNER_MARKETPLACE" "the manifest and the drop disagree"; continue }
    pass "plugin ${p}: plugin.json v$($pjo.version), installers present, marketplace-listed"
}
if (Test-Path -LiteralPath (Join-Path $ROOT $PARTNER_SETUP_ENTRY)) { pass "setup entrypoint present: $PARTNER_SETUP_ENTRY" }
else { jam 'drop-structural' "setup entrypoint missing: $PARTNER_SETUP_ENTRY" "the drop is not a valid partner harness tree" }
if (Test-Path -LiteralPath (Join-Path $ROOT $PARTNER_GOLDEN_DOC)) { pass "golden-path doc present: $PARTNER_GOLDEN_DOC" }
else { jam 'drop-structural' "golden-path doc missing: $PARTNER_GOLDEN_DOC" "the partner's golden path is undocumented in this drop" }

# =========================================================================
phase 2 "read-only setup check (optional -- pwsh-gated)"
# A partner's setup.ps1 check may need pwsh 7; the base python/Server Core image
# ships only
# Windows PowerShell 5.1. Run the check only if a pwsh is present; otherwise defer
# to the unittest suite (phase 3), which is the authoritative *_ps1 contract gate.
$pwsh = (Get-Command pwsh -ErrorAction SilentlyContinue)
if ($pwsh -and (Test-Path -LiteralPath (Join-Path $ROOT $PARTNER_SETUP_ENTRY))) {
    Push-Location $ROOT
    $rc = capture 'setup-check' { & $pwsh.Source -NoProfile -File $PARTNER_SETUP_ENTRY check --non-interactive }
    Pop-Location
    $log = Join-Path $env:CR_LOGDIR 'setup-check.log'
    $crash = $false
    if (Test-Path $log) { $crash = (Select-String -Path $log -Pattern 'ParserError|is not recognized as|Unexpected token' -Quiet) }
    if ($crash) { jam 'drop-structural' "read-only check hit an interpreter error (see setup-check.log)" "the setup script is broken; fix upstream" }
    else { pass "read-only setup check ran (exit $rc; no interpreter error)" }
}
else {
    info "no pwsh in this image -- deferring the read-only check to the *_ps1 unittest suite (phase 3)"
}

# =========================================================================
phase 3 "partner's OWN *_ps1 setup/update test suite passes"
$have = $true
foreach ($m in $TestMods) { $f = Join-Path $ROOT (($m.Replace('.', '\')) + '.py'); if (-not (Test-Path -LiteralPath $f)) { $have = $false } }
if ($have) {
    Push-Location $ROOT
    $rc = capture 'setup-tests' { & $py.Source -m unittest @TestMods }
    Pop-Location
    $log = Join-Path $env:CR_LOGDIR 'setup-tests.log'
    $ran = ''
    if (Test-Path $log) { $m = Select-String -Path $log -Pattern 'Ran \d+ tests' | Select-Object -Last 1; if ($m) { $ran = $m.Line } }
    if ($rc -eq 0) { pass ("partner *_ps1 setup/update tests passed (" + $(if ($ran) { $ran } else { 'ok' }) + ")") }
    else { jam 'setup-script-contract' ("partner *_ps1 suite FAILED (" + $(if ($ran) { $ran } else { 'see setup-tests.log' }) + ") -- $PARTNER_SETUP_TESTS") "the drop breaks the partner's Windows setup contract; fix upstream, do not publish" }
}
else {
    jam 'setup-script-contract' "partner has no *_ps1 tests ($PARTNER_SETUP_TESTS)" "the partner cannot be behaviorally validated on Windows"
}

cr_finalize
