_finite_runtime_uninstall() {
    local mode="$1" python="" candidate helper="$SCRIPT_DIR/installer_engine_uninstall.py"
    [[ -f "$helper" && ! -L "$helper" ]] || helper="$SCRIPT_DIR/../../../libs/installer-engine/installer_engine_uninstall.py"
    for candidate in python3 python; do
        python="$(command -v "$candidate")" || continue
        case "$python" in "$INSTALL_DIR"/*) python=""; continue ;; esac
        break
    done
    [[ -n "$python" ]] || { _fail "Runtime uninstall requires an external metadata Python"; return 1; }
    "$python" -I -B "$helper" --payload-root "$PLUGIN_DIR" --plugin-id budget-guidance \
        --root "$INSTALL_DIR" --shell "$BASH" "$mode"
}
