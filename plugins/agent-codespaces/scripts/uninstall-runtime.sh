#!/usr/bin/env bash
# shellcheck shell=bash
uninstall_state_preserving_runtime() {
    local preview="${1:-0}" py
    local arguments=()
    py="$(_bootstrap_python)" || return 1
    [[ -n "$py" ]] || { echo 'Uninstall requires an existing bootstrap Python' >&2; return 1; }
    if [[ -e "$HOME/.config/systemd/user/agent-codespaces-owner.service" ||
          -L "$HOME/.config/systemd/user/agent-codespaces-owner.service" ]]; then
        echo 'Uninstall refuses Connection Owner service custody; runtime and transports left intact' >&2
        return 1
    fi
    [[ "$preview" != 1 ]] || arguments+=(--preview)
    if [[ "${2:-}" == validate ]]; then
        "$py" -I -B "$SCRIPT_DIR/uninstall-state.py" "$INSTALL_DIR" --shell "$BASH" --preview >/dev/null
        return
    fi
    "$py" -I -B "$SCRIPT_DIR/uninstall-state.py" "$INSTALL_DIR" --shell "$BASH" "${arguments[@]}"
}
