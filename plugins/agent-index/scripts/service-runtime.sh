#!/bin/bash
# Linux native-service ingress; both runtimes retain their own support closure.
set -euo pipefail

root="${1:?runtime root required}"
uv="${2:?prepared uv required}"
mode="${3:?service or engine required}"
legacy_python="${4:-}"
shift 4
[[ "$root" == /* && ( "$mode" == service || "$mode" == engine ) ]] || exit 126
for support in resolve-runtime.sh bootstrap-uv.sh native_owned.sh daemon_instance_runtime.pyz; do
    [[ -f "$root/bin/$support" ]] || {
        printf '[agent-index] service admission support unavailable; use explicit installation management.\n' >&2
        exit 126
    }
done
export PYTHONUTF8=1
if [[ "$mode" == engine ]]; then
    export AGENT_INDEX_ENGINE_HOME="$root"
    env_file="$root/engine.env"
else
    env_file="$root/service.env"
fi
if [[ -f "$env_file" ]]; then
    set -a
    . "$env_file"
    set +a
fi
AGENT_RT_PY=""
if [[ "$mode" == engine && ! -e "$root/current-version" &&
      ! -L "$root/current-version" && ! -e "$root/last-known-good" &&
      ! -L "$root/last-known-good" ]]; then
    AGENT_RT_PY="$legacy_python"
else
    AGENT_RT_ROOT="$root"
    AGENT_RT_RESOLVE_TRACE_MODE=read-only
    if ! . "$root/bin/resolve-runtime.sh"; then
        printf '[agent-index] service runtime resolver failed.\n' >&2
        exit 126
    fi
fi
[[ -n "$AGENT_RT_PY" ]] || {
    printf '[agent-index] no completed service runtime is selectable.\n' >&2
    exit 126
}
. "$root/bin/native_owned.sh"
selection="$(mktemp -d -t agent-index-selector.XXXXXXXX)" || exit 1
_selection_owner=$BASHPID
_unlink_selection_files() {
    [[ "$BASHPID" == "$_selection_owner" && -n "$selection" ]] || return 0
    rm -f -- "$selection/lifetime" "$selection/readiness" \
        "$selection/selector.log" "$selection/python" || return 1
    rmdir -- "$selection" || return 1
    selection=''
}
trap _unlink_selection_files EXIT
selection="$(CDPATH= cd -P -- "$selection" && pwd -P)" || exit 1
exec {_selection_result}<>"$selection/python" {_selection_log}<>"$selection/selector.log"
start_native_owned_process "$selection" "$selection/selector.log" 10 \
    "$BASH" -c '. "$1"; resolve_uv_runtime_python "$2" "$3" > "$4"' \
    service-selector "$root/bin/bootstrap-uv.sh" "$AGENT_RT_PY" "$uv" \
    "/proc/self/fd/$_selection_result" || exit 1
_unlink_selection_files || exit 1
trap - EXIT
wait_native_owned_process 10 || exit 1
cat <&"$_selection_log" >&2
[[ "$__native_owned_code" == 0 ]] || exit "$__native_owned_code"
python="$(cat <&"$_selection_result")"
exec {_selection_result}>&- {_selection_log}>&-
[[ "$python" == "$AGENT_RT_PY" ]] || exit 1
unset PYTHONHOME PYTHONPATH
cd -- "$root"
if [[ "$mode" == engine ]]; then
    set -- engine run "$@"
else
    set -- start "$@"
fi
exec "$python" -I -B -c '
import os
import sys
sys.path.insert(0, sys.argv[2])
from agent_procutil.native_child import NativeChildCustody
NativeChildCustody(int(sys.argv[1])).retire()
os.close(8)
os.execv(sys.argv[3], sys.argv[3:])
' "$__native_owned_pid" "$root/bin/daemon_instance_runtime.pyz" \
    "$python" -I -B -X utf8 -m agent_index "$@"
