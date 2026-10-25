_initialize_source_runtime() {
    PACKAGE_VERSION="$SRC_VERSION"
    SOURCE_RUNTIME_IDENTITY=""
    SOURCE_RUNTIME_SNAPSHOT=""
    [[ -n "${COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN+x}" || -e "$PLUGIN_DIR/runtime-source.json" || -L "$PLUGIN_DIR/runtime-source.json" ]] || return 0
    local py
    py="$(_bootstrap_python)" || return 1
    SOURCE_RUNTIME_IDENTITY="$("$py" -I -B "$SCRIPT_DIR/versioned_runtime_source.py" \
        --payload "$PLUGIN_DIR" --package-version "$PACKAGE_VERSION" --plugin agent-vault --format json)" || return 1
    SRC_VERSION="$("$py" -I -c 'import json,sys; print(json.loads(sys.argv[1])["runtime_version"])' "$SOURCE_RUNTIME_IDENTITY")" || return 1
    VENV_DIR="$INSTALL_DIR/versions/$SRC_VERSION"
    VENV_PYTHON="$VENV_DIR/bin/python"
    LINK_PYTHON="$VENV_PYTHON"
}

_assert_source_runtime() {
    if [[ -n "${SOURCE_RUNTIME_IDENTITY:-}" || -e "$PLUGIN_DIR/runtime-source.json" || -L "$PLUGIN_DIR/runtime-source.json" ]]; then
        local py identity
        py="$(_bootstrap_python)" || return 1
        identity="$("$py" -I -B "$SCRIPT_DIR/versioned_runtime_source.py" \
            --payload "$PLUGIN_DIR" --package-version "$PACKAGE_VERSION" --plugin agent-vault \
            --expected-runtime-version "$SRC_VERSION" --format json)" || return 1
        if [[ "$identity" != "${SOURCE_RUNTIME_IDENTITY:-}" ]]; then
            _fail "Source runtime identity changed after runtime selection; refusing publication"
            return 1
        fi
        local marker
        for marker in payload-dir stamped-version; do
            if [[ -d "$INSTALL_DIR/$marker" || -L "$INSTALL_DIR/$marker" ]]; then
                _fail "Source redirect must be an ordinary file: $INSTALL_DIR/$marker"
                return 1
            fi
        done
    fi
}

_publish_source_snapshot() {
    _assert_source_runtime || return 1
    local py snapshot
    py="$(_bootstrap_python)" || return 1
    snapshot="$("$py" -I -B "$SCRIPT_DIR/versioned_runtime_source.py" \
        --payload "$PLUGIN_DIR" --package-version "$PACKAGE_VERSION" --plugin agent-vault \
        --expected-runtime-version "$SRC_VERSION" \
        --expected-source-identity "$SOURCE_RUNTIME_IDENTITY" \
        --snapshot-root "$INSTALL_DIR")" || return 1
    _assert_source_runtime || return 1
    printf '%s\n' "$snapshot"
}

_stamp_source_payload() {
    local snapshot
    snapshot="$(_publish_source_snapshot)" || return 1
    local py
    py="$(_bootstrap_python)" || return 1
    _prepare_source_manifest_context || return 1
    _assert_source_runtime || return 1
    "$py" -I -B "$SCRIPT_DIR/versioned_runtime.py" --root "$INSTALL_DIR" \
        publish-redirects --payload-dir "$snapshot" --version "$SRC_VERSION" \
        --expected-source-identity "$SOURCE_RUNTIME_IDENTITY" || return 1
    SOURCE_RUNTIME_SNAPSHOT="$snapshot"
}

_prepare_source_manifest_context() {
    SOURCE_MANIFEST_ORIGIN="${COPILOT_PLUGIN_STAGED_FROM:-$PLUGIN_DIR}"
    SOURCE_MANIFEST_GIT_INFO=""
    case "${SOURCE_MANIFEST_ORIGIN//\\//}" in
        */.copilot/installed-plugins/*) ;;
        *) SOURCE_MANIFEST_GIT_INFO="$(_git_info "$(dirname "$SOURCE_MANIFEST_ORIGIN")")" || return 1 ;;
    esac
}
