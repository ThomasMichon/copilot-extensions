#!/usr/bin/env bash
# Bootstrap the agent-containers runtime (Linux / WSL / macOS).
#
# Creates the shared runtime at ~/.agent-containers/ -- a venv with the
# agent_containers package installed (via uv pip install) -- and deploys the
# `agent-containers` binstub into ~/.local/bin.
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
INSTALL_DIR=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --force) FORCE=1; shift ;;
        --install-dir) INSTALL_DIR="$2"; shift 2 ;;
        *) shift ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PKG_SRC_DIR="$PLUGIN_DIR/src/agent_containers"

INSTALL_DIR="${INSTALL_DIR:-$HOME/.agent-containers}"
VENV_DIR="$INSTALL_DIR/.venv"
LOCAL_BIN="$HOME/.local/bin"
VENV_PYTHON="$VENV_DIR/bin/python"
# credential-relay dir (vendored): plugin-vendored or repo-root. Force-reinstalled
# below so a local code change propagates even without a version bump.
CRED_RELAY_DIR="$PLUGIN_DIR/libs/credential-relay"
if [[ ! -f "$CRED_RELAY_DIR/pyproject.toml" ]]; then
    CRED_RELAY_DIR="$(cd "$PLUGIN_DIR/../.." && pwd)/libs/credential-relay"
fi

echo ''
echo '=== agent-containers init ==='
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

if command -v docker >/dev/null 2>&1; then
    _ok "Docker: $(docker --version 2>/dev/null)"
else
    _step 'docker CLI not found -- agent-containers requires Docker for fleet operations'
fi

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
if [[ "$HAVE_UV" -eq 1 ]]; then
    # credential-relay first (vendored lib), force-reinstalled so local code
    # changes propagate even without a version bump; then agent-containers.
    if [[ ! -f "$CRED_RELAY_DIR/pyproject.toml" ]]; then
        _fail "credential-relay source not found at $CRED_RELAY_DIR"
        exit 1
    fi
    if ! uv pip install --python "$VENV_PYTHON" --reinstall-package agent-credential-relay "$CRED_RELAY_DIR" --quiet 2>/dev/null; then
        _fail 'credential-relay install failed'
        exit 1
    fi
    if ! uv pip install --python "$VENV_PYTHON" "$PLUGIN_DIR" --quiet 2>/dev/null; then
        _fail 'Failed to install agent-containers package into venv'
        exit 1
    fi
else
    if ! "$VENV_PYTHON" -m pip install --quiet "$PLUGIN_DIR" 2>/dev/null; then
        _fail 'Failed to install agent-containers package into venv'
        exit 1
    fi
fi
_ok 'Package installed: agent-containers'

# -- 4. Binstub --------------------------------------------------------
STUB="$LOCAL_BIN/agent-containers"
cat > "$STUB" << 'STUBEOF'
#!/usr/bin/env bash
export PYTHONUTF8=1
exec "$HOME/.agent-containers/.venv/bin/python" -m agent_containers "$@"
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
  "service": "agent-containers",
  "deployed_at": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "deployed_by": "$(hostname)-$(uname -s | tr '[:upper:]' '[:lower:]')",
  "source": {
    "kind": "$KIND",
    "path": "$PLUGIN_DIR",
    "repo": "copilot-extensions",
    "plugin": "agent-containers",
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
if "$VENV_PYTHON" -c 'import agent_containers' 2>/dev/null; then
    _ok 'Verification: module imports successfully'
else
    _fail 'Verification: module import failed'
    exit 1
fi

if "$VENV_PYTHON" -c 'import credential_relay' 2>/dev/null; then
    _ok 'credential-relay: importable in venv'
else
    _fail 'credential-relay not importable in venv'
    exit 1
fi

case ":$PATH:" in
    *":$LOCAL_BIN:"*) _ok "PATH: $LOCAL_BIN is on PATH" ;;
    *) _step "Add $LOCAL_BIN to your PATH (e.g. in ~/.bashrc): export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

echo ''
echo '=== agent-containers init complete ==='
echo '  Try: agent-containers version'
exit 0
