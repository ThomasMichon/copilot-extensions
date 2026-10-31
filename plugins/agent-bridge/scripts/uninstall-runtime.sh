_uninstall_external_python() {
    local root="${1:?runtime root required}" candidate selected probe
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
        case "$candidate" in "$root"/*|*WindowsApps*) continue ;; esac
        case "${OSTYPE:-}" in
            msys*|cygwin*) [[ "$candidate" == *.exe || ! -f "$candidate.exe" ]] || candidate="$candidate.exe" ;;
        esac
        selected="$("$candidate" -I -B -c "$probe" "$root" "$candidate" 2>/dev/null)" || continue
        [[ -n "$selected" && -x "$selected" ]] || continue
        printf '%s' "$selected"
        return 0
    done < <(type -aP python3 python 2>/dev/null)
    _fail "Uninstall requires an existing external Python outside runtime and prefix closure" >&2
    return 1
}

_finite_runtime_uninstall() {
    local mode="$1" python helper="$SCRIPT_DIR/installer_engine_uninstall.py"
    [[ -f "$helper" && ! -L "$helper" ]] || helper="$SCRIPT_DIR/../../../libs/installer-engine/installer_engine_uninstall.py"
    python="$(_uninstall_external_python "$INSTALL_DIR")" || return 1
    "$python" -I -B "$helper" --payload-root "$PLUGIN_DIR" --plugin-id agent-bridge \
        --root "$INSTALL_DIR" --shell "$BASH" "$mode"
}
