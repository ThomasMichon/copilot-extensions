#!/usr/bin/env bash
# Bootstrap the agent-dispatch runtime (Linux / WSL / macOS).
#
# Creates the shared runtime at ~/.agent-dispatch/ -- a venv with the
# agent_dispatch package installed (via uv pip install) -- and deploys the
# `agent-dispatch` binstub into ~/.local/bin.
#
# Run once per machine. Idempotent -- safe to re-run for repairs or upgrades.
#
# Usage:
#   ./init.sh [--force] [--install-dir DIR]

set -euo pipefail

_ok()   { printf '  [OK]   %s\n' "$1"; }
_skip() { printf '  [SKIP] %s\n' "$1"; }
_fail() { printf '  [FAIL] %s\n' "$1" >&2; }
_step() { printf '  ...    %s\n' "$1"; }

FORCE=0
SERVICE=0
INSTALL_DIR=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --force) FORCE=1; shift ;;
        --service) SERVICE=1; shift ;;
        --install-dir) INSTALL_DIR="$2"; shift 2 ;;
        *) shift ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PKG_SRC_DIR="$PLUGIN_DIR/src/agent_dispatch"

INSTALL_DIR="${INSTALL_DIR:-$HOME/.agent-dispatch}"
VENV_DIR="$INSTALL_DIR/.venv"
LOCAL_BIN="$HOME/.local/bin"
VENV_PYTHON="$VENV_DIR/bin/python"

echo ''
echo '=== agent-dispatch init ==='
echo ''

if [[ ! -d "$PKG_SRC_DIR" ]]; then
    _fail "Package source not found at $PKG_SRC_DIR"
    exit 1
fi

# Find a Python interpreter
PYTHON_CMD=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" --version 2>&1 | grep -qi python; then
            PYTHON_CMD="$candidate"
            break
        fi
    fi
done
if [[ -z "$PYTHON_CMD" ]]; then
    _fail 'Python not found on PATH (need 3.10+)'
    exit 1
fi
_ok "Python: $PYTHON_CMD"

HAVE_UV=0
if command -v uv >/dev/null 2>&1; then HAVE_UV=1; fi

# -- 1. Directories ----------------------------------------------------
mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"
_ok "Directories: $INSTALL_DIR"

# -- 2. Venv -----------------------------------------------------------
if [[ "$FORCE" -eq 1 || ! -x "$VENV_PYTHON" ]]; then
    if [[ "$HAVE_UV" -eq 1 ]]; then
        _step 'Creating venv via uv...'
        uv venv "$VENV_DIR" --allow-existing >/dev/null 2>&1 || {
            _step 'uv venv failed -- falling back to python -m venv'
            "$PYTHON_CMD" -m venv "$VENV_DIR" >/dev/null 2>&1
        }
    else
        _step 'Creating venv via python -m venv...'
        "$PYTHON_CMD" -m venv "$VENV_DIR" >/dev/null 2>&1
    fi
    if [[ ! -x "$VENV_PYTHON" ]]; then
        _fail "Venv creation failed -- $VENV_PYTHON not found"
        exit 1
    fi
    _ok 'Venv created'
else
    _skip 'Venv already exists'
fi

# -- 3. Install the package into the venv ------------------------------
# The [mcp] extra ships the `agent-dispatch mcp` stdio server dependency.
if [[ "$HAVE_UV" -eq 1 ]]; then
    if ! uv pip install --python "$VENV_PYTHON" "${PLUGIN_DIR}[mcp]" --quiet 2>/dev/null; then
        _fail 'Failed to install agent-dispatch package into venv'
        exit 1
    fi
else
    if ! "$VENV_PYTHON" -m pip install --quiet "${PLUGIN_DIR}[mcp]" 2>/dev/null; then
        _fail 'Failed to install agent-dispatch package into venv'
        exit 1
    fi
fi
_ok 'Package installed: agent-dispatch'

# -- 4. Binstub --------------------------------------------------------
STUB="$LOCAL_BIN/agent-dispatch"
cat > "$STUB" << 'STUBEOF'
#!/usr/bin/env bash
export PYTHONUTF8=1
exec "$HOME/.agent-dispatch/.venv/bin/python" -m agent_dispatch "$@"
STUBEOF
chmod +x "$STUB"
_ok "Binstub: $STUB"

# -- 5. Deploy manifest ------------------------------------------------
# === install-contract:v3 source-kind -- keep byte-identical across plugins ===
_source_kind() {
    case "$(printf '%s' "$1" | tr '\\' '/')" in
        */.copilot/installed-plugins/*) printf 'marketplace' ;;
        *) printf 'local' ;;
    esac
}
# === end install-contract:v3 source-kind ===
_git_info() {
    local path="$1" commit branch dirty
    commit=$(git -C "$path" rev-parse --short HEAD 2>/dev/null || echo "unknown")
    branch=$(git -C "$path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
    dirty="false"
    [[ -n "$(git -C "$path" status --porcelain 2>/dev/null)" ]] && dirty="true"
    echo "$commit $branch $dirty"
}

# Unified schema_version 3 manifest (install-contract): records the source
# footprint (marketplace vs local) so deploys are auditable like the siblings.
MANIFEST_PATH="$INSTALL_DIR/deploy-manifest.json"
KIND="$(_source_kind "$PLUGIN_DIR")"
VER="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/pyproject.toml" 2>/dev/null || echo 0.0.0)"
COMMIT="null"; BRANCH="null"; DIRTY="false"
if [[ "$KIND" == "local" ]]; then
    REPO_ROOT="$(cd "$PLUGIN_DIR/../.." && pwd)"
    read -r _c _b _d <<< "$(_git_info "$REPO_ROOT")"
    COMMIT="\"$_c\""; BRANCH="\"$_b\""; DIRTY="$_d"
fi
TMP="$MANIFEST_PATH.tmp"
cat > "$TMP" << EOF
{
  "schema_version": 3,
  "service": "agent-dispatch",
  "deployed_at": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "deployed_by": "$(hostname)-$(uname -s | tr '[:upper:]' '[:lower:]')",
  "source": {
    "kind": "$KIND",
    "path": "$PLUGIN_DIR",
    "repo": "copilot-extensions",
    "plugin": "agent-dispatch",
    "version": "$VER",
    "commit": $COMMIT,
    "branch": $BRANCH,
    "dirty": $DIRTY
  },
  "venv": "$VENV_DIR",
  "runtime": "python"
}
EOF
mv -f "$TMP" "$MANIFEST_PATH"
_ok "Deploy manifest written (source: $KIND)"

# -- 6. Verify ---------------------------------------------------------
echo ''
if "$VENV_PYTHON" -c 'import agent_dispatch' 2>/dev/null; then
    _ok 'Verification: module imports successfully'
else
    _fail 'Verification: module import failed'
    exit 1
fi

case ":$PATH:" in
    *":$LOCAL_BIN:"*) _ok "PATH: $LOCAL_BIN is on PATH" ;;
    *) _step "Add $LOCAL_BIN to your PATH (e.g. in ~/.bashrc): export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

# -- 6b. Register the worktree-picker "Tasks" pivot --------------------
# agent-worktrees discovers contributed picker pivots from a filesystem
# manifest registry (separate venvs rule out Python entry-points). Drop our
# manifest into the shared runtime root so the picker grows a "Tasks" pivot.
# Best-effort: never fail the install if the copy can't happen.
PIVOT_SRC="$PLUGIN_DIR/pivots/agent-dispatch.json"
PIVOT_DIR="$HOME/.agent-worktrees/pivots"
if [[ -f "$PIVOT_SRC" ]]; then
    if mkdir -p "$PIVOT_DIR" 2>/dev/null && cp -f "$PIVOT_SRC" "$PIVOT_DIR/agent-dispatch.json" 2>/dev/null; then
        _ok "Picker pivot registered: $PIVOT_DIR/agent-dispatch.json"
    else
        _skip "Could not register picker pivot (agent-worktrees runtime root not writable)"
    fi
else
    _skip "Picker pivot manifest not found at $PIVOT_SRC"
fi

# -- 7. Optional coordinator service (systemd user unit) ---------------
# The coordinator is the always-on single writer. Install it as a service only
# when asked (--service); a machine that is only a *client* of a remote
# coordinator does not run a local one.
if [[ "$SERVICE" -eq 1 ]]; then
    SYSTEMD_UNIT="agent-dispatch.service"
    if ! command -v systemctl >/dev/null 2>&1; then
        _skip "systemd not available -- skipping coordinator service (run 'agent-dispatch serve')"
    else
        UNIT_DIR="$HOME/.config/systemd/user"
        ENV_FILE="$INSTALL_DIR/service.env"
        mkdir -p "$UNIT_DIR"
        if [[ ! -f "$ENV_FILE" ]]; then
            cat > "$ENV_FILE" << 'ENVEOF'
# agent-dispatch coordinator service environment (edit + `systemctl --user restart agent-dispatch`)
AGENT_DISPATCH_HOST=127.0.0.1
AGENT_DISPATCH_PORT=9330
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
        _ok "Coordinator service installed + started ($SYSTEMD_UNIT)"
    fi
fi

echo ''
echo '=== agent-dispatch init complete ==='
if [[ "$SERVICE" -eq 1 ]]; then
    echo '  Coordinator: systemctl --user status agent-dispatch'
else
    echo '  Try: agent-dispatch version   (add --service to run the coordinator as a systemd unit)'
fi
exit 0
