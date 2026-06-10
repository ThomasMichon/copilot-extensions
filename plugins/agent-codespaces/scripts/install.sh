#!/usr/bin/env bash
# =============================================================================
# install.sh -- Agent Codespaces -- standardized installer interface
# =============================================================================
# Manages the agent-codespaces infrastructure lifecycle: install, uninstall,
# status, update.
#
# Runtime at ~/.agent-codespaces/; binstub at ~/.local/bin/agent-codespaces.
#
# Usage:
#   bash plugins/agent-codespaces/scripts/install.sh install
#   bash plugins/agent-codespaces/scripts/install.sh status
#   bash plugins/agent-codespaces/scripts/install.sh update
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$PLUGIN_DIR/../.." && pwd)"

# Ensure ~/.local/bin is on PATH
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    export PATH="$HOME/.local/bin:$PATH"
fi

# -- Parse arguments -------------------------------------------------------

ACTION="${1:-status}"
shift || true

FORCE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force) FORCE=true; shift ;;
        *)       echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# -- Metadata --------------------------------------------------------------

SERVICE_NAME="Agent Codespaces"
INSTALL_DIR="$HOME/.agent-codespaces"
LOCAL_BIN="$HOME/.local/bin"
LIB_DIR="$INSTALL_DIR/lib"
VENV_DIR="$INSTALL_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
PKG_SRC_DIR="$PLUGIN_DIR/src/agent_codespaces"
# ssh-manager: prefer the plugin-vendored copy (marketplace layout), fall back
# to the repo-root copy (git checkout layout).
SSH_MGR_DIR="$PLUGIN_DIR/libs/ssh-manager"
if [[ ! -d "$SSH_MGR_DIR/src/ssh_manager" ]]; then
    SSH_MGR_DIR="$REPO_ROOT/libs/ssh-manager"
fi
SSH_MGR_SRC="$SSH_MGR_DIR/src/ssh_manager"

DEPLOY_SOURCE_PATHS=("plugins/agent-codespaces/")
INSTALLER_REL_PATH="plugins/agent-codespaces/scripts/install.sh"

# -- Status output helpers -------------------------------------------------

_ok()      { echo "  [OK]   $*"; }
_changed() { echo "  [->]   $*"; }
_skip()    { echo "  [SKIP] $*"; }
_warn()    { echo "  [WARN] $*"; }
_fail()    { echo "  [FAIL] $*" >&2; }
_step()    { echo "  ...    $*"; }
_header()  { echo ""; echo "=== $* ==="; }

# -- Helpers ---------------------------------------------------------------

deploy_venv() {
    mkdir -p "$VENV_DIR"
    if command -v uv &>/dev/null; then
        if ! uv venv "$VENV_DIR" --allow-existing 2>/dev/null; then
            python3 -m venv "$VENV_DIR"
        fi
    else
        python3 -m venv "$VENV_DIR"
    fi

    if [[ ! -f "$VENV_PYTHON" ]]; then
        _fail "Venv creation failed"
        return 1
    fi

    # Install pyyaml
    if command -v uv &>/dev/null; then
        uv pip install --python "$VENV_PYTHON" pyyaml 2>/dev/null
    else
        "$VENV_PYTHON" -m pip install --quiet pyyaml 2>/dev/null
    fi

    _ok "Venv ready at $VENV_DIR"
}

deploy_package() {
    local dst="$LIB_DIR/agent_codespaces"
    if [[ ! -d "$PKG_SRC_DIR" ]]; then
        _fail "Package source not found: $PKG_SRC_DIR"
        return 1
    fi

    rm -rf "$dst"
    mkdir -p "$LIB_DIR"
    cp -r "$PKG_SRC_DIR" "$dst"

    # Deploy ssh-manager
    local ssh_dst="$LIB_DIR/ssh_manager"
    if [[ ! -d "$SSH_MGR_SRC" ]]; then
        _fail "ssh-manager source not found: $SSH_MGR_SRC"
        return 1
    fi
    rm -rf "$ssh_dst"
    cp -r "$SSH_MGR_SRC" "$ssh_dst"
    _ok "ssh-manager deployed"

    # Stamp build info
    local _commit _branch _ts _src_norm _ver
    _commit="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
    _branch="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
    _ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    _src_norm="$(echo "$PLUGIN_DIR" | tr '\\' '/')"
    _ver="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/pyproject.toml" 2>/dev/null || echo 0.0.0)"
    cat > "$dst/_build_info.py" <<PYEOF
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

    _ok "Package deployed to $dst"
}

deploy_binstub() {
    mkdir -p "$LOCAL_BIN"
    local stub_path="$LOCAL_BIN/agent-codespaces"
    cat > "$stub_path" << 'STUB'
#!/usr/bin/env bash
export PYTHONUTF8=1
export PYTHONPATH="$HOME/.agent-codespaces/lib${PYTHONPATH:+:$PYTHONPATH}"
exec "$HOME/.agent-codespaces/.venv/bin/python" -m agent_codespaces "$@"
STUB
    chmod +x "$stub_path"
    _ok "Binstub: $stub_path"
}

write_deploy_manifest() {
    local _commit _ts
    _commit="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
    _ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    local manifest_path="$INSTALL_DIR/deploy-manifest.json"
    cat > "$manifest_path" << MANIFEST
{
  "service": "agent-codespaces",
  "commit": "$_commit",
  "deployed_at": "$_ts",
  "runtime": "python",
  "plugin_source": "$PLUGIN_DIR",
  "install_dir": "$INSTALL_DIR"
}
MANIFEST
    _ok "Deploy manifest: $manifest_path"
}

# -- Actions ---------------------------------------------------------------

do_install() {
    _header "$SERVICE_NAME Install"

    # Create directories
    mkdir -p "$INSTALL_DIR" "$LIB_DIR" "$LOCAL_BIN"

    # Deploy venv
    deploy_venv || return 1

    # Deploy package
    deploy_package || return 1

    # Deploy binstub
    deploy_binstub

    # Write manifest
    write_deploy_manifest

    # Verify
    export PYTHONPATH="$LIB_DIR"
    local check
    check="$("$VENV_PYTHON" -c 'import agent_codespaces; print("OK")' 2>/dev/null || true)"
    if [[ "$check" == "OK" ]]; then
        _ok "Verification: module imports successfully"
    else
        _fail "Verification: module import failed"
    fi

    echo ""
    _ok "$SERVICE_NAME installed"
}

do_uninstall() {
    _header "$SERVICE_NAME Uninstall"

    # Remove binstub
    local stub_path="$LOCAL_BIN/agent-codespaces"
    if [[ -f "$stub_path" ]]; then
        rm -f "$stub_path"
        _changed "Removed binstub: $stub_path"
    else
        _skip "Binstub not found"
    fi

    # Remove install directory
    if [[ -d "$INSTALL_DIR" ]]; then
        rm -rf "$INSTALL_DIR"
        _changed "Removed: $INSTALL_DIR"
    else
        _skip "Install directory not found"
    fi

    _ok "$SERVICE_NAME uninstalled"
}

do_status() {
    _header "$SERVICE_NAME Status"

    # Install dir
    if [[ -d "$INSTALL_DIR" ]]; then
        _ok "Install dir: $INSTALL_DIR"
    else
        _fail "Not installed ($INSTALL_DIR not found)"
        return
    fi

    # Venv
    if [[ -f "$VENV_PYTHON" ]]; then
        _ok "Venv: $VENV_DIR"
    else
        _fail "Venv missing"
    fi

    # Package
    if [[ -d "$LIB_DIR/agent_codespaces" ]]; then
        _ok "Package: $LIB_DIR/agent_codespaces"
    else
        _fail "Package missing"
    fi

    # ssh-manager
    if [[ -d "$LIB_DIR/ssh_manager" ]]; then
        _ok "ssh-manager: $LIB_DIR/ssh_manager"
    else
        _fail "ssh-manager missing"
    fi

    # Binstub
    local stub_path="$LOCAL_BIN/agent-codespaces"
    if [[ -f "$stub_path" ]]; then
        _ok "Binstub: $stub_path"
    else
        _warn "Binstub not found at $stub_path"
    fi

    # Build info
    local build_info="$LIB_DIR/agent_codespaces/_build_info.py"
    if [[ -f "$build_info" ]]; then
        export PYTHONPATH="$LIB_DIR"
        local ver_info
        ver_info="$("$VENV_PYTHON" -c "
from agent_codespaces._build_info import BUILD_INFO
print(f'v{BUILD_INFO[\"version\"]} ({BUILD_INFO[\"commit\"][:8]})')
" 2>/dev/null || true)"
        if [[ -n "$ver_info" ]]; then
            _ok "Version: $ver_info"
        fi
    fi

    # Deploy manifest
    local manifest="$INSTALL_DIR/deploy-manifest.json"
    if [[ -f "$manifest" ]]; then
        local deployed_at
        deployed_at="$(python3 -c "import json; print(json.load(open('$manifest'))['deployed_at'])" 2>/dev/null || true)"
        if [[ -n "$deployed_at" ]]; then
            _ok "Deployed: $deployed_at"
        fi
    fi

    # gh CLI
    if command -v gh &>/dev/null; then
        _ok "gh CLI: $(command -v gh)"
    else
        _warn "gh CLI not found"
    fi

    # ssh
    if command -v ssh &>/dev/null; then
        _ok "ssh: $(command -v ssh)"
    else
        _warn "ssh not found"
    fi
}

do_update() {
    _header "$SERVICE_NAME Update"

    if [[ ! -d "$INSTALL_DIR" ]]; then
        _warn "Not installed -- running full install"
        do_install
        return
    fi

    # Re-deploy venv
    deploy_venv

    # Re-deploy package
    deploy_package

    # Re-deploy binstub
    deploy_binstub

    # Update manifest
    write_deploy_manifest

    _ok "$SERVICE_NAME updated"
}

# -- Dispatch --------------------------------------------------------------

case "$ACTION" in
    install)   do_install ;;
    uninstall) do_uninstall ;;
    status)    do_status ;;
    update)    do_update ;;
    *)
        echo "Usage: $0 {install|uninstall|status|update}" >&2
        exit 1
        ;;
esac
