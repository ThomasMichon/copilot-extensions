#!/usr/bin/env bash
# shellcheck shell=bash
_uninstall_python() {
    local candidate selected probe
    # Use the source-selector PATH strategy, not construction's installed slot.
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
    while IFS= read -r candidate; do
        [[ "$candidate" != *WindowsApps* ]] || continue
        selected="$("$candidate" -I -B -c "$probe" "$INSTALL_DIR" "$candidate" 2>/dev/null)" || continue
        [[ -n "$selected" && -x "$selected" ]] || continue
        printf '%s' "$selected"
        return 0
    done < <(type -aP python3 python 2>/dev/null)
    echo 'Uninstall requires an existing external Python outside runtime and prefix closure; nothing removed' >&2
    return 1
}

uninstall_state_preserving_runtime() {
    local preview="${1:-0}" py
    local arguments=()
    py="$(_uninstall_python)" || return 1
    [[ "$preview" != 1 ]] || arguments+=(--preview)
    if [[ "${2:-}" == validate ]]; then
        "$py" -I -B "$SCRIPT_DIR/uninstall-state.py" "$INSTALL_DIR" --shell "$BASH" --preview >/dev/null
        return
    fi
    "$py" -I -B "$SCRIPT_DIR/uninstall-state.py" "$INSTALL_DIR" --shell "$BASH" "${arguments[@]}"
}
