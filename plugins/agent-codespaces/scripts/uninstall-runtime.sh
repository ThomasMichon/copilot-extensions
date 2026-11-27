#!/usr/bin/env bash
# shellcheck shell=bash
_uninstall_python() {
    local candidate selected probe support="$SCRIPT_DIR" capture
    if grep -Fqx '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"' "$SCRIPT_DIR/install.sh"; then
        support="$(CDPATH= cd -- "$SCRIPT_DIR/../../../libs/installer-engine" && pwd -P)" || return 1
    fi
    capture="$SCRIPT_DIR/native_capture.sh"
    [[ -f "$capture" ]] ||
        capture="$SCRIPT_DIR/../../../libs/agent-procutil/src/agent_procutil/native_capture.sh"
    [[ -f "$support/bootstrap-uv.sh" && -f "$support/installer-instance-python-version.txt" &&
        -f "$capture" ]] || {
        echo 'Prepared uninstall bootstrap support is unavailable; restore the owning payload.' >&2
        return 1
    }
    . "$capture" || return 1
    candidate="$(
        unset BASH_ENV ENV
        # Transaction settlement belongs to the installer, not this capture child.
        trap - EXIT
        exec 5>&- 6>&- 7>&- 8>&- 9>&-
        if capture_native_process 30 "$BASH" --noprofile --norc -c '
            . "$1/bootstrap-uv.sh" || exit
            resolve_uv_bootstrap_python "$1/installer-instance-python-version.txt" Inspect
        ' codespaces-uninstall-bootstrap "$support"; then
            printf '%s' "$__native_capture_output"
        else
            exit $?
        fi
    )" || return 1
    probe='import sys
from pathlib import Path
assert sys.version_info >= (3, 10)
root = Path(sys.argv[1]).absolute()
assert ".." not in root.parts
roots = (root, root.resolve())
paths = (sys.argv[2], sys.executable, sys.prefix, sys.base_prefix,
         getattr(sys, "_base_executable", sys.executable))
for value in paths:
    path = Path(value).absolute()
    for actual in (path, path.resolve(strict=True)):
        assert all(not actual.is_relative_to(boundary) for boundary in roots)
sys.stdout.write(str(Path(sys.executable).resolve(strict=True)))'
    case "$candidate" in "$INSTALL_DIR"/*|*WindowsApps*) return 1 ;; esac
    selected="$("$candidate" -I -B -c "$probe" "$INSTALL_DIR" "$candidate" 2>/dev/null)" || return 1
    [[ -n "$selected" && -x "$selected" ]] || return 1
    printf '%s' "$selected"
}

uninstall_state_preserving_runtime() {
    local preview="${1:-0}" py
    local arguments=()
    py="$(_uninstall_python)" || return 1
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
