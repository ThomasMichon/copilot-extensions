#!/bin/sh
if [ -z "${BASH_VERSION:-}" ]; then
    exec /bin/bash "$0" "$@"
fi
set -euo pipefail

python="${1:-}"
root="${2:-}"
[[ "$#" -ge 2 && "$root" == /* ]] || exit 126
shift 2
for support in bootstrap-uv.sh native_owned.sh daemon_instance_runtime.pyz vault-cache-service.sh vault-service-uv; do
    [[ -f "$root/bin/$support" ]] || {
        printf '[agent-vault] service support unavailable; use explicit installation management.\n' >&2
        exit 126
    }
done
uv="$(cat "$root/bin/vault-service-uv")"
. "$root/bin/native_owned.sh"
selection="$(mktemp -d -t agent-vault-selector.XXXXXXXX)" || exit 1
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
    service-selector "$root/bin/bootstrap-uv.sh" "$python" "$uv" \
    "/proc/self/fd/$_selection_result" || exit 1
_unlink_selection_files || exit 1
trap - EXIT
wait_native_owned_process 10 || exit 1
cat <&"$_selection_log" >&2
[[ "$__native_owned_code" == 0 ]] || exit "$__native_owned_code"
selected_python="$(cat <&"$_selection_result")"
exec {_selection_result}>&- {_selection_log}>&-
[[ "$selected_python" == "$python" ]] || exit 1
unset PYTHONHOME PYTHONPATH
exec "$python" -I -B -c '
import os
import sys
sys.path.insert(0, sys.argv[2])
from agent_procutil.native_child import NativeChildCustody
NativeChildCustody(int(sys.argv[1])).retire()
os.close(8)
os.execv("/bin/sh", ["/bin/sh", *sys.argv[3:]])
' "$__native_owned_pid" "$root/bin/daemon_instance_runtime.pyz" \
    "$root/bin/vault-cache-service.sh" "$python" "$root" "$@"
