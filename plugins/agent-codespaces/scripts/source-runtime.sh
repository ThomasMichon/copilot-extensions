_initialize_source_runtime() {
    PACKAGE_VERSION="$SRC_VERSION"
    SOURCE_RUNTIME_IDENTITY=""
    [[ -e "$PLUGIN_DIR/runtime-source.json" || -L "$PLUGIN_DIR/runtime-source.json" ]] || return 0
    local py
    py="$(_bootstrap_python)" || return 1
    SOURCE_RUNTIME_IDENTITY="$("$py" -I -B "$SCRIPT_DIR/versioned_runtime_source.py" \
        --payload "$PLUGIN_DIR" --package-version "$PACKAGE_VERSION" --plugin agent-codespaces --format json)" || return 1
    SRC_VERSION="$("$py" -I -c 'import json,sys; print(json.loads(sys.argv[1])["runtime_version"])' "$SOURCE_RUNTIME_IDENTITY")" || return 1
    VENV_DIR="$INSTALL_DIR/versions/$SRC_VERSION"
    VENV_PYTHON="$VENV_DIR/bin/python"
    LINK_PYTHON="$VENV_PYTHON"
    VENV_BIN="$VENV_DIR/bin/agent-codespaces"
}

_assert_source_runtime() {
    if [[ -n "${SOURCE_RUNTIME_IDENTITY:-}" || -e "$PLUGIN_DIR/runtime-source.json" || -L "$PLUGIN_DIR/runtime-source.json" ]]; then
        local py identity
        py="$(_bootstrap_python)" || return 1
        identity="$("$py" -I -B "$SCRIPT_DIR/versioned_runtime_source.py" \
            --payload "$PLUGIN_DIR" --package-version "$PACKAGE_VERSION" --plugin agent-codespaces \
            --expected-runtime-version "$SRC_VERSION" --format json)" || return 1
        if [[ "$identity" != "${SOURCE_RUNTIME_IDENTITY:-}" ]]; then
            _fail "Source runtime identity changed after runtime selection; refusing publication"
            return 1
        fi
    fi
}

_stamp_source_snapshot() {
    _assert_source_runtime || return 1
    local snap="$INSTALL_DIR/snapshots/$SRC_VERSION" stage="$INSTALL_DIR/snapshots/$SRC_VERSION.tmp-$$"
    local py identity
    mkdir -p "$stage" || return 1
    cp -a "$PLUGIN_DIR/." "$stage/" || return 1
    py="$(_bootstrap_python)" || return 1
    identity="$("$py" -I -B "$SCRIPT_DIR/versioned_runtime_source.py" \
        --payload "$stage" --package-version "$PACKAGE_VERSION" --plugin agent-codespaces \
        --expected-runtime-version "$SRC_VERSION" --format json)" || return 1
    if [[ "$identity" != "$SOURCE_RUNTIME_IDENTITY" ]]; then
        _fail "Stamped source snapshot identity differs from the selected source; redirects were not published"
        return 1
    fi
    _assert_source_runtime || return 1
    if [[ -e "$snap" ]]; then
        identity="$("$py" -I -B "$SCRIPT_DIR/versioned_runtime_source.py" \
            --payload "$snap" --package-version "$PACKAGE_VERSION" --plugin agent-codespaces \
            --expected-runtime-version "$SRC_VERSION" --format json)" || return 1
        if [[ "$identity" != "$SOURCE_RUNTIME_IDENTITY" ]]; then
            _fail "Existing source snapshot identity differs from the selected source; redirects were not published"
            return 1
        fi
        rm -rf "$stage" || return 1
    else
        mv "$stage" "$snap" || return 1
    fi
    _assert_source_runtime || return 1
    printf '%s\n' "$snap" > "$INSTALL_DIR/payload-dir"
    printf '%s\n' "$SRC_VERSION" > "$INSTALL_DIR/stamped-version"
}
