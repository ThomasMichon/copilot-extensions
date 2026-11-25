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
    linux_guidance() (
        local support="$root/scripts/_bootstrap_python" elapsed ignored deadline directory owner remaining
        local result_fd log_fd input_fd
        local __native_owned_pid=''
        for required in select.sh native_owned.sh; do
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
        exec {input_fd}<&0 {result_fd}<>"$directory/python" {log_fd}<>"$directory/selector.log"
        read -r elapsed ignored < /proc/uptime || return 1
        remaining=$(( deadline - ${elapsed%%.*} ))
        (( remaining > 0 )) || return 1
        start_native_owned_process "$directory" "$directory/selector.log" "$remaining" \
            "$BASH" -c '
ulimit -f 64 || exit
"$BASH" "$1" Inspect > "$2" || exit
python="$(cat "$2")"
[[ "$python" == /* && "$python" != *"$6"* && "$python" != *"$7"* &&
    -f "$python" && -x "$python" ]] || exit 1
unset PYTHONHOME PYTHONPATH PYTHONEXECUTABLE VIRTUAL_ENV
exec "$python" -I -B -X utf8 -c "
import os
import runpy
import sys
import time

deadline, writer = sys.argv[1:]
with open(\"/proc/uptime\", encoding=\"ascii\") as clock:
    remaining = float(deadline) - float(clock.read(64).split()[0])
os.environ[\"COPILOT_EXTENSIONS_GUIDANCE_DEADLINE\"] = str(time.monotonic() + remaining - 3)
sys.argv = [writer]
runpy.run_path(writer, run_name=\"__main__\")
" "$4" "$5" <&"$3"
' guidance-operation "$support/select.sh" "/proc/self/fd/$result_fd" \
            "$input_fd" "$deadline" "$script" $'\n' $'\r' || return 1
        unlink_guidance || return 1
        read -r elapsed ignored < /proc/uptime || return 1
        remaining=$(( deadline - ${elapsed%%.*} ))
        (( remaining > 0 )) || return 1
        wait_native_owned_process "$remaining" || return 1
        [[ "$__native_owned_code" == 0 ]]
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