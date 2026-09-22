#!/usr/bin/env bash
# tmux-drive.sh -- first-class helper API for driving a HEADED (real
# interactive-TTY) `copilot` session inside a clean-room scenario via tmux +
# send-keys, for cases where `-p`/`--acp` do not exercise the code path under
# test.
#
# Why this exists: investigating github/copilot-agent-runtime#22266 (the
# context-handoff extension-connection clash) established that `copilot -p`
# (headless mode) never launches extensions at all -- only skills load
# headlessly. Extensions (the JS runtime-hosted kind that register tools,
# like context-handoff) only activate under a REAL interactive session.
# `--acp` was the other candidate automation surface, but as of this writing
# no clean-room-usable CLI-mode agent-bridge driver exists yet for it (a
# separate in-flight effort). tmux + `-i/--interactive` + send-keys is the
# durable, present-day way to drive and observe a real headed session from a
# scenario.sh, so this is factored out as shared infrastructure rather than
# a one-off hack in a single scenario -- any future scenario that needs a
# genuinely interactive session (not just a headless prompt/response) should
# use this instead of re-deriving its own tmux incantations.
#
# IMPORTANT -- also pass `--experimental` to the driven `copilot` invocation
# whenever the scenario cares about the extension-host mechanism specifically:
# without it, `/env` reports "Extensions: No extensions loaded" even when a
# plugin's skills/hooks load fine -- the JS extension-host component is
# gated behind that flag entirely, independent of headed vs. headless mode.
# This helper does not add the flag automatically (callers that only need a
# headed session for some OTHER reason should not be forced into
# experimental-features territory), but every extension-focused scenario
# built on this file should include it explicitly, as
# context-handoff-connection-race does.
#
# Public helper API:
#   cr_tmux_ensure                    -- install tmux via apt if missing
#                                        (best-effort; returns 1 if it
#                                        cannot be installed, e.g. no network)
#   cr_tmux_start <session> <cwd> <prompt> [extra copilot args...]
#                                     -- kill any stale session of the same
#                                        name, then start a detached tmux
#                                        session in <cwd> running
#                                        `copilot -i <prompt> <extra args...>`;
#                                        auto-dismisses the one-time "Confirm
#                                        folder trust" prompt every headed
#                                        session hits against a not-yet-
#                                        trusted cwd (accepts the default
#                                        "Yes", does NOT persist trust)
#   cr_tmux_send <session> <keys>     -- tmux send-keys wrapper; appends Enter
#   cr_tmux_capture <session>         -- print the pane's visible text PLUS
#                                        its scrollback history (tmux only
#                                        keeps a bounded history buffer, so
#                                        long sessions may still truncate --
#                                        prefer polling early/often over
#                                        capturing once at the end)
#   cr_tmux_wait_for <session> <pattern> [timeout_s]
#                                     -- poll cr_tmux_capture (grep -E
#                                        <pattern>) until it matches or
#                                        <timeout_s> (default 30) elapses;
#                                        returns 1 on timeout, leaving the
#                                        session running for post-mortem
#                                        capture
#   cr_tmux_stop <session>            -- kill-session; always succeeds
#                                        (best-effort cleanup, never blocks
#                                        scenario teardown on tmux's state)
#
# Callers still own composing the actual `copilot` invocation's plugin/auth
# flags (this file has no opinion on marketplace/plugin-dir setup) and their
# own pass/fail assertions against cr_tmux_capture's output via the normal
# clean-room-lib.sh `pass`/`fail`/`jam` vocabulary.
#
# MUST be LF.

cr_tmux_ensure() {
    command -v tmux >/dev/null 2>&1 && return 0
    sudo apt-get update -q >/dev/null 2>&1
    sudo apt-get install -y --no-install-recommends tmux >/dev/null 2>&1
    command -v tmux >/dev/null 2>&1
}

cr_tmux_start() {  # <session> <cwd> <prompt> [extra copilot args...]
    local session="$1" cwd="$2" prompt="$3"
    shift 3
    tmux kill-session -t "$session" 2>/dev/null || true
    # A generous scrollback history-limit: a long agent turn can print far
    # more than tmux's small default buffer, and cr_tmux_capture/wait_for
    # both rely on the full transcript still being there to grep.
    tmux new-session -d -s "$session" -x 220 -y 50 -c "$cwd" \
        "copilot -i $(printf '%q' "$prompt") $(printf '%q ' "$@")" \
        2>/dev/null
    tmux set-option -t "$session" history-limit 100000 2>/dev/null || true
    _cr_tmux_dismiss_folder_trust_prompt "$session"
}

# The FIRST headed launch against any not-yet-trusted cwd blocks on a
# "Confirm folder trust" TUI prompt before anything else happens (no flag
# bypasses it as of this writing) -- every scenario driving a real session
# would otherwise need to rediscover and dismiss this itself, so it is
# handled here once, centrally. Accepts the prompt's own default ("1. Yes")
# via a bare Enter; does not opt into "remember this folder", so trust is
# scoped to the one session under test rather than silently persisting.
_cr_tmux_dismiss_folder_trust_prompt() {
    local session="$1" waited=0
    while [ "$waited" -lt 10 ]; do
        if cr_tmux_capture "$session" 2>/dev/null | grep -q "Confirm folder trust"; then
            tmux send-keys -t "$session" Enter 2>/dev/null
            return 0
        fi
        sleep 0.5
        waited=$((waited + 1))
    done
    return 0   # no prompt seen within 5s -- likely already trusted; not an error
}

cr_tmux_send() {  # <session> <keys>
    local session="$1"; shift
    tmux send-keys -t "$session" "$*" Enter 2>/dev/null
}

cr_tmux_capture() {  # <session>
    local session="$1"
    # -S -<n>: start <n> lines into scrollback history; a large negative
    # value pulls the whole retained buffer (bounded by history-limit above).
    tmux capture-pane -t "$session" -p -S -100000 2>/dev/null
}

cr_tmux_wait_for() {  # <session> <pattern> [timeout_s]
    local session="$1" pattern="$2" timeout="${3:-30}"
    local deadline=$((SECONDS + timeout))
    while [ "$SECONDS" -lt "$deadline" ]; do
        if cr_tmux_capture "$session" | grep -Eq "$pattern"; then
            return 0
        fi
        sleep 0.5
    done
    return 1
}

cr_tmux_stop() {  # <session>
    tmux kill-session -t "$1" 2>/dev/null || true
}
