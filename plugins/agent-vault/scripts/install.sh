#!/usr/bin/env bash
# =============================================================================
# install.sh -- agent-vault -- plugin installer for Linux / WSL / macOS
# =============================================================================
# Manages the agent-vault runtime lifecycle: install, update, status, start,
# stop, uninstall. Runtime lives at ~/.agent-vault/ (venv + daemon state), the
# CLI binstub goes to ~/.local/bin/agent-vault, and the persistent daemon runs
# as a systemd user service when systemd is available.
# =============================================================================

set -euo pipefail

_ok()   { printf '  [OK]   %s\n' "$1"; }
_skip() { printf '  [SKIP] %s\n' "$1"; }
_fail() { printf '  [FAIL] %s\n' "$1" >&2; }
_warn() { printf '  [WARN] %s\n' "$1" >&2; }
_step() { printf '  ...    %s\n' "$1"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PKG_SRC_DIR="$PLUGIN_DIR/src/agent_vault"

ACTION="${1:-status}"
shift || true

NO_SERVICE=0
PURGE=0
INSTALL_DIR=""
FORCE="${AGENT_VAULT_ALLOW_DOWNGRADE:-0}"
[[ "$FORCE" == "1" ]] && FORCE=1 || FORCE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-service) NO_SERVICE=1; shift ;;
        --purge) PURGE=1; shift ;;
        --force) FORCE=1; shift ;;
        --install-dir) INSTALL_DIR="${2:?--install-dir requires a directory}"; shift 2 ;;
        *) _fail "Unknown option: $1"; exit 2 ;;
    esac
done

INSTALL_DIR="${INSTALL_DIR:-$HOME/.agent-vault}"
VENV_DIR="$INSTALL_DIR/.venv"
LOCAL_BIN="$HOME/.local/bin"
VENV_PYTHON="$VENV_DIR/bin/python"
STUB="$LOCAL_BIN/agent-vault"
ASKPASS="$LOCAL_BIN/vault-askpass"
SYSTEMD_UNIT="agent-vault.service"
UNIT_DIR="$HOME/.config/systemd/user"

# === install-contract:v3 source-kind -- keep byte-identical across plugins ===
_source_kind() {
    case "$(printf '%s' "$1" | tr '\\' '/')" in
        */.copilot/installed-plugins/*) printf 'marketplace' ;;
        *) printf 'local' ;;
    esac
}
# === end install-contract:v3 source-kind ===

_installed_version() {
    [[ -x "$VENV_PYTHON" ]] || return 1
    local v
    v="$("$VENV_PYTHON" -c \
        'from importlib.metadata import version; print(version("agent-vault"))' \
        2>/dev/null)" || return 1
    [[ -n "$v" ]] || return 1
    printf '%s\n' "$v"
}

_source_version() {
    local manifest="$PLUGIN_DIR/plugin.json"
    [[ -f "$manifest" ]] || return 1
    local v
    v="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$manifest" | head -n1)"
    [[ -n "$v" ]] || return 1
    printf '%s\n' "$v"
}

_version_lt() {
    local a="${1//-/.}" b="${2//-/.}"
    [[ "$a" == "$b" ]] && return 1
    local lower
    lower="$(printf '%s\n%s\n' "$a" "$b" | sort -V | head -n1)"
    [[ "$lower" == "$a" ]]
}

_downgrade_guard() {
    local installed source
    installed="$(_installed_version)" || return 0
    source="$(_source_version)" || {
        _warn "Could not read source version from plugin.json -- skipping downgrade guard"
        return 0
    }
    if _version_lt "$source" "$installed"; then
        if [[ "$FORCE" -eq 1 ]]; then
            _warn "Downgrade $installed -> $source forced (--force / AGENT_VAULT_ALLOW_DOWNGRADE)"
            return 0
        fi
        echo ""
        _fail "Refusing to downgrade agent-vault: installed $installed > source $source"
        _fail "Override intentionally (deliberate rollback):"
        _fail "    $0 $ACTION --force"
        echo ""
        exit 1
    fi
}

_find_python() {
    local candidate
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 \
           && "$candidate" --version 2>&1 | grep -qi python; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

_check_keepassxc() {
    if command -v keepassxc-cli >/dev/null 2>&1; then
        _ok 'Prerequisite: keepassxc-cli found'
    else
        _warn 'Prerequisite missing: keepassxc-cli (KeePassXC). agent-vault installed, but unlocks will fail until KeePassXC is present.'
    fi
}

_write_binstub() {
    mkdir -p "$LOCAL_BIN"
    cat > "$STUB" << EOF
#!/usr/bin/env bash
export PYTHONUTF8=1
exec "$VENV_PYTHON" -m agent_vault "\$@"
EOF
    chmod +x "$STUB"
    _ok "Binstub: $STUB"
}

_write_askpass() {
    mkdir -p "$LOCAL_BIN"
    cat > "$ASKPASS" << 'EOF'
#!/usr/bin/env bash
export VAULT_NONINTERACTIVE=1
exec "$HOME/.local/bin/agent-vault" get "${VAULT_SUDO_ENTRY:?set VAULT_SUDO_ENTRY to your sudo KeePass entry path}" password
EOF
    chmod +x "$ASKPASS"
    _ok "SUDO_ASKPASS helper: $ASKPASS"
    _step 'To enable sudo askpass, export SUDO_ASKPASS="$HOME/.local/bin/vault-askpass" and export VAULT_SUDO_ENTRY="<their entry>"'
}

_ensure_runtime() {
    if [[ ! -d "$PKG_SRC_DIR" ]]; then
        _fail "Package source not found at $PKG_SRC_DIR"
        exit 1
    fi
    local py have_uv=0
    py="$(_find_python)" || { _fail 'Python not found on PATH (need 3.10+)'; exit 1; }
    _ok "Python: $py"
    command -v uv >/dev/null 2>&1 && have_uv=1

    mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"
    _ok "Directories: $INSTALL_DIR"

    if [[ ! -x "$VENV_PYTHON" ]]; then
        if [[ "$have_uv" -eq 1 ]]; then
            _step 'Creating venv via uv...'
            uv venv "$VENV_DIR" --allow-existing >/dev/null 2>&1 \
                || "$py" -m venv "$VENV_DIR" >/dev/null 2>&1
        else
            _step 'Creating venv via python -m venv...'
            "$py" -m venv "$VENV_DIR" >/dev/null 2>&1
        fi
        [[ -x "$VENV_PYTHON" ]] || { _fail "Venv creation failed -- $VENV_PYTHON not found"; exit 1; }
        _ok 'Venv created'
    else
        _skip 'Venv already exists'
    fi

    if [[ "$have_uv" -eq 1 ]]; then
        uv pip install --python "$VENV_PYTHON" "$PLUGIN_DIR" --quiet 2>/dev/null \
            || { _fail 'Failed to install agent-vault package into venv'; exit 1; }
    else
        "$VENV_PYTHON" -m pip install --quiet "$PLUGIN_DIR" 2>/dev/null \
            || { _fail 'Failed to install agent-vault package into venv'; exit 1; }
    fi
    _ok 'Package installed: agent-vault'

    _write_binstub
    _write_askpass
    _write_manifest
    _check_keepassxc

    if "$VENV_PYTHON" -c 'import agent_vault' 2>/dev/null; then
        _ok 'Verification: module imports successfully'
    else
        _fail 'Verification: module import failed'
        exit 1
    fi

    case ":$PATH:" in
        *":$LOCAL_BIN:"*) _ok "PATH: $LOCAL_BIN is on PATH" ;;
        *) _step "Add $LOCAL_BIN to your PATH: export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
    esac
}

_write_manifest() {
    _git_info() {
        local path="$1" commit branch dirty
        commit=$(git -C "$path" rev-parse --short HEAD 2>/dev/null || echo "unknown")
        branch=$(git -C "$path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
        dirty="false"
        [[ -n "$(git -C "$path" status --porcelain 2>/dev/null)" ]] && dirty="true"
        echo "$commit $branch $dirty"
    }
    local manifest="$INSTALL_DIR/deploy-manifest.json"
    local kind ver commit branch dirty
    kind="$(_source_kind "$PLUGIN_DIR")"
    ver="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/pyproject.toml" 2>/dev/null | head -n1)"
    [[ -n "$ver" ]] || ver="$(_source_version 2>/dev/null || echo 0.0.0)"
    commit="null"; branch="null"; dirty="false"
    if [[ "$kind" == "local" ]]; then
        local repo_root _c _b _d
        repo_root="$(cd "$PLUGIN_DIR/../.." && pwd)"
        read -r _c _b _d <<< "$(_git_info "$repo_root")"
        commit="\"$_c\""; branch="\"$_b\""; dirty="$_d"
    fi
    local tmp="$manifest.tmp"
    cat > "$tmp" << EOF
{
  "schema_version": 3,
  "service": "agent-vault",
  "deployed_at": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "deployed_by": "$(hostname)-$(uname -s | tr '[:upper:]' '[:lower:]')",
  "source": {
    "kind": "$kind",
    "path": "$PLUGIN_DIR",
    "repo": "copilot-extensions",
    "plugin": "agent-vault",
    "version": "$ver",
    "commit": $commit,
    "branch": $branch,
    "dirty": $dirty
  },
  "venv": "$VENV_DIR",
  "runtime": "python"
}
EOF
    mv -f "$tmp" "$manifest"
    _ok "Deploy manifest written (source: $kind)"
}

_install_service() {
    if [[ "$NO_SERVICE" -eq 1 ]]; then
        _skip "agent-vault service skipped (--no-service): this host is a client only"
        return 0
    fi
    if ! command -v systemctl >/dev/null 2>&1; then
        _skip "systemd not available -- the CLI can cold-start the daemon on demand"
        return 0
    fi
    mkdir -p "$UNIT_DIR"
    cat > "$UNIT_DIR/$SYSTEMD_UNIT" << EOF
[Unit]
Description=agent-vault -- local KeePassXC-backed secret store
After=default.target

[Service]
Type=simple
Environment=PYTHONUTF8=1
ExecStart=$VENV_PYTHON -m agent_vault.service --foreground --persistent
Restart=on-failure
RestartSec=5
WorkingDirectory=$INSTALL_DIR

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable "$SYSTEMD_UNIT" 2>/dev/null || true
    systemctl --user restart "$SYSTEMD_UNIT" 2>/dev/null || true
    if systemctl --user is-active "$SYSTEMD_UNIT" >/dev/null 2>&1; then
        _ok "agent-vault service installed + started ($SYSTEMD_UNIT)"
    else
        _warn "agent-vault service installed but not active -- check: systemctl --user status $SYSTEMD_UNIT"
    fi
}

do_install() {
    echo ''; echo '=== agent-vault install ==='; echo ''
    _ensure_runtime
    _install_service
    echo ''; echo '=== agent-vault install complete ==='
}

do_update() {
    echo ''; echo '=== agent-vault update ==='; echo ''
    _downgrade_guard
    _ensure_runtime
    _install_service
    echo ''; echo '=== agent-vault update complete ==='
}

do_start() {
    command -v systemctl >/dev/null 2>&1 || { _fail 'systemd not available'; exit 1; }
    if [[ ! -f "$UNIT_DIR/$SYSTEMD_UNIT" ]]; then
        _fail "No service unit installed -- run: $0 install"
        exit 1
    fi
    systemctl --user start "$SYSTEMD_UNIT"
    systemctl --user is-active "$SYSTEMD_UNIT" >/dev/null 2>&1 \
        && _ok "agent-vault service started" || { _fail "Failed to start agent-vault service"; exit 1; }
}

do_stop() {
    command -v systemctl >/dev/null 2>&1 || { _fail 'systemd not available'; exit 1; }
    if systemctl --user is-active "$SYSTEMD_UNIT" >/dev/null 2>&1; then
        systemctl --user stop "$SYSTEMD_UNIT" 2>/dev/null || true
        _ok "agent-vault service stopped"
    else
        _skip "agent-vault service not running"
    fi
}

do_status() {
    echo ''; echo '=== agent-vault status ==='
    if [[ -f "$INSTALL_DIR/deploy-manifest.json" ]]; then
        local kind ver
        kind=$(grep -o '"kind": *"[^"]*"' "$INSTALL_DIR/deploy-manifest.json" | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
        ver=$(grep -o '"version": *"[^"]*"' "$INSTALL_DIR/deploy-manifest.json" | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
        _ok "Deployed: $ver (source: $kind)"
    else
        _skip "No deploy manifest -- not installed?"
    fi
    [[ -x "$STUB" ]] && _ok "Binstub: $STUB" || _skip "No binstub at $STUB"
    [[ -x "$ASKPASS" ]] && _ok "SUDO_ASKPASS helper: $ASKPASS" || _skip "No SUDO_ASKPASS helper at $ASKPASS"
    _check_keepassxc
    if command -v systemctl >/dev/null 2>&1 && [[ -f "$UNIT_DIR/$SYSTEMD_UNIT" ]]; then
        local state enabled
        state=$(systemctl --user is-active "$SYSTEMD_UNIT" 2>/dev/null || echo inactive)
        enabled=$(systemctl --user is-enabled "$SYSTEMD_UNIT" 2>/dev/null || echo disabled)
        _ok "Service: $state ($enabled)"
    else
        _skip "No systemd user service (client-only host, or systemd unavailable)"
    fi
}

do_uninstall() {
    echo ''; echo '=== agent-vault uninstall ==='; echo ''
    if command -v systemctl >/dev/null 2>&1; then
        systemctl --user stop "$SYSTEMD_UNIT" 2>/dev/null || true
        systemctl --user disable "$SYSTEMD_UNIT" 2>/dev/null || true
        rm -f "$UNIT_DIR/$SYSTEMD_UNIT"
        systemctl --user daemon-reload 2>/dev/null || true
        _ok "Service removed"
    fi
    rm -f "$STUB"; _ok "Binstub removed"
    rm -f "$ASKPASS"; _ok "SUDO_ASKPASS helper removed"
    if [[ "$PURGE" -eq 1 ]]; then
        rm -rf "$INSTALL_DIR"; _ok "Runtime purged: $INSTALL_DIR"
    else
        rm -rf "$VENV_DIR"; _ok "Venv removed (state kept; --purge to delete)"
    fi
}

case "$ACTION" in
    install)   do_install ;;
    update)    do_update ;;
    start)     do_start ;;
    stop)      do_stop ;;
    status)    do_status ;;
    uninstall) do_uninstall ;;
    *) _fail "Unknown action: $ACTION (use: install|update|status|start|stop|uninstall)"; exit 2 ;;
esac
exit 0
