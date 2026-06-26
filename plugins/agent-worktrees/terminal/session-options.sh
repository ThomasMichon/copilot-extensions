#!/usr/bin/env bash
# session-options.sh -- per-session tmux options for agent-worktrees panes.
#
# agent-worktrees does NOT own your global ~/.tmux.conf. Instead the launcher
# stamps these options onto each session it creates, scoped to that one session
# (`tmux set -t <session>`, no -g), so your personal tmux config and any ad-hoc
# tmux sessions sharing the same server are left untouched.
#
# Settings that CANNOT be session-scoped -- the server-global `escape-time` and
# the keystroke-passthrough root key table -- are NOT applied here. They live in
# the optional, opt-in apply-mux-keybinds.sh; run it once per machine (or wire
# it into a machine-restore flow) if you want them.
#
# This file is sourced, not executed. It defines one function.

# aw_apply_tmux_session_options <session-name>
#
# Apply the worktree status bar + session behaviors to a single tmux session.
# Idempotent and side-effect-free on global state -- only ever touches the named
# session (and its active window). Safe to call after every new-session.
#
# Note (tmux 3.4): the `=name` exact-match target form does NOT resolve for
# `set`/`show` (it works for has-session/switch-client). Session ids here are
# unique timestamped slugs, so the plain prefix-matching name is unambiguous.
aw_apply_tmux_session_options() {
    local sess="$1"
    [ -n "$sess" ] || return 0
    command -v tmux >/dev/null 2>&1 || return 0

    # -- Status bar -------------------------------------------------------
    # Left: worktree identity (machine | env | repo:id4).
    # Right: worktree git disposition block + clock.
    # Both #() jobs run per-pane in the pane's cwd, so they self-contextualize
    # to whatever worktree the pane is actually in -- a single session-scoped
    # value renders correctly per pane.
    tmux set -t "$sess" status-interval 15
    tmux set -t "$sess" status-left-length 100
    tmux set -t "$sess" status-left '#(agent-worktrees status-context) '
    tmux set -t "$sess" status-right-length 150
    tmux set -t "$sess" status-right '#(agent-worktrees status-segment) %H:%M '

    # Drop the center window list (e.g. "0:bash*"): single-window worktree
    # sessions don't benefit from it and it crowds the identity/status segments.
    # window-status-format is a window option -> -w, applied to the active window.
    tmux set -t "$sess" -w window-status-format ' '
    tmux set -t "$sess" -w window-status-current-format ' '

    # -- Behaviors --------------------------------------------------------
    # mouse on: relay wheel events to the pane; Shift+click for native select.
    tmux set -t "$sess" mouse on
    # focus-events on: pass terminal focus in/out to the inner application.
    tmux set -t "$sess" focus-events on
    # remain-on-exit failed: keep a crashed pane (non-zero exit) visible so the
    # error can be read; normal exits close as usual. Window option -> -w.
    tmux set -t "$sess" -w remain-on-exit failed
}
