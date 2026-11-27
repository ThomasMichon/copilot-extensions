#!/usr/bin/env bash

_resolve_cell_lifecycle_python() (
    local scripts support capture python code
    exec 5>&- 6>&- 7>&- 8>&- 9>&-
    unset BASH_ENV ENV
    scripts="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)" || return
    support="$scripts"
    if [[ ! -e "$support/bootstrap-uv.sh" && ! -L "$support/bootstrap-uv.sh" &&
        ! -e "$support/installer-instance-python-version.txt" &&
        ! -L "$support/installer-instance-python-version.txt" ]]; then
        support="$(CDPATH= cd -- "$scripts/../../../libs/installer-engine" && pwd -P)" || return
    fi
    capture="$scripts/native_capture.sh"
    [[ -f "$capture" ]] ||
        capture="$scripts/../../../libs/agent-procutil/src/agent_procutil/native_capture.sh"
    if [[ ! -f "$support/bootstrap-uv.sh" ||
        ! -f "$support/installer-instance-python-version.txt" || ! -f "$capture" ]]; then
        printf '%s\n' 'Cell lifecycle bootstrap support is unavailable; restore the owning installation payload.' >&2
        return 1
    fi
    . "$capture" || return
    if capture_native_process 30 "$BASH" --noprofile --norc -c '
        . "$1/bootstrap-uv.sh" || exit
        python="$(resolve_uv_bootstrap_python "$1/installer-instance-python-version.txt" Inspect)" || exit
        "$python" -I -S -B -c "import sys; sys.exit(sys.version_info < (3, 10))" </dev/null || exit
        printf "%s\n" "$python"
    ' cell-lifecycle-bootstrap "$support"; then
        python="$__native_capture_output"
        [[ "$python" == /* && "$python" != *$'\n'* && "$python" != *$'\r'* &&
            -f "$python" && -x "$python" ]] || return 1
        printf '%s\n' "$python"
    else
        code=$?
        printf '%s\n' 'Cell lifecycle requires prepared uv-managed installer Python; run prerequisite setup. No installation or PATH Python fallback was attempted.' >&2
        return "$code"
    fi
)
