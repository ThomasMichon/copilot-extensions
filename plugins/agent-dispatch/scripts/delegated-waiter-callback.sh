#!/bin/sh
set -eu
if [ "$#" -ne 1 ]; then
    printf '%s\n' 'agent-dispatch: invalid delegated callback arguments' >&2
    exit 1
fi
case "$1" in
    hex:*) ;;
    *) printf '%s\n' 'agent-dispatch: invalid delegated callback arguments' >&2; exit 1 ;;
esac
entry_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
AGENT_RT_ROOT=$(dirname -- "$entry_dir")
AGENT_RT_RESOLVE_TRACE_MODE=read-only
export AGENT_RT_ROOT AGENT_RT_RESOLVE_TRACE_MODE
. "$entry_dir/resolve-runtime.sh"
if [ -z "$AGENT_RT_PY" ]; then
    printf '%s\n' 'agent-dispatch: delegated callback runtime unavailable' >&2
    exit 1
fi
exec "$AGENT_RT_PY" -I -X utf8 -m agent_dispatch _delegated-waiter-callback "$1"
