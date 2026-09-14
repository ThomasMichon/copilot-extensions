#!/usr/bin/env bash
# apply-mux-keybinds.sh -- OPTIONAL tmux keystroke-passthrough + escape-time.
#
# WHY THIS IS OPT-IN
# ------------------
# These settings cannot be scoped to a single tmux session: `escape-time` is a
# server option and key bindings live in server-global key tables (there is no
# per-session key table in tmux). Applying them automatically would leak onto
# your personal / ad-hoc tmux sessions. So agent-worktrees does NOT apply them
# for you, and its installer never touches ~/.tmux.conf. Run this yourself --
# once per machine, or from a machine-restore flow -- if you want the
# keystroke-passthrough behavior the worktree panes were designed around.
#
# By default this is a ONE-TIME action: it persists a clearly-marked managed
# block in ~/.tmux.conf (read by tmux at server startup, so it survives server
# restarts) AND applies the same settings to any already-running server. The
# block is the ONLY thing it manages in that file -- the rest is left untouched,
# and deleting the marked block (or passing nothing) removes the settings.
# Re-running is idempotent. Pass --no-persist to only tune the running server.
#
# The per-session status bar + behaviors are applied automatically by the
# launcher (see session-options.sh); this script is only the server-global part.
set -euo pipefail

PERSIST=true
for arg in "$@"; do
    case "$arg" in
        --no-persist) PERSIST=false ;;
        -h|--help) sed -n '2,21p' "$0"; exit 0 ;;
        *) echo "apply-mux-keybinds: unknown argument: $arg" >&2; exit 2 ;;
    esac
done

if ! command -v tmux >/dev/null 2>&1; then
    echo "apply-mux-keybinds: tmux not found on PATH" >&2
    exit 1
fi

CONF="${HOME}/.tmux.conf"
BEGIN="# >>> agent-worktrees mux keybinds (opt-in) >>>"
END="# <<< agent-worktrees mux keybinds (opt-in) <<<"

read -r -d '' BLOCK_BODY <<'EOF' || true
# Managed by agent-worktrees `apply-mux-keybinds.sh` -- you elected to install
# these by running that script. Delete this whole block (markers included) to
# remove them, or re-run the script to refresh it.
#
# escape-time 0: deliver escape sequences immediately so arrow/function keys
# don't lag in TUI apps (Copilot CLI, editors).
set -sg escape-time 0
set -g focus-events on
# Opt-in intercept: every unprefixed key/mouse event passes straight through to
# the inner application; only the prefix (Ctrl+B) is intercepted by tmux.
set -g prefix C-b
unbind-key -a -T root
# Re-add mouse-wheel passthrough (cleared by the unbind above).
bind-key -T root WheelUpPane   send-keys -M
bind-key -T root WheelDownPane send-keys -M
EOF

persist_block() {
    local tmp
    tmp="$(mktemp)"
    if [[ -f "$CONF" ]]; then
        # Drop any existing managed block; preserve everything else verbatim.
        awk -v b="$BEGIN" -v e="$END" '
            $0==b {skip=1; next}
            $0==e {skip=0; next}
            !skip {print}
        ' "$CONF" > "$tmp"
        # Trim trailing blank lines so repeated runs don't accumulate them.
        awk 'NF{p=NR} {a[NR]=$0} END{for(i=1;i<=p;i++) print a[i]}' "$tmp" > "$tmp.t" \
            && mv -f "$tmp.t" "$tmp"
    fi
    # One blank separator line before the block when the file already has content.
    [[ -s "$tmp" ]] && printf '\n' >> "$tmp"
    {
        printf '%s\n' "$BEGIN"
        printf '%s\n' "$BLOCK_BODY"
        printf '%s\n' "$END"
    } >> "$tmp"
    mv -f "$tmp" "$CONF"
    echo "apply-mux-keybinds: persisted managed block to $CONF"
}

live_apply() {
    if [[ -z "$(tmux ls 2>/dev/null)" ]]; then
        echo "apply-mux-keybinds: no running tmux server -- the persisted block applies when one starts"
        return 0
    fi
    tmux set -sg escape-time 0
    tmux set -g focus-events on
    tmux set -g prefix C-b
    tmux unbind-key -a -T root
    tmux bind-key -T root WheelUpPane   send-keys -M
    tmux bind-key -T root WheelDownPane send-keys -M
    echo "apply-mux-keybinds: applied escape-time + keystroke passthrough to the running tmux server"
}

$PERSIST && persist_block
live_apply
