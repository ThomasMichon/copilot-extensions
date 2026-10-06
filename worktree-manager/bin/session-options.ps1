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

# Set-AwPsmuxServerPriority <session-name>
#
# Interim mitigation for psmux#608 (https://github.com/psmux/psmux/issues/608):
# a multiplexer server starved at Normal priority on a loaded box drops/lags
# keystrokes. Upstream now defaults to `set -g priority above-normal` for its
# own server + client processes, but that fix postdates our installed (WinGet)
# psmux build -- `psmux show-options -g` has no `priority` option yet. Until a
# release ships with it, bump the server ourselves.
#
# Finds THIS session's psmux server by command line (`server -s <session>`),
# the same ownership-proving pattern Stop-AwOwnedPsmuxSession uses for cleanup
# -- never by parsing $env:TMUX, whose field order is easy to get backwards
# (psmux's own docs/integration.md: the first field embeds the server PID, the
# second is a TCP port, not a pid -- the reverse of vanilla tmux). Matching on
# the actual `server -s <session>` arguments is unambiguous and immune to that
# confusion. Best-effort and session-scoped: a failure (or lack of rights,
# e.g. hitting another user's server on a shared box) never blocks the launch
# and never touches any process outside this one session's own server.
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
