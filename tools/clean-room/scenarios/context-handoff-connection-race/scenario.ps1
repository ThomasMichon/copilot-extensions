<#
  context-handoff-connection-race\scenario.ps1 -- WINDOWS arm of the
  same-session double-discovery tool-name-clash repro
  (copilot-agent-runtime#22266). Windows counterpart of scenario.sh; see that
  file's own header for the full mechanism writeup (same-session
  double-discovery of one plugin from two sources, NOT a manual out-of-band
  join -- that path was tried on Linux first and failed for an unrelated,
  informative reason: a standalone-spawned process's stdio isn't wired to any
  real host).

  UNVALIDATED as of authoring: ported by close analogy to the working Linux
  scenario.sh and this repo's existing Windows clean-room conventions
  (clean-room-lib.ps1, psmux-drive.ps1), but not yet run against a real
  Windows container -- Docker on the authoring machine is in Linux-container
  mode; Windows-container validation is the explicit next step, on a
  different host. Smoke-test each phase (especially psmux drive + the
  runtime-payload-cache path discovery, which may differ from Linux's
  ~/.cache/copilot/pkg layout -- check %LOCALAPPDATA%\copilot\pkg or
  equivalent on Windows) before trusting a full run's PASS/FAIL verdict.

  This scenario's real purpose on the Windows arm is broader than the
  partner-*_ps1-validation design run.ps1's Windows branch was originally
  built for: it needs `copilot` itself running in the container (see
  Dockerfile.windows's Node+Copilot+psmux additions), not a partner tree. A
  `-PartnerPath`/`-PartnerRepo` is still MANDATORY for run.ps1's Windows arm
  today (throws otherwise) -- pass anything harmless (e.g. this scenario's
  own directory) to satisfy that precondition; $env:CR_PARTNER_PATH is
  intentionally unused below.

  Env: CR_MARKETPLACE_REPO / CR_MARKETPLACE_NAME.
#>

Set-StrictMode -Version Latest

. $env:CR_LIB
. (Join-Path (Split-Path $env:CR_LIB -Parent) 'psmux-drive.ps1')

$MarketplaceRepo = if ($env:CR_MARKETPLACE_REPO) { $env:CR_MARKETPLACE_REPO } else { 'ThomasMichon/copilot-extensions' }
$MarketplaceName = if ($env:CR_MARKETPLACE_NAME) { $env:CR_MARKETPLACE_NAME } else { 'copilot-extensions' }
$Plugin = 'context-handoff'
$InstalledRoot = Join-Path $HOME ".copilot\installed-plugins\$MarketplaceName"
$ExtLogDir = Join-Path $HOME '.copilot\logs\extensions'

cr_init
cr_meta 'plugin' $Plugin
cr_meta 'validates' 'same-session double-discovery tool-name clash reproduces on Windows (copilot-agent-runtime#22266)'

# =========================================================================
phase 0 'environment (fresh machine)'
envdump
if (Test-Path (Join-Path $InstalledRoot $Plugin)) {
    fail "environment is NOT clean -- $Plugin already installed"
}
else {
    pass "clean slate: no pre-existing $Plugin install"
}

# =========================================================================
phase 1 "install $Plugin (marketplace source)"
New-Item -ItemType Directory -Force -Path (Join-Path $HOME '.copilot') | Out-Null
$settings = @{
    extraKnownMarketplaces = @{ "$MarketplaceName" = @{ source = @{ source = 'github'; repo = "$MarketplaceRepo" } } }
    enabledPlugins         = @{ "$Plugin@$MarketplaceName" = $true }
} | ConvertTo-Json -Depth 6
Set-Content -Path (Join-Path $HOME '.copilot\settings.json') -Value $settings -Encoding utf8
capture 'marketplace-add' { & copilot plugin marketplace add $MarketplaceRepo }
capture 'install' { & copilot plugin install "$Plugin@$MarketplaceName" }
$InstalledExtDir = Join-Path $InstalledRoot "$Plugin\extensions\$Plugin"
if (Test-Path $InstalledExtDir) {
    pass "$Plugin payload present on disk ($InstalledExtDir)"
}
else {
    jam 'npm-registry' "$Plugin payload NOT installed (see cr-logs\install.log)" 'check marketplace source + node/npm feed'
}

# =========================================================================
phase 2 'duplicate the extension as a project-level source in the working repo'
$RepoDir = Join-Path $HOME 'ch-repro'
New-Item -ItemType Directory -Force -Path $RepoDir | Out-Null
Push-Location $RepoDir
& git init -q
& git config user.email t@e
& git config user.name t
Set-Content -Path 'README.md' -Value '# ch-repro' -Encoding utf8
& git add -A
& git commit -qm init
Pop-Location
$ProjectExtDir = Join-Path $RepoDir '.github\extensions\context-handoff'
if (Test-Path $InstalledExtDir) {
    New-Item -ItemType Directory -Force -Path (Split-Path $ProjectExtDir -Parent) | Out-Null
    Copy-Item -LiteralPath $InstalledExtDir -Destination $ProjectExtDir -Recurse
    pass "duplicated $Plugin's extension payload into $ProjectExtDir (project-level source)"
}
else {
    info 'phase 2 skipped: no installed payload to duplicate (see phase 1 jam)'
}

# =========================================================================
phase 3 'run one HEADED session against BOTH sources; both extensions launch'
New-Item -ItemType Directory -Force -Path $ExtLogDir | Out-Null
$beforeLogs = @()
if (Test-Path $ExtLogDir) { $beforeLogs = Get-ChildItem -LiteralPath $ExtLogDir -File | ForEach-Object { $_.Name } }

if (-not (Test-CrPsmux)) {
    jam 'psmux-unavailable' 'psmux is not on PATH' 'extensions only load under a real headed session; without psmux this scenario cannot drive one'
}
else {
    $PluginArgs = @()
    if (Test-Path (Join-Path $InstalledRoot $Plugin)) { $PluginArgs = @('--plugin-dir', (Join-Path $InstalledRoot $Plugin)) }
    $Session = 'cr-ch-race'
    # --experimental is REQUIRED: without it, `/env` reports "Extensions: No
    # extensions loaded" even when a plugin's skills/hooks load fine -- the
    # JS extension-host component is gated behind that flag entirely,
    # independent of headed vs. headless mode (confirmed on the Linux arm;
    # unconfirmed but assumed identical on Windows -- verify here).
    Start-CrPsmux -Session $Session -Cwd $RepoDir -Prompt 'What is 19+23? Reply with only the number.' `
        -ExtraArgs (@('--experimental', '--allow-all-tools') + $PluginArgs)
    if (Wait-CrPsmuxFor -Session $Session -Pattern '\b42\b' -TimeoutSec 60) {
        pass 'headed session completed its first turn (psmux pane shows the reply)'
    }
    else {
        jam 'headed-session-timeout' 'psmux pane never showed the expected reply within 60s' 'inspect cr-logs\psmux-pane.log (captured below) for a startup hang'
    }
    # Settle before teardown -- see scenario.sh's own note: the reply lands
    # as soon as the model's OWN turn finishes, which can race ahead of a
    # still-connecting (or still-being-rejected) extension subprocess.
    Start-Sleep -Seconds 8
    Get-CrPsmuxCapture -Session $Session | Out-File -FilePath (Join-Path $env:CR_LOGDIR 'psmux-pane.log') -Encoding utf8
    Stop-CrPsmux -Session $Session
}
Start-Sleep -Seconds 2

$afterLogs = @()
if (Test-Path $ExtLogDir) { $afterLogs = Get-ChildItem -LiteralPath $ExtLogDir -File | ForEach-Object { $_.Name } }
$newLogs = @($afterLogs | Where-Object { ($beforeLogs -notcontains $_) -and ($_ -match $Plugin) })
cr_meta 'new_context_handoff_extension_logs' $newLogs.Count
if ($newLogs.Count -ge 2) {
    pass "session launched $($newLogs.Count) separate $Plugin extension connections (installed + project source both discovered)"
}
elseif ($newLogs.Count -eq 1) {
    jam 'single-discovery' 'only 1 context-handoff extension connection was launched -- the duplicate source was not discovered as a distinct candidate' 'check whether project-level extension discovery requires an explicit trust/add-dir step in this CLI version'
}
else {
    jam 'no-launch' 'no context-handoff extension logs appeared for this session at all' 'check cr-logs\psmux-pane.log for a plugin-load failure before extensions ever start'
}

# =========================================================================
phase 4 'assert the tool-name clash on the second (losing) connection'
$clashFound = $false
foreach ($logName in $newLogs) {
    $logPath = Join-Path $ExtLogDir $logName
    if ((Get-Content -LiteralPath $logPath -Raw -ErrorAction SilentlyContinue) -match 'already registered by another connection') {
        $clashFound = $true
        info "clash confirmed in $logName"
    }
}
if ($clashFound) {
    pass "same-session double-discovery clash REPRODUCED: a second connection was rejected with 'already registered by another connection'"
}
else {
    jam 'no-repro' 'no new context-handoff extension log contained the expected tool-name clash text' 'either the guard/discovery order changed upstream, this Windows-specific path/timing assumption is wrong, or the underlying bug genuinely does not reproduce on Windows -- any of these is a real, actionable finding for the Linux-vs-Windows comparison this scenario exists to make'
}

cr_finalize
