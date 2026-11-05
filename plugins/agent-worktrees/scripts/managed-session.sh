#!/bin/sh

managed_worktrees_python() (
    scripts="$1"
    runtime_root="$HOME/.agent-worktrees"
    if [ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]; then
        pointer="$COPILOT_EXTENSIONS_CONTEXT"
        case "$pointer" in /*/plugins/agent-worktrees/install.json) ;; *) return 1 ;; esac
        [ -f "$pointer" ] || return 1
        ancestor="$pointer"
        while [ "$ancestor" != / ]; do
            [ ! -L "$ancestor" ] || return 1
            ancestor="$(dirname -- "$ancestor")"
        done
        runtime_root="$(cd -- "$(dirname -- "$pointer")" && pwd -P)" || return 1
        [ "${runtime_root##*/}" = agent-worktrees ] || return 1
        [ "$(basename -- "$(dirname -- "$runtime_root")")" = plugins ] || return 1
    fi
    if [ -f "$scripts/resolve-runtime.sh" ]; then
        AGENT_RT_ROOT="$runtime_root"
        AGENT_RT_RESOLVE_TRACE_MODE=read-only
        . "$scripts/resolve-runtime.sh"
        if [ -n "${AW_PY:-}" ] && [ -x "$AW_PY" ]; then
            printf '%s\n' "$AW_PY"
            return 0
        fi
    fi
    [ -z "${COPILOT_EXTENSIONS_CONTEXT:-}" ] || return 1
    command -v python3 || command -v python
)
