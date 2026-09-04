<#
.SYNOPSIS
    Worktree session launcher — resolves via Python, executes in the shell.

.DESCRIPTION
    Calls agent_worktrees resolve to get a JSON launch plan, then executes
    the plan natively. Python exits before Copilot starts, freeing the venv.

    After Copilot exits, calls agent_worktrees post-exit for finalization.

    Accepts --project to name the active project when CWD cannot.
    Runtime lives at ~/.agent-worktrees/; project config at ~/.{project}/.
#>
# Accept all arguments via $args (not param block) to avoid PowerShell's
# parameter binding rejecting unknown flags like --acp, --stdio, --no-mux
# when called via 'pwsh -File'.
$CopilotArgs = @($args)
$script:LaunchProject = $null

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Setup log — per-launch log file with PID disambiguation
# ---------------------------------------------------------------------------
$script:SetupLogDir = Join-Path $env:TEMP 'worktree-setup-logs'
if (-not (Test-Path $script:SetupLogDir)) {
    try { New-Item -ItemType Directory -Path $script:SetupLogDir -Force | Out-Null } catch {}
}
$script:SetupLog = Join-Path $script:SetupLogDir "setup-$PID.log"
$env:WORKTREE_SETUP_LOG = $script:SetupLog

# ---------------------------------------------------------------------------
# Launch-flow correlation id -- minted once per launcher run and threaded
# through the whole flow (activity marks, the psmux server env, and thus the
# in-pane session hooks) so one launch is reconstructable via
# `agent-worktrees activity --launch-id`. Mirrors launch-session.sh.
# ---------------------------------------------------------------------------
$script:LaunchId = ([guid]::NewGuid().ToString('N').Substring(0, 12))
$env:WORKTREE_LAUNCH_ID = $script:LaunchId

function Write-SetupLog {
    param([string]$Message, [string]$Level = 'INFO')
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'
    $line = "[$ts] [$Level] $Message"
    try { Add-Content -Path $script:SetupLog -Value $line -ErrorAction SilentlyContinue } catch {}
}

function Write-LaunchTrace {
    param([string]$Event)
    $path = $env:AGENT_WORKTREES_LAUNCH_TRACE
    if (-not $path -or $path -match '^(?i:0|false|no|off)$') { return }
    try {
        $record = [ordered]@{
            timestamp = [DateTime]::UtcNow.ToString('o')
            event = $Event
            launch_id = $env:AGENT_WORKTREES_LAUNCH_ID
            project = $script:LaunchProject
        }
        [IO.Directory]::CreateDirectory((Split-Path -Parent $path)) | Out-Null
        [IO.File]::AppendAllText(
            $path,
            ($record | ConvertTo-Json -Compress) + [Environment]::NewLine
        )
    } catch {}
}

# Launch-status line: always logged, and ALSO echoed to the console during an
# interactive launch so the operator understands what the (otherwise silent)
# post-Picker/pre-mux pause is waiting on -- the staged update join + apply,
# which can block for up to 90s. Gated on $script:ShowLaunchStatus so
# machine/direct-dispatch and JSON paths stay quiet (they never enable it). In
# --stdio mode the global Write-Host override routes these to stderr, keeping
# them off the ACP JSON-RPC channel. ASCII only (this launcher declares no
# UTF-8 console context).
$script:ShowLaunchStatus = $false
function Write-SetupStatus {
    param([string]$Message, [string]$Level = 'INFO')
    Write-SetupLog $Message $Level
    if ($script:ShowLaunchStatus) {
        $color = if ($Level -eq 'WARN') { 'Yellow' } else { 'DarkGray' }
        Write-Host "  $Message" -ForegroundColor $color
    }
}

function Write-PlanDiagnostics {
    param([object]$Plan, [string]$Prefix)
    if (-not $Plan) { return }
    $property = $Plan.PSObject.Properties['diagnostics']
    if (-not $property) { return }
    foreach ($diagnostic in @($property.Value)) {
        if (-not $diagnostic) { continue }
        Write-SetupLog (
            "{0}: {1} [{2}] {3}" -f
            $Prefix, $diagnostic.service, $diagnostic.reason, $diagnostic.message
        ) 'WARN'
    }
}

# Write header and create a "latest" copy for easy access
try {
    $header = @(
        "# Worktree Manager — session launch log"
        "# Started: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fffzzz')"
        "# PID: $PID"
        "# Host: $env:COMPUTERNAME"
        ""
    ) -join "`n"
    Set-Content -Path $script:SetupLog -Value $header -ErrorAction SilentlyContinue
    # Prune old logs (keep last 10)
    Get-ChildItem $script:SetupLogDir -Filter 'setup-*.log' |
        Sort-Object LastWriteTime -Descending |
        Select-Object -Skip 10 |
        Remove-Item -Force -ErrorAction SilentlyContinue
} catch {}

# --recovery: bypass worktree resolution entirely, go straight to setup script
# --project: explicit project identity for CWD-neutral callers
# --no-update: skip pre-launch self-update (propagated via WORKTREE_NO_UPDATE)
# --: everything after this separator is copilot passthrough args (e.g. --acp --stdio)
$FilteredArgs = @()
$CopilotPassthrough = @()
$RecoveryMode = $false
$SeenSeparator = $false
$index = 0
while ($index -lt $CopilotArgs.Count) {
    $arg = $CopilotArgs[$index]
    if ($SeenSeparator) {
        $CopilotPassthrough += $arg
    } elseif ($arg -eq '--') {
        $SeenSeparator = $true
    } elseif ($arg -eq '--project') {
        if ($script:LaunchProject) {
            Write-SetupLog '--project may be specified only once' 'ERROR'
            [Console]::Error.WriteLine('ERROR: --project may be specified only once.')
            exit 2
        }
        if ($index + 1 -ge $CopilotArgs.Count) {
            Write-SetupLog '--project requires a value' 'ERROR'
            [Console]::Error.WriteLine('ERROR: --project requires a value.')
            exit 2
        }
        $candidate = [string]$CopilotArgs[$index + 1]
        if ($candidate -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$') {
            Write-SetupLog "Invalid --project value: $candidate" 'ERROR'
            [Console]::Error.WriteLine("ERROR: Invalid --project value '$candidate'.")
            exit 2
        }
        $script:LaunchProject = $candidate
        $index++
    } elseif ($arg -eq '--recovery' -or $arg -eq '-Recovery' -or $arg -eq 'recovery') {
        $RecoveryMode = $true
        Write-SetupLog 'Recovery mode requested via CLI arg'
    } elseif ($arg -eq '--no-update') {
        $env:WORKTREE_NO_UPDATE = '1'
        Write-SetupLog '--no-update: pre-launch update disabled'
    } else {
        $FilteredArgs += $arg
    }
    $index++
}
$CopilotArgs = $FilteredArgs
Write-SetupLog 'launch-session.ps1 starting'
Write-LaunchTrace 'launcher_start'
if ($CopilotPassthrough.Count -gt 0) {
    Write-SetupLog "Copilot passthrough args: $($CopilotPassthrough -join ' ')"
}

# When launched in --stdio mode (ACP protocol), stdout is the JSON-RPC
# channel.  Redirect Write-Host to stderr so status messages don't
# corrupt the protocol stream.
if ($CopilotPassthrough -contains '--stdio') {
    Write-SetupLog 'stdio mode detected -- redirecting Write-Host to stderr'
    function global:Write-Host {
        param(
            [Parameter(Position = 0, ValueFromRemainingArguments)]
            [object[]]$Object,
            [switch]$NoNewline,
            [ConsoleColor]$ForegroundColor,
            [ConsoleColor]$BackgroundColor
        )
        $text = ($Object -join ' ')
        if ($NoNewline) {
            [Console]::Error.Write($text)
        } else {
            [Console]::Error.WriteLine($text)
        }
    }
}

# Recovery fast-path: skip resolve/picker, launch directly in anchor repo
if ($RecoveryMode) {
    Write-SetupLog 'Recovery fast-path — bypassing worktree resolution'
    # Explicit project config takes precedence over unrelated ambient CWD.
    $candidates = @()
    if ($script:LaunchProject) {
        $cfgPath = Join-Path $env:USERPROFILE ".$($script:LaunchProject)\config.yaml"
        if (Test-Path $cfgPath) {
            $anchorLine = Select-String -Path $cfgPath -Pattern '^\s+anchor:\s+(.+)$' | Select-Object -First 1
            if ($anchorLine) {
                $candidates += $anchorLine.Matches[0].Groups[1].Value.Trim().Trim('"').Trim("'")
            }
        }
    }
    $gitRoot = git rev-parse --show-toplevel 2>$null
    if ($LASTEXITCODE -eq 0 -and $gitRoot) {
        $candidates += ($gitRoot -replace '/', '\')
    }
    $candidates += $PWD.Path
    $anchor = $null
    $setupScript = $null
    foreach ($candidate in $candidates) {
        $candidateScript = Join-Path $candidate 'tools\setup\setup.ps1'
        if (Test-Path -LiteralPath $candidateScript -PathType Leaf) {
            $anchor = $candidate
            $setupScript = $candidateScript
            break
        }
    }
    if (-not $setupScript) {
        [Console]::Error.WriteLine('ERROR: Cannot find a recovery setup script in the project anchor, Git root, or current directory.')
        exit 1
    }
    Write-SetupLog "Recovery: launching $setupScript in $anchor"
    Set-Location $anchor
    $setupArgs = @('-Recovery') + $CopilotArgs
    if ($CopilotPassthrough.Count -gt 0) {
        $setupArgs += $CopilotPassthrough
    }
    & pwsh.exe -NoProfile -NoLogo -File $setupScript @setupArgs
    exit $LASTEXITCODE
}

# Runtime resolution (junction-free, marker-only). The active version is
# published by a plain-text `current-version` marker; resolve
# versions\<ver>\Scripts\python.exe directly so nothing traverses a reparse
# point (a junction is blocked under RedirectionGuard / WinError 448, dotfiles
# #637, and prone to drift; a marker file never is). Fallback: the newest
# installed slot only -- the `.venv` link is retired (#1106).
$RuntimeDir = Join-Path $env:USERPROFILE '.agent-worktrees'
$AwPy = $null
$runtimeResolver = Join-Path $RuntimeDir 'bin\resolve-runtime.ps1'
if (Test-Path -LiteralPath $runtimeResolver -PathType Leaf) {
    . $runtimeResolver
}

function Resolve-RuntimePython {
    $AwPy = $null
    if (-not (Test-Path -LiteralPath $runtimeResolver -PathType Leaf)) {
        return $null
    }
    . $runtimeResolver
    return $AwPy
}

$VenvPython = $AwPy

if ($VenvPython -and (Test-Path -LiteralPath $VenvPython)) {
    Write-SetupLog "Venv resolved: $RuntimeDir"
} else {
    Write-SetupLog 'Venv not found - aborting' 'ERROR'
    Write-Error "Venv not found. Run the installer: pwsh -File plugins\agent-worktrees\scripts\install.ps1 install"
    exit 1
}

$env:PYTHONPATH = Join-Path $RuntimeDir 'lib'
$env:PYTHONHOME = $null

# Append a high-level lifecycle event to the persistent Tier-A activity log,
# at parity with launch-session.sh's activity_log(). Best-effort and fully
# detached -- a diagnostic write must never block or fail the launch (the
# fail-silent logging invariant). Extra context is passed as key=value pairs
# (forwarded as --field). $LaunchId is stamped on every record for correlation.
function Write-ActivityLog {
    param(
        [Parameter(Mandatory)][string]$EventName,
        [string]$WorktreeId,
        [string[]]$Fields
    )
    if ([string]::IsNullOrWhiteSpace($WorktreeId)) { return }
    if (-not ($VenvPython -and (Test-Path -LiteralPath $VenvPython))) { return }
    try {
        $alArgs = @('-m', 'agent_worktrees', 'activity-log', $EventName,
                    '--worktree-id', $WorktreeId, '--source', 'launcher')
        if ($script:LaunchId) { $alArgs += @('--launch-id', $script:LaunchId) }
        foreach ($kv in $Fields) { if ($kv) { $alArgs += @('--field', $kv) } }
        # conhost --headless: -WindowStyle Hidden alone is ignored by the DefTerm
        # handoff and can flash a console (windows-launch-hardening #786).
        Start-Process -FilePath 'conhost.exe' `
            -ArgumentList (@('--headless', "`"$VenvPython`"") + $alArgs) `
            -WindowStyle Hidden -ErrorAction Stop | Out-Null
    } catch {
        Write-SetupLog "activity-log '$EventName' failed: $($_.Exception.Message)" 'WARN'
    }
}

function Invoke-AwPostExit {
    param([string]$WorktreeId)
    $postArgs = @('-m', 'agent_worktrees')
    if ($script:LaunchProject) {
        $postArgs += @('--project', $script:LaunchProject)
    }
    $postArgs += @('post-exit', $WorktreeId)
    & $VenvPython @postArgs
    return $LASTEXITCODE
}

# ── Plugin auto-update ────────────────────────────────────────────────────
# If installed from the copilot-extensions marketplace plugin, check for
# updates.  When the plugin source changes: run the full installer (which
# deploys package, launch scripts, binstubs, terminal configs), then
# re-exec into the newly deployed launch-session so the rest of the boot
# uses updated code.
#
# Guard: WORKTREE_NO_UPDATE=1 skips this block entirely (set by --no-update
# and by the re-exec below to prevent infinite loops).

$noUpdate = ($env:WORKTREE_NO_UPDATE -eq '1')
$script:UpdateApplied = $false
$script:StageJob = $null

# ── Background update: stage-then-join (#1430) ───────────────────────────
# The Picker runs from the installed runtime venv, so the slow marketplace
# download is STAGED in the background while the Picker is open, then the
# apply (installer -> runtime, pre-launch, reconcile) runs at the JOIN, after
# the Picker closes and before the psmux/Copilot handoff. The launcher script
# itself is applied via the installer but NOT re-exec'd mid-flight: a launcher
# change takes effect on the NEXT launch (stage-next).

function Start-UpdateStage {
    # Spawn the background stage (marketplace download + fingerprint + plan).
    # Runs headless in a job so it never writes to the Picker's console.
    if ($noUpdate) { return $null }
    try {
        Write-SetupLog 'Starting background update stage (stage-update)'
        return Start-Job -Name 'aw-stage-update' -ScriptBlock {
            param($py)
            & $py -m agent_worktrees stage-update *> $null
        } -ArgumentList $VenvPython
    } catch {
        Write-SetupLog "Update stage spawn failed: $_ (will stage inline at join)" 'WARN'
        return $null
    }
}

function Push-UpdateEnvironment {
    param([object]$Update)
    $saved = @{}
    if ($Update -and $Update.PSObject.Properties['unset_environment']) {
        foreach ($key in @($Update.unset_environment | Where-Object { $_ })) {
            $saved[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
            [Environment]::SetEnvironmentVariable($key, $null, 'Process')
        }
    }
    if ($Update -and $Update.PSObject.Properties['environment'] -and $Update.environment) {
        foreach ($property in @($Update.environment.PSObject.Properties)) {
            if (-not $saved.ContainsKey($property.Name)) {
                $saved[$property.Name] = [Environment]::GetEnvironmentVariable(
                    $property.Name, 'Process'
                )
            }
            [Environment]::SetEnvironmentVariable(
                $property.Name, [string]$property.Value, 'Process'
            )
        }
    }
    return $saved
}

function Pop-UpdateEnvironment {
    param([hashtable]$Saved)
    foreach ($entry in $Saved.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable(
            $entry.Key, $entry.Value, 'Process'
        )
    }
}

function Invoke-UpdateApply {
    # Join the background stage and apply any pending update. Idempotent: runs
    # its body at most once per launch.
    param($StageJob, [switch]$WithReconcile, [switch]$ShowStatus)
    if ($script:UpdateApplied) { return }
    $script:UpdateApplied = $true

    # Enable console status echo for interactive launches (the caller passes
    # -ShowStatus on the Picker exec / refresh paths, never on direct dispatch).
    $script:ShowLaunchStatus = [bool]$ShowStatus

    if (-not $noUpdate) {
        Write-SetupStatus 'Finalizing launch: applying any pending plugin and runtime updates...'
        # Join the background stage (bounded wait). This is the step most
        # likely to make the launch look "stuck": the marketplace download
        # staged while the Picker was open is joined here, up to 90s.
        if ($StageJob) {
            Write-SetupStatus 'Waiting for the background plugin-update download to finish (up to 90s)...'
            try { Wait-Job $StageJob -Timeout 90 | Out-Null } catch {}
            try { Receive-Job $StageJob -ErrorAction SilentlyContinue | Out-Null } catch {}
            try { Remove-Job $StageJob -Force -ErrorAction SilentlyContinue } catch {}
        }
        $statusFile = Join-Path $env:USERPROFILE '.agent-worktrees\updater-status.json'
        $status = $null
        if (Test-Path $statusFile) {
            try { $status = Get-Content $statusFile -Raw | ConvertFrom-Json } catch {}
        }
        # No usable staged result (stage failed, or a peer launch held the
        # lock): run one inline so the marketplace pull still happens.
        if (-not $status -or -not $status.stage_done -or $status.skipped -eq 'locked') {
            Write-SetupStatus 'Background download unavailable; downloading the plugin update now...'
            & $VenvPython -m agent_worktrees stage-update *> $null
            if (Test-Path $statusFile) {
                try { $status = Get-Content $statusFile -Raw | ConvertFrom-Json } catch {}
            }
        }
        if ($status -and $status.runtime_apply_blocked) {
            Write-SetupLog (
                "Plugin runtime apply blocked: $($status.runtime_apply_blocked)"
            ) 'WARN'
        }

        # (1) Marketplace installer, iff the download changed the payload.
        #     NO re-exec: a launcher-script change applies on the next launch.
        if ($status -and $status.plugin_changed) {
            $pdir = $status.plugin_dir
            $pluginInstaller = if ($pdir) { Join-Path $pdir 'scripts\install.ps1' } else { $null }
            if ($pluginInstaller -and (Test-Path $pluginInstaller)) {
                $savedEnvironment = Push-UpdateEnvironment $status
                if ($env:WORKTREE_BLOCKING_INSTALL) {
                    # Escape hatch (recovery/debug): apply synchronously.
                    Write-SetupStatus 'A new plugin version was downloaded; installing the updated runtime...'
                    $installerArgs = @(
                        'update',
                        '-InstallDir',
                        [string]$status.runtime_root
                    )
                    if ($script:LaunchProject) { $installerArgs += @('-ProjectName', $script:LaunchProject) }
                    try {
                        & pwsh.exe -NoProfile -File $pluginInstaller @installerArgs 2>&1 |
                            ForEach-Object { Write-SetupLog "installer: $_" }
                        if ($LASTEXITCODE -eq 0) {
                            Write-SetupLog 'Installer update succeeded (launcher change, if any, applies next launch)'
                        } else {
                            Write-SetupLog "Installer update failed (exit $LASTEXITCODE) — continuing with existing version" 'WARN'
                        }
                    } finally {
                        Pop-UpdateEnvironment $savedEnvironment
                    }
                } else {
                    # Default: DETACH the install so the launch never blocks on the
                    # (slow) venv rebuild. Immutable versioned slots make this safe --
                    # the installer builds a NEW versions\<v> slot and flips the
                    # current-version marker atomically, never touching the slot THIS
                    # session runs from. Launch on the active slot now; the new version
                    # applies on the next launch (stage-next), the same way the runtime
                    # reconcile already runs detached. The installer carries its own
                    # single-instance lock, so a concurrent background install can't
                    # collide.
                    Write-SetupStatus 'A new plugin version was downloaded; installing it in the background (applies on the next launch)...'
                    $bgArgs = @(
                        '-NoProfile',
                        '-File',
                        "`"$pluginInstaller`"",
                        'update',
                        '-InstallDir',
                        "`"$([string]$status.runtime_root)`""
                    )
                    if ($script:LaunchProject) { $bgArgs += @('-ProjectName', "`"$($script:LaunchProject)`"") }
                    try {
                        # conhost --headless: -WindowStyle Hidden alone is ignored
                        # by the DefTerm handoff (windows-launch-hardening #786).
                        Start-Process -FilePath 'conhost.exe' -ArgumentList (@('--headless', 'pwsh.exe') + $bgArgs) -WindowStyle Hidden | Out-Null
                        Write-SetupLog 'Background install started (new version applies on the next launch)'
                    } catch {
                        Write-SetupLog "Background install failed to start ($($_.Exception.Message)) — continuing" 'WARN'
                    } finally {
                        Pop-UpdateEnvironment $savedEnvironment
                    }
                }
            } else {
                Write-SetupLog "Plugin installer not found ($pluginInstaller) — skipping" 'WARN'
            }
        }

        # (2) Pre-launch self-update (bootstrap-service staleness; two-pass).
        Write-SetupStatus 'Checking bootstrap services for pending updates...'
        $preJson = & $VenvPython -m agent_worktrees pre-launch 2>$null
        if ($LASTEXITCODE -eq 0 -and $preJson) {
            $prePlan = ($preJson -join "`n") | ConvertFrom-Json -ErrorAction SilentlyContinue
            if (-not $prePlan) {
                Write-SetupLog 'pre-launch returned invalid JSON — proceeding' 'WARN'
            } else {
                Write-PlanDiagnostics $prePlan 'Pre-launch'
            }
            if ($prePlan -and $prePlan.action -eq 'self-update') {
                Write-SetupStatus 'Bootstrap services are stale; updating them...'
                foreach ($update in $prePlan.updates) {
                    Write-SetupStatus "Updating $($update.service)..."
                    Write-SetupLog "  command: $($update.command)"
                    $argv = @($update.argv)
                    if ($argv.Count -gt 0) {
                        $exe = $argv[0]
                        $rest = if ($argv.Count -gt 1) { $argv[1..($argv.Count - 1)] } else { @() }
                        $savedEnvironment = Push-UpdateEnvironment $update
                        try {
                            & $exe @rest
                            if ($LASTEXITCODE -ne 0) {
                                Write-SetupLog "Update failed for $($update.service) (exit $LASTEXITCODE)" 'WARN'
                            } else {
                                Write-SetupLog "Updated $($update.service) successfully"
                            }
                        } finally {
                            Pop-UpdateEnvironment $savedEnvironment
                        }
                    }
                }
                Write-SetupLog 'Re-checking staleness after update'
                $preJson = & $VenvPython -m agent_worktrees pre-launch 2>$null
                if ($LASTEXITCODE -eq 0 -and $preJson) {
                    $prePlan = ($preJson -join "`n") | ConvertFrom-Json -ErrorAction SilentlyContinue
                    Write-PlanDiagnostics $prePlan 'Pre-launch'
                    if ($prePlan -and $prePlan.action -eq 'self-update') {
                        Write-SetupLog 'Still stale after update — proceeding anyway' 'WARN'
                    }
                }
            }
        } else {
            Write-SetupLog 'pre-launch check failed or produced no output — proceeding'
        }
    } else {
        Write-SetupStatus 'Skipping plugin update check (--no-update).'
        if ($StageJob) { try { Remove-Job $StageJob -Force -ErrorAction SilentlyContinue } catch {} }
    }

    # (3) Plugin reconciliation (repo-configured payloads + gated runtimes).
    #     Independent of WORKTREE_NO_UPDATE; opt out with WORKTREE_NO_RECONCILE=1.
    #     Two passes: payload first, then runtime (readable only next pass).
    if ($WithReconcile -and $env:WORKTREE_NO_RECONCILE -ne '1') {
        foreach ($rpass in 1, 2) {
            $recJson = & $VenvPython -m agent_worktrees reconcile-plugins 2>$null
            if (-not $recJson) { break }
            try { $recPlan = ($recJson | ConvertFrom-Json) } catch { break }
            Write-PlanDiagnostics $recPlan 'Plugin reconcile'
            if ($recPlan.action -ne 'reconcile') {
                if ($rpass -eq 1) {
                    Write-SetupLog 'Plugin reconcile: no executable actions'
                }
                break
            }
            $recUpdates = @($recPlan.updates)
            Write-SetupStatus "Reconciling plugins (pass ${rpass}): $($recUpdates.Count) action(s)..."
            foreach ($u in $recUpdates) {
                $rargv = @($u.argv)
                if ($rargv.Count -eq 0) { continue }
                if ($rargv[0] -eq 'copilot' -and -not (Get-Command copilot -ErrorAction SilentlyContinue)) {
                    Write-SetupLog "Plugin reconcile: skipping $($u.service) (copilot not on PATH)" 'WARN'
                    continue
                }
                $exe = $rargv[0]
                $rest = @()
                if ($rargv.Count -gt 1) { $rest = $rargv[1..($rargv.Count - 1)] }
                Write-SetupLog "Plugin reconcile: $($u.service) -> $($rargv -join ' ')"
                $savedEnvironment = @{}
                if ($u.PSObject.Properties['unset_environment']) {
                    foreach ($key in @($u.unset_environment | Where-Object { $_ })) {
                        $savedEnvironment[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
                        [Environment]::SetEnvironmentVariable($key, $null, 'Process')
                    }
                }
                if ($u.PSObject.Properties['environment'] -and $u.environment) {
                    foreach ($property in @($u.environment.PSObject.Properties)) {
                        if (-not $savedEnvironment.ContainsKey($property.Name)) {
                            $savedEnvironment[$property.Name] = [Environment]::GetEnvironmentVariable($property.Name, 'Process')
                        }
                        [Environment]::SetEnvironmentVariable(
                            $property.Name, [string]$property.Value, 'Process'
                        )
                    }
                }
                try {
                    & $exe @rest 2>&1 | ForEach-Object { Write-SetupLog "reconcile: $_" }
                } finally {
                    foreach ($entry in $savedEnvironment.GetEnumerator()) {
                        [Environment]::SetEnvironmentVariable(
                            $entry.Key, $entry.Value, 'Process'
                        )
                    }
                }
            }
        }
    }
}

# ── Direct-dispatch commands (bypass resolve/picker) ─────────────────────
# Subcommands that agent_worktrees's main() handles directly — these
# must NOT fall through to the resolve→picker flow.  Keep in sync with
# COMMAND_MAP in __main__.py, plus "services" and "agent-worktrees".
$DirectCommands = @(
    'services', 'repos', 'knowledge', 'agent-worktrees',
    'resolve', 'post-exit', 'finalize', 'push-changes', 'mark-complete',
    'session-backend',
    'status', 'list', 'create', 'cleanup', 'validate', 'install',
    'register', 'unregister', 'uninstall', 'update', 'install-status',
    'deploy-instructions', 'get', 'pre-launch', 'stage-update', 'reconcile-plugins', 'dev', 'handoff-cutover',
    'register-session', 'deregister-session', 'backfill-sessions',
    'anchor-check'
)
if ($CopilotArgs.Count -gt 0 -and $CopilotArgs[0] -in $DirectCommands) {
    Write-SetupLog "Direct dispatch: $($CopilotArgs[0]) (bypassing resolve)"
    # No Picker window to hide behind: stage + apply synchronously (no
    # reconcile, matching the historical direct-command behavior) before
    # dispatching.
    Invoke-UpdateApply -StageJob (Start-UpdateStage)
    $directArgs = @('-m', 'agent_worktrees')
    if ($script:LaunchProject) {
        $directArgs += @('--project', $script:LaunchProject)
    }
    $directArgs += $CopilotArgs
    & $VenvPython @directArgs
    exit $LASTEXITCODE
}

# ── Background update stage (#1430) ──────────────────────────────────────
# Spawn the marketplace download now so it runs WHILE the Picker is open. It is
# joined and applied (installer + pre-launch + reconcile) after resolve returns
# an exec plan, before the psmux handoff -- see Invoke-UpdateApply below.
$script:StageJob = Start-UpdateStage

# ── Resolve launch plan via Python ────────────────────────────────────────
# Python resolve writes JSON to stdout and UI (picker) to stderr.
# Capture stdout only; stderr flows naturally to the terminal.

$resolveArgs = @('-m', 'agent_worktrees')
if ($script:LaunchProject) {
    $resolveArgs += @('--project', $script:LaunchProject)
}
$resolveArgs += @('resolve') + $CopilotArgs
Write-SetupLog "Calling agent_worktrees resolve"
Write-LaunchTrace 'resolve_start'

$jsonOutput = & $VenvPython @resolveArgs

if ($LASTEXITCODE -ne 0) {
    Write-SetupLog "agent_worktrees resolve failed (exit $LASTEXITCODE)" 'ERROR'
    exit $LASTEXITCODE
}

if (-not $jsonOutput) {
    Write-SetupLog 'resolve produced no stdout output' 'ERROR'
    Write-Error 'resolve produced no output on stdout'
    exit 1
}

# ── Parse the JSON plan ──────────────────────────────────────────────────

$plan = ($jsonOutput -join "`n") | ConvertFrom-Json -ErrorAction Stop

# Non-interactive resolves (`resolve --json --worktree-id` / `--json --new`,
# used by agent-bridge ACP launches) emit the bridge's nested plan shape:
#   { worktree = {...}; launch = { action = 'exec'; ... } }
# The handling below consumes the *flat* plan ($plan.action / .work_dir / .cmd);
# the nested `launch` object carries the identical keys, so unwrap it when
# present. A flat plan (no `launch` property) is used unchanged.
if ($plan.PSObject.Properties.Name -contains 'launch') {
    $plan = $plan.launch
}
if (-not $script:LaunchProject -and $plan.PSObject.Properties.Name -contains 'project') {
    $script:LaunchProject = [string]$plan.project
}

Write-SetupLog "Plan resolved: action=$($plan.action) work_dir=$($plan.work_dir) worktree_id=$($plan.worktree_id)"

if ($plan.action -eq 'none') {
    exit ([int]($plan.exit_code))
}

# ── Remote machine handoff via SSH ───────────────────────────────────────
if ($plan.action -eq 'remote') {
    $sshAlias = $plan.ssh_alias
    $remoteCmd = $plan.remote_command
    Write-SetupLog "Handing off to remote machine: $($plan.display_name) via $sshAlias"
    Write-Host "Connecting to $($plan.display_name)..." -ForegroundColor Cyan
    # exec ssh with TTY allocation; the remote binstub takes over
    & ssh -t $sshAlias $remoteCmd
    exit $LASTEXITCODE
}

# ── Picker refresh: apply the staged update, then relaunch (#1430) ───────
# The picker's refresh icon exits with action=refresh. The picker runs from
# the runtime venv the update replaces, so it can't apply in place -- apply
# here (venv now free), then re-exec the (now-updated) launcher to reopen the
# picker on the new version.
if ($plan.action -eq 'refresh') {
    Write-SetupLog 'Picker refresh -- running full update and relaunching'
    # The explicit "Update available" -> enter gesture runs the SAME
    # comprehensive update as the `update` command (every registered plugin
    # payload + sibling modules + the runtime installer), not just the
    # opportunistic staged apply. The staged apply only pulls agent-worktrees
    # and gates its installer on a fingerprint diff / venv-drift, so an
    # already-pulled-but-not-yet-deployed payload -- or any sibling
    # plugin/module -- could relaunch stale (dotfiles#443). `update` is itself
    # version-gated, so it stays quick when everything is already current.
    if (-not $noUpdate) {
        $updateArgs = @('-m', 'agent_worktrees')
        if ($script:LaunchProject) {
            $updateArgs += @('--project', $script:LaunchProject)
        }
        $updateArgs += 'update'
        & $VenvPython @updateArgs
        if ($LASTEXITCODE -ne 0) {
            Write-SetupLog "Full update returned exit $LASTEXITCODE -- continuing to reconcile/relaunch" 'WARN'
        }
    }
    # Reconcile repo-gated payloads/runtimes + reap the staged job (installer
    # and pre-launch here no-op: the full update above already deployed).
    Invoke-UpdateApply -StageJob $script:StageJob -WithReconcile -ShowStatus
    $newLauncher = Join-Path $env:USERPROFILE '.agent-worktrees\bin\launch-session.ps1'
    if (Test-Path $newLauncher) {
        $relaunchArgs = @()
        if ($script:LaunchProject) {
            $relaunchArgs += @('--project', $script:LaunchProject)
        }
        $relaunchArgs += $CopilotArgs
        if ($CopilotPassthrough.Count -gt 0) {
            $relaunchArgs += @('--') + $CopilotPassthrough
        }
        & pwsh.exe -NoProfile -File $newLauncher @relaunchArgs
        exit $LASTEXITCODE
    }
    Write-SetupLog 'Relaunch launcher missing after refresh; exiting' 'WARN'
    exit 1
}

if ($plan.action -ne 'exec') {
    Write-Error "Unknown action: $($plan.action)"
    exit 1
}

# ── Fast re-attach: skip the update when JOINING an already-live session ──
# Opening a worktree whose mux session is already running just re-attaches to
# the Copilot already executing inside it. The plugin/runtime update is
# irrelevant to that running process (it applies on the process's next fresh
# start), so paying for the staged download + installer + pre-launch here only
# delays the re-attach. When a live `wt-<id>` session exists, reap the staged
# job and skip the apply for a fast jump-back-in. A fresh create/resume (no live
# session) still updates normally. Self-contained probe (the psmux-bin resolver
# + $noMux/$nested are defined later in the psmux-handoff block) so this stays a
# surgical gate without reordering the rest of the launcher.
function Test-AwJoiningLiveSession {
    # No-mux launches always (re)start Copilot directly -- the update is relevant.
    $noMuxNow = ($env:WORKTREE_NO_MUX -eq '1') -or [bool]$plan.no_mux
    if ($noMuxNow) { return $false }
    $cmd = Get-Command psmux -ErrorAction SilentlyContinue
    if (-not $cmd) { return $false }
    $pathHelper = Join-Path $PSScriptRoot 'psmux-path.ps1'
    if (Test-Path -LiteralPath $pathHelper) {
        . $pathHelper
        $desired = Find-AwCompatiblePsmuxPackageBinary `
            -PackageRoot (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages') `
            -MinimumVersion '3.3.8'
        if ($desired) { $cmd = Get-Command $desired.Path }
    }
    # Resolve the WinGet reparse-stub to the real exe (pwsh 7.4 can't launch the
    # 0-byte App Execution Alias); mirrors Resolve-AwPsmuxBin below.
    $bin = $cmd.Source
    try {
        $item = Get-Item -LiteralPath $bin -ErrorAction Stop
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.Length -eq 0) {
            $real = Get-ChildItem -LiteralPath (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages') `
                -Recurse -Filter 'psmux.exe' -ErrorAction SilentlyContinue |
                Select-Object -First 1 -ExpandProperty FullName
            if ($real) { $bin = $real }
        }
    } catch {}
    $wtId = if ([string]::IsNullOrWhiteSpace($plan.worktree_id)) { 'base' } else { $plan.worktree_id }
    # `.` is the window/pane separator in a mux target -- keep in sync with
    # `sessions.mux_session_name` (and the bash launcher).
    $wtId = $wtId -replace '\.', '_'
    try {
        $null = & $bin has-session -t "wt-$wtId" 2>&1
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

# ── Join the background update + apply, before the psmux handoff (#1430) ──
# The Picker has closed, so it is now safe to swap the runtime venv. This waits
# for the staged marketplace download, runs the installer if it changed the
# payload (no re-exec -- a launcher change applies next launch), then the
# pre-launch self-update and plugin reconcile, so Copilot starts on the
# finished update.
$joiningLiveSession = Test-AwJoiningLiveSession
if ($joiningLiveSession) {
    Write-SetupLog 'Joining an already-live mux session; skipping pre-launch update for a fast re-attach (update applies on the process next fresh start).'
    if ($script:StageJob) {
        try { Remove-Job $script:StageJob -Force -ErrorAction SilentlyContinue } catch {}
        $script:StageJob = $null
    }
} else {
    Invoke-UpdateApply -StageJob $script:StageJob -WithReconcile -ShowStatus
}

# ── Execute the launch plan ──────────────────────────────────────────────

Set-Location $plan.work_dir

# Compose the paired private knowledge repo's plugin settings into the harness
# worktree before Copilot starts and performs plugin discovery. status_path is
# the real worktree during deprecated Bare resume (work_dir is HOME).
$refreshedVenvPython = Resolve-RuntimePython
if (-not $refreshedVenvPython) {
    $runtimeMessage = (
        'Agent-worktrees runtime is unavailable after update apply; ' +
        "expected a complete slot under $RuntimeDir"
    )
    Write-SetupLog $runtimeMessage 'ERROR'
    [Console]::Error.WriteLine("ERROR: $runtimeMessage")
    exit 1
}
$VenvPython = $refreshedVenvPython
Write-SetupLog "Runtime refreshed before knowledge preflight: $VenvPython"

$knowledgeCwd = if ($plan.PSObject.Properties['status_path'] -and $plan.status_path) {
    [string]$plan.status_path
} else {
    [string]$plan.work_dir
}
$knowledgeArgs = @('-m', 'agent_worktrees')
if ($script:LaunchProject) {
    $knowledgeArgs += @('--project', $script:LaunchProject)
}
$knowledgeArgs += @('knowledge', 'compose-plugins', '--cwd', $knowledgeCwd, '--json')
$savedErrorAction = $ErrorActionPreference
try {
    # Handle native non-zero explicitly even when the host enabled
    # PSNativeCommandUseErrorActionPreference.
    $ErrorActionPreference = 'Continue'
    $knowledgeOutput = & $VenvPython @knowledgeArgs 2>&1
    $knowledgeExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $savedErrorAction
}
$knowledgeText = (($knowledgeOutput | ForEach-Object { "$_" }) -join '').Trim()
if ($knowledgeExit -eq 0) {
    Write-SetupLog "Knowledge plugin preflight completed: $knowledgeText"
} else {
    Write-SetupLog "Knowledge plugin preflight failed (exit ${knowledgeExit}): $knowledgeText" 'ERROR'
    [Console]::Error.WriteLine("ERROR: Knowledge plugin preflight failed: $knowledgeText")
    exit $knowledgeExit
}

$marketplaceArgs = @('-m', 'agent_worktrees', 'reconcile-marketplaces',
    '--cwd', $knowledgeCwd, '--ensure-ignored', '--json')
$savedErrorAction = $ErrorActionPreference
try {
    $ErrorActionPreference = 'Continue'
    $marketplaceOutput = & $VenvPython @marketplaceArgs 2>&1
    $marketplaceExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $savedErrorAction
}
$marketplaceText = (($marketplaceOutput | ForEach-Object { "$_" }) -join '').Trim()
if ($marketplaceExit -eq 0) {
    Write-SetupLog "Marketplace override preflight completed: $marketplaceText"
} else {
    Write-SetupLog "Marketplace override preflight failed (exit ${marketplaceExit}): $marketplaceText" 'ERROR'
    [Console]::Error.WriteLine("ERROR: Marketplace override preflight failed: $marketplaceText")
    exit $marketplaceExit
}

# Apply environment variables from the launch plan
if ($plan.env) {
    foreach ($prop in $plan.env.PSObject.Properties) {
        [System.Environment]::SetEnvironmentVariable($prop.Name, [string]$prop.Value, 'Process')
    }
}

# Identity vars are NOT published into the child Copilot session -- in-session
# tools resolve context from CWD (git-like). Clear inherited legacy copies so
# the session env carries no ambient project/worktree identity.
Remove-Item Env:WORKTREE_ID -ErrorAction SilentlyContinue
Remove-Item Env:WORKTREE_PROJECT -ErrorAction SilentlyContinue

$cmd = @($plan.cmd)

$sessionBackend = $null
$ahpArgs = @()
if (
    -not $joiningLiveSession -and
    -not [string]::IsNullOrWhiteSpace([string]$plan.worktree_id)
) {
    $backendBaseArgs = @('-m', 'agent_worktrees')
    if ($script:LaunchProject) {
        $backendBaseArgs += @('--project', $script:LaunchProject)
    }
    $backendStatusArgs = $backendBaseArgs + @(
        'session-backend', 'status',
        '--worktree-id', [string]$plan.worktree_id,
        '--json'
    )
    $savedErrorAction = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $backendStatusOutput = & $VenvPython @backendStatusArgs 2>&1
        $backendStatusExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $savedErrorAction
    }
    $backendStatusText = (
        ($backendStatusOutput | ForEach-Object { "$_" }) -join ''
    ).Trim()
    if ($backendStatusExit -ne 0) {
        Write-SetupLog (
            "Session backend status failed (exit ${backendStatusExit}): " +
            $backendStatusText
        ) 'ERROR'
        [Console]::Error.WriteLine(
            "ERROR: Session backend status failed: $backendStatusText"
        )
        exit $backendStatusExit
    }
    try {
        $sessionBackend = $backendStatusText |
            ConvertFrom-Json -ErrorAction Stop
    } catch {
        Write-SetupLog (
            "Session backend returned invalid JSON: $backendStatusText"
        ) 'ERROR'
        [Console]::Error.WriteLine('ERROR: Session backend returned invalid JSON.')
        exit 3
    }
    if ($sessionBackend.enabled -and $sessionBackend.kind -eq 'ahp') {
        $token = & gh auth token --user ([string]$sessionBackend.auth_account) 2>$null
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($token)) {
            $message = "Could not mint the AHP client token for account $($sessionBackend.auth_account)."
            Write-SetupLog $message 'ERROR'
            [Console]::Error.WriteLine("ERROR: $message")
            exit 3
        }
        $backendEnsureArgs = $backendBaseArgs + @(
            'session-backend', 'ensure',
            '--worktree-id', [string]$plan.worktree_id,
            '--json'
        )
        $savedErrorAction = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            $env:AGENT_WORKTREES_AHP_AUTH_TOKEN = $token.Trim()
            $backendOutput = & $VenvPython @backendEnsureArgs 2>&1
            $backendExit = $LASTEXITCODE
        } finally {
            Remove-Item Env:AGENT_WORKTREES_AHP_AUTH_TOKEN `
                -ErrorAction SilentlyContinue
            $ErrorActionPreference = $savedErrorAction
        }
        $backendText = (
            ($backendOutput | ForEach-Object { "$_" }) -join ''
        ).Trim()
        if ($backendExit -ne 0) {
            Write-SetupLog (
                "Session backend ensure failed (exit ${backendExit}): " +
                $backendText
            ) 'ERROR'
            [Console]::Error.WriteLine(
                "ERROR: Session backend ensure failed: $backendText"
            )
            exit $backendExit
        }
        try {
            $sessionBackend = $backendText |
                ConvertFrom-Json -ErrorAction Stop
        } catch {
            Write-SetupLog (
                "Session backend returned invalid JSON: $backendText"
            ) 'ERROR'
            [Console]::Error.WriteLine(
                'ERROR: Session backend returned invalid JSON.'
            )
            exit 3
        }
        $features = @(
            ([string]$env:COPILOT_CLI_ENABLED_FEATURE_FLAGS -split ',')
            | ForEach-Object { $_.Trim() }
            | Where-Object { $_ }
        )
        if ($features -notcontains 'AHP_CLIENT') {
            $features += 'AHP_CLIENT'
        }
        $env:COPILOT_CLI_ENABLED_FEATURE_FLAGS = $features -join ','
        $ahpArgs = @(
            '--ahp', [string]$sessionBackend.endpoint_url,
            "--resume=$($sessionBackend.session_id)"
        )
        $plan.post_exit = $false
        Write-SetupLog (
            "AHP client bound to session $($sessionBackend.session_id) " +
            "at $($sessionBackend.endpoint_url)"
        )
    }
}

# Append copilot passthrough args (from after -- separator)
if ($CopilotPassthrough.Count -gt 0) {
    $cmd += $CopilotPassthrough
}

if ($ahpArgs.Count -gt 0) {
    $filteredCmd = [System.Collections.Generic.List[string]]::new()
    $skipNext = $false
    foreach ($arg in $cmd) {
        if ($skipNext) {
            $skipNext = $false
            continue
        }
        if ($arg -eq '--ahp') {
            $skipNext = $true
            continue
        }
        if ($arg -like '--ahp=*' -or $arg -like '--resume=*') {
            continue
        }
        $filteredCmd.Add([string]$arg)
    }
    $cmd = @($filteredCmd)
    if ($cmd -notcontains '--experimental') {
        $cmd += '--experimental'
    }
    $cmd += $ahpArgs
}

# ── psmux pane command: pass VERBATIM (`pwsh -File <script>`) ─────────────
# An earlier optimization (copilot-extensions #102) collapsed the pane command
# to a single in-process `& '<script>' <args>` string so psmux's wrapper shell
# (`pwsh -NoLogo -Command "<args>"`) ran default-setup.ps1 without spawning a
# second pwsh -- one ~80 MB shell per tab instead of two. It was REVERTED: under
# `-Command`, PowerShell re-tokenizes the string and treats a `--`-prefixed
# passthrough arg (notably the always-appended `--allow-all`) as the
# end-of-parameters marker, so `allow-all` binds POSITIONALLY to a random string
# param (-SetupHook / -EnvScript) instead of reaching Copilot's
# ValueFromRemainingArguments -- silently dropping auto-approve and warning
# "env_script not found: --allow-all". The `pwsh -File` form passes args
# literally (no re-tokenization), so `--allow-all` survives. Correctness over the
# ~80 MB; this also unifies the interactive launch with the cutover/embody paths
# (sessions.py `_mux_pane_cmd`), which already pass the command verbatim. A
# `--`-safe re-collapse (e.g. explicit `-CopilotArgs` binding) can revisit #102.

# ── psmux session-per-worktree ───────────────────────────────────────
# Each worktree gets a single shared psmux session. Multiple terminal
# connections all land in the same session. The psmux session ends when
# the launched process exits (command passed directly to new-session).
#
# Mirrors the Linux tmux integration in launch-session.sh.
# --no-mux / WORKTREE_NO_MUX=1 bypasses psmux for debugging.

$noMux = ($env:WORKTREE_NO_MUX -eq '1') -or [bool]$plan.no_mux
if ($noMux) {
    Write-SetupLog 'Mux disabled; launching directly'
}

# Durable launcher-start mark (Tier-A), at parity with launch-session.sh. Fires
# once the worktree id + mux mode are known, before the (possibly hanging)
# psmux/handoff step, so a launcher that dies mid-flow still leaves a persistent
# trace. Records the mux mode and links to the verbose Tier-B setup log.
Write-ActivityLog -Event 'launcher_started' -WorktreeId $plan.worktree_id -Fields @(
    "mux=$(if ($noMux) { 'none' } else { 'psmux' })",
    "setup_log=$script:SetupLog"
)

$psmuxCmd = Get-Command psmux -ErrorAction SilentlyContinue

function Write-AwMuxFailure {
    param(
        [Parameter(Mandatory)][string]$Reason,
        [int]$ExitCode = 1,
        [string[]]$Fields = @()
    )
    Write-ActivityLog -Event 'mux_failed' -WorktreeId $plan.worktree_id -Fields (@(
        'mux=psmux',
        "reason=$Reason",
        "exit_code=$ExitCode"
    ) + $Fields)
}

function Test-AwCanPromptForMuxRetry {
    if (-not $script:ShowLaunchStatus) { return $false }
    if ($CopilotArgs -contains '--stdio') { return $false }
    try { return -not [Console]::IsInputRedirected } catch { return $false }
}

function Read-AwMuxRetryChoice {
    param([Parameter(Mandatory)][string]$Session)
    if (-not (Test-AwCanPromptForMuxRetry)) { return $false }
    try {
        $choice = Read-Host "PSMux could not create '$Session'. Retry? [y/N]"
        return $choice -match '^(?i:y|yes)$'
    } catch {
        Write-SetupLog "psmux: retry prompt failed: $($_.Exception.Message)" 'WARN'
        return $false
    }
}

function Stop-AwOwnedPsmuxSession {
    param([Parameter(Mandatory)][string]$Session)

    # A failed `new-session` can leave a server and pane subtree running before
    # the port file becomes discoverable. The launch id is injected into the
    # server command line, so cleanup can prove ownership instead of killing a
    # same-name session won by a concurrent launcher.
    try {
        if ([string]::IsNullOrWhiteSpace($script:LaunchId)) {
            Write-SetupLog "psmux: cannot prove ownership of partial session $Session; leaving it intact" 'WARN'
            return
        }
        $all = @(Get-CimInstance Win32_Process -ErrorAction Stop)
        $escapedSession = [regex]::Escape($Session)
        $launchToken = "WORKTREE_LAUNCH_ID=$($script:LaunchId)"
        $roots = @(
            $all | Where-Object {
                $_.Name -eq 'psmux.exe' -and
                [string]$_.CommandLine -match "(?:^|\s)server\s+-s\s+$escapedSession(?:\s|$)" -and
                [string]$_.CommandLine -like "*$launchToken*"
            }
        )
        if (-not $roots) {
            Write-SetupLog "psmux: no partial session owned by this launch was found for $Session" 'WARN'
            return
        }

        $depth = @{}
        $identity = @{}
        $frontier = @()
        foreach ($root in $roots) {
            $pidValue = [int]$root.ProcessId
            $depth[$pidValue] = 0
            $identity[$pidValue] = $root
            $frontier += $pidValue
        }
        while ($frontier.Count -gt 0) {
            $next = @()
            foreach ($parentPid in $frontier) {
                foreach ($child in @($all | Where-Object {
                    [int]$_.ParentProcessId -eq $parentPid
                })) {
                    $childPid = [int]$child.ProcessId
                    if (-not $depth.ContainsKey($childPid)) {
                        $depth[$childPid] = [int]$depth[$parentPid] + 1
                        $identity[$childPid] = $child
                        $next += $childPid
                    }
                }
            }
            $frontier = $next
        }

        foreach ($pidValue in @(
            $depth.GetEnumerator() |
                Sort-Object Value -Descending |
                ForEach-Object { [int]$_.Key }
        )) {
            try {
                $expected = $identity[$pidValue]
                $process = [Diagnostics.Process]::GetProcessById($pidValue)
                $expectedStart = ([datetime]$expected.CreationDate).ToUniversalTime()
                $actualStart = $process.StartTime.ToUniversalTime()
                $startDeltaMs = [Math]::Abs(($actualStart - $expectedStart).TotalMilliseconds)
                $expectedName = [IO.Path]::GetFileNameWithoutExtension([string]$expected.Name)
                if ($startDeltaMs -gt 1 -or
                        -not $process.ProcessName.Equals(
                            $expectedName, [StringComparison]::OrdinalIgnoreCase
                        )) {
                    Write-SetupLog "psmux: skipped reused process id $pidValue during cleanup" 'WARN'
                    continue
                }
                $process.Kill()
            } catch {}
        }
        Write-SetupLog "psmux: reaped session process tree owned by this launch for $Session" 'WARN'
    } catch {
        Write-SetupLog "psmux: failed to reap owned session $Session`: $($_.Exception.Message)" 'WARN'
    }
}

if (-not $noMux -and -not $psmuxCmd) {
    $message = 'psmux is required for interactive sessions but was not found. Use --no-mux to request a direct session explicitly.'
    Write-SetupLog $message 'ERROR'
    Write-AwMuxFailure -Reason 'not_found'
    Write-Error $message -ErrorAction Continue
    exit 1
}

# Resolve psmux to a *launchable* executable path. WinGet installs psmux as a
# 0-byte App Execution Alias reparse stub at
# %LOCALAPPDATA%\Microsoft\WinGet\Links\psmux.exe. PowerShell 7.4.x cannot
# launch such stubs from its native-command path: every `& psmux ...` raises a
# *terminating* error ("Program 'psmux.exe' failed to run: StandardOutputEncoding
# is only supported when standard output is redirected") regardless of TTY or
# stream redirection. Launching the real target under
# %LOCALAPPDATA%\Microsoft\WinGet\Packages\...\psmux.exe sidesteps the bug on
# every pwsh version (pwsh >=7.5 launches the stub fine too). All psmux calls
# below go through $script:AwPsmuxBin so both the launcher and the dot-sourced
# session-options helper use the resolved path.
function Resolve-AwPsmuxBin {
    param($Cmd)
    if (-not $Cmd) { return 'psmux' }
    $pathHelper = Join-Path $PSScriptRoot 'psmux-path.ps1'
    if (Test-Path -LiteralPath $pathHelper) {
        . $pathHelper
        $desired = Find-AwCompatiblePsmuxPackageBinary `
            -PackageRoot (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages') `
            -MinimumVersion '3.3.8'
        if ($desired) { return $desired.Path }
    }
    $src = $Cmd.Source
    try {
        $item = Get-Item -LiteralPath $src -ErrorAction Stop
        $isReparse = [bool]($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
        if ($isReparse -or $item.Length -eq 0) {
            $pkgRoot = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
            $real = Get-ChildItem -LiteralPath $pkgRoot -Recurse -Filter 'psmux.exe' `
                -ErrorAction SilentlyContinue |
                Select-Object -First 1 -ExpandProperty FullName
            if ($real) { return $real }
        }
    } catch {}
    return $src
}
$script:AwPsmuxBin = Resolve-AwPsmuxBin $psmuxCmd
if ($psmuxCmd -and $script:AwPsmuxBin -ne $psmuxCmd.Source) {
    Write-SetupLog "psmux: resolved WinGet reparse stub to real exe: $($script:AwPsmuxBin)"
}

# Detect nested invocation: if we're already inside a psmux/tmux session,
# we must NOT call attach-session — doing so steals the parent's terminal.
$nested = [bool]$env:TMUX

# Windows 10 ConPTY leaks conhost.exe title text into the SSH stream at
# session start, creating a scroll offset that pushes psmux's status bar
# below the visible area.  Clear the viewport before attach to reset.
function Reset-SshConptyViewport {
    if ($env:SSH_CONNECTION) { [Console]::Write("`e[2J`e[H") }
}

# Smart App Control / WDAC can block an unsigned psmux.exe, and PowerShell
# 7.4.x cannot launch a WinGet reparse-stub psmux (resolved above). Either way
# a blocked/unlaunchable psmux raises a *terminating* error rather than a
# non-zero exit code. Probe with --version, whose zero-success contract avoids
# confusing an expected "session absent" result with a launch failure. A direct
# session is never an implicit recovery path; callers must request --no-mux.
if (-not $noMux) {
    $probeExit = 1
    $probeError = ''
    try {
        $null = & $script:AwPsmuxBin --version 2>&1
        $probeExit = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
    } catch {
        $probeError = $_.Exception.Message.Split([Environment]::NewLine)[0].Trim()
    }
    if ($probeExit -ne 0) {
        $detail = if ($probeError) { ": $probeError" } else { '' }
        $message = "psmux launch probe failed (exit code $probeExit)$detail. Use --no-mux to request a direct session explicitly."
        Write-SetupLog $message 'ERROR'
        Write-AwMuxFailure -Reason 'launch_probe_failed' -ExitCode $probeExit
        Write-Error $message -ErrorAction Continue
        exit $probeExit
    }
}

# psmux 3.3.6 regression: `attach-session -t <name>` ignores -t and attaches to
# whatever session is recorded in ~/.psmux/last_session, so every worktree
# launch lands in the most-recent session instead of the one we asked for.
# Writing the target name to that file immediately before attach forces psmux to
# honor the intended session. Harmless on fixed/older psmux (which honor -t and
# rewrite the file on attach anyway). See install.ps1 for the version pin.
function Set-PsmuxLastSession {
    param([string]$Name)
    try {
        $psmuxDir = Join-Path $env:USERPROFILE '.psmux'
        if (Test-Path $psmuxDir) {
            Set-Content -Path (Join-Path $psmuxDir 'last_session') `
                -Value $Name -NoNewline -ErrorAction SilentlyContinue
        }
    } catch {}
}
# Attach to a psmux session while forwarding Ctrl+C into the pane (#1453).
# A human Ctrl+C at the attached client otherwise raises PipelineStoppedException
# in THIS launcher pwsh around `attach-session`, aborting attach before psmux
# forwards the interrupt into the pane -- so the inner Copilot never receives it
# and never runs its graceful (double-Ctrl+C) quit. Treating Ctrl+C as input
# suppresses the signal at the launcher host, so psmux's attach client passes the
# 0x03 through to the pane where Copilot (a raw-mode TUI) handles the quit itself.
# The setting is restored afterward so the launcher's own post-attach cleanup
# keeps normal Ctrl+C semantics. Guarded: a redirected / no-console context makes
# the property throw ("handle is invalid") -- degrade to the prior default (a
# Ctrl+C there still returns from attach, just without forwarding). $LASTEXITCODE
# is set by the native attach-session and preserved across the .NET restore.
function Invoke-AwPsmuxAttach {
    param([Parameter(Mandatory)][string]$Session)
    $prevTreatCtrlC = $null
    $treatSet = $false
    try {
        $prevTreatCtrlC = [Console]::TreatControlCAsInput
        [Console]::TreatControlCAsInput = $true
        $treatSet = $true
    } catch {
        Write-SetupLog "psmux: could not set TreatControlCAsInput ($($_.Exception.Message)); attaching with default Ctrl+C handling" 'WARN'
    }
    try {
        & $script:AwPsmuxBin attach-session -t $Session
    } finally {
        if ($treatSet) {
            try { [Console]::TreatControlCAsInput = $prevTreatCtrlC } catch {}
        }
    }
}
# Start the detached status-bar updater for a session. It renders the
# identity (@aw_ctx) once and refreshes the git-disposition (@aw_seg) off
# psmux's paint path, so the status bar never spawns a process per render.
# Best-effort: a failure here just leaves a static/blank bar, never blocks
# the launch.  Safe to call on every create/join: the updater's @aw_updater
# token elects a single live instance, so older ones self-retire.
function Start-StatusUpdater {
    param([string]$Session, [string]$WorkDir)
    if (-not $Session) { return }
    try {
        $updArgs = @('-m', 'agent_worktrees', 'status-updater',
                     '--session', $Session, '--mux', 'psmux')
        if ($WorkDir) { $updArgs += @('--path', $WorkDir) }
        # conhost --headless: -WindowStyle Hidden alone is ignored by the DefTerm
        # handoff and can flash a console (windows-launch-hardening #786).
        $savedAuth = @{}
        foreach ($name in @(
            'GH_TOKEN',
            'GITHUB_TOKEN',
            'AGENT_WORKTREES_AHP_AUTH_TOKEN'
        )) {
            $savedAuth[$name] = [Environment]::GetEnvironmentVariable(
                $name,
                'Process'
            )
            [Environment]::SetEnvironmentVariable($name, $null, 'Process')
        }
        try {
            Start-Process -FilePath 'conhost.exe' `
                -ArgumentList (@('--headless', "`"$VenvPython`"") + $updArgs) `
                -WorkingDirectory $HOME -WindowStyle Hidden `
                -ErrorAction Stop | Out-Null
        } finally {
            foreach ($name in $savedAuth.Keys) {
                [Environment]::SetEnvironmentVariable(
                    $name,
                    $savedAuth[$name],
                    'Process'
                )
            }
        }
        Write-SetupLog "psmux: started status-updater for $Session"
    } catch {
        Write-SetupLog "psmux: status-updater spawn failed: $($_.Exception.Message)" 'WARN'
    }
}
# Per-session psmux options (status bar + behaviors). agent-worktrees does NOT
# own ~/.psmux.conf; the launcher stamps these onto each session it creates or
# joins (psmux set-option -t <session>, no -g), mirroring the Linux/WSL
# session-options.sh. Dot-source the helper deployed alongside this launcher
# (~/.agent-worktrees/bin/session-options.ps1).
$script:AwSessionOptions = Join-Path $PSScriptRoot 'session-options.ps1'
if (Test-Path $script:AwSessionOptions) {
    try { . $script:AwSessionOptions }
    catch { Write-SetupLog "psmux: failed to load session-options.ps1: $($_.Exception.Message)" 'WARN' }
}
function Set-AwSessionOptionsSafe {
    param([string]$Session)
    if (Get-Command Set-AwPsmuxSessionOptions -ErrorAction SilentlyContinue) {
        try { Set-AwPsmuxSessionOptions -Session $Session }
        catch { Write-SetupLog "psmux: session-options apply failed: $($_.Exception.Message)" 'WARN' }
    }
}
# Apply the psmux keystroke passthrough to this session's server via source-file
# (per-session; the only primitive that reliably applies key-table directives on
# psmux -- command-line bind-key/unbind-key no-op). Called at create + join so
# every session gets PageUp/wheel/arrow passthrough when it starts, restoring the
# per-session-at-launch model (#1453/#3946 mux thread; regression 25c41b7).
function Invoke-AwPsmuxPassthroughSafe {
    param([string]$Session)
    if (Get-Command Invoke-AwPsmuxPassthrough -ErrorAction SilentlyContinue) {
        try { Invoke-AwPsmuxPassthrough -Session $Session }
        catch { Write-SetupLog "psmux: passthrough apply failed: $($_.Exception.Message)" 'WARN' }
    }
}
if (-not $noMux) {
    $wtId = if ([string]::IsNullOrWhiteSpace($plan.worktree_id)) { 'base' } else { $plan.worktree_id }
    # `.` is the window/pane separator in a mux target -- keep in sync with
    # `sessions.mux_session_name` (and the bash launcher).
    $wtId = $wtId -replace '\.', '_'
    $sessName = "wt-$wtId"
    Write-SetupLog "psmux: looking for session $sessName"

    # Path the status-bar updater renders from. Normally the pane cwd, but for
    # deprecated Bare resume the pane launches in HOME while the bar must still
    # show the worktree's identity + git disposition -- so prefer the plan's
    # status_path (the real worktree) and
    # fall back to work_dir for every other launch.
    $muxStatusPath = if ($plan.PSObject.Properties['status_path'] -and $plan.status_path) {
        [string]$plan.status_path
    } else {
        [string]$plan.work_dir
    }

    # If a psmux session already exists for this worktree, join it.
    # Note: psmux does not support tmux's "=" exact-match prefix on -t.
    $null = & $script:AwPsmuxBin has-session -t $sessName 2>&1
    if ($LASTEXITCODE -eq 0) {
        if ($nested) {
            Write-Host "Session already exists: $sessName (open a new terminal to join)"
            exit 0
        }
        Write-Host "Joining existing session: $sessName"
        Write-ActivityLog -Event 'mux_attached' -WorktreeId $plan.worktree_id -Fields @('mux=join')
        Reset-SshConptyViewport
        # Re-stamp per-session options on (re)connect so a long-lived session
        # picks up the current bar without us owning the global config.
        Set-AwSessionOptionsSafe $sessName
        # Apply the keystroke passthrough to this session's server (per-session
        # source-file) so a rejoined long-lived session picks up PageUp/wheel/
        # arrow passthrough.
        Invoke-AwPsmuxPassthroughSafe $sessName
        # (Re)assert the updater on join: if the prior one died, this revives
        # the bar; if it's alive, the token guard makes the new one retire.
        Start-StatusUpdater $sessName $muxStatusPath
        # Write last_session AFTER spawning the updater, immediately before
        # attach -- mirroring the create branch below. The updater connects to
        # psmux as a background client, which can rewrite ~/.psmux/last_session;
        # since the 3.3.6 attach regression reads that file instead of honoring
        # -t, setting it any earlier lets the updater clobber our target and the
        # join lands in whatever session was last current (collapsing two
        # worktrees onto one session). Set-PsmuxLastSession must be the final
        # psmux-affecting action before attach.
        Set-PsmuxLastSession $sessName
        $attachExit = 1
        $attachError = ''
        try {
            Invoke-AwPsmuxAttach $sessName
            $attachExit = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
        } catch [System.Management.Automation.PipelineStoppedException] {
            $attachExit = 130
        } catch {
            $attachError = $_.Exception.Message.Split([Environment]::NewLine)[0].Trim()
        }
        if ($attachExit -eq 0) {
            exit 0
        }
        $detail = if ($attachError) { ": $attachError" } else { '' }
        $message = "Failed to attach to existing psmux session '$sessName' (exit code $attachExit)$detail."
        Write-SetupLog $message 'ERROR'
        Write-AwMuxFailure -Reason 'attach_failed' -ExitCode $attachExit
        Write-Error $message -ErrorAction Continue
        exit $attachExit
    }

    # Build -e flags for env propagation into the psmux server.
    # Merge plan.env with launcher-owned vars; launcher values win. Identity
    # vars (WORKTREE_PROJECT/WORKTREE_ID) are deliberately NOT injected -- the
    # child resolves context from CWD.
    $mergedEnv = [ordered]@{}
    if ($plan.env) {
        foreach ($prop in $plan.env.PSObject.Properties) {
            if (
                $ahpArgs.Count -gt 0 -and
                $prop.Name -in @(
                    'GH_TOKEN',
                    'GITHUB_TOKEN',
                    'AGENT_WORKTREES_AHP_AUTH_TOKEN'
                )
            ) {
                continue
            }
            $mergedEnv[$prop.Name] = [string]$prop.Value
        }
    }
    $mergedEnv['WORKTREE_SETUP_LOG'] = [string]$script:SetupLog
    if ($script:LaunchId) { $mergedEnv['WORKTREE_LAUNCH_ID'] = [string]$script:LaunchId }

    $envFlags = @()
    foreach ($kv in $mergedEnv.GetEnumerator()) {
        $envFlags += '-e'
        $envFlags += "$($kv.Key)=$($kv.Value)"
    }

    Write-SetupLog "psmux: creating session $sessName"
    Write-Host "Creating psmux session: $sessName"
    Write-Host ''

    # Pass the command directly to new-session so the psmux session
    # (and its single pane) exits when the process finishes — no
    # lingering shell, matching the Linux tmux behavior. The command is
    # passed VERBATIM (`pwsh -File <script> … --allow-all`): psmux wraps it in
    # its own `pwsh -Command` shell, but the -File child receives its args
    # literally, so `--allow-all` (and any other `--` passthrough) reaches
    # Copilot intact. (Do NOT collapse to `& '<script>' …` -- see the note above
    # the session block: that broke `--allow-all` binding, #102.)
    #
    # Clear nesting vars so psmux doesn't kill the detached session.
    # The new session is independent — it shouldn't inherit the parent's
    # nesting state even though we're creating it from inside a psmux pane.
    Write-SetupLog "psmux: pane command: $($cmd -join ' ')"

    # Route the pane through pane-wrapper.ps1 so the child's real exit code is
    # observable (recorded as a pane_exited activity mark) and a crash shows a
    # diagnostic before the pane closes -- the Windows counterpart of the Linux
    # pane-wrapper.sh path. The wrapper uses $args (no param block) and invokes
    # the child via `& $exe @args`, so the verbatim `pwsh -File … --allow-all`
    # form is preserved through both this prefix and psmux's outer `-Command`
    # wrap (validated). `-AwWt <id>` (added only when known) is consumed by the
    # wrapper, never forwarded to Copilot. If the wrapper is missing, fall back
    # to the verbatim command unchanged.
    $paneCmd = $cmd
    $paneWrapper = Join-Path $PSScriptRoot 'pane-wrapper.ps1'
    $ahpTokenFile = $null
    if ($ahpArgs.Count -gt 0) {
        if (-not (Test-Path -LiteralPath $paneWrapper -PathType Leaf)) {
            $message = 'AHP psmux launch requires the agent-worktrees pane wrapper.'
            Write-SetupLog $message 'ERROR'
            Write-Error $message -ErrorAction Continue
            exit 3
        }
        $ahpTokenFile = Join-Path $env:TEMP (
            "agent-worktrees-ahp-$PID-$([guid]::NewGuid().ToString('N')).token"
        )
        try {
            $secureToken = ConvertTo-SecureString $token.Trim() `
                -AsPlainText -Force
            $encryptedToken = ConvertFrom-SecureString $secureToken
            [IO.File]::WriteAllText($ahpTokenFile, $encryptedToken)
        } catch {
            Remove-Item -LiteralPath $ahpTokenFile `
                -Force -ErrorAction SilentlyContinue
            Write-SetupLog 'Could not create the protected AHP token handoff.' 'ERROR'
            Write-Error 'Could not create the protected AHP token handoff.' `
                -ErrorAction Continue
            exit 3
        }
    }
    if (Test-Path -LiteralPath $paneWrapper) {
        $wrapPrefix = @('pwsh.exe', '-NoProfile', '-NoLogo', '-File', $paneWrapper)
        if (-not [string]::IsNullOrWhiteSpace($plan.worktree_id)) {
            $wrapPrefix += @('-AwWt', [string]$plan.worktree_id)
        }
        if ($ahpTokenFile) {
            $wrapPrefix += @('-AwAhpTokenFile', $ahpTokenFile)
        }
        $paneCmd = $wrapPrefix + $cmd
    } else {
        Write-SetupLog "pane wrapper missing at $paneWrapper; using verbatim command" 'WARN'
    }

    $savedPsmuxSession = $env:PSMUX_SESSION; $env:PSMUX_SESSION = $null
    $savedTmux = $env:TMUX; $env:TMUX = $null
    $savedTmuxPane = $env:TMUX_PANE; $env:TMUX_PANE = $null
    $maxCreateAttempts = 3
    $retryDelayMs = 1000
    $totalCreateAttempts = 0
    $newSessionExit = 1
    $newSessionError = ''
    $retryCycle = $true
    while ($retryCycle) {
        $retryCycle = $false
        for ($attempt = 1; $attempt -le $maxCreateAttempts; $attempt++) {
            $totalCreateAttempts++
            $newSessionExit = 1
            $newSessionError = ''
            try {
                $savedAuth = $null
                if ($ahpArgs.Count -gt 0) {
                    $savedAuth = @{}
                    foreach ($name in @(
                        'GH_TOKEN',
                        'GITHUB_TOKEN',
                        'AGENT_WORKTREES_AHP_AUTH_TOKEN'
                    )) {
                        $savedAuth[$name] = (
                            [Environment]::GetEnvironmentVariable(
                                $name,
                                'Process'
                            )
                        )
                        [Environment]::SetEnvironmentVariable(
                            $name,
                            $null,
                            'Process'
                        )
                    }
                }
                try {
                    & $script:AwPsmuxBin new-session -d -s $sessName `
                        -c $plan.work_dir @envFlags @paneCmd
                    $newSessionExit = if ($null -eq $LASTEXITCODE) {
                        0
                    } else {
                        [int]$LASTEXITCODE
                    }
                } finally {
                    if ($null -ne $savedAuth) {
                        foreach ($name in $savedAuth.Keys) {
                            [Environment]::SetEnvironmentVariable(
                                $name,
                                $savedAuth[$name],
                                'Process'
                            )
                        }
                    }
                }
            } catch {
                $newSessionError = $_.Exception.Message.Split([Environment]::NewLine)[0].Trim()
            }
            if ($newSessionExit -eq 0) { break }

            Stop-AwOwnedPsmuxSession $sessName
            $detail = if ($newSessionError) { ": $newSessionError" } else { '' }
            Write-SetupLog (
                "psmux: create attempt $attempt/$maxCreateAttempts failed " +
                "(exit $newSessionExit)$detail"
            ) 'WARN'
            if ($attempt -lt $maxCreateAttempts) {
                Write-SetupStatus (
                    "PSMux startup attempt $attempt/$maxCreateAttempts failed; " +
                    "retrying in $retryDelayMs ms..."
                ) 'WARN'
                Start-Sleep -Milliseconds $retryDelayMs
            }
        }

        if ($newSessionExit -ne 0 -and (Read-AwMuxRetryChoice $sessName)) {
            Write-SetupStatus 'Retrying PSMux startup...' 'WARN'
            $retryCycle = $true
        }
    }
    $env:PSMUX_SESSION = $savedPsmuxSession
    $env:TMUX = $savedTmux
    $env:TMUX_PANE = $savedTmuxPane
    if ($newSessionExit -ne 0) {
        if ($ahpTokenFile) {
            Remove-Item -LiteralPath $ahpTokenFile `
                -Force -ErrorAction SilentlyContinue
        }
        $detail = if ($newSessionError) { ": $newSessionError" } else { '' }
        $recoveryProject = if ($script:LaunchProject) { $script:LaunchProject } else { 'agent-worktrees' }
        $recoveryCommand = "$recoveryProject --worktree-id $($plan.worktree_id)"
        $preservedPath = if ($plan.PSObject.Properties['status_path']) {
            [string]$plan.status_path
        } else {
            [string]$plan.work_dir
        }
        $message = (
            "Failed to create psmux session '$sessName' after $totalCreateAttempts attempts " +
            "(exit code $newSessionExit)$detail. The worktree remains at '$preservedPath'. " +
            "Run '$recoveryCommand' to retry, or use --no-mux to request a direct session explicitly."
        )
        Write-SetupLog $message 'ERROR'
        Write-AwMuxFailure -Reason 'create_failed' -ExitCode $newSessionExit -Fields @(
            "attempts=$totalCreateAttempts",
            'recoverable=true'
        )
        Write-Error $message -ErrorAction Continue
        exit $newSessionExit
    } else {
        if ($ahpTokenFile) {
            $tokenDeadline = [DateTime]::UtcNow.AddSeconds(5)
            while (
                (Test-Path -LiteralPath $ahpTokenFile) -and
                [DateTime]::UtcNow -lt $tokenDeadline
            ) {
                Start-Sleep -Milliseconds 50
            }
            if (Test-Path -LiteralPath $ahpTokenFile) {
                Remove-Item -LiteralPath $ahpTokenFile `
                    -Force -ErrorAction SilentlyContinue
                Stop-AwOwnedPsmuxSession $sessName
                Write-AwMuxFailure -Reason 'ahp_token_handoff_failed' `
                    -ExitCode 3
                Write-Error (
                    'AHP client did not consume its protected token handoff.'
                ) -ErrorAction Continue
                exit 3
            }
        }
        Write-ActivityLog -Event 'mux_attached' -WorktreeId $plan.worktree_id -Fields @(
            'mux=create',
            "attempts=$totalCreateAttempts"
        )
        # Session created: stamp per-session options + start its status-bar
        # updater (one per session, before any nested-create early-exit so the
        # bar populates either way).
        Set-AwSessionOptionsSafe $sessName
        # Apply the keystroke passthrough to the new session's server
        # (per-session source-file) so PageUp/wheel/arrows reach Copilot.
        Invoke-AwPsmuxPassthroughSafe $sessName
        Start-StatusUpdater $sessName $muxStatusPath
        if ($nested) {
            Write-Host "Session created: $sessName (open a new terminal to join)"
            exit 0
        }
        Reset-SshConptyViewport
        Set-PsmuxLastSession $sessName
        $attachExit = 1
        $attachError = ''
        try {
            Invoke-AwPsmuxAttach $sessName
            $attachExit = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
        } catch [System.Management.Automation.PipelineStoppedException] {
            # Defensive: only reachable if TreatControlCAsInput could not be set
            # (no console); with it set, a Ctrl+C never raises here -- it is
            # forwarded into the pane for Copilot to handle.
            Write-SetupLog "psmux attach interrupted (Ctrl+C)"
            $attachExit = 130
        } catch {
            $attachError = $_.Exception.Message.Split([Environment]::NewLine)[0].Trim()
        }
        if ($attachExit -ne 0) {
            $detail = if ($attachError) { ": $attachError" } else { '' }
            $message = "Failed to attach to new psmux session '$sessName' (exit code $attachExit)$detail."
            Write-SetupLog $message 'ERROR'
            Write-AwMuxFailure -Reason 'attach_failed' -ExitCode $attachExit
            Write-Error $message -ErrorAction Continue
            exit $attachExit
        }

        # We're back — either the user detached or the session ended.
        # Only run post-exit if the session is truly gone.
        Write-SetupLog "psmux attach returned, checking session state"
        Write-ActivityLog -Event 'mux_detached' -WorktreeId $plan.worktree_id
        $null = & $script:AwPsmuxBin has-session -t $sessName 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-SetupLog "psmux session gone, running post-exit checks"
            Write-ActivityLog -Event 'copilot_exited' -WorktreeId $plan.worktree_id -Fields @('mux=psmux')

            # Post-exit finalization
            if ($plan.post_exit -and $plan.worktree_id) {
                Write-SetupLog "Running post-exit finalization"
                $postExitCode = Invoke-AwPostExit $plan.worktree_id
                if ($postExitCode -ne 0) {
                    Write-SetupLog "Post-exit finalization failed (exit=$postExitCode)" 'ERROR'
                    Write-Warning "Post-exit finalization failed (exit code $postExitCode). Run 'agent-worktrees finalize' to retry."
                    Write-Host "Exiting in 10 seconds..." -ForegroundColor Yellow
                    Start-Sleep -Seconds 10
                }
            }
        } else {
            Write-SetupLog "psmux session still alive (detached)"
        }

        exit 0
    }
}

# ── Direct launch (explicit --no-mux only) ──────────────────────────
# Wrap in try/finally so Ctrl+C (PipelineStoppedException) kills the
# child but the launcher survives to check for handoff state.

if (-not $noMux) {
    $message = 'Internal launcher error: reached direct launch without --no-mux.'
    Write-SetupLog $message 'ERROR'
    Write-AwMuxFailure -Reason 'unexpected_fallthrough'
    Write-Error $message -ErrorAction Continue
    exit 1
}

Write-SetupLog "Handing off to setup script: $($cmd -join ' ')"
Write-Host 'Launching Copilot...'
Write-Host ''

$copilotExit = 0
$savedDirectAuth = @{}
foreach ($name in @(
    'GH_TOKEN',
    'GITHUB_TOKEN',
    'AGENT_WORKTREES_AHP_AUTH_TOKEN'
)) {
    $savedDirectAuth[$name] = [Environment]::GetEnvironmentVariable(
        $name,
        'Process'
    )
}
try {
    try {
        if ($ahpArgs.Count -gt 0) {
            [Environment]::SetEnvironmentVariable(
                'GITHUB_TOKEN',
                $null,
                'Process'
            )
            [Environment]::SetEnvironmentVariable(
                'AGENT_WORKTREES_AHP_AUTH_TOKEN',
                $null,
                'Process'
            )
            [Environment]::SetEnvironmentVariable(
                'GH_TOKEN', $token.Trim(), 'Process'
            )
        }
        & $cmd[0] $cmd[1..($cmd.Count - 1)]
        $copilotExit = $LASTEXITCODE
    } catch [System.Management.Automation.PipelineStoppedException] {
        $copilotExit = 130  # 128 + SIGINT(2)
        Write-SetupLog "Session interrupted (Ctrl+C)"
    }
} finally {
    foreach ($name in $savedDirectAuth.Keys) {
        [Environment]::SetEnvironmentVariable(
            $name,
            $savedDirectAuth[$name],
            'Process'
        )
    }
    Write-ActivityLog -Event 'copilot_exited' -WorktreeId $plan.worktree_id -Fields @('mux=none', "exit_code=$copilotExit")
    # ── Post-exit finalization ───────────────────────────────────────────
    if ($plan.post_exit -and $plan.worktree_id) {
        $postExitCode = Invoke-AwPostExit $plan.worktree_id
        if ($postExitCode -ne 0) {
            Write-Warning "Post-exit finalization failed (exit code $postExitCode). Run 'agent-worktrees finalize' to retry."
        }
    }
}

exit $copilotExit
