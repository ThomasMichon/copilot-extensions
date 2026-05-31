#!/usr/bin/env bash
# =============================================================================
# install.sh -- Agent Bridge -- standardized installer interface
# =============================================================================
# Manages the agent-bridge service lifecycle: install, uninstall, start, stop,
# status, update.
#
# Runtime lives at ~/.agent-bridge/ (venv, config, DB, auth).
# Binstub goes to ~/.local/bin/agent-bridge.
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
        *)       echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# -- Helpers -----------------------------------------------------------------

_info()  { echo "[agent-bridge] $*"; }
_ok()    { echo "[OK] $*"; }
_fail()  { echo "[FAIL] $*" >&2; }
_warn()  { echo "[WARN] $*" >&2; }

_ensure_venv() {
    if [[ ! -d "$VENV_DIR" ]]; then
        _info "Creating venv at $VENV_DIR"
        python3 -m venv "$VENV_DIR"
    fi
}

_pip() {
    "$VENV_DIR/bin/pip" "$@"
}

_venv_python() {
    "$VENV_DIR/bin/python" "$@"
}

_find_repo_root() {
    # Walk up from PLUGIN_DIR to find .git
    local dir="$PLUGIN_DIR"
    while [[ "$dir" != "/" ]]; do
        if [[ -d "$dir/.git" ]]; then
            echo "$dir"
            return 0
        fi
        dir="$(dirname "$dir")"
    done
    echo "$PLUGIN_DIR"
}

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

# -- Actions -----------------------------------------------------------------

do_install() {
    _info "Installing agent-bridge"

    mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"

    _ensure_venv

    _info "Installing package (editable from repo)"
    _pip install --quiet --upgrade pip
    _pip install --quiet -e "$PLUGIN_DIR"

    # Create binstub
    cat > "$BINSTUB" << 'STUB'
#!/usr/bin/env bash
# Agent Bridge binstub -- delegates to venv
exec "$HOME/.agent-bridge/venv/bin/agent-bridge" "$@"
STUB
    chmod +x "$BINSTUB"

    # Generate default config if missing
    _venv_python -c "from agent_bridge.config import load_config, write_default_config; write_default_config(load_config())" 2>/dev/null || true

    _ok "agent-bridge installed"
    _info "Binstub: $BINSTUB"
    _info "Config:  $INSTALL_DIR/config.yaml"
    _info "Run 'agent-bridge start' to start the service"
}

do_uninstall() {
    _info "Uninstalling agent-bridge"

    # Stop if running
    if pid=$(_get_pid); then
        _info "Stopping running instance (pid=$pid)"
        kill "$pid" 2>/dev/null || true
        sleep 1
        rm -f "$PID_FILE"
    fi

    # Remove binstub
    rm -f "$BINSTUB"

    # Remove venv
    if [[ -d "$VENV_DIR" ]]; then
        rm -rf "$VENV_DIR"
        _info "Removed venv"
    fi

    if $PURGE; then
        _info "Purging config, DB, and auth"
        rm -rf "$INSTALL_DIR"
    else
        _info "Preserved config/DB at $INSTALL_DIR (use --purge to remove)"
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

    _info "Starting agent-bridge"
    nohup "$VENV_DIR/bin/agent-bridge" start > "$INSTALL_DIR/agent-bridge.log" 2>&1 &
    local pid=$!
    echo "$pid" > "$PID_FILE"
    sleep 1

    if kill -0 "$pid" 2>/dev/null; then
        _ok "agent-bridge started (pid=$pid)"
    else
        _fail "agent-bridge failed to start -- check $INSTALL_DIR/agent-bridge.log"
        rm -f "$PID_FILE"
        exit 1
    fi
}

do_stop() {
    if pid=$(_get_pid); then
        _info "Stopping agent-bridge (pid=$pid)"
        kill "$pid" 2>/dev/null || true
        sleep 1
        rm -f "$PID_FILE"
        _ok "agent-bridge stopped"
    else
        _info "agent-bridge is not running"
    fi
}

do_status() {
    if pid=$(_get_pid); then
        _ok "agent-bridge is running (pid=$pid)"

        # Try health check
        local cfg_bind cfg_port
        cfg_bind="127.0.0.1"
        cfg_port="9280"
        if curl -sf "http://$cfg_bind:$cfg_port/health" > /dev/null 2>&1; then
            _ok "Health check passed"
        else
            _warn "Process running but health check failed"
        fi
    else
        _info "agent-bridge is not running"
    fi

    # Show install state
    if [[ -x "$VENV_DIR/bin/agent-bridge" ]]; then
        local version
        version=$("$VENV_DIR/bin/agent-bridge" version 2>/dev/null || echo "unknown")
        _info "Installed: $version"
    else
        _info "Not installed"
    fi
}

do_update() {
    _info "Updating agent-bridge"

    if [[ ! -d "$VENV_DIR" ]]; then
        _fail "agent-bridge not installed. Run: install.sh install"
        exit 1
    fi

    # Reinstall from source
    _pip install --quiet --upgrade pip
    _pip install --quiet -e "$PLUGIN_DIR"

    _ok "agent-bridge updated"

    # Restart if running
    if pid=$(_get_pid); then
        _info "Restarting service"
        do_stop
        do_start
    fi
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
