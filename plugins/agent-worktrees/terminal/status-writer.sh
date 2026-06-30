#!/usr/bin/env bash
# status-writer.sh -- background producer for the worktree status bar.
#
# The mux status bar must NOT shell out to the (heavy, Python) agent-worktrees
# CLI on its render path: real tmux runs `#()` jobs async + cached, but psmux
# repaints synchronously, so a Python cold-start per frame turns the terminal to
# molasses (worse on Windows). Instead the status bar reads a tiny cache file
# with a bare `#(cat ...)`, and THIS script keeps that file fresh off the render
# path -- one cheap detached loop per session, not one Python spawn per frame.
#
# Usage:
#   status-writer.sh <session> <worktree-id> <worktree-path> [interval-seconds]
#
# Writes two files under $AW_STATUS_DIR/<worktree-id>/ :
#   context  -- the left segment (machine | env | repo:id4); static, written once
#   segment  -- the right segment (git disposition + title); refreshed each tick
#
# The loop self-terminates within one interval of the tmux session ending, and a
# flock guard ensures only one writer runs per worktree even across re-attaches.

set -u

sess="${1:-}"
wid="${2:-}"
wpath="${3:-}"
interval="${4:-${AW_STATUS_INTERVAL:-15}}"

[ -n "$sess" ] && [ -n "$wid" ] || exit 0
command -v tmux >/dev/null 2>&1 || exit 0

# Resolve the agent-worktrees binary once (PATH may be thin in a detached job).
AW="${AW_BIN:-}"
if [ -z "$AW" ] || [ ! -x "$AW" ]; then
    AW="$(command -v agent-worktrees 2>/dev/null || true)"
fi
[ -n "$AW" ] && [ -x "$AW" ] || { AW="$HOME/.local/bin/agent-worktrees"; }
[ -x "$AW" ] || exit 0

status_dir="${AW_STATUS_DIR:-$HOME/.agent-worktrees/run/status}/$wid"
mkdir -p "$status_dir" 2>/dev/null || exit 0

# Single-instance guard: a second launcher attaching to the same session must
# not spawn a duplicate writer. flock is released automatically on exit.
exec 9>"$status_dir/.writer.lock" 2>/dev/null || exit 0
flock -n 9 || exit 0

_write_atomic() {
    # _write_atomic <dest-file> <content-producing-command...>
    local dest="$1"; shift
    local tmp="$dest.$$"
    if "$@" >"$tmp" 2>/dev/null; then
        mv -f "$tmp" "$dest" 2>/dev/null
    else
        rm -f "$tmp" 2>/dev/null
    fi
}

# Left segment is effectively static for the life of the worktree -- write once.
_write_atomic "$status_dir/context" "$AW" status-context --path "$wpath"

# Right segment tracks the live git disposition -- refresh on a slow cadence.
while tmux has-session -t "=$sess" 2>/dev/null; do
    _write_atomic "$status_dir/segment" "$AW" status-segment --path "$wpath"
    sleep "$interval"
done

exit 0
