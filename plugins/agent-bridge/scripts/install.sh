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
# On first install, detects and migrates from a legacy project-service
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
#   --force    On install/update: bypass the downgrade guard and install an
#              older version over a newer one (see #1790). The sanctioned
#              update path is the marketplace flow
#              (`aperture-labs services agent-bridge update`), NOT a raw
#              checkout installer -- the guard exists to stop a stale checkout
#              silently downgrading (and de-featuring) the running daemon.
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

INSTALL_DIR="$HOME/.agent-bridge"
VENV_DIR="$INSTALL_DIR/venv"
LOCAL_BIN="$HOME/.local/bin"
BINSTUB="$LOCAL_BIN/agent-bridge"
PID_FILE="$INSTALL_DIR/agent-bridge.pid"
# Effective listen port. A host is 9280; only a WSL guest (which shares the
# Windows host's TCP port namespace) uses 9281 -- matching
# agent_bridge.models.default_port(). Prefer the deployed config's explicit
# port (source of truth: honors an operator override AND catches config drift
# where the running service is on a non-default port), else the WSL-guest
# discriminator ("am I a WSL guest?", not "am I non-Windows?").
_cfg_yaml="${AGENT_BRIDGE_CONFIG_DIR:-$INSTALL_DIR}/config.yaml"
PORT=""
if [[ -f "$_cfg_yaml" ]]; then
    PORT="$(sed -n 's/^[[:space:]]*port:[[:space:]]*\([0-9]\{1,\}\).*/\1/p' "$_cfg_yaml" | head -1)"
fi
if [[ -z "$PORT" ]]; then
    if [[ -n "${WSL_DISTRO_NAME:-}" ]] || grep -qiE 'microsoft|wsl' /proc/sys/kernel/osrelease 2>/dev/null; then
        PORT=9281
    else
        PORT=9280
    fi
fi
RELAY_PORT=9857   # integrated credential relay (in-process with the bridge)
SYSTEMD_UNIT="agent-bridge.service"

# Ensure ~/.local/bin is on PATH
if [[ ":$PATH:" != *":$LOCAL_BIN:"* ]]; then
    export PATH="$LOCAL_BIN:$PATH"
fi

# -- Parse arguments ---------------------------------------------------------

ACTION="${1:-status}"
shift || true

PURGE=false
# Bypass the downgrade guard (#1790). Env var lets the marketplace/ZDD paths
# opt in without threading a flag; the CLI flag is the interactive escape hatch.
FORCE="${AGENT_BRIDGE_ALLOW_DOWNGRADE:-false}"
[[ "$FORCE" == "1" ]] && FORCE=true

while [[ $# -gt 0 ]]; do
    case "$1" in
        --purge) PURGE=true; shift ;;
        --force) FORCE=true; shift ;;
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

# Best-effort graceful drain before a stop: give in-flight turns a chance to
# settle so a routine update does not hard-kill an active session (Phase 1
# zero-downtime). Bounded by --timeout and --force so an update never blocks
# indefinitely. Non-fatal -- the stop that follows is the backstop.
_drain_service() {
    local timeout="${1:-120}"
    [[ -x "$VENV_DIR/bin/agent-bridge" ]] || return 0
    _step "Draining in-flight sessions (up to ${timeout}s)..."
    if "$VENV_DIR/bin/agent-bridge" drain --timeout "$timeout" --force \
            > /dev/null 2>&1; then
        _ok "Drain window complete"
    else
        _warn "Drain reported busy sessions -- proceeding with swap"
    fi
}

# Resolve a vendored library path (libs/<name>) across multiple layouts.
# Prints the resolved directory path to stdout (nothing else).
# Returns 0 if found, 1 if not.
_resolve_vendored_lib() {
    local lib_name="$1"
    local candidate

    # 1. Vendored inside agent-bridge (marketplace install layout)
    candidate="$PLUGIN_DIR/libs/$lib_name"
    if [[ -f "$candidate/pyproject.toml" ]]; then
        cd "$candidate" && pwd
        return 0
    fi

    # 2. Relative path (git checkout layout: plugins/agent-bridge/../../libs/<name>)
    candidate="$PLUGIN_DIR/../../libs/$lib_name"
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
    lib = os.path.join(p, 'libs', '$lib_name')
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
    candidate="$HOME/src/copilot-extensions/libs/$lib_name"
    if [[ -f "$candidate/pyproject.toml" ]]; then
        cd "$candidate" && pwd
        return 0
    fi

    return 1
}

# Resolve the ssh-manager / credential-relay vendored libs (thin wrappers).
_resolve_ssh_manager() { _resolve_vendored_lib ssh-manager; }
_resolve_credential_relay() { _resolve_vendored_lib credential-relay; }
# zero-downtime cutover primitives (module ``zdd``), extracted from this plugin.
_resolve_zdd() { _resolve_vendored_lib zdd; }

# Check if ssh-manager is already importable in the venv.
# Returns 0 if the key symbols can be imported successfully.
_ssh_manager_installed() {
    [[ -x "$VENV_DIR/bin/python" ]] || return 1
    "$VENV_DIR/bin/python" -c 'from ssh_manager import SSHProfileSource, get_default_manager' 2>/dev/null
}

# Check if credential-relay is already importable in the venv.
_credential_relay_installed() {
    [[ -x "$VENV_DIR/bin/python" ]] || return 1
    "$VENV_DIR/bin/python" -c 'from credential_relay import RelayBuilder' 2>/dev/null
}

# Check if the zdd cutover lib is already importable in the venv.
_zdd_installed() {
    [[ -x "$VENV_DIR/bin/python" ]] || return 1
    "$VENV_DIR/bin/python" -c 'from zdd.cutover import CutoverOrchestrator' 2>/dev/null
}

# Install sibling plugin packages (e.g. agent-codespaces) into the bridge venv.
# This provides the `codespace:` namespace resolver and credential relay that
# agent-bridge imports at startup. Package is installed for IMPORT ONLY -- the
# canonical agent-codespaces CLI binstub is owned by ~/.agent-codespaces via its
# own installer. A missing sibling is non-fatal but WARNED loudly, because it
# disables that sibling's namespace resolver / relay.
#   $1 = "reinstall" to force reinstall, empty for fresh install
_install_sibling_plugins() {
    local mode="${1:-}"
    local plugins_root
    plugins_root="$(cd "$PLUGIN_DIR/.." && pwd)"
    local siblings=(agent-codespaces agent-containers)
    for name in "${siblings[@]}"; do
        local sib_dir="$plugins_root/$name"
        if [[ ! -f "$sib_dir/pyproject.toml" ]]; then
            # Check marketplace vendor layout
            sib_dir="$PLUGIN_DIR/plugins/$name"
            if [[ ! -f "$sib_dir/pyproject.toml" ]]; then
                _warn "Sibling plugin '$name' not found -- its namespace resolver / relay will be UNAVAILABLE."
                _warn "  Install it from the marketplace: copilot plugin install $name@copilot-extensions"
                continue
            fi
        fi
        local pkg_name="${name//-/_}"
        if [[ "$mode" == "reinstall" ]]; then
            if uv pip install --python "$VENV_DIR/bin/python" --reinstall-package "$pkg_name" \
                    "$sib_dir" --quiet 2>/dev/null; then
                _ok "Sibling plugin (relay import): $name"
            else
                _warn "Sibling plugin $name install failed -- its namespace resolver / relay will be UNAVAILABLE."
            fi
        else
            if uv pip install --python "$VENV_DIR/bin/python" "$sib_dir" --quiet 2>/dev/null; then
                _ok "Sibling plugin (relay import): $name"
            else
                _warn "Sibling plugin $name install failed -- its namespace resolver / relay will be UNAVAILABLE."
            fi
        fi
    done
}

# Sibling plugin binstubs (e.g. agent-codespaces) are owned by their own
# installer (~/.agent-codespaces), not by agent-bridge. Uninstall leaves them.
_remove_sibling_binstubs() {
    _step "Leaving sibling CLI binstubs in place (owned by their own installers)"
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

# === install-contract:v3 source-kind -- keep byte-identical across plugins ===
# A runtime footprint's source is inferred from where the installer runs.
# Vendored under the Copilot CLI installed-plugins dir => marketplace;
# anything else (a git checkout) => local.
_source_kind() {
    case "$(printf '%s' "$1" | tr '\\' '/')" in
        */.copilot/installed-plugins/*) printf 'marketplace' ;;
        *) printf 'local' ;;
    esac
}
# === end install-contract:v3 source-kind ===

# Unified schema_version 3 manifest writer. Self-contained per plugin (no shared
# module -- plugins are pulled independently from the marketplace). Records the
# source footprint (local vs marketplace) and is written atomically (temp+move).
_write_deploy_manifest_for() {
    local service="$1" plugin="$2" install_path="$3" plugin_path="$4" venv_path="$5"
    local manifest="$install_path/deploy-manifest.json"
    local kind
    kind="$(_source_kind "$plugin_path")"

    local ver="0.0.0"
    if [[ -f "$plugin_path/pyproject.toml" ]]; then
        ver=$(grep -m1 '^version' "$plugin_path/pyproject.toml" | sed 's/.*"\(.*\)".*/\1/' || echo "0.0.0")
    fi

    # Git provenance only applies to a local checkout.
    local commit="null" branch="null" dirty="false"
    if [[ "$kind" == "local" ]]; then
        local repo_root c b d
        repo_root="$(cd "$plugin_path/.." && pwd)"
        read -r c b d <<< "$(_git_info "$repo_root")"
        commit="\"$c\""; branch="\"$b\""; dirty="$d"
    fi

    local tmp="$manifest.tmp"
    cat > "$tmp" << EOF
{
  "schema_version": 3,
  "service": "$service",
  "deployed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "deployed_by": "$(hostname)-$(uname -s | tr '[:upper:]' '[:lower:]')",
  "source": {
    "kind": "$kind",
    "path": "$plugin_path",
    "repo": "copilot-extensions",
    "plugin": "$plugin",
    "version": "$ver",
    "commit": $commit,
    "branch": $branch,
    "dirty": $dirty
  },
  "venv": "$venv_path",
  "runtime": "python"
}
EOF
    mv -f "$tmp" "$manifest"
    _ok "Deploy manifest written (source: $kind)"
}

_write_deploy_manifest() {
    _write_deploy_manifest_for "agent-bridge" "agent-bridge" \
        "$INSTALL_DIR" "$PLUGIN_DIR" "$VENV_DIR"
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
# KillMode=process: on stop/restart, signal ONLY the main daemon process, not
# the whole cgroup. This lets a survivable Session Host (session_host_enabled)
# and its Copilot --acp child outlive an agent-bridge restart so the new daemon
# can reattach (effort agent-bridge-version-mux, #1759; fixes #1780 -- the
# default KillMode=control-group cgroup-kills the host). The daemon's own
# lifespan shutdown gracefully stops SSH masters, the credential relay, and
# non-host sessions, so nothing else leaks. This is the systemd analog of the
# Windows Job Object breakaway.
KillMode=process
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
        _step "Migrating from legacy project-service installer"
        _step "  Preserving config, auth, and DB"

        # Stop old instance
        if pid=$(_get_pid); then
            _step "  Stopping running instance (pid=$pid)"
            kill "$pid" 2>/dev/null || true
            sleep 2
            rm -f "$PID_FILE"
        fi

        # Stop old systemd unit if managed by the legacy installer
        if systemctl --user is-active "$SYSTEMD_UNIT" &>/dev/null; then
            systemctl --user stop "$SYSTEMD_UNIT" 2>/dev/null || true
        fi

        _ok "Migration from legacy project-service installer detected"
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

    # Guard against a stale checkout downgrading an existing healthy install
    # (#1790). No-op on first install (no installed version to compare).
    _downgrade_guard

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
        _fail "Cannot locate ssh-manager library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer."
        exit 1
    fi
    # credential-relay (the relay framework agent-bridge runs in its daemon).
    local cred_relay_dir
    if cred_relay_dir="$(_resolve_credential_relay)"; then
        if ! uv pip install --python "$VENV_DIR/bin/python" "$cred_relay_dir" --quiet; then
            _fail "credential-relay install failed"
            exit 1
        fi
    elif _credential_relay_installed; then
        _step "credential-relay already installed in venv (marketplace layout)"
    else
        _fail "Cannot locate credential-relay library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer."
        exit 1
    fi
    # zdd (zero-downtime cutover primitives: routing table + orchestrator).
    local zdd_dir
    if zdd_dir="$(_resolve_zdd)"; then
        if ! uv pip install --python "$VENV_DIR/bin/python" "$zdd_dir" --quiet; then
            _fail "zdd install failed"
            exit 1
        fi
    elif _zdd_installed; then
        _step "zdd already installed in venv (marketplace layout)"
    else
        _fail "Cannot locate zdd library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer."
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

    # Also ensure the integrated credential relay is down. It runs in-process
    # with the bridge, but free its port explicitly to catch an orphaned relay.
    local relay_pid
    relay_pid="$(ss -tlnp 2>/dev/null | grep ":${RELAY_PORT} " | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | head -1)"
    if [[ -n "$relay_pid" ]]; then
        _warn "Credential relay port $RELAY_PORT still in use -- killing (pid=$relay_pid)"
        kill "$relay_pid" 2>/dev/null || true
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

    # Runtime source footprint (local checkout vs marketplace)
    if [[ -f "$INSTALL_DIR/deploy-manifest.json" ]]; then
        local _kind _ver
        _kind=$(grep -o '"kind": *"[^"]*"' "$INSTALL_DIR/deploy-manifest.json" | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
        _ver=$(grep -o '"version": *"[^"]*"' "$INSTALL_DIR/deploy-manifest.json" | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
        [[ -n "$_kind" ]] && _ok "Source: $_kind ($_ver)"
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

_runtime_healthy() {
    # True if the venv python can import the agent-bridge runtime + key deps.
    # Used to decide whether to snapshot the current venv and to verify a fresh
    # install before declaring the update good (#52). uvicorn + credential_relay
    # are the modules that went missing in the observed broken-venv outage.
    [[ -x "$VENV_DIR/bin/python" ]] || return 1
    "$VENV_DIR/bin/python" -c 'import agent_bridge, uvicorn, credential_relay, zdd' 2>/dev/null
}

# Version of the agent-bridge package currently installed in the runtime venv.
# Prints the version to stdout; returns 1 if it cannot be determined (e.g. no
# venv, or a broken install) so the caller can skip the downgrade guard.
_installed_version() {
    [[ -x "$VENV_DIR/bin/python" ]] || return 1
    local v
    v="$("$VENV_DIR/bin/python" -c \
        'from importlib.metadata import version; print(version("agent-bridge"))' \
        2>/dev/null)" || return 1
    [[ -n "$v" ]] || return 1
    printf '%s\n' "$v"
}

# Version of the agent-bridge source about to be installed (this checkout).
# Read from plugin.json (single source of truth for the plugin build). Prints
# the version to stdout; returns 1 if it cannot be determined.
_source_version() {
    local manifest="$PLUGIN_DIR/plugin.json"
    [[ -f "$manifest" ]] || return 1
    local v
    v="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
        "$manifest" | head -n1)"
    [[ -n "$v" ]] || return 1
    printf '%s\n' "$v"
}

# True (0) if version $1 is strictly older than version $2. Normalizes the PEP
# 440 dev separator first -- plugin.json carries `0.4.0-dev93` (hyphen) but
# importlib.metadata reports the normalized `0.4.0.dev93` (dot), so without this
# an equal version would not compare equal. `sort -V` then orders our
# `0.4.0.devN` build stream correctly (dev71 < dev93 < dev100).
_version_lt() {
    local a="${1//-/.}" b="${2//-/.}"
    [[ "$a" == "$b" ]] && return 1
    local lower
    lower="$(printf '%s\n%s\n' "$a" "$b" | sort -V | head -n1)"
    [[ "$lower" == "$a" ]]
}

# Downgrade guard (#1790). A stress test caught an agent running the raw
# installer from a STALE checkout (dev71) over a live dev87 daemon, silently
# downgrading it -- reverting the Session-Host survival code and the
# KillMode=process fix, and stranding the agent's own session. Refuse to
# install an OLDER version over a newer running one unless --force
# (AGENT_BRIDGE_ALLOW_DOWNGRADE=1) is given, and steer to the marketplace path.
# Non-fatal when either version is unknown -- the guard only fires on a
# confirmed downgrade.
_downgrade_guard() {
    local installed source
    installed="$(_installed_version)" || return 0
    source="$(_source_version)" || {
        _warn "Could not read source version from plugin.json -- skipping downgrade guard"
        return 0
    }
    if _version_lt "$source" "$installed"; then
        if [[ "$FORCE" == true ]]; then
            _warn "Downgrade $installed -> $source forced (--force / AGENT_BRIDGE_ALLOW_DOWNGRADE)"
            return 0
        fi
        echo ""
        _fail "Refusing to downgrade agent-bridge: installed $installed > source $source"
        _fail "This checkout is OLDER than the running daemon. Installing it would"
        _fail "revert live features (e.g. Session-Host survival, KillMode=process)"
        _fail "and can strand active Copilot sessions (#1790)."
        _fail ""
        _fail "Use the sanctioned marketplace update instead:"
        _fail "    aperture-labs services agent-bridge update"
        _fail "Or, to override intentionally (e.g. a deliberate rollback):"
        _fail "    $0 $ACTION --force"
        echo ""
        exit 1
    fi
}

_backup_venv() {
    # Snapshot $VENV_DIR so a failed update can roll back. Clears any stale copy.
    rm -rf "$VENV_DIR.bak"
    cp -a "$VENV_DIR" "$VENV_DIR.bak" 2>/dev/null
}

_restore_venv() {
    # Replace a broken $VENV_DIR with the snapshot at $VENV_DIR.bak.
    [[ -d "$VENV_DIR.bak" ]] || return 1
    rm -rf "$VENV_DIR" && mv "$VENV_DIR.bak" "$VENV_DIR"
}

_remove_venv_backup() {
    rm -rf "$VENV_DIR.bak"
}

# Core update steps (venv repair + package installs + verify). Returns non-zero
# on any failure WITHOUT exiting, so the caller can roll back. The service must
# already be stopped before this runs.
_update_core() {
    # Repair venv if python binary is missing
    if [[ ! -x "$VENV_DIR/bin/python" ]]; then
        if [[ -d "$VENV_DIR" ]]; then
            _step "Repairing venv (python binary missing)..."
        else
            _fail "agent-bridge not installed. Run: install.sh install"
            return 1
        fi
        if ! uv venv "$VENV_DIR" --python 3.10 --allow-existing; then
            uv venv "$VENV_DIR" --allow-existing || { _fail "Venv repair failed"; return 1; }
        fi
        _ok "Venv repaired"
    fi

    _step "Updating agent-bridge package..."
    local ssh_manager_dir
    if ssh_manager_dir="$(_resolve_ssh_manager)"; then
        if ! uv pip install --python "$VENV_DIR/bin/python" --reinstall-package agent-ssh-manager \
                "$ssh_manager_dir" --quiet; then
            _fail "ssh-manager update failed"
            return 1
        fi
    elif _ssh_manager_installed; then
        _step "ssh-manager already installed in venv (marketplace layout)"
    else
        _fail "Cannot locate ssh-manager library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer."
        return 1
    fi
    # credential-relay: force-reinstall so a local code change propagates even
    # without a version bump (uv otherwise skips a same-version path dep).
    local cred_relay_dir
    if cred_relay_dir="$(_resolve_credential_relay)"; then
        if ! uv pip install --python "$VENV_DIR/bin/python" --reinstall-package agent-credential-relay \
                "$cred_relay_dir" --quiet; then
            _fail "credential-relay update failed"
            return 1
        fi
    elif _credential_relay_installed; then
        _step "credential-relay already installed in venv (marketplace layout)"
    else
        _fail "Cannot locate credential-relay library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer."
        return 1
    fi
    # zdd: force-reinstall so a local code change propagates even without a
    # version bump (uv otherwise skips a same-version path dep).
    local zdd_dir
    if zdd_dir="$(_resolve_zdd)"; then
        if ! uv pip install --python "$VENV_DIR/bin/python" --reinstall-package agent-zdd \
                "$zdd_dir" --quiet; then
            _fail "zdd update failed"
            return 1
        fi
    elif _zdd_installed; then
        _step "zdd already installed in venv (marketplace layout)"
    else
        _fail "Cannot locate zdd library. Reinstall the agent-bridge plugin from the marketplace (copilot plugin install agent-bridge@copilot-extensions), then rerun this installer."
        return 1
    fi
    if ! uv pip install --python "$VENV_DIR/bin/python" --reinstall-package agent-bridge \
            "$PLUGIN_DIR" --quiet; then
        _fail "Package update failed"
        return 1
    fi

    # Verify the freshly-installed runtime imports before declaring success --
    # catches a half-installed venv (e.g. a wheel/dependency gap) while we can
    # still roll back, rather than starting a broken service.
    if ! _runtime_healthy; then
        _fail "Post-install verification failed (agent_bridge / uvicorn / credential_relay not importable)"
        return 1
    fi
    _ok "Package updated"
    return 0
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

    # Refuse a downgrade from a stale checkout before touching the live daemon
    # (#1790). Runs first so a rejected update never drains/stops the service.
    _downgrade_guard

    # Is the service currently running?
    local was_running=false
    if pid=$(_get_pid) || (command -v systemctl &>/dev/null && systemctl --user is-active "$SYSTEMD_UNIT" &>/dev/null); then
        was_running=true
    fi

    # Snapshot the current healthy venv so a failed install can roll back to the
    # previous-good runtime instead of leaving the service DOWN with a broken/
    # empty venv (#52). Only snapshot a venv that actually works.
    local have_backup=false
    if _runtime_healthy; then
        if _backup_venv; then have_backup=true; fi
    fi

    # Decide the swap strategy:
    #   - Zero-downtime cutover (opt-in via AGENT_BRIDGE_ZERO_DOWNTIME=1): leave
    #     the old daemon RUNNING, reinstall the venv, then `agent-bridge deploy`
    #     stands the new daemon up beside it on a fresh port, flips the routing
    #     table, drains the old daemon, and retires it. No API-unavailable
    #     window and no hard-killed turns. EXPERIMENTAL: the survivor runs
    #     outside systemd until service-manager reconciliation lands -- validate
    #     before relying on it. Falls back to stop/start on any failure.
    #   - Default (drain-then-swap): drain in-flight work for a grace window,
    #     then stop/reinstall/start. No active turn is hard-killed up to the
    #     drain timeout, though a brief API-unavailable window remains.
    local cutover=false
    if [[ "${AGENT_BRIDGE_ZERO_DOWNTIME:-0}" == "1" && "$was_running" == true \
          && -x "$VENV_DIR/bin/agent-bridge" ]]; then
        cutover=true
    fi

    # Stop the running instance before the in-place reinstall, UNLESS we are
    # doing a cutover (which keeps the old daemon up and retires it afterward).
    # Either way, drain first so in-flight turns get a chance to settle.
    if [[ "$was_running" == true && "$cutover" == false ]]; then
        _drain_service "${AGENT_BRIDGE_DRAIN_TIMEOUT:-120}"
        do_stop
    fi

    # Run the protected update; on any failure, roll back to the snapshot.
    if ! _update_core; then
        _fail "Update failed"
        if [[ "$have_backup" == true ]]; then
            _step "Rolling back to the previous venv..."
            if _restore_venv; then
                _ok "Previous venv restored"
                # Only restart in the default path -- in cutover mode the old
                # daemon was never stopped, so it is still serving.
                if [[ "$was_running" == true && "$cutover" == false ]]; then
                    _step "Restarting the previous service..."
                    do_start
                fi
            else
                _fail "Rollback failed -- run install.sh install to rebuild the runtime"
            fi
        else
            _warn "No healthy venv snapshot to roll back to -- run install.sh install to rebuild"
        fi
        exit 1
    fi

    # Success: discard the rollback snapshot.
    _remove_venv_backup

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

    # Bring the new version into service.
    if [[ "$cutover" == true ]]; then
        _step "Zero-downtime cutover (agent-bridge deploy)..."
        if "$VENV_DIR/bin/agent-bridge" deploy \
                --drain-timeout "${AGENT_BRIDGE_DRAIN_TIMEOUT:-300}"; then
            _ok "Cutover complete -- new daemon active, old retired"
        else
            _warn "Cutover failed -- falling back to drain/stop/start"
            _drain_service 30
            do_stop
            do_start
        fi
    else
        _step "Starting service..."
        do_start
    fi

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
