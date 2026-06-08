#!/usr/bin/env bash
# =============================================================================
# install.sh -- Agent Bridge -- plugin installer for Linux/WSL
# =============================================================================
# Manages the agent-bridge service lifecycle: install, uninstall, start, stop,
# status, update.
#
# Runtime lives at ~/.agent-bridge/ (venv, config, DB, auth).
# Binstub goes to ~/.local/bin/agent-bridge.
#
# On first install, detects and migrates from the aperture-labs service
# installer (services/agent-bridge/) if present, preserving config, auth,
# and DB.
#
# Usage:
#   bash plugins/agent-bridge/scripts/install.sh install
#   bash plugins/agent-bridge/scripts/install.sh status
#   bash plugins/agent-bridge/scripts/install.sh update
#
# Options:
#   --purge    On uninstall: also delete config, DB, and auth token
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

INSTALL_DIR="$HOME/.agent-bridge"
VENV_DIR="$INSTALL_DIR/venv"
LOCAL_BIN="$HOME/.local/bin"
BINSTUB="$LOCAL_BIN/agent-bridge"
PID_FILE="$INSTALL_DIR/agent-bridge.pid"
PORT=9281
SYSTEMD_UNIT="agent-bridge.service"

# Ensure ~/.local/bin is on PATH
if [[ ":$PATH:" != *":$LOCAL_BIN:"* ]]; then
    export PATH="$LOCAL_BIN:$PATH"
fi

# -- Parse arguments ---------------------------------------------------------

ACTION="${1:-status}"
shift || true

PURGE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --purge) PURGE=true; shift ;;
        *)       echo "[FAIL] Unknown option: $1" >&2; exit 1 ;;
    esac
done

# -- Helpers -----------------------------------------------------------------

_ok()   { echo "  [OK]   $*"; }
_skip() { echo "  [SKIP] $*"; }
_fail() { echo "  [FAIL] $*" >&2; }
_step() { echo "  ...    $*"; }
_warn() { echo "  [WARN] $*" >&2; }

_get_pid() {
    if [[ -f "$PID_FILE" ]]; then
        local pid
        pid="$(cat "$PID_FILE")"
        if kill -0 "$pid" 2>/dev/null; then
            echo "$pid"
            return 0
        fi
        rm -f "$PID_FILE"
    fi
    return 1
}

_health_check() {
    local retries=5
    for i in $(seq 1 $retries); do
        if curl -sf "http://127.0.0.1:${PORT}/health" > /dev/null 2>&1; then
            return 0
        fi
        sleep 2
    done
    return 1
}

# Wait until the port is free (no listener). Returns 0 once clear, 1 on timeout.
_wait_port_free() {
    local retries=10
    for i in $(seq 1 $retries); do
        if ! ss -tlnp 2>/dev/null | grep -q ":${PORT} " && \
           ! curl -sf "http://127.0.0.1:${PORT}/health" > /dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

# Resolve ssh-manager library path across multiple layouts.
# Prints the resolved directory path to stdout (nothing else).
# Returns 0 if found, 1 if not.
_resolve_ssh_manager() {
    local candidate

    # 1. Vendored inside agent-bridge (marketplace install layout)
    candidate="$PLUGIN_DIR/libs/ssh-manager"
    if [[ -f "$candidate/pyproject.toml" ]]; then
        cd "$candidate" && pwd
        return 0
    fi

    # 2. Relative path (git checkout layout: plugins/agent-bridge/../../libs/ssh-manager)
    candidate="$PLUGIN_DIR/../../libs/ssh-manager"
    if [[ -f "$candidate/pyproject.toml" ]]; then
        cd "$candidate" && pwd
        return 0
    fi

    # 3. Git repo registry (~/.git-repos) -- use Python for safe YAML parsing
    if [[ -f "$HOME/.git-repos" ]]; then
        candidate="$(python3 -c "
import pathlib, os
try:
    import yaml
except ImportError:
    raise SystemExit(1)
reg = yaml.safe_load(pathlib.Path.home().joinpath('.git-repos').read_text())
repo = (reg or {}).get('repos', {}).get('copilot-extensions', {})
if repo:
    p = repo.get('path', os.path.join(reg.get('srcroot', ''), 'copilot-extensions'))
    p = os.path.expanduser(p)
    lib = os.path.join(p, 'libs', 'ssh-manager')
    if os.path.isfile(os.path.join(lib, 'pyproject.toml')):
        print(lib)
        raise SystemExit(0)
raise SystemExit(1)
" 2>/dev/null)" && {
            echo "$candidate"
            return 0
        }
    fi

    # 4. Common checkout path (repo exists but registry absent/stale)
    candidate="$HOME/src/copilot-extensions/libs/ssh-manager"
    if [[ -f "$candidate/pyproject.toml" ]]; then
        cd "$candidate" && pwd
        return 0
    fi

    return 1
}

# Check if ssh-manager is already importable in the venv.
# Returns 0 if the key symbols can be imported successfully.
_ssh_manager_installed() {
    [[ -x "$VENV_DIR/bin/python" ]] || return 1
    "$VENV_DIR/bin/python" -c 'from ssh_manager import SSHProfileSource, get_default_manager' 2>/dev/null
}

# Install sibling plugin packages (e.g. agent-codespaces) into the venv.
# These provide optional namespace resolvers that agent-bridge discovers
# at startup. Missing siblings are silently skipped.
#   $1 = "reinstall" to force reinstall, empty for fresh install
_install_sibling_plugins() {
    local mode="${1:-}"
    local plugins_root
    plugins_root="$(cd "$PLUGIN_DIR/.." && pwd)"
    local siblings=(agent-codespaces)
    for name in "${siblings[@]}"; do
        local sib_dir="$plugins_root/$name"
        if [[ ! -f "$sib_dir/pyproject.toml" ]]; then
            # Check marketplace vendor layout
            sib_dir="$PLUGIN_DIR/plugins/$name"
            [[ -f "$sib_dir/pyproject.toml" ]] || continue
        fi
        local pkg_name="${name//-/_}"
        if [[ "$mode" == "reinstall" ]]; then
            if uv pip install --python "$VENV_DIR/bin/python" --reinstall-package "$pkg_name" \
                    "$sib_dir" --quiet 2>/dev/null; then
                _ok "Sibling plugin: $name"
            else
                _warn "Sibling plugin $name install failed (non-fatal)"
                continue
            fi
        else
            if uv pip install --python "$VENV_DIR/bin/python" "$sib_dir" --quiet 2>/dev/null; then
                _ok "Sibling plugin: $name"
            else
                _warn "Sibling plugin $name install failed (non-fatal)"
                continue
            fi
        fi
        # Create binstub for sibling if it has a console_scripts entry point
        local sib_bin="$VENV_DIR/bin/$name"
        if [[ -x "$sib_bin" ]]; then
            local sib_stub="$LOCAL_BIN/$name"
            cat > "$sib_stub" << SIBSTUB
#!/usr/bin/env bash
export PYTHONUTF8=1
exec "$HOME/.agent-bridge/venv/bin/$name" "\$@"
SIBSTUB
            chmod +x "$sib_stub"
            _ok "Binstub: $sib_stub"
        fi
    done
}

# Remove binstubs for sibling plugins during uninstall.
_remove_sibling_binstubs() {
    local siblings=(agent-codespaces)
    for name in "${siblings[@]}"; do
        local sib_stub="$LOCAL_BIN/$name"
        if [[ -f "$sib_stub" ]]; then
            rm -f "$sib_stub"
            _ok "Sibling binstub removed: $name"
        fi
    done
}

_git_info() {
    local path="$1"
    local commit branch dirty
    commit=$(git -C "$path" rev-parse --short HEAD 2>/dev/null || echo "unknown")
    branch=$(git -C "$path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
    dirty="false"
    if [[ -n "$(git -C "$path" status --porcelain 2>/dev/null)" ]]; then
        dirty="true"
    fi
    echo "$commit $branch $dirty"
}

_write_deploy_manifest() {
    local manifest="$INSTALL_DIR/deploy-manifest.json"
    local repo_root
    repo_root="$(cd "$PLUGIN_DIR/.." && pwd)"

    local ver="0.0.0"
    if [[ -f "$PLUGIN_DIR/pyproject.toml" ]]; then
        ver=$(grep -m1 '^version' "$PLUGIN_DIR/pyproject.toml" | sed 's/.*"\(.*\)".*/\1/' || echo "0.0.0")
    fi

    read -r commit branch dirty <<< "$(_git_info "$repo_root")"

    cat > "$manifest" << EOF
{
  "schema_version": 2,
  "service": "agent-bridge",
  "installer": "plugin",
  "deployed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "deployed_by": "$(hostname)",
  "runtime_source": {
    "repo": "copilot-extensions",
    "plugin": "agent-bridge",
    "version": "$ver",
    "commit": "$commit",
    "branch": "$branch",
    "dirty": $dirty,
    "path": "$PLUGIN_DIR"
  }
}
EOF
    _ok "Deploy manifest written"
}

_install_systemd_unit() {
    # Only install systemd unit if systemd is available and we have user units
    if ! command -v systemctl &>/dev/null; then
        _skip "systemd not available -- skipping unit installation"
        return
    fi

    local unit_dir="$HOME/.config/systemd/user"
    mkdir -p "$unit_dir"

    local venv_bridge="$VENV_DIR/bin/agent-bridge"

    cat > "$unit_dir/$SYSTEMD_UNIT" << EOF
[Unit]
Description=Agent-Bridge -- inter-agent communication service
After=network.target

[Service]
Type=simple
ExecStart=$venv_bridge start
ExecStopPost=/bin/sleep 2
Restart=on-failure
RestartSec=5
WorkingDirectory=$INSTALL_DIR
Environment=PYTHONUTF8=1

[Install]
WantedBy=default.target
EOF

    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable "$SYSTEMD_UNIT" 2>/dev/null || true
    _ok "systemd user unit installed and enabled"
}

_migration_check() {
    local old_manifest="$INSTALL_DIR/deploy-manifest.json"
    [[ -f "$old_manifest" ]] || return 0

    if grep -q '"installer_path".*services/agent-bridge' "$old_manifest" 2>/dev/null; then
        _step "Migrating from aperture-labs service installer"
        _step "  Preserving config, auth, and DB"

        # Stop old instance
        if pid=$(_get_pid); then
            _step "  Stopping running instance (pid=$pid)"
            kill "$pid" 2>/dev/null || true
            sleep 2
            rm -f "$PID_FILE"
        fi

        # Stop old systemd unit if managed by aperture-labs
        if systemctl --user is-active "$SYSTEMD_UNIT" &>/dev/null; then
            systemctl --user stop "$SYSTEMD_UNIT" 2>/dev/null || true
        fi

        _ok "Migration from aperture-labs installer detected"
    fi
}

# -- Actions -----------------------------------------------------------------

do_install() {
    echo ""
    echo "=== agent-bridge install ==="
    echo ""

    # Prerequisite: uv
    if ! command -v uv &>/dev/null; then
        _fail "uv not found on PATH (required for venv + package management)"
        _fail "Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
        exit 1
    fi

    _migration_check

    mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"

    # Create venv via uv
    if [[ ! -x "$VENV_DIR/bin/python" ]]; then
        _step "Creating venv via uv..."
        if ! uv venv "$VENV_DIR" --python 3.10 --allow-existing; then
            if ! uv venv "$VENV_DIR" --allow-existing; then
                _fail "Failed to create venv at $VENV_DIR"
                exit 1
            fi
        fi
        _ok "Venv created"
    else
        _skip "Venv already exists"
    fi

    # Install package via uv (ssh-manager library first, then agent-bridge)
    _step "Installing agent-bridge package..."
    local ssh_manager_dir
    if ssh_manager_dir="$(_resolve_ssh_manager)"; then
        if ! uv pip install --python "$VENV_DIR/bin/python" "$ssh_manager_dir" --quiet; then
            _fail "ssh-manager install failed"
            exit 1
        fi
    elif _ssh_manager_installed; then
        _step "ssh-manager already installed in venv (marketplace layout)"
    else
        _fail "Cannot locate ssh-manager library. Clone copilot-extensions so libs/ssh-manager exists, then rerun: aperture-labs services agent-bridge update"
        exit 1
    fi
    if ! uv pip install --python "$VENV_DIR/bin/python" "$PLUGIN_DIR" --quiet; then
        _fail "Package install failed"
        exit 1
    fi
    _ok "Package installed"

    # Install sibling plugins (e.g. agent-codespaces for codespace: namespace)
    _install_sibling_plugins

    # Create binstub
    cat > "$BINSTUB" << 'STUB'
#!/usr/bin/env bash
export PYTHONUTF8=1
exec "$HOME/.agent-bridge/venv/bin/agent-bridge" "$@"
STUB
    chmod +x "$BINSTUB"
    _ok "Binstub: $BINSTUB"

    # Generate default config
    "$VENV_DIR/bin/python" -c \
        "from agent_bridge.config import load_config, write_default_config; write_default_config(load_config())" \
        2>/dev/null || true
    _ok "Default config generated"

    # Install systemd unit
    _install_systemd_unit

    # Write deploy manifest
    _write_deploy_manifest

    echo ""
    _ok "agent-bridge installed"
    echo "  Install dir: $INSTALL_DIR"
    echo "  Binstub:     $BINSTUB"
    echo "  Config:      agent-bridge config show"
    echo "  API:         http://127.0.0.1:$PORT"

    # Start service and verify health
    echo ""
    _step "Starting service after install..."
    do_start
}

do_uninstall() {
    echo ""
    echo "=== agent-bridge uninstall ==="
    echo ""

    do_stop

    # Remove systemd unit
    if command -v systemctl &>/dev/null; then
        systemctl --user disable "$SYSTEMD_UNIT" 2>/dev/null || true
        rm -f "$HOME/.config/systemd/user/$SYSTEMD_UNIT"
        systemctl --user daemon-reload 2>/dev/null || true
        _ok "systemd unit removed"
    fi

    rm -f "$BINSTUB"
    _ok "Binstub removed"

    _remove_sibling_binstubs

    if [[ -d "$VENV_DIR" ]]; then
        rm -rf "$VENV_DIR"
        _ok "Venv removed"
    fi

    if $PURGE; then
        _warn "Purging config, DB, and auth"
        rm -rf "$INSTALL_DIR"
    else
        _skip "Preserved config/DB at $INSTALL_DIR (use --purge to remove)"
    fi

    _ok "agent-bridge uninstalled"
}

do_start() {
    if pid=$(_get_pid); then
        _warn "agent-bridge is already running (pid=$pid)"
        return 0
    fi

    if [[ ! -x "$VENV_DIR/bin/agent-bridge" ]]; then
        _fail "agent-bridge not installed. Run: install.sh install"
        exit 1
    fi

    _step "Starting agent-bridge..."

    # Prefer systemd if available
    if command -v systemctl &>/dev/null && [[ -f "$HOME/.config/systemd/user/$SYSTEMD_UNIT" ]]; then
        systemctl --user start "$SYSTEMD_UNIT"
        sleep 2
        if systemctl --user is-active "$SYSTEMD_UNIT" &>/dev/null; then
            if _health_check; then
                _ok "agent-bridge started via systemd (port=$PORT)"
            else
                _warn "agent-bridge started via systemd but health check failed"
            fi
            return 0
        fi
        _warn "systemd start failed -- falling back to direct start"
    fi

    # Direct start
    nohup "$VENV_DIR/bin/agent-bridge" start > "$INSTALL_DIR/agent-bridge.log" 2>&1 &
    local pid=$!
    echo "$pid" > "$PID_FILE"
    sleep 2

    if kill -0 "$pid" 2>/dev/null; then
        if _health_check; then
            _ok "agent-bridge started (pid=$pid, port=$PORT)"
        else
            _warn "agent-bridge started (pid=$pid) but health check failed"
        fi
    else
        _fail "agent-bridge failed to start -- check $INSTALL_DIR/agent-bridge.log"
        rm -f "$PID_FILE"
        exit 1
    fi
}

do_stop() {
    # Try systemd first
    if command -v systemctl &>/dev/null; then
        if systemctl --user is-active "$SYSTEMD_UNIT" &>/dev/null; then
            _step "Stopping agent-bridge via systemd..."
            systemctl --user stop "$SYSTEMD_UNIT" 2>/dev/null || true
            _wait_port_free || _warn "Port $PORT still in use after stop"
            _ok "agent-bridge stopped (systemd)"
            rm -f "$PID_FILE"
            return
        fi
    fi

    # Direct stop via PID
    if pid=$(_get_pid); then
        _step "Stopping agent-bridge (pid=$pid)..."
        kill "$pid" 2>/dev/null || true
        _wait_port_free || _warn "Port $PORT still in use after stop"
        rm -f "$PID_FILE"
        _ok "agent-bridge stopped"
    else
        # Last resort: find orphan by port binding (PID file lost)
        local port_pid
        port_pid="$(ss -tlnp 2>/dev/null | grep ":${PORT} " | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | head -1)"
        if [[ -n "$port_pid" ]]; then
            _step "Stopping orphaned agent-bridge (pid=$port_pid, found by port)..."
            kill "$port_pid" 2>/dev/null || true
            _wait_port_free || _warn "Port $PORT still in use after stop"
            _ok "agent-bridge stopped"
        else
            _skip "agent-bridge is not running"
        fi
    fi
}

do_status() {
    local running=false

    # Check systemd
    if command -v systemctl &>/dev/null && systemctl --user is-active "$SYSTEMD_UNIT" &>/dev/null; then
        _ok "agent-bridge is running (systemd)"
        running=true
    elif pid=$(_get_pid); then
        _ok "agent-bridge is running (pid=$pid)"
        running=true
    else
        _step "agent-bridge is not running"
    fi

    if $running; then
        if _health_check; then
            _ok "Health check passed (port $PORT)"
        else
            _warn "Process running but health check failed"
        fi
    fi

    # Install state
    if [[ -x "$VENV_DIR/bin/agent-bridge" ]]; then
        local version
        version=$("$VENV_DIR/bin/agent-bridge" version 2>/dev/null || echo "unknown")
        _ok "Installed: $version"
    else
        _step "Not installed"
    fi

    # Config
    if [[ -f "$INSTALL_DIR/config.yaml" ]]; then
        _ok "Config: $INSTALL_DIR/config.yaml"
    fi

    # Systemd unit
    if command -v systemctl &>/dev/null && [[ -f "$HOME/.config/systemd/user/$SYSTEMD_UNIT" ]]; then
        local state
        state=$(systemctl --user is-enabled "$SYSTEMD_UNIT" 2>/dev/null || echo "not found")
        _ok "systemd unit: $state"
    fi

    # Exit non-zero when not installed (used by module update orchestrator)
    if [[ ! -x "$VENV_DIR/bin/agent-bridge" ]]; then
        exit 1
    fi
}

do_update() {
    echo ""
    echo "=== agent-bridge update ==="
    echo ""

    # Prerequisite: uv
    if ! command -v uv &>/dev/null; then
        _fail "uv not found on PATH (required for package management)"
        _fail "Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
        exit 1
    fi

    # Repair venv if python binary is missing
    if [[ ! -x "$VENV_DIR/bin/python" ]]; then
        if [[ -d "$VENV_DIR" ]]; then
            _step "Repairing venv (python binary missing)..."
        else
            _fail "agent-bridge not installed. Run: install.sh install"
            exit 1
        fi
        if ! uv venv "$VENV_DIR" --python 3.10 --allow-existing; then
            uv venv "$VENV_DIR" --allow-existing || { _fail "Venv repair failed"; exit 1; }
        fi
        _ok "Venv repaired"
    fi

    # Stop running instance
    local was_running=false
    if pid=$(_get_pid) || (command -v systemctl &>/dev/null && systemctl --user is-active "$SYSTEMD_UNIT" &>/dev/null); then
        was_running=true
        do_stop
    fi

    # Reinstall package via uv (ssh-manager + agent-bridge)
    _step "Updating agent-bridge package..."
    local ssh_manager_dir
    if ssh_manager_dir="$(_resolve_ssh_manager)"; then
        if ! uv pip install --python "$VENV_DIR/bin/python" --reinstall-package ssh-manager \
                "$ssh_manager_dir" --quiet; then
            _fail "ssh-manager update failed"
            exit 1
        fi
    elif _ssh_manager_installed; then
        _step "ssh-manager already installed in venv (marketplace layout)"
    else
        _fail "Cannot locate ssh-manager library. Clone copilot-extensions so libs/ssh-manager exists, then rerun: aperture-labs services agent-bridge update"
        exit 1
    fi
    if ! uv pip install --python "$VENV_DIR/bin/python" --reinstall-package agent-bridge \
            "$PLUGIN_DIR" --quiet; then
        _fail "Package update failed"
        exit 1
    fi
    _ok "Package updated"

    # Update sibling plugins (e.g. agent-codespaces for codespace: namespace)
    _install_sibling_plugins reinstall

    # Update binstub
    cat > "$BINSTUB" << 'STUB'
#!/usr/bin/env bash
export PYTHONUTF8=1
exec "$HOME/.agent-bridge/venv/bin/agent-bridge" "$@"
STUB
    chmod +x "$BINSTUB"

    # Update systemd unit
    _install_systemd_unit

    # Update deploy manifest
    _write_deploy_manifest

    # (Re)start service -- always ensure running after update
    _step "Starting service..."
    do_start

    _ok "Update complete"
}

# -- Dispatch ----------------------------------------------------------------

case "$ACTION" in
    install)   do_install ;;
    uninstall) do_uninstall ;;
    start)     do_start ;;
    stop)      do_stop ;;
    status)    do_status ;;
    update)    do_update ;;
    *)
        echo "Usage: $0 {install|uninstall|start|stop|status|update} [options]" >&2
        exit 1
        ;;
esac
