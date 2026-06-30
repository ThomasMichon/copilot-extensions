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
        try { & psmux set-option -t $Session $opt[0] $opt[1] 2>&1 | Out-Null } catch {}
    }
}
