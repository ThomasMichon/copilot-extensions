#!/bin/sh
set -eu
case "$0" in
    */agent-worktrees) exec /lab/state/runtime/worktrees/bin/agent-worktrees "$@" ;;
    */agent-bridge) exec /lab/state/runtime/bridge/bin/agent-bridge "$@" ;;
    */agent-dispatch) exec /lab/state/runtime/dispatch/bin/agent-dispatch "$@" ;;
    *) printf '%s\n' 'Unsupported reproducer runtime command' >&2; exit 2 ;;
esac
