#!/usr/bin/env bash
# shellcheck shell=bash
uninstall_state_preserving_runtime() {
    local py
    py="$(_bootstrap_python)" || return 1
    [[ -n "$py" ]] || { echo 'Uninstall requires an existing bootstrap Python' >&2; return 1; }
    if [[ "${1:-}" == validate ]]; then
        "$py" -I -B "$SCRIPT_DIR/uninstall-state.py" "$INSTALL_DIR" --shell "$BASH" --preview >/dev/null
        return
    fi
    "$py" -I -B "$SCRIPT_DIR/uninstall-state.py" "$INSTALL_DIR" --shell "$BASH"
}
