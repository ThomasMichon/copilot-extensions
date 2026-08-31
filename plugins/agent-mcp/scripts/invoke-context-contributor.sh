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

authority="$(dirname "$root")/context-injection"
engine="$authority/scripts/aggregate_context.py"
python_bin="$(command -v python3 || command -v python || true)"
if [[ -n "$python_bin" && -f "$engine" ]]; then
    output="$(mktemp "${TMPDIR:-/tmp}/context-producer.XXXXXXXX")" || {
        printf '{}'
        exit 0
    }
    trap 'rm -f "$output"' EXIT
    if "$python_bin" "$engine" --producer "$source_id/$contributor_id" >"$output"; then
        cat "$output"
    else
        printf '%s\n' \
            "[$source_id] context authority failed after selection; context suppressed" >&2
        printf '{}'
    fi
    exit 0
fi

script="$root/$relative_script"
if [[ -f "$script" && "$script" == "$root/"* && "$script" == *.sh ]]; then
    bash "$script" "$@"
else
    printf '{}'
fi
