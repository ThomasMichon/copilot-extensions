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
if (-not $noUpdate) {
    # Discover the active plugin directory (marketplace or _direct layout)
    $pluginDir = $null
    $pluginLayout = $null
    $marketplaceDir = Join-Path $env:USERPROFILE '.copilot\installed-plugins\copilot-extensions\agent-worktrees'
    $directPlugins = Join-Path $env:USERPROFILE '.copilot\installed-plugins\_direct'

    if (Test-Path $marketplaceDir) {
        $pluginDir = $marketplaceDir
        $pluginLayout = 'marketplace'
    } elseif (Test-Path $directPlugins) {
        # Scan _direct layout for agent-worktrees plugin
        foreach ($dir in Get-ChildItem -Directory $directPlugins -ErrorAction SilentlyContinue) {
            $manifest = Join-Path $dir.FullName 'plugin.json'
            if (Test-Path $manifest) {
                try {
                    $pj = Get-Content $manifest -Raw | ConvertFrom-Json
                    if ($pj.name -eq 'agent-worktrees') {
                        $pluginDir = $dir.FullName
                        $pluginLayout = 'direct'
                        break
                    }
                } catch {}
            }
        }
    }

    if ($pluginDir) {
        Write-SetupLog "Plugin auto-update: layout=$pluginLayout dir=$pluginDir"

        # Snapshot key plugin files to detect changes after update
        $hashFiles = @('pyproject.toml', 'plugin.json',
                       'bin\launch-session.ps1', 'bin\launch-session.sh',
                       'scripts\install.ps1', 'scripts\install.sh')
        $oldFingerprint = ''
        foreach ($f in $hashFiles) {
            $fp = Join-Path $pluginDir $f
            if (Test-Path $fp) {
                $oldFingerprint += (Get-FileHash $fp -Algorithm SHA256).Hash
            }
        }

        # Try to update the plugin from the marketplace
        if ($pluginLayout -eq 'marketplace') {
            if (Get-Command copilot -ErrorAction SilentlyContinue) {
                Write-SetupLog 'Running: copilot plugin update agent-worktrees@copilot-extensions'
                $updateOutput = copilot plugin update agent-worktrees@copilot-extensions 2>&1
                Write-SetupLog "Plugin update result: $updateOutput"
            }
        } else {
            Write-SetupLog 'Direct-install layout — skipping marketplace update'
        }

        # Check if any tracked files changed
        $newFingerprint = ''
        foreach ($f in $hashFiles) {
            $fp = Join-Path $pluginDir $f
            if (Test-Path $fp) {
                $newFingerprint += (Get-FileHash $fp -Algorithm SHA256).Hash
            }
        }

        if ($newFingerprint -ne $oldFingerprint -and $newFingerprint -ne '') {
            Write-SetupLog 'Plugin source changed — running full installer update'

            $pluginInstaller = Join-Path $pluginDir 'scripts\install.ps1'
            if (Test-Path $pluginInstaller) {
                $installerArgs = @('update')
                if ($env:WORKTREE_PROJECT) {
                    $installerArgs += @('-ProjectName', $env:WORKTREE_PROJECT)
                }

                & pwsh.exe -NoProfile -File $pluginInstaller @installerArgs 2>&1 |
                    ForEach-Object { Write-SetupLog "installer: $_" }

                if ($LASTEXITCODE -eq 0) {
                    Write-SetupLog 'Installer update succeeded — re-execing into new launch-session'

                    # Re-exec into the newly deployed launch-session
                    $newLauncher = Join-Path $env:USERPROFILE '.agent-worktrees\bin\launch-session.ps1'
                    if (Test-Path $newLauncher) {
                        $env:WORKTREE_NO_UPDATE = '1'
                        $env:APERTURE_NO_UPDATE = '1'
                        & pwsh.exe -NoProfile -File $newLauncher @CopilotArgs
                        exit $LASTEXITCODE
                    } else {
                        Write-SetupLog 'Updated but deployed launcher missing; continuing current process' 'WARN'
                    }
                } else {
                    Write-SetupLog "Installer update failed (exit $LASTEXITCODE) — continuing with existing version" 'WARN'
                }
            } else {
                Write-SetupLog "Plugin installer not found at $pluginInstaller — skipping" 'WARN'
            }
        } else {
            Write-SetupLog 'Plugin source unchanged — no update needed'
        }
    }
}

# ── Pre-launch self-update (two-pass) ────────────────────────────────────
# Checks bootstrap service staleness and runs updates if needed.
# Mirrors the equivalent block in launch-session.sh.

if (-not $noUpdate) {
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

            # Re-check (one retry max)
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
    Write-SetupLog 'Pre-launch update skipped (WORKTREE_NO_UPDATE=1)'
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
    'deploy-instructions', 'get', 'pre-launch', 'dev', 'handoff',
    'register-session', 'deregister-session', 'backfill-sessions',
    'anchor-check'
)
if ($CopilotArgs.Count -gt 0 -and $CopilotArgs[0] -in $DirectCommands) {
    Write-SetupLog "Direct dispatch: $($CopilotArgs[0]) (bypassing resolve)"
    & $VenvPython -m agent_worktrees @CopilotArgs
    exit $LASTEXITCODE
}

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

if ($plan.action -ne 'exec') {
    Write-Error "Unknown action: $($plan.action)"
    exit 1
}

# ── Execute the launch plan ──────────────────────────────────────────────

Set-Location $plan.work_dir

# Apply environment variables from the launch plan
if ($plan.env) {
    foreach ($prop in $plan.env.PSObject.Properties) {
        [System.Environment]::SetEnvironmentVariable($prop.Name, [string]$prop.Value, 'Process')
    }
}

# Publish worktree ID so tools (finalize, mark-complete) can auto-detect
if ($plan.worktree_id) {
    [System.Environment]::SetEnvironmentVariable('WORKTREE_ID', $plan.worktree_id, 'Process')
    [System.Environment]::SetEnvironmentVariable('APERTURE_WORKTREE_ID', $plan.worktree_id, 'Process')  # backward compat
}

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
        & psmux attach-session -t $sessName
        if ($LASTEXITCODE -eq 0) {
            exit 0
        }
        # Join failed — kill the stale session so we can recreate it
        Write-Warning "Failed to join psmux session — killing stale session."
        & psmux kill-session -t $sessName 2>&1 | Out-Null
    }

    # Build -e flags for env propagation into the psmux server.
    # Merge plan.env with launcher-owned vars; launcher values win.
    $mergedEnv = [ordered]@{}
    if ($plan.env) {
        foreach ($prop in $plan.env.PSObject.Properties) {
            $mergedEnv[$prop.Name] = [string]$prop.Value
        }
    }
    if ($plan.worktree_id) {
        $mergedEnv['WORKTREE_ID'] = [string]$plan.worktree_id
        $mergedEnv['APERTURE_WORKTREE_ID'] = [string]$plan.worktree_id
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
        if ($nested) {
            Write-Host "Session created: $sessName (open a new terminal to join)"
            exit 0
        }
        Reset-SshConptyViewport
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
