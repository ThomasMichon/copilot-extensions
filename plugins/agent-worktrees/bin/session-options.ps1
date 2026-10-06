# session-options.ps1 -- per-session psmux options for agent-worktrees panes.
#
# agent-worktrees does NOT own your global ~/.psmux.conf. Instead the launcher
# dot-sources this file and stamps these options onto each session it
# creates/joins, scoped to that one session (set-option -t <session>, no -g), so
# your personal psmux config and any ad-hoc psmux sessions sharing the same
# server are left untouched. This mirrors the Linux/WSL session-options.sh
# (tmux) integration.
#
# Settings that CANNOT be session-scoped -- the server-global keystroke
# passthrough root key table and the prefix key -- are NOT applied here. They
# live in the optional, opt-in apply-mux-keybinds.ps1; run it once per machine
# (or wire it into a machine-restore flow) if you want them.
#
# This file is dot-sourced, not executed. It defines one function.

# Set-AwPsmuxSessionOptions <session-name>
#
# Apply the worktree status bar + session behaviors to a single psmux session.
# Idempotent and side-effect-free on global state -- only ever touches the named
# session. Safe to call after every new-session / on every join. Best-effort:
# psmux failures are swallowed so a status-bar tweak never blocks the launch.
function Set-AwPsmuxSessionOptions {
    param([string]$Session)
    if ([string]::IsNullOrWhiteSpace($Session)) { return }
    if (-not (Get-Command psmux -ErrorAction SilentlyContinue)) { return }
    # Use the launcher-resolved psmux path (real WinGet Packages exe when the
    # command on PATH is a 0-byte reparse stub pwsh 7.4.x can't launch); fall
    # back to bare 'psmux' when called outside the launcher's scope.
    $muxBin = if ($script:AwPsmuxBin) { $script:AwPsmuxBin } else { 'psmux' }

    # Each (option, value) pair is stamped session-scoped via `set-option -t`.
    #
    # -- Status bar -------------------------------------------------------
    # Left: worktree identity (machine | env | repo:id4), static per session.
    # Right: worktree git disposition block + clock.
    #
    # CRITICAL: the status bar must NOT invoke the (heavy, Python) agent-worktrees
    # CLI on its render path. psmux repaints synchronously (no tmux-style #()
    # caching), so a Python cold-start per frame makes the terminal crawl.
    # Instead the bar reads precomputed session options that the common
    # `status-updater` watcher refreshes OFF the render path (#{@aw_ctx} once,
    # #{@aw_seg} each tick, via set-option). Between updates the bar does zero
    # process work -- only the %H:%M clock. Unset (non-worktree) sessions render
    # a blank bar. The writer is spawned by the launcher.
    #
    # -- Behaviors --------------------------------------------------------
    # mouse on: relay wheel events to the pane; Shift+click for native select.
    # scroll-enter-copy-mode off: wheel scrolls the inner app, not copy-mode.
    # pwsh-mouse-selection off: let the terminal emulator handle text selection.
    $opts = @(
        @('status-interval',              '15'),
        @('status-left-length',           '100'),
        @('status-left',                  '#{@aw_ctx} '),
        @('status-right-length',          '150'),
        @('status-right',                 '#{@aw_seg} %H:%M '),
        @('window-status-format',         ' '),
        @('window-status-current-format', ' '),
        @('mouse',                        'on'),
        @('scroll-enter-copy-mode',       'off'),
        @('pwsh-mouse-selection',         'off')
    )
    foreach ($opt in $opts) {
        try { & $muxBin set-option -t $Session $opt[0] $opt[1] 2>&1 | Out-Null } catch {}
    }
}

# Invoke-AwPsmuxPassthrough <session-name>
#
# Apply the psmux keystroke passthrough (root-table reset + wheel relay + prefix)
# to ONE session's psmux server via `source-file`. psmux runs a separate server
# per wt-<id> (key tables are per-server) and command-line `bind-key`/`unbind-key`
# silently no-op there -- `source-file` is the only primitive that reliably
# applies key-table directives, and `-t <session>` scopes it to that session's
# server (isolated from siblings + any personal psmux session). This restores the
# per-session-at-launch model that mux-config-decoupling's one-time global script
# lost (regression 25c41b7). Best-effort: a failure never blocks the launch. The
# fragment is deployed alongside this script as psmux-passthrough.conf.
function Invoke-AwPsmuxPassthrough {
    param([string]$Session)
    if ([string]::IsNullOrWhiteSpace($Session)) { return }
    if (-not (Get-Command psmux -ErrorAction SilentlyContinue)) { return }
    $muxBin = if ($script:AwPsmuxBin) { $script:AwPsmuxBin } else { 'psmux' }
    $fragment = Join-Path $PSScriptRoot 'psmux-passthrough.conf'
    if (-not (Test-Path $fragment)) { return }
    try { & $muxBin source-file -t $Session $fragment 2>&1 | Out-Null } catch {}
}

function Set-AwPsmuxServerPriority {
    param([string]$Session)
    if ([string]::IsNullOrWhiteSpace($Session)) { return }
    try {
        $escapedSession = [regex]::Escape($Session)
        $servers = @(
            Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
                $_.Name -eq 'psmux.exe' -and
                [string]$_.CommandLine -match "(?:^|\s)server\s+-s\s+$escapedSession(?:\s|$)"
            }
        )
        foreach ($server in $servers) {
            try {
                $proc = [Diagnostics.Process]::GetProcessById([int]$server.ProcessId)
                if ($proc.PriorityClass -ne 'AboveNormal') {
                    $proc.PriorityClass = 'AboveNormal'
                }
            } catch {}
        }
    } catch {}
}

# Get-AwMuxCompanionKeybindFragment -ManagerRoot <path>
#
# Build (pure, no side effects, no mux dependency) the root-key-table
# directive that delivers the Mux Companion (visions/mux-companion
# §mux-bind-keybind-relay): Ctrl+K opens the popup.
#
# `uv run --quiet --project <ManagerRoot> -m worktree_manager companion`
# mirrors launch-session.ps1's own Invoke-ManagedMuxRegister invocation
# pattern -- resolves the Companion from its own project root directly, no
# separately-published `worktree-manager` binstub needed. `display-popup -E`
# closes the popup the instant the Companion process exits (it is a normal
# TUI app that exits on 'q'/Ctrl+C), so no separate dismiss wiring is needed.
#
# NOTE: a clickable status-right region (§mux-bind-clickable-status-region)
# was prototyped alongside this but is NOT included here -- empirically
# confirmed (mux-bind-relay effort Journal) that sourcing a
# `MouseDown1Status` bind-key directive on this psmux build (3.3.5) silently
# CORRUPTS the session's entire root key table, wiping out this very Ctrl+K
# binding too (reproduced 3x; a plain re-sourced C-k-only fragment is stable
# across repeated application). Deferred until that is root-caused on a psmux
# build that doesn't exhibit it -- shipping it anyway would silently break
# the one keybind already proven to work.
#
# Separated from the side-effecting Invoke-AwMuxCompanionBind below
# specifically so its exact text is unit-testable without a live mux server
# (mux-bind-relay effort, Step 1).
function Get-AwMuxCompanionKeybindFragment {
    param([Parameter(Mandatory)][string]$ManagerRoot)
    $popupCmd = "uv run --quiet --project \`"$ManagerRoot\`" -m worktree_manager companion"
    "bind-key -T root C-k display-popup -E -w 80% -h 80% `"$popupCmd`""
}

# Invoke-AwMuxCompanionBind <session-name> <manager-root>
#
# Apply Get-AwMuxCompanionKeybindFragment's directives to ONE session's psmux
# server via `source-file` (the same no-op-on-command-line constraint
# Invoke-AwPsmuxPassthrough documents applies here too). Call this AFTER
# Invoke-AwPsmuxPassthrough at every call site -- passthrough's own
# `unbind-key -a -T root` would otherwise wipe this binding if applied first.
# Best-effort: a failure never blocks the launch.
function Invoke-AwMuxCompanionBind {
    param([string]$Session, [string]$ManagerRoot)
    if ([string]::IsNullOrWhiteSpace($Session)) { return }
    if ([string]::IsNullOrWhiteSpace($ManagerRoot)) { return }
    if (-not (Get-Command psmux -ErrorAction SilentlyContinue)) { return }
    $muxBin = if ($script:AwPsmuxBin) { $script:AwPsmuxBin } else { 'psmux' }
    $tmpFile = [System.IO.Path]::GetTempFileName()
    try {
        Set-Content -LiteralPath $tmpFile -Value (Get-AwMuxCompanionKeybindFragment -ManagerRoot $ManagerRoot) -Encoding ascii
        & $muxBin source-file -t $Session $tmpFile 2>&1 | Out-Null
    } catch {}
    finally { Remove-Item -LiteralPath $tmpFile -ErrorAction SilentlyContinue }
}
