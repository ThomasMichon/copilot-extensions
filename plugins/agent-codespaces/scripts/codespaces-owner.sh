#!/bin/sh
if [ -z "${BASH_VERSION:-}" ]; then
    exec /bin/bash "$0" "$@"
fi
set -euo pipefail

root="$(CDPATH= cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
for support in resolve-runtime.sh bootstrap-uv.sh native_owned.sh daemon_instance_runtime.pyz codespaces-owner-uv; do
    [[ -f "$root/bin/$support" ]] || {
        printf '[agent-codespaces] owner support unavailable; use explicit installation management.\n' >&2
        exit 126
    }
done
AGENT_RT_ROOT="$root"
AGENT_RT_RESOLVE_TRACE_MODE=read-only
if ! . "$root/bin/resolve-runtime.sh"; then
    printf '[agent-codespaces] owner runtime resolver failed.\n' >&2
    exit 126
fi
[[ -n "$AGENT_RT_PY" ]] || {
    printf '[agent-codespaces] owner runtime unavailable; use explicit installation management.\n' >&2
    exit 126
}
uv="$(cat "$root/bin/codespaces-owner-uv")"
. "$root/bin/native_owned.sh"
selection="$(mktemp -d -t agent-codespaces-selector.XXXXXXXX)" || exit 1
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
    owner-selector "$root/bin/bootstrap-uv.sh" "$AGENT_RT_PY" "$uv" \
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
export PYTHONUTF8=1
export AGENT_CODESPACES_HOME="$root"
cd -- "$root"
exec "$python" -I -B -c '
import os
import sys
sys.path.insert(0, sys.argv[2])
from agent_procutil.native_child import NativeChildCustody
NativeChildCustody(int(sys.argv[1])).retire()
os.close(8)
os.execv(sys.argv[3], sys.argv[3:])
' "$__native_owned_pid" "$root/bin/daemon_instance_runtime.pyz" \
    "$python" -m agent_codespaces owner "$@"
