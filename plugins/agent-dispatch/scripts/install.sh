#!/usr/bin/env bash
# =============================================================================
# install.sh -- agent-dispatch -- plugin installer for Linux / WSL / macOS
# =============================================================================
# Manages the agent-dispatch coordinator lifecycle: install, update, status,
# start, stop, uninstall -- the same shape as the agent-bridge installer, so
# the agent-worktrees plugin reconciler (runtimeScope: machine-gated) and the
# facility `aperture-labs services agent-dispatch <action>` path both drive it.
#
# Runtime lives at ~/.agent-dispatch/ (venv, config, DB). Binstub goes to
# ~/.local/bin/agent-dispatch. On its deploy machines the coordinator runs as a
# systemd **user** service (loopback 127.0.0.1:9847) -- a per-host local
# coordinator, matching agent-bridge's per-host service model.
#
# Usage:
#   bash scripts/install.sh install        # venv + binstub + service + pivot
#   bash scripts/install.sh update         # idempotent refresh (downgrade-guarded)
#   bash scripts/install.sh status
#   bash scripts/install.sh start | stop
#   bash scripts/install.sh uninstall [--purge]
#
# Options:
#   --no-service       Install/update the client (venv + binstub) but do NOT
#                      install/start the coordinator service (client-only host).
#   --purge            On uninstall: also delete config, DB, and env file.
#   --force            On update: bypass the downgrade guard (deliberate
#                      rollback). Env: AGENT_DISPATCH_ALLOW_DOWNGRADE=1.
#   --install-dir DIR  Override the runtime root (default ~/.agent-dispatch).
# =============================================================================

set -euo pipefail

_ok()   { printf '  [OK]   %s\n' "$1"; }
_skip() { printf '  [SKIP] %s\n' "$1"; }
_fail() { printf '  [FAIL] %s\n' "$1" >&2; }
_warn() { printf '  [WARN] %s\n' "$1" >&2; }
_step() { printf '  ...    %s\n' "$1"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PKG_SRC_DIR="$PLUGIN_DIR/src/agent_dispatch"

# -- Parse arguments ---------------------------------------------------------
ACTION="${1:-status}"
shift || true

NO_SERVICE=0
PURGE=0
INSTALL_DIR=""
FORCE="${AGENT_DISPATCH_ALLOW_DOWNGRADE:-0}"
[[ "$FORCE" == "1" ]] && FORCE=1 || FORCE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-service) NO_SERVICE=1; shift ;;
        --purge) PURGE=1; shift ;;
        --force) FORCE=1; shift ;;
        --install-dir) INSTALL_DIR="$2"; shift 2 ;;
        *) shift ;;
    esac
done

INSTALL_DIR="${INSTALL_DIR:-$HOME/.agent-dispatch}"
VENV_DIR="$INSTALL_DIR/.venv"
LOCAL_BIN="$HOME/.local/bin"
VENV_PYTHON="$VENV_DIR/bin/python"
STUB="$LOCAL_BIN/agent-dispatch"
SYSTEMD_UNIT="agent-dispatch.service"
UNIT_DIR="$HOME/.config/systemd/user"
ENV_FILE="$INSTALL_DIR/service.env"

# === install-contract:v3 source-kind -- keep byte-identical across plugins ===
_source_kind() {
    case "$(printf '%s' "$1" | tr '\\' '/')" in
        */.copilot/installed-plugins/*) printf 'marketplace' ;;
        *) printf 'local' ;;
    esac
}
# === end install-contract:v3 source-kind ===

# -- Version helpers + downgrade guard (parity with agent-bridge #1790) ------
_installed_version() {
    [[ -x "$VENV_PYTHON" ]] || return 1
    local v
    v="$("$VENV_PYTHON" -c \
        'from importlib.metadata import version; print(version("agent-dispatch"))' \
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

# True (0) if version $1 is strictly older than $2. Normalizes the PEP 440 dev
# separator (plugin.json `0.1.0-dev19` vs importlib `0.1.0.dev19`) so `sort -V`
# orders the devN build stream correctly.
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
            _warn "Downgrade $installed -> $source forced (--force / AGENT_DISPATCH_ALLOW_DOWNGRADE)"
            return 0
        fi
        echo ""
        _fail "Refusing to downgrade agent-dispatch: installed $installed > source $source"
        _fail "This checkout is OLDER than the deployed runtime. Use the sanctioned path:"
        _fail "    aperture-labs services agent-dispatch update"
        _fail "Or override intentionally (deliberate rollback):"
        _fail "    $0 $ACTION --force"
        echo ""
        exit 1
    fi
}

# -- Python + package -------------------------------------------------------
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

_ensure_runtime() {
    if [[ ! -d "$PKG_SRC_DIR" ]]; then
        _fail "Package source not found at $PKG_SRC_DIR"
        exit 1
    fi
    local py
    py="$(_find_python)" || { _fail 'Python not found on PATH (need 3.10+)'; exit 1; }
    _ok "Python: $py"
    local have_uv=0
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

    # The [mcp] extra ships the `agent-dispatch mcp` stdio server dependency.
    if [[ "$have_uv" -eq 1 ]]; then
        uv pip install --python "$VENV_PYTHON" "${PLUGIN_DIR}[mcp]" --quiet 2>/dev/null \
            || { _fail 'Failed to install agent-dispatch package into venv'; exit 1; }
    else
        "$VENV_PYTHON" -m pip install --quiet "${PLUGIN_DIR}[mcp]" 2>/dev/null \
            || { _fail 'Failed to install agent-dispatch package into venv'; exit 1; }
    fi
    _ok 'Package installed: agent-dispatch'

    cat > "$STUB" << 'STUBEOF'
#!/usr/bin/env bash
export PYTHONUTF8=1
exec "$HOME/.agent-dispatch/.venv/bin/python" -m agent_dispatch "$@"
STUBEOF
    chmod +x "$STUB"
    _ok "Binstub: $STUB"

    _write_manifest

    if "$VENV_PYTHON" -c 'import agent_dispatch' 2>/dev/null; then
        _ok 'Verification: module imports successfully'
    else
        _fail 'Verification: module import failed'
        exit 1
    fi

    case ":$PATH:" in
        *":$LOCAL_BIN:"*) _ok "PATH: $LOCAL_BIN is on PATH" ;;
        *) _step "Add $LOCAL_BIN to your PATH: export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
    esac

    _register_pivot
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
    ver="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/pyproject.toml" 2>/dev/null || echo 0.0.0)"
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
  "service": "agent-dispatch",
  "deployed_at": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "deployed_by": "$(hostname)-$(uname -s | tr '[:upper:]' '[:lower:]')",
  "source": {
    "kind": "$kind",
    "path": "$PLUGIN_DIR",
    "repo": "copilot-extensions",
    "plugin": "agent-dispatch",
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

# Register the worktree-picker "Tasks" pivot (best-effort; never fatal).
_register_pivot() {
    local src="$PLUGIN_DIR/pivots/agent-dispatch.json"
    local dir="$HOME/.agent-worktrees/pivots"
    if [[ -f "$src" ]]; then
        if mkdir -p "$dir" 2>/dev/null && cp -f "$src" "$dir/agent-dispatch.json" 2>/dev/null; then
            _ok "Picker pivot registered: $dir/agent-dispatch.json"
        else
            _skip "Could not register picker pivot (agent-worktrees runtime root not writable)"
        fi
    else
        _skip "Picker pivot manifest not found at $src"
    fi
}

# -- Coordinator service (systemd user unit; default-on on deploy machines) --
_install_service() {
    if [[ "$NO_SERVICE" -eq 1 ]]; then
        _skip "Coordinator service skipped (--no-service): this host is a client only"
        return 0
    fi
    if ! command -v systemctl >/dev/null 2>&1; then
        _skip "systemd not available -- run 'agent-dispatch serve' manually if this host hosts a coordinator"
        return 0
    fi
    mkdir -p "$UNIT_DIR"
    if [[ ! -f "$ENV_FILE" ]]; then
        cat > "$ENV_FILE" << 'ENVEOF'
# agent-dispatch coordinator service environment (edit + `systemctl --user restart agent-dispatch`)
AGENT_DISPATCH_HOST=127.0.0.1
AGENT_DISPATCH_PORT=9847
# AGENT_DISPATCH_DB=%h/.agent-dispatch/tasks.db   # default; uncomment to override
# AGENT_DISPATCH_TOKEN=                            # set to require bearer auth
ENVEOF
        _ok "Service env: $ENV_FILE (defaults; edit to expose on the network / add a token)"
    else
        _skip "Service env already exists: $ENV_FILE"
    fi
    cat > "$UNIT_DIR/$SYSTEMD_UNIT" << EOF
[Unit]
Description=agent-dispatch -- portable agent task-queue coordinator
After=network.target

[Service]
Type=simple
EnvironmentFile=-$ENV_FILE
Environment=PYTHONUTF8=1
ExecStart=$VENV_PYTHON -m agent_dispatch serve
Restart=on-failure
RestartSec=5
WorkingDirectory=$INSTALL_DIR

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable "$SYSTEMD_UNIT" 2>/dev/null || true
    systemctl --user restart "$SYSTEMD_UNIT" 2>/dev/null || true
    if systemctl --user is-active "$SYSTEMD_UNIT" &>/dev/null; then
        _ok "Coordinator service installed + started ($SYSTEMD_UNIT)"
    else
        _warn "Coordinator service installed but not active -- check: systemctl --user status agent-dispatch"
    fi
}

# -- Actions ----------------------------------------------------------------
do_install() {
    echo ''; echo '=== agent-dispatch install ==='; echo ''
    _ensure_runtime
    _install_service
    echo ''; echo '=== agent-dispatch install complete ==='
    echo '  Coordinator: systemctl --user status agent-dispatch'
}

do_update() {
    echo ''; echo '=== agent-dispatch update ==='; echo ''
    _downgrade_guard
    _ensure_runtime
    _install_service
    echo ''; echo '=== agent-dispatch update complete ==='
}

do_start() {
    command -v systemctl >/dev/null 2>&1 || { _fail 'systemd not available'; exit 1; }
    if [[ ! -f "$UNIT_DIR/$SYSTEMD_UNIT" ]]; then
        _fail "No service unit installed -- run: $0 install"
        exit 1
    fi
    systemctl --user start "$SYSTEMD_UNIT"
    systemctl --user is-active "$SYSTEMD_UNIT" &>/dev/null \
        && _ok "Coordinator started" || { _fail "Failed to start coordinator"; exit 1; }
}

do_stop() {
    command -v systemctl >/dev/null 2>&1 || { _fail 'systemd not available'; exit 1; }
    if systemctl --user is-active "$SYSTEMD_UNIT" &>/dev/null; then
        systemctl --user stop "$SYSTEMD_UNIT" 2>/dev/null || true
        _ok "Coordinator stopped"
    else
        _skip "Coordinator not running"
    fi
}

do_status() {
    echo ''; echo '=== agent-dispatch status ==='
    if [[ -f "$INSTALL_DIR/deploy-manifest.json" ]]; then
        local kind ver
        kind=$(grep -o '"kind": *"[^"]*"' "$INSTALL_DIR/deploy-manifest.json" | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
        ver=$(grep -o '"version": *"[^"]*"' "$INSTALL_DIR/deploy-manifest.json" | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
        _ok "Deployed: $ver (source: $kind)"
    else
        _skip "No deploy manifest -- not installed?"
    fi
    if command -v systemctl >/dev/null 2>&1 && [[ -f "$UNIT_DIR/$SYSTEMD_UNIT" ]]; then
        local state
        state=$(systemctl --user is-active "$SYSTEMD_UNIT" 2>/dev/null || echo inactive)
        _ok "Coordinator service: $state ($(systemctl --user is-enabled "$SYSTEMD_UNIT" 2>/dev/null || echo disabled))"
    else
        _skip "No coordinator service unit (client-only host, or systemd unavailable)"
    fi
}

do_uninstall() {
    echo ''; echo '=== agent-dispatch uninstall ==='; echo ''
    if command -v systemctl >/dev/null 2>&1; then
        systemctl --user stop "$SYSTEMD_UNIT" 2>/dev/null || true
        systemctl --user disable "$SYSTEMD_UNIT" 2>/dev/null || true
        rm -f "$UNIT_DIR/$SYSTEMD_UNIT"
        systemctl --user daemon-reload 2>/dev/null || true
        _ok "Coordinator service removed"
    fi
    rm -f "$STUB"; _ok "Binstub removed"
    rm -f "$HOME/.agent-worktrees/pivots/agent-dispatch.json" 2>/dev/null || true
    if [[ "$PURGE" -eq 1 ]]; then
        rm -rf "$INSTALL_DIR"; _ok "Runtime purged: $INSTALL_DIR (config + DB deleted)"
    else
        rm -rf "$VENV_DIR"; _ok "Venv removed (config + DB kept; --purge to delete)"
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
