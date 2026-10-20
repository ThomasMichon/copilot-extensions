_initialize_source_runtime() {
    PACKAGE_VERSION="$SRC_VERSION"
    SOURCE_RUNTIME_IDENTITY=""
    [[ -e "$PLUGIN_DIR/runtime-source.json" || -L "$PLUGIN_DIR/runtime-source.json" ]] || return 0
    local py
    py="$(_bootstrap_python)" || return 1
    SOURCE_RUNTIME_IDENTITY="$("$py" -I -B "$SCRIPT_DIR/versioned_runtime_source.py" \
        --payload "$PLUGIN_DIR" --package-version "$PACKAGE_VERSION" --plugin agent-index --format json)" || return 1
    SRC_VERSION="$("$py" -I -c 'import json,sys; print(json.loads(sys.argv[1])["runtime_version"])' "$SOURCE_RUNTIME_IDENTITY")" || return 1
    VENV_DIR="$INSTALL_DIR/versions/$SRC_VERSION"
    VENV_PYTHON="$VENV_DIR/bin/python"
    LINK_PYTHON="$VENV_PYTHON"
}

_assert_source_runtime() {
    [[ -n "${SOURCE_RUNTIME_IDENTITY:-}${PLUGIN_DIR:-}" ]] || return 0
    if [[ -n "${SOURCE_RUNTIME_IDENTITY:-}" || -e "$PLUGIN_DIR/runtime-source.json" || -L "$PLUGIN_DIR/runtime-source.json" ]]; then
        local py identity
        py="$(_bootstrap_python)" || return 1
        identity="$("$py" -I -B "$SCRIPT_DIR/versioned_runtime_source.py" \
            --payload "$PLUGIN_DIR" --package-version "$PACKAGE_VERSION" --plugin agent-index \
            --expected-runtime-version "$SRC_VERSION" --format json)" || return 1
        if [[ "$identity" != "${SOURCE_RUNTIME_IDENTITY:-}" ]]; then
            _fail "Source runtime identity changed after runtime selection; refusing publication"
            return 1
        fi
    fi
}

_source_snapshot() {
    _assert_source_runtime || return 1
    local py snap
    py="$(_bootstrap_python)" || return 1
    snap="$("$py" -I -B "$SCRIPT_DIR/versioned_runtime_source.py" \
        --payload "$PLUGIN_DIR" --package-version "$PACKAGE_VERSION" --plugin agent-index \
        --expected-runtime-version "$SRC_VERSION" \
        --expected-source-identity "$SOURCE_RUNTIME_IDENTITY" \
        --snapshot-root "$INSTALL_DIR")" || return 1
    _assert_source_runtime || return 1
    printf '%s\n' "$snap"
}

_publish_source_redirects() {
    _assert_source_runtime || return 1
    local py receipt_file
    local receipt_args=()
    py="$(_bootstrap_python)" || return 1
    if [[ -n "${_RUNTIME_PUBLICATION_EVIDENCE:-}" ]]; then
        _runtime_publication_expect_value "$INSTALL_DIR/payload-dir" "$1"$'\n' || return 1
        _runtime_publication_expect_value "$INSTALL_DIR/stamped-version" "$SRC_VERSION"$'\n' || return 1
        _runtime_publication_native_begin receipt_file || return 1
        receipt_args=(--ownership-receipt "$receipt_file")
    fi
    "$py" -I -B "$SCRIPT_DIR/versioned_runtime.py" --root "$INSTALL_DIR" \
        publish-redirects --payload-dir "$1" --version "$SRC_VERSION" \
        --expected-source-identity "$SOURCE_RUNTIME_IDENTITY" "${receipt_args[@]}" || return 1
    if [[ -n "${_RUNTIME_PUBLICATION_EVIDENCE:-}" ]]; then
        _runtime_publication_accept "$INSTALL_DIR/payload-dir" || return 1
        _runtime_publication_accept "$INSTALL_DIR/stamped-version" || return 1
    fi
}

_runtime_publication_begin_install() {
    _runtime_publication_begin \
        "$INSTALL_DIR/bin/resolve-runtime.sh" "$INSTALL_DIR/bin/resolve-runtime.ps1" \
        "${STUB:-$LOCAL_BIN/agent-index}" "$INSTALL_DIR/payload-dir" "$INSTALL_DIR/stamped-version" \
        "$INSTALL_DIR/deploy-manifest.json" "$INSTALL_DIR/last-known-good" "$INSTALL_DIR/current-version"
}
