#!/usr/bin/env bash
# Bootstrap the agent-codespaces runtime.
# Creates ~/.agent-codespaces/ with venv, package, ssh-manager, binstub.
# Idempotent -- safe to re-run for repairs or upgrades.
set -euo pipefail

# -- Output helpers -----------------------------------------------------

ok()   { echo "  [OK]   $1"; }
skip() { echo "  [SKIP] $1"; }
fail() { echo "  [FAIL] $1" >&2; }
step() { echo "  ...    $1"; }

# -- Paths --------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PKG_SRC_DIR="$PLUGIN_DIR/src/agent_codespaces"

# ssh-manager: prefer the plugin-vendored copy (marketplace layout), fall back
# to the repo-root copy (git checkout layout).
REPO_ROOT="$(cd "$PLUGIN_DIR/../.." && pwd)"
SSH_MGR_DIR="$PLUGIN_DIR/libs/ssh-manager"
if [[ ! -d "$SSH_MGR_DIR/src/ssh_manager" ]]; then
    SSH_MGR_DIR="$REPO_ROOT/libs/ssh-manager"
fi

INSTALL_DIR="${1:-$HOME/.agent-codespaces}"
LIB_DIR="$INSTALL_DIR/lib"
VENV_DIR="$INSTALL_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
LOCAL_BIN="$HOME/.local/bin"

# -- Preflight checks --------------------------------------------------

echo ''
echo '=== agent-codespaces init ==='
echo ''

if [[ ! -d "$PKG_SRC_DIR" ]]; then
    fail "Package source not found at $PKG_SRC_DIR"
    exit 1
fi

if [[ ! -d "$SSH_MGR_DIR/src/ssh_manager" ]]; then
    fail "ssh-manager not found (looked in plugin libs/ and repo libs/)"
    echo "  Reinstall the agent-codespaces plugin from the marketplace:"
    echo "    copilot plugin install agent-codespaces@copilot-extensions"
    exit 1
fi

# Find Python -- install if missing
PYTHON_CMD=""
for candidate in python3 python; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON_CMD="$candidate"
        break
    fi
done
if [[ -z "$PYTHON_CMD" ]]; then
    step "Python not found -- attempting install..."
    if command -v apt-get &>/dev/null; then
        sudo apt-get update -qq && sudo apt-get install -y -qq python3 python3-venv 2>/dev/null
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y -q python3 2>/dev/null
    elif command -v brew &>/dev/null; then
        brew install python3 2>/dev/null
    fi
    for candidate in python3 python; do
        if command -v "$candidate" &>/dev/null; then
            PYTHON_CMD="$candidate"
            break
        fi
    done
    if [[ -z "$PYTHON_CMD" ]]; then
        fail "Python not found on PATH (need 3.10+)"
        echo "  Install with: apt install python3, dnf install python3, or brew install python3"
        exit 1
    fi
fi

py_ver="$($PYTHON_CMD -c 'import sys; print("{}.{}".format(sys.version_info.major, sys.version_info.minor))')"
ok "Python: $PYTHON_CMD ($py_ver)"

git_ver="$(git --version 2>/dev/null || true)"
if [[ -z "$git_ver" ]]; then
    fail "git not found on PATH"
    exit 1
fi
ok "Git: $git_ver"

gh_ver="$(gh --version 2>/dev/null | head -1 || true)"
if [[ -z "$gh_ver" ]]; then
    step "gh CLI not found -- agent-codespaces requires it for CodeSpace operations"
else
    ok "gh CLI: $gh_ver"
fi

# Check for uv -- install if missing
if ! command -v uv &>/dev/null; then
    step "uv not found -- installing..."
    if command -v curl &>/dev/null; then
        curl -LsSf https://astral.sh/uv/install.sh 2>/dev/null | sh 2>/dev/null
        export PATH="$HOME/.local/bin:$PATH"
        if command -v uv &>/dev/null; then
            ok "uv installed"
        fi
    fi
fi

# Ensure ~/.local/bin is on PATH
if [[ ":$PATH:" != *":$LOCAL_BIN:"* ]]; then
    export PATH="$LOCAL_BIN:$PATH"
fi

# -- 1. Create directories ---------------------------------------------

mkdir -p "$INSTALL_DIR" "$LIB_DIR" "$LOCAL_BIN"
ok "Directories: $INSTALL_DIR"

# -- 2. Create venv ----------------------------------------------------

if [[ ! -f "$VENV_PYTHON" ]]; then
    if command -v uv &>/dev/null; then
        step "Creating venv via uv..."
        if ! uv venv "$VENV_DIR" --allow-existing 2>/dev/null; then
            step "uv failed -- falling back to python -m venv"
            $PYTHON_CMD -m venv "$VENV_DIR"
        fi
    else
        step "Creating venv via python -m venv..."
        $PYTHON_CMD -m venv "$VENV_DIR"
    fi

    if [[ ! -f "$VENV_PYTHON" ]]; then
        fail "Venv creation failed -- $VENV_PYTHON not found"
        exit 1
    fi
    ok "Venv created"
else
    skip "Venv already exists"
fi

# -- 3. Install dependencies -------------------------------------------

if command -v uv &>/dev/null; then
    uv pip install --python "$VENV_PYTHON" pyyaml 2>/dev/null
else
    "$VENV_PYTHON" -m pip install --quiet pyyaml 2>/dev/null
fi
ok "Dependencies: pyyaml"

# -- 4. Deploy package (file copy) -------------------------------------

PKG_DST="$LIB_DIR/agent_codespaces"
rm -rf "$PKG_DST"
cp -r "$PKG_SRC_DIR" "$PKG_DST"

# Deploy ssh-manager alongside (agent_codespaces imports it)
SSH_MGR_SRC="$SSH_MGR_DIR/src/ssh_manager"
SSH_MGR_DST="$LIB_DIR/ssh_manager"
if [[ -d "$SSH_MGR_SRC" ]]; then
    rm -rf "$SSH_MGR_DST"
    cp -r "$SSH_MGR_SRC" "$SSH_MGR_DST"
    ok "ssh-manager deployed to $SSH_MGR_DST"
else
    fail "ssh-manager source not found at $SSH_MGR_SRC"
    exit 1
fi

# Stamp build info so --version reflects this deployment
_commit="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
_branch="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
_ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
_src_norm="$(echo "$PLUGIN_DIR" | tr '\\' '/')"
_ver="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/pyproject.toml" 2>/dev/null || echo 0.0.0)"
cat > "$PKG_DST/_build_info.py" <<PYEOF
"""Build provenance -- auto-generated at deploy time. Do not edit."""

from __future__ import annotations

BUILD_INFO: dict[str, str] = {
    "version": "$_ver",
    "commit": "$_commit",
    "branch": "$_branch",
    "build_timestamp": "$_ts",
    "source": "$_src_norm",
}
PYEOF

ok "Package deployed to $PKG_DST"

# -- 5. Deploy binstub -------------------------------------------------

stub_path="$LOCAL_BIN/agent-codespaces"
cat > "$stub_path" << 'STUB'
#!/usr/bin/env bash
export PYTHONUTF8=1
export PYTHONPATH="$HOME/.agent-codespaces/lib${PYTHONPATH:+:$PYTHONPATH}"
exec "$HOME/.agent-codespaces/.venv/bin/python" -m agent_codespaces "$@"
STUB
chmod +x "$stub_path"
ok "Binstub: $stub_path"

# -- 6. Write deploy manifest ------------------------------------------

manifest_path="$INSTALL_DIR/deploy-manifest.json"
cat > "$manifest_path" << MANIFEST
{
  "service": "agent-codespaces",
  "commit": "${_commit:-unknown}",
  "deployed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "runtime": "python",
  "plugin_source": "$PLUGIN_DIR",
  "install_dir": "$INSTALL_DIR"
}
MANIFEST
ok "Manifest: $manifest_path"

# -- 7. Verify ----------------------------------------------------------

echo ''
export PYTHONPATH="$LIB_DIR"
import_check="$("$VENV_PYTHON" -c 'import agent_codespaces; print("OK")' 2>/dev/null || true)"
if [[ "$import_check" == "OK" ]]; then
    ok "Verification: module imports successfully"
else
    fail "Verification: module import failed"
    exit 1
fi

# Check PATH and add ~/.local/bin if missing
if echo "$PATH" | tr ':' '\n' | grep -qx "$LOCAL_BIN"; then
    ok "PATH: $LOCAL_BIN is on PATH"
else
    export PATH="$LOCAL_BIN:$PATH"
    ok "PATH: Added $LOCAL_BIN to current session"

    shell_profile=""
    if [[ -n "${ZSH_VERSION:-}" ]]; then
        shell_profile="$HOME/.zshrc"
    elif [[ -f "$HOME/.bashrc" ]]; then
        shell_profile="$HOME/.bashrc"
    elif [[ -f "$HOME/.profile" ]]; then
        shell_profile="$HOME/.profile"
    fi
    if [[ -n "$shell_profile" ]] && ! grep -q '.local/bin' "$shell_profile" 2>/dev/null; then
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$shell_profile"
        ok "PATH: Added to $shell_profile"
    fi
fi

echo ''
echo '=== agent-codespaces init complete ==='
echo ''
