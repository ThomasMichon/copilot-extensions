#!/usr/bin/env bash
# Side-effect-only sessionStart wrapper: invokes write_session_guidance.py.
# Never emits additionalContext; this raw hooks.json entry writes the
# exact-session guidance file. See scripts/write_session_guidance.py.
set -uo pipefail

root="${COPILOT_PLUGIN_ROOT:-${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd -P)}}}"
case "${OSTYPE:-}" in
    msys*|cygwin*)
        host="$(cygpath -u "${SYSTEMROOT:-${SystemRoot:-}}")/System32/WindowsPowerShell/v1.0/powershell.exe"
        "$host" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass \
            -File "$(cygpath -w "$root/scripts/write-session-guidance.ps1")" \
            >/dev/null 2>&1 || true
        printf '{}'
        exit 0
        ;;
esac
script="$root/scripts/write_session_guidance.py"
if [[ "${OSTYPE:-}" == linux* ]]; then
    guidance_parent=$BASHPID
    linux_guidance() (
        local support="$root/scripts/_bootstrap_python" elapsed ignored deadline directory owner
        local result_fd log_fd python
        local __native_owned_pid=''
        for required in select.sh native_owned.sh daemon_instance_runtime.pyz bootstrap_uv_capture.py; do
            [[ -f "$support/$required" ]] || return 1
        done
        [[ -f "$script" ]] || return 1
        read -r elapsed ignored < /proc/uptime || return 1
        deadline=$(( ${elapsed%%.*} + 40 ))
        . "$support/native_owned.sh"
        directory="$(mktemp -d -t agent-mcp-guidance.XXXXXXXX)" || return 1
        directory="$(CDPATH= cd -P -- "$directory" && pwd -P)" || return 1
        owner=$BASHPID
        unlink_guidance() {
            [[ "$BASHPID" == "$owner" ]] || return 0
            [[ -d "$directory" ]] || return 0
            rm -f -- "$directory/lifetime" "$directory/readiness" \
                "$directory/selector.log" "$directory/python"
            rmdir -- "$directory"
        }
        cleanup_guidance() {
            [[ "$BASHPID" == "$owner" ]] || return 0
            if [[ -n "${__native_owned_pid:-}" ]]; then
                settle_native_owned_process 3 || return 1
            fi
            unlink_guidance
        }
        trap cleanup_guidance EXIT
        exec {result_fd}<>"$directory/python" {log_fd}<>"$directory/selector.log"
        start_native_owned_process "$directory" "$directory/selector.log" 10 \
            "$BASH" -c 'ulimit -f 64 || exit; "$BASH" "$1" Inspect > "$2"' \
            guidance-selector "$support/select.sh" "/proc/self/fd/$result_fd" || return 1
        unlink_guidance || return 1
        wait_native_owned_process 15 || return 1
        [[ "$__native_owned_code" == 0 ]] || return 1
        python="$(cat <&"$result_fd")"
        exec {result_fd}>&- {log_fd}>&-
        [[ "$python" == /* && "$python" != *$'\n'* && "$python" != *$'\r'* &&
            -f "$python" && -x "$python" ]] || return 1
        unset PYTHONHOME PYTHONPATH PYTHONEXECUTABLE VIRTUAL_ENV
        exec "$python" -I -B -X utf8 -c '
import os
import runpy
import sys
import time

support, child, deadline, writer, parent, bash = sys.argv[1:]
with open("/proc/uptime", encoding="ascii") as clock:
    remaining = float(deadline) - float(clock.read(64).split()[0])
deadline = str(time.monotonic() + remaining)
sys.path.insert(0, support + "/daemon_instance_runtime.pyz")
try:
    from agent_procutil.native_child import NativeChildCustody
    NativeChildCustody(int(child)).retire()
except Exception:
    os.close(8)
    os.execv(bash, [bash, support + "/native_owned.sh", "--wait-group-settlement", child, "3"])
else:
    os.close(8)
os.environ["COPILOT_EXTENSIONS_GUIDANCE_DEADLINE"] = str(float(deadline) - 4)
sys.argv = [
    support + "/bootstrap_uv_capture.py", "--deadline", deadline,
    "--cleanup-reserve", "3", "--input-stdin", "--parent-pid", parent,
    "--", sys.executable, "-I", "-B", "-X", "utf8", writer,
]
runpy.run_path(sys.argv[0], run_name="__main__")
' "$support" "$__native_owned_pid" "$deadline" "$script" "$guidance_parent" "$BASH"
    )
    linux_guidance >/dev/null 2>&1 || true
    printf '{}'
    exit 0
fi
helper="$root/scripts/hook-uv-python.sh"
if [[ ! -f "$helper" || ! -f "$script" ]]; then
    printf '{}'
    exit 0
fi
. "$helper"
hook_uv_python "$script" || printf '{}'