_finite_runtime_uninstall() {
    local mode="$1" python="" candidate helper="$SCRIPT_DIR/installer_engine_uninstall.py"
    [[ -f "$helper" && ! -L "$helper" ]] || helper="$SCRIPT_DIR/../../../libs/installer-engine/installer_engine_uninstall.py"
    for candidate in python3 python; do
        python="$(command -v "$candidate")" || continue
        case "$python" in "$INSTALL_DIR"/*) python=""; continue ;; esac
        break
    done
    [[ -n "$python" ]] || { _fail "Runtime uninstall requires an external metadata Python"; return 1; }
    "$python" -I -B "$helper" --payload-root "$PLUGIN_DIR" --plugin-id agent-dispatch \
        --root "$INSTALL_DIR" --shell "$BASH" "$mode"
}

_finite_stop_tool() {
    local python="" candidate helper="$SCRIPT_DIR/installer_engine_stop.py"
    [[ -f "$helper" && ! -L "$helper" ]] || helper="$SCRIPT_DIR/../../../libs/installer-engine/installer_engine_stop.py"
    for candidate in python3 python; do
        python="$(command -v "$candidate")" || continue
        case "$python" in "$INSTALL_DIR"/*) python=""; continue ;; esac
        break
    done
    [[ -n "$python" ]] || { _fail "Native STOP requires an external metadata Python"; return 1; }
    "$python" -I -B "$helper" "$@" --root "$INSTALL_DIR" --plugin agent-dispatch
}

_finite_owned_pids() {
    _finite_stop_admission="$(_finite_stop_tool capture)" || return 1
    _finite_stop_tool pids --admission "$_finite_stop_admission"
}

_finite_stop_owned() {
    if [[ -z "${_finite_stop_admission:-}" ]]; then
        _finite_owned_pids >/dev/null || return 1
    fi
    _finite_stop_tool stop --admission "$_finite_stop_admission"
}
