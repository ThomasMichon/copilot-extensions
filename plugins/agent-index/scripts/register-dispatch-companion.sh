#!/usr/bin/env bash
set -u
session_start_json_emitted=0
emit_session_start_json() {
    if [ "${session_start_json_emitted:-0}" -eq 0 ]; then
        printf '{}'
        session_start_json_emitted=1
    fi
}
trap 'emit_session_start_json' EXIT

script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
python="$(command -v python3 || command -v python || true)"
if [ -n "$python" ] && [ -f "$script_dir/register-dispatch-companion.py" ]; then
    "$python" -E -X utf8 "$script_dir/register-dispatch-companion.py"
fi
exit 0
