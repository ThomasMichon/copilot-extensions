#!/usr/bin/env bash

_machines_installer_support() {
    local scripts
    scripts="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)" || return
    if [[ -e "$scripts/bootstrap-uv.sh" || -L "$scripts/bootstrap-uv.sh" ||
        -e "$scripts/installer-instance-python-version.txt" ||
        -L "$scripts/installer-instance-python-version.txt" ]]; then
        printf '%s\n' "$scripts"
    else
        (CDPATH= cd -- "$scripts/../../../libs/installer-engine" && pwd -P)
    fi
}

_machines_uv_install_dir() {
    local directory="${UV_UNMANAGED_INSTALL:-${UV_INSTALL_DIR:-${XDG_BIN_HOME:-}}}"
    if [[ -z "$directory" ]]; then
        directory="$HOME/.local/bin"
        [[ -z "${XDG_DATA_HOME:-}" ]] || directory="$XDG_DATA_HOME/../bin"
    fi
    [[ "$directory" == /* ]] || directory="$PWD/$directory"
    printf '%s\n' "$directory"
}

_machines_find_uv() {
    local candidate version
    while IFS= read -r candidate; do
        [[ "$candidate" == /* && -x "$candidate" ]] || continue
        version="$(invoke_uv_bootstrap_environment "$candidate" --version)" || continue
        if [[ "$version" =~ ^uv[[:space:]]+([0-9]+)\.([0-9]+)\.([0-9]+)($|[[:space:]\(+]) ]] &&
            ((10#${BASH_REMATCH[1]} > 0 || 10#${BASH_REMATCH[2]} >= 8)); then
            printf '%s\n' "$candidate"
            return 0
        fi
    done < <(type -ap uv; printf '%s/uv\n' "$(_machines_uv_install_dir)")
    return 1
}

_machines_select_bootstrap() (
    local support="$1" operation="$2" uv python local_bin
    unset -f uv 2>/dev/null || true
    . "$support/bootstrap-uv.sh" || return
    if ! uv="$(_machines_find_uv)"; then
        [[ "$operation" == Prepare ]] || {
            printf '%s\n' 'Prepared uv 0.8.0 or newer is required; run explicit prerequisite setup.' >&2
            return 1
        }
        local_bin="$(_machines_uv_install_dir)"
        # The official prerequisite owner persists command exposure for later shells.
        if command -v curl >/dev/null 2>&1; then
            set -o pipefail
            curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR="$local_bin" sh >&2 || return
        elif command -v wget >/dev/null 2>&1; then
            set -o pipefail
            wget -qO- https://astral.sh/uv/install.sh | UV_INSTALL_DIR="$local_bin" sh >&2 || return
        else
            printf '%s\n' 'UV prerequisite setup requires curl or wget; no Python fallback was attempted.' >&2
            return 1
        fi
        export PATH="$local_bin:$PATH"
        uv="$(_machines_find_uv)" || {
            printf '%s\n' 'UV prerequisite setup did not expose a working uv 0.8.0 or newer.' >&2
            return 1
        }
    fi
    export PATH="${uv%/*}:$PATH"
    python="$(resolve_uv_bootstrap_python "$support/installer-instance-python-version.txt" "$operation")" || return
    invoke_uv_bootstrap_environment "$python" -I -S -B -c \
        'import sys; sys.exit(sys.version_info < (3, 10))' </dev/null || return
    printf '%s\n%s\n' "$uv" "$python"
)

_initialize_machines_bootstrap() {
    local operation="${1:-Inspect}" maximum_seconds="${2:-480}" scripts support capture timeout selection
    [[ "$operation" == Inspect || "$operation" == Prepare ]] || return 2
    [[ "$maximum_seconds" =~ ^[1-9][0-9]*$ ]] || return 2
    scripts="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)" || return
    support="$(_machines_installer_support)" || return
    capture="$scripts/native_capture.sh"
    [[ -f "$capture" ]] ||
        capture="$scripts/../../../libs/agent-procutil/src/agent_procutil/native_capture.sh"
    [[ -f "$support/bootstrap-uv.sh" && -f "$support/installer-instance-python-version.txt" &&
        -f "$capture" ]] || {
        printf '%s\n' 'Installer bootstrap support is unavailable; restore the owning payload.' >&2
        return 1
    }
    . "$capture" || return
    timeout=30
    [[ "$operation" != Prepare ]] || timeout=480
    ((maximum_seconds >= timeout)) || timeout="$maximum_seconds"
    if selection="$(
        unset BASH_ENV ENV
        exec 5>&- 6>&- 7>&- 8>&- 9>&-
        if capture_native_process "$timeout" "$BASH" --noprofile --norc -c '
            . "$1/installer-bootstrap.sh" || exit
            _machines_select_bootstrap "$2" "$3"
        ' machines-installer-bootstrap "$scripts" "$support" "$operation"; then
            printf '%s' "$__native_capture_output"
        else
            exit $?
        fi
    )"; then
        :
    else
        return $?
    fi
    [[ "$selection" == *$'\n'* ]] || return 1
    MACHINES_UV="${selection%%$'\n'*}"
    MACHINES_BOOTSTRAP_PYTHON="${selection#*$'\n'}"
    [[ "$MACHINES_UV" == /* && "$MACHINES_UV" != *$'\r'* && -x "$MACHINES_UV" &&
        "$MACHINES_BOOTSTRAP_PYTHON" == /* && "$MACHINES_BOOTSTRAP_PYTHON" != *$'\n'* &&
        "$MACHINES_BOOTSTRAP_PYTHON" != *$'\r'* && -x "$MACHINES_BOOTSTRAP_PYTHON" ]] || return 1
    export PATH="${MACHINES_UV%/*}:$PATH"
}
