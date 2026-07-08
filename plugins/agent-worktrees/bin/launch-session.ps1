<#
.SYNOPSIS
    Worktree session launcher — resolves via Python, executes in the shell.

.DESCRIPTION
    Calls agent_worktrees resolve to get a JSON launch plan, then executes
    the plan natively. Python exits before Copilot starts, freeing the venv.

    After Copilot exits, calls agent_worktrees post-exit for finalization.

    Uses $WORKTREE_PROJECT to determine the active project.
    Runtime lives at ~/.agent-worktrees/; project config at ~/.{project}/.
#>
# Accept all arguments via $args (not param block) to avoid PowerShell's
# parameter binding rejecting unknown flags like --acp, --stdio, --no-mux
# when called via 'pwsh -File'.
$CopilotArgs = $args

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
$env:APERTURE_SETUP_LOG = $script:SetupLog  # backward compat

function Write-SetupLog {
    param([string]$Message, [string]$Level = 'INFO')
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'
    $line = "[$ts] [$Level] $Message"
    try { Add-Content -Path $script:SetupLog -Value $line -ErrorAction SilentlyContinue } catch {}
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

Write-SetupLog 'launch-session.ps1 starting'

# --recovery: bypass worktree resolution entirely, go straight to setup script
# --no-update: skip pre-launch self-update (propagated via WORKTREE_NO_UPDATE)
# --: everything after this separator is copilot passthrough args (e.g. --acp --stdio)
$FilteredArgs = @()
$CopilotPassthrough = @()
$RecoveryMode = $false
$SeenSeparator = $false
foreach ($arg in $CopilotArgs) {
    if ($SeenSeparator) {
        $CopilotPassthrough += $arg
    } elseif ($arg -eq '--') {
        $SeenSeparator = $true
    } elseif ($arg -eq '--recovery' -or $arg -eq '-Recovery' -or $arg -eq 'recovery') {
        $RecoveryMode = $true
        $env:WORKTREE_RECOVERY = '1'
        $env:APERTURE_RECOVERY = '1'  # backward compat
        Write-SetupLog 'Recovery mode requested via CLI arg'
    } elseif ($arg -eq '--no-update') {
        $env:WORKTREE_NO_UPDATE = '1'
        $env:APERTURE_NO_UPDATE = '1'  # backward compat
        Write-SetupLog '--no-update: pre-launch update disabled'
    } else {
        $FilteredArgs += $arg
    }
}
$CopilotArgs = $FilteredArgs
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
    # Find the anchor repo: try git rev-parse from cwd, then project config
    $anchor = $null
    $gitRoot = git rev-parse --show-toplevel 2>$null
    if ($LASTEXITCODE -eq 0 -and $gitRoot) {
        $anchor = $gitRoot -replace '/', '\'
    }
    if (-not $anchor -or -not (Test-Path $anchor)) {
        $project = if ($env:WORKTREE_PROJECT) { $env:WORKTREE_PROJECT } else { $null }
        if ($project) {
            $cfgPath = Join-Path $env:USERPROFILE ".$project\config.yaml"
            if (Test-Path $cfgPath) {
                $anchorLine = Select-String -Path $cfgPath -Pattern '^\s+anchor:\s+(.+)$' | Select-Object -First 1
                if ($anchorLine) {
                    $anchor = $anchorLine.Matches[0].Groups[1].Value.Trim()
                }
            }
        }
    }
    if (-not $anchor -or -not (Test-Path $anchor)) {
        $anchor = $PWD.Path
    }
    $setupScript = Join-Path $anchor 'tools\setup\setup.ps1'
    Write-SetupLog "Recovery: launching $setupScript in $anchor"
    Set-Location $anchor
    & pwsh.exe -NoProfile -NoLogo -File $setupScript -Recovery @CopilotArgs
    exit $LASTEXITCODE
}

# Runtime resolution
$RuntimeDir = Join-Path $env:USERPROFILE '.agent-worktrees'

if (Test-Path (Join-Path $RuntimeDir '.venv\Scripts\python.exe')) {
    Write-SetupLog "Venv resolved: $RuntimeDir"
} else {
    Write-SetupLog 'Venv not found - aborting' 'ERROR'
    Write-Error "Venv not found. Run the installer: pwsh -File plugins\agent-worktrees\scripts\install.ps1 install"
    exit 1
}

$VenvPython = Join-Path $RuntimeDir '.venv\Scripts\python.exe'
$env:PYTHONPATH = Join-Path $RuntimeDir 'lib'
$env:PYTHONHOME = $null

# ── Plugin auto-update ────────────────────────────────────────────────────
# If installed from the copilot-extensions marketplace plugin, check for
# updates.  When the plugin source changes: run the full installer (which
# deploys package, launch scripts, binstubs, terminal configs), then
# re-exec into the newly deployed launch-session so the rest of the boot
# uses updated code.
#
# Guard: WORKTREE_NO_UPDATE=1 skips this block entirely (set by --no-update
# and by the re-exec below to prevent infinite loops).

$noUpdate = ($env:WORKTREE_NO_UPDATE -eq '1') -or ($env:APERTURE_NO_UPDATE -eq '1')
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

function Invoke-UpdateApply {
    # Join the background stage and apply any pending update. Idempotent: runs
    # its body at most once per launch.
    param($StageJob, [switch]$WithReconcile)
    if ($script:UpdateApplied) { return }
    $script:UpdateApplied = $true

    if (-not $noUpdate) {
        # Join the background stage (bounded wait).
        if ($StageJob) {
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
            Write-SetupLog 'No usable staged update result; staging inline'
            & $VenvPython -m agent_worktrees stage-update *> $null
            if (Test-Path $statusFile) {
                try { $status = Get-Content $statusFile -Raw | ConvertFrom-Json } catch {}
            }
        }

        # (1) Marketplace installer, iff the download changed the payload.
        #     NO re-exec: a launcher-script change applies on the next launch.
        if ($status -and $status.plugin_changed) {
            Write-SetupLog 'Staged update changed the plugin payload — running installer'
            $pdir = $status.plugin_dir
            $pluginInstaller = if ($pdir) { Join-Path $pdir 'scripts\install.ps1' } else { $null }
            if ($pluginInstaller -and (Test-Path $pluginInstaller)) {
                $installerArgs = @('update')
                if ($env:WORKTREE_PROJECT) { $installerArgs += @('-ProjectName', $env:WORKTREE_PROJECT) }
                & pwsh.exe -NoProfile -File $pluginInstaller @installerArgs 2>&1 |
                    ForEach-Object { Write-SetupLog "installer: $_" }
                if ($LASTEXITCODE -eq 0) {
                    Write-SetupLog 'Installer update succeeded (launcher change, if any, applies next launch)'
                } else {
                    Write-SetupLog "Installer update failed (exit $LASTEXITCODE) — continuing with existing version" 'WARN'
                }
            } else {
                Write-SetupLog "Plugin installer not found ($pluginInstaller) — skipping" 'WARN'
            }
        }

        # (2) Pre-launch self-update (bootstrap-service staleness; two-pass).
        Write-SetupLog 'Running pre-launch staleness check'
        $preJson = & $VenvPython -m agent_worktrees pre-launch 2>$null
        if ($LASTEXITCODE -eq 0 -and $preJson) {
            $prePlan = ($preJson -join "`n") | ConvertFrom-Json -ErrorAction SilentlyContinue
            if (-not $prePlan) {
                Write-SetupLog 'pre-launch returned invalid JSON — proceeding' 'WARN'
            } elseif ($prePlan.action -eq 'self-update') {
                Write-SetupLog 'Self-update required — running update commands'
                foreach ($update in $prePlan.updates) {
                    Write-SetupLog "Updating $($update.service): $($update.command)"
                    $argv = @($update.argv)
                    if ($argv.Count -gt 0) {
                        $exe = $argv[0]
                        $rest = if ($argv.Count -gt 1) { $argv[1..($argv.Count - 1)] } else { @() }
                        & $exe @rest
                        if ($LASTEXITCODE -ne 0) {
                            Write-SetupLog "Update failed for $($update.service) (exit $LASTEXITCODE)" 'WARN'
                        } else {
                            Write-SetupLog "Updated $($update.service) successfully"
                        }
                    }
                }
                Write-SetupLog 'Re-checking staleness after update'
                $preJson = & $VenvPython -m agent_worktrees pre-launch 2>$null
                if ($LASTEXITCODE -eq 0 -and $preJson) {
                    $prePlan = ($preJson -join "`n") | ConvertFrom-Json -ErrorAction SilentlyContinue
                    if ($prePlan -and $prePlan.action -eq 'self-update') {
                        Write-SetupLog 'Still stale after update — proceeding anyway' 'WARN'
                    }
                }
            }
        } else {
            Write-SetupLog 'pre-launch check failed or produced no output — proceeding'
        }
    } else {
        Write-SetupLog 'Update apply skipped (WORKTREE_NO_UPDATE=1)'
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
            if ($recPlan.action -ne 'reconcile') {
                if ($rpass -eq 1) { Write-SetupLog 'Plugin reconcile: nothing to do' }
                break
            }
            $recUpdates = @($recPlan.updates)
            Write-SetupLog "Plugin reconcile pass ${rpass}: $($recUpdates.Count) action(s)"
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
                & $exe @rest 2>&1 | ForEach-Object { Write-SetupLog "reconcile: $_" }
            }
        }
    }
}

# ── Direct-dispatch commands (bypass resolve/picker) ─────────────────────
# Subcommands that agent_worktrees's main() handles directly — these
# must NOT fall through to the resolve→picker flow.  Keep in sync with
# COMMAND_MAP in __main__.py, plus "services" and "agent-worktrees".
$DirectCommands = @(
    'services', 'repos', 'agent-worktrees',
    'resolve', 'post-exit', 'finalize', 'push-changes', 'mark-complete',
    'status', 'list', 'create', 'cleanup', 'validate', 'install',
    'register', 'unregister', 'uninstall', 'update', 'install-status',
    'deploy-instructions', 'get', 'pre-launch', 'stage-update', 'reconcile-plugins', 'dev', 'handoff',
    'register-session', 'deregister-session', 'backfill-sessions',
    'anchor-check'
)
if ($CopilotArgs.Count -gt 0 -and $CopilotArgs[0] -in $DirectCommands) {
    Write-SetupLog "Direct dispatch: $($CopilotArgs[0]) (bypassing resolve)"
    # No Picker window to hide behind: stage + apply synchronously (no
    # reconcile, matching the historical direct-command behavior) before
    # dispatching.
    Invoke-UpdateApply -StageJob (Start-UpdateStage)
    & $VenvPython -m agent_worktrees @CopilotArgs
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

$resolveArgs = @('-m', 'agent_worktrees', 'resolve') + $CopilotArgs
Write-SetupLog "Calling agent_worktrees resolve"

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
    Write-SetupLog 'Picker refresh -- applying staged update and relaunching'
    Invoke-UpdateApply -StageJob $script:StageJob -WithReconcile
    $newLauncher = Join-Path $env:USERPROFILE '.agent-worktrees\bin\launch-session.ps1'
    if (Test-Path $newLauncher) {
        & pwsh.exe -NoProfile -File $newLauncher @CopilotArgs
        exit $LASTEXITCODE
    }
    Write-SetupLog 'Relaunch launcher missing after refresh; exiting' 'WARN'
    exit 1
}

if ($plan.action -ne 'exec') {
    Write-Error "Unknown action: $($plan.action)"
    exit 1
}

# ── Join the background update + apply, before the psmux handoff (#1430) ──
# The Picker has closed, so it is now safe to swap the runtime venv. This waits
# for the staged marketplace download, runs the installer if it changed the
# payload (no re-exec -- a launcher change applies next launch), then the
# pre-launch self-update and plugin reconcile, so Copilot starts on the
# finished update.
Invoke-UpdateApply -StageJob $script:StageJob -WithReconcile

# ── Execute the launch plan ──────────────────────────────────────────────

Set-Location $plan.work_dir

# Apply environment variables from the launch plan
if ($plan.env) {
    foreach ($prop in $plan.env.PSObject.Properties) {
        [System.Environment]::SetEnvironmentVariable($prop.Name, [string]$prop.Value, 'Process')
    }
}

# Identity vars are NOT published into the child Copilot session -- in-session
# tools resolve context from CWD (git-like). Clear any inherited copies so the
# session env carries no ambient project/worktree identity. The launcher uses
# $plan.worktree_id (never $env) for its own psmux + post-exit logic, and its
# $env:WORKTREE_PROJECT uses (recovery / self-update) are all earlier.
Remove-Item Env:WORKTREE_ID -ErrorAction SilentlyContinue
Remove-Item Env:APERTURE_WORKTREE_ID -ErrorAction SilentlyContinue
Remove-Item Env:WORKTREE_PROJECT -ErrorAction SilentlyContinue

$cmd = @($plan.cmd)

# Append copilot passthrough args (from after -- separator)
if ($CopilotPassthrough.Count -gt 0) {
    $cmd += $CopilotPassthrough
}

# ── psmux session-per-worktree ───────────────────────────────────────
# Each worktree gets a single shared psmux session. Multiple terminal
# connections all land in the same session. The psmux session ends when
# the launched process exits (command passed directly to new-session).
#
# Mirrors the Linux tmux integration in launch-session.sh.
# --no-mux / WORKTREE_NO_MUX=1 bypasses psmux for debugging.

$noMux = ($env:WORKTREE_NO_MUX -eq '1') -or ($env:APERTURE_NO_MUX -eq '1') -or [bool]$plan.no_mux
if ($noMux) {
    Write-SetupLog 'Mux disabled; launching directly'
}

$psmuxCmd = Get-Command psmux -ErrorAction SilentlyContinue
# Detect nested invocation: if we're already inside a psmux/tmux session,
# we must NOT call attach-session — doing so steals the parent's terminal.
$nested = [bool]$env:TMUX

# Windows 10 ConPTY leaks conhost.exe title text into the SSH stream at
# session start, creating a scroll offset that pushes psmux's status bar
# below the visible area.  Clear the viewport before attach to reset.
function Reset-SshConptyViewport {
    if ($env:SSH_CONNECTION) { [Console]::Write("`e[2J`e[H") }
}

# Smart App Control (or another Application Control / WDAC policy) can block
# an unsigned psmux.exe from executing even though Get-Command resolves it on
# PATH. A blocked launch raises a *terminating* error
# (ResourceUnavailable: "Program 'psmux.exe' failed to run...") rather than a
# non-zero exit code, so the normal $LASTEXITCODE fallbacks never fire. Probe
# once with a harmless has-session call; if psmux cannot actually run, drop to
# a direct (un-multiplexed) launch instead of crashing the session.
if (-not $noMux -and $psmuxCmd) {
    try {
        $null = & psmux has-session -t '__aw_probe__' 2>&1
    } catch {
        Write-Warning "psmux is installed but cannot run ($($_.Exception.Message.Split([Environment]::NewLine)[0].Trim())). Launching directly without a multiplexer."
        Write-SetupLog "psmux blocked/unavailable, falling back to direct launch: $($_.Exception.Message)" 'WARN'
        $psmuxCmd = $null
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
        Start-Process -FilePath $VenvPython -ArgumentList $updArgs `
            -WindowStyle Hidden -ErrorAction Stop | Out-Null
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
if (-not $noMux -and $psmuxCmd) {
    $wtId = if ([string]::IsNullOrWhiteSpace($plan.worktree_id)) { 'base' } else { $plan.worktree_id }
    $sessName = "wt-$wtId"
    Write-SetupLog "psmux: looking for session $sessName"

    # If a psmux session already exists for this worktree, join it.
    # Note: psmux does not support tmux's "=" exact-match prefix on -t.
    $null = & psmux has-session -t $sessName 2>&1
    if ($LASTEXITCODE -eq 0) {
        if ($nested) {
            Write-Host "Session already exists: $sessName (open a new terminal to join)"
            exit 0
        }
        Write-Host "Joining existing session: $sessName"
        Reset-SshConptyViewport
        # Re-stamp per-session options on (re)connect so a long-lived session
        # picks up the current bar without us owning the global config.
        Set-AwSessionOptionsSafe $sessName
        # (Re)assert the updater on join: if the prior one died, this revives
        # the bar; if it's alive, the token guard makes the new one retire.
        Start-StatusUpdater $sessName $plan.work_dir
        # Write last_session AFTER spawning the updater, immediately before
        # attach -- mirroring the create branch below. The updater connects to
        # psmux as a background client, which can rewrite ~/.psmux/last_session;
        # since the 3.3.6 attach regression reads that file instead of honoring
        # -t, setting it any earlier lets the updater clobber our target and the
        # join lands in whatever session was last current (collapsing two
        # worktrees onto one session). Set-PsmuxLastSession must be the final
        # psmux-affecting action before attach.
        Set-PsmuxLastSession $sessName
        & psmux attach-session -t $sessName
        if ($LASTEXITCODE -eq 0) {
            exit 0
        }
        # Join failed — kill the stale session so we can recreate it
        Write-Warning "Failed to join psmux session — killing stale session."
        & psmux kill-session -t $sessName 2>&1 | Out-Null
    }

    # Build -e flags for env propagation into the psmux server.
    # Merge plan.env with launcher-owned vars; launcher values win. Identity
    # vars (WORKTREE_PROJECT/WORKTREE_ID) are deliberately NOT injected -- the
    # child resolves context from CWD.
    $mergedEnv = [ordered]@{}
    if ($plan.env) {
        foreach ($prop in $plan.env.PSObject.Properties) {
            $mergedEnv[$prop.Name] = [string]$prop.Value
        }
    }
    $mergedEnv['WORKTREE_SETUP_LOG'] = [string]$script:SetupLog
    $mergedEnv['APERTURE_SETUP_LOG'] = [string]$script:SetupLog

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
    # lingering shell, matching the Linux tmux behavior.
    #
    # Clear nesting vars so psmux doesn't kill the detached session.
    # The new session is independent — it shouldn't inherit the parent's
    # nesting state even though we're creating it from inside a psmux pane.
    $savedPsmuxSession = $env:PSMUX_SESSION; $env:PSMUX_SESSION = $null
    $savedTmux = $env:TMUX; $env:TMUX = $null
    $savedTmuxPane = $env:TMUX_PANE; $env:TMUX_PANE = $null
    try {
        & psmux new-session -d -s $sessName -c $plan.work_dir @envFlags @cmd
    } finally {
        $env:PSMUX_SESSION = $savedPsmuxSession
        $env:TMUX = $savedTmux
        $env:TMUX_PANE = $savedTmuxPane
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Failed to create psmux session. Falling back to direct launch."
    } else {
        # Session created: stamp per-session options + start its status-bar
        # updater (one per session, before any nested-create early-exit so the
        # bar populates either way).
        Set-AwSessionOptionsSafe $sessName
        Start-StatusUpdater $sessName $plan.work_dir
        if ($nested) {
            Write-Host "Session created: $sessName (open a new terminal to join)"
            exit 0
        }
        Reset-SshConptyViewport
        Set-PsmuxLastSession $sessName
        try {
            & psmux attach-session -t $sessName
        } catch [System.Management.Automation.PipelineStoppedException] {
            Write-SetupLog "psmux attach interrupted (Ctrl+C)"
        }

        # We're back — either the user detached or the session ended.
        # Only run post-exit if the session is truly gone.
        Write-SetupLog "psmux attach returned, checking session state"
        $null = & psmux has-session -t $sessName 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-SetupLog "psmux session gone, running post-exit checks"

            # Post-exit finalization
            if ($plan.post_exit -and $plan.worktree_id) {
                Write-SetupLog "Running post-exit finalization"
                & $VenvPython -m agent_worktrees post-exit $plan.worktree_id
                if ($LASTEXITCODE -ne 0) {
                    Write-SetupLog "Post-exit finalization failed (exit=$LASTEXITCODE)" 'ERROR'
                    Write-Warning "Post-exit finalization failed (exit code $LASTEXITCODE). Run 'agent-worktrees finalize' to retry."
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

# ── Direct launch (no psmux, or psmux failed) ───────────────────────
# Wrap in try/finally so Ctrl+C (PipelineStoppedException) kills the
# child but the launcher survives to check for handoff state.

Write-SetupLog "Handing off to setup script: $($cmd -join ' ')"
Write-Host 'Launching Copilot...'
Write-Host ''

$copilotExit = 0
try {
    try {
        & $cmd[0] $cmd[1..($cmd.Count - 1)]
        $copilotExit = $LASTEXITCODE
    } catch [System.Management.Automation.PipelineStoppedException] {
        $copilotExit = 130  # 128 + SIGINT(2)
        Write-SetupLog "Session interrupted (Ctrl+C)"
    }
} finally {
    # ── Post-exit finalization ───────────────────────────────────────────
    if ($plan.post_exit -and $plan.worktree_id) {
        & $VenvPython -m agent_worktrees post-exit $plan.worktree_id
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Post-exit finalization failed (exit code $LASTEXITCODE). Run 'agent-worktrees finalize' to retry."
        }
    }
}

exit $copilotExit
