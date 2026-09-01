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
authority="$(dirname "$root")/context-injection"
engine="$authority/scripts/aggregate_context.py"
python_bin="$(command -v python3 || command -v python || true)"
script="$root/$relative_script"
payload="$(cat)" || {
    printf '{}'
    exit 0
}
if [[ -z "$python_bin" ]]; then
    # Without a JSON parser, retain the safe standalone path and use only a
    # host-provided absolute project directory (otherwise the hook process cwd).
    launch_cwd="${COPILOT_PROJECT_DIR:-$PWD}"
    if [[ "$launch_cwd" != /* || ! -d "$launch_cwd" ]]; then
        launch_cwd="$PWD"
    fi
    if [[ -f "$script" && "$script" == "$root/"* && "$script" == *.sh ]]; then
        (
            cd "$launch_cwd" || {
                printf '{}'
                exit 0
            }
            printf '%s' "$payload" | bash "$script" "$@"
        )
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
if [[ -n "$python_bin" && -f "$engine" ]]; then
    output="$(mktemp "${TMPDIR:-/tmp}/context-producer.XXXXXXXX")" || {
        printf '{}'
        exit 0
    }
    trap 'rm -f "$output"' EXIT
    if printf '%s' "$payload" |
        "$python_bin" "$engine" --producer "$source_id/$contributor_id" >"$output"; then
        cat "$output"
    else
        printf '%s\n' \
            "[$source_id] context authority failed after selection; context suppressed" >&2
        printf '{}'
    fi
    exit 0
fi

if [[ -f "$script" && "$script" == "$root/"* && "$script" == *.sh ]]; then
    (
        cd "$launch_cwd" || {
            printf '{}'
            exit 0
        }
        printf '%s' "$payload" | bash "$script" "$@"
    )
else
    printf '{}'
fi
