#!/usr/bin/env bash
# Emit effort-enforcement context for an adopting repository.

set -uo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" 2>/dev/null && pwd -P)" || {
    printf '{}'
    exit 0
}
python="$(command -v python3 || command -v python || true)"
if [[ -z "$python" || ! -f "$script_dir/emit-policy.py" ]]; then
    printf '[efforts] Python policy producer is unavailable; no policy context emitted\n' >&2
    printf '{}'
    exit 0
fi

if output="$("$python" "$script_dir/emit-policy.py" "$@")" &&
    [[ "$output" == "{}" ||
       "$output" == '{"additionalContext":'*'}' ||
       "$output" == '{"version":1,"capability":"efforts","adopted":true}' ]]; then
    printf '%s' "$output"
else
    printf '[efforts] Python policy producer failed; no policy context emitted\n' >&2
    printf '{}'
fi
