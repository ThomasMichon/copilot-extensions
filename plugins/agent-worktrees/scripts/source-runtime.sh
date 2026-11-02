_source_runtime_identity() {
    local payload="${1:-$PLUGIN_DIR}" expected="${2:-}" py
    py="$(_bootstrap_python exclude-runtime-root)" || return 1
    local args=(-I -B "$SCRIPT_DIR/versioned_runtime_source.py"
        --payload "$payload" --package-version "$PACKAGE_VERSION"
        --plugin agent-worktrees --format json)
    [[ -z "$expected" ]] || args+=(--expected-runtime-version "$expected")
    "$py" "${args[@]}"
}

_initialize_source_runtime() {
    PACKAGE_VERSION="$SRC_VERSION"
    SOURCE_RUNTIME_IDENTITY=""
    SOURCE_RUNTIME_MANIFEST=""
    [[ -n "${COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN+x}" || -e "$PLUGIN_DIR/runtime-source.json" || -L "$PLUGIN_DIR/runtime-source.json" ]] || return 0
    SOURCE_RUNTIME_IDENTITY="$(_source_runtime_identity)" || return 1
    local py
    py="$(_bootstrap_python exclude-runtime-root)" || return 1
    SRC_VERSION="$("$py" -I -c 'import json,sys; print(json.loads(sys.argv[1])["runtime_version"])' "$SOURCE_RUNTIME_IDENTITY")" || return 1
    VENV_DIR="$INSTALL_DIR/versions/$SRC_VERSION"
    VENV_PYTHON="$VENV_DIR/bin/python"
    VENV_BIN="$VENV_DIR/bin/agent-worktrees"
    LINK_PYTHON="$VENV_PYTHON"
}

_assert_source_runtime() {
    local payload="${1:-$PLUGIN_DIR}" identity
    if [[ -n "${SOURCE_RUNTIME_IDENTITY:-}" || -e "$payload/runtime-source.json" || -L "$payload/runtime-source.json" ]]; then
        identity="$(_source_runtime_identity "$payload" "$SRC_VERSION")" || return 1
        if [[ "$identity" != "${SOURCE_RUNTIME_IDENTITY:-}" ]]; then
            err "Source runtime identity changed after runtime selection; refusing publication"
            return 1
        fi
        local marker
        for marker in payload-dir stamped-version; do
            if [[ -L "$INSTALL_DIR/$marker" || ( -e "$INSTALL_DIR/$marker" && ! -f "$INSTALL_DIR/$marker" ) ]]; then
                err "Source redirect must be an ordinary file: $INSTALL_DIR/$marker"
                return 1
            fi
        done
    fi
}

_stamp_source_snapshot() {
    _assert_source_runtime || return 1
    local py snap
    py="$(_bootstrap_python exclude-runtime-root)" || return 1
    snap="$("$py" -I -B "$SCRIPT_DIR/versioned_runtime_source.py" \
        --payload "$PLUGIN_DIR" --package-version "$PACKAGE_VERSION" --plugin agent-worktrees \
        --expected-runtime-version "$SRC_VERSION" \
        --expected-source-identity "$SOURCE_RUNTIME_IDENTITY" \
        --snapshot-root "$INSTALL_DIR")" || return 1
    _assert_source_runtime || return 1
    "$py" -I -B "$SCRIPT_DIR/versioned_runtime.py" --root "$INSTALL_DIR" \
        publish-redirects --payload-dir "$snap" --version "$SRC_VERSION" \
        --expected-source-identity "$SOURCE_RUNTIME_IDENTITY" || return 1
}
