#!/usr/bin/env bash
set -uo pipefail

source_id="${1:-}"
contributor_id="${2:-}"
relative_script="${3:-}"
shift "$(( $# < 3 ? $# : 3 ))"

root="${COPILOT_PLUGIN_ROOT:-${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-}}}"
if [[ -z "$root" || -z "$source_id" || -z "$contributor_id" || -z "$relative_script" ]]; then
    printf '{}'
    exit 0
fi

case "$relative_script" in
    /*|*..*) printf '{}'; exit 0 ;;
esac

export COPILOT_PLUGIN_ROOT="$root"
python_bin="$(command -v python3 || command -v python || true)"
script="$root/$relative_script"
resolver="$root/scripts/resolve_context_authority.py"
payload="$(cat)" || {
    printf '{}'
    exit 0
}
if [[ -z "$python_bin" ]]; then
    launch_cwd="${COPILOT_PROJECT_DIR:-$PWD}"
    if [[ "$launch_cwd" != /* || ! -d "$launch_cwd" ]]; then
        launch_cwd="$PWD"
    fi
    if [[ -f "$script" && "$script" == "$root/"* && "$script" == *.sh ]]; then
        output="$(
            set +o pipefail
            cd "$launch_cwd" || exit 1
            printf '%s' "$payload" | bash "$script" "$@" 2> >(cat >&2)
        )"
        status=$?
        if [[ $status -eq 0 && "$output" == \{*\} ]]; then
            printf '%s' "$output"
        else
            printf '{}'
        fi
    else
        printf '{}'
    fi
    exit 0
fi

launch_cwd="$(
    printf '%s' "$payload" | "$python_bin" -c '
import json
import os
import sys
from pathlib import Path

try:
    value = json.load(sys.stdin)
    raw = value.get("cwd") if isinstance(value, dict) else None
    if not isinstance(raw, str) or not raw or not os.path.isabs(raw):
        raise ValueError
    cwd = Path(raw).resolve(strict=True)
    if not cwd.is_dir():
        raise ValueError
    print(cwd, end="")
except (OSError, ValueError, json.JSONDecodeError):
    pass
' 2>/dev/null
)"
if [[ -z "$launch_cwd" ]]; then
    printf '{}'
    exit 0
fi

validate_output() {
    "$python_bin" -c '
import json
import sys

raw = sys.stdin.buffer.read()
try:
    value = json.loads(raw)
except (UnicodeDecodeError, json.JSONDecodeError):
    raise SystemExit(1)
if not isinstance(value, dict):
    raise SystemExit(1)
sys.stdout.buffer.write(raw)
'
}

run_buffered() {
    local output status
    output="$("$@" 2> >(cat >&2))"
    status=$?
    if [[ $status -eq 0 ]] && printf '%s' "$output" | validate_output; then
        return 0
    fi
    printf '{}'
    return 0
}

authority=""
if [[ -f "$resolver" ]]; then
    authority="$(
        printf '%s' "$payload" | "$python_bin" "$resolver" 2>/dev/null
    )"
fi
engine="$authority/scripts/aggregate_context.py"
if [[ -n "$authority" && -f "$engine" ]]; then
    printf '%s' "$payload" |
        run_buffered "$python_bin" "$engine" --producer "$source_id/$contributor_id"
    exit 0
fi

if [[ -f "$script" && "$script" == "$root/"* && "$script" == *.sh ]]; then
    printf '%s' "$payload" | (
        cd "$launch_cwd" || {
            printf '{}'
            exit 0
        }
        run_buffered bash "$script" "$@"
    )
    exit 0
fi

printf '{}'
exit 0
