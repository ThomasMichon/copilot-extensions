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
VENV_DIR="$INSTALL_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_BIN="$VENV_DIR/bin/agent-codespaces"
# ssh-manager dir (contains pyproject.toml): plugin-vendored (marketplace
# layout) or repo-root (git checkout layout).
SSH_MGR_DIR="$PLUGIN_DIR/libs/ssh-manager"
if [[ ! -f "$SSH_MGR_DIR/pyproject.toml" ]]; then
    SSH_MGR_DIR="$REPO_ROOT/libs/ssh-manager"
fi

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

_assert_uv() {
    command -v uv &>/dev/null || { _fail "uv is required but not found on PATH."; exit 1; }
}

# uv pip install ssh-manager (sibling lib) then agent-codespaces into the given
# venv python. Non-editable; deps resolved from pyproject.toml.
_install_package_into() {
    local py="$1"
    if [[ ! -f "$SSH_MGR_DIR/pyproject.toml" ]]; then
        _fail "ssh-manager source not found at $SSH_MGR_DIR"
        return 1
    fi
    uv pip install --python "$py" --reinstall-package ssh-manager "$SSH_MGR_DIR" --quiet || {
        _fail "ssh-manager install failed"; return 1; }
    uv pip install --python "$py" --reinstall-package agent-codespaces "$PLUGIN_DIR" --quiet || {
        _fail "agent-codespaces install failed"; return 1; }
}

# Stamp _build_info.py into the INSTALLED site-packages copy (post-install).
_stamp_build_info() {
    local py="$1" pkg_dir ts commit branch src_norm ver
    pkg_dir="$("$py" -c 'import agent_codespaces, os; print(os.path.dirname(agent_codespaces.__file__))' 2>/dev/null || true)"
    [[ -z "$pkg_dir" ]] && { _warn "Could not locate installed agent_codespaces -- build info not stamped"; return; }
    ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    commit="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    branch="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
    src_norm="$(printf '%s' "$PLUGIN_DIR" | tr '\\' '/')"
    ver="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/pyproject.toml" 2>/dev/null || echo 0.0.0)"
    cat > "$pkg_dir/_build_info.py" <<PYEOF
"""Build provenance -- auto-generated at deploy time. Do not edit."""

from __future__ import annotations

BUILD_INFO: dict[str, str] = {
    "version": "$ver",
    "commit": "$commit",
    "branch": "$branch",
    "build_timestamp": "$ts",
    "source": "$src_norm",
}
PYEOF
}

deploy_venv() {
    _assert_uv
    mkdir -p "$VENV_DIR"
    if ! uv venv "$VENV_DIR" --python 3.11 --allow-existing 2>/dev/null; then
        uv venv "$VENV_DIR" --allow-existing 2>/dev/null || true
    fi
    if [[ ! -f "$VENV_PYTHON" ]]; then
        _fail "Venv creation failed"
        return 1
    fi
    _ok "Venv ready at $VENV_DIR"
}

deploy_package() {
    _install_package_into "$VENV_PYTHON" || return 1
    _stamp_build_info "$VENV_PYTHON"
    _ok "Package installed into venv"

    # Keep the agent-bridge venv's in-process resolver in sync (issue #14): the
    # bridge imports agent_codespaces for the codespace: namespace + relay, so a
    # standalone codespaces update must refresh that copy or it drifts stale.
    local bridge_py="$HOME/.agent-bridge/venv/bin/python"
    if [[ -x "$bridge_py" ]]; then
        if _install_package_into "$bridge_py"; then
            _ok "Refreshed agent-bridge venv resolver copy"
        else
            _warn "Could not refresh agent-bridge venv -- its codespace resolver may be stale"
        fi
    fi
}

deploy_binstub() {
    mkdir -p "$LOCAL_BIN"
    local stub_path="$LOCAL_BIN/agent-codespaces"
    cat > "$stub_path" << 'STUB'
#!/usr/bin/env bash
export PYTHONUTF8=1
exec "$HOME/.agent-codespaces/.venv/bin/agent-codespaces" "$@"
STUB
    chmod +x "$stub_path"
    _ok "Binstub: $stub_path"
}

write_deploy_manifest() {
    local manifest_path="$INSTALL_DIR/deploy-manifest.json"
    local kind ver commit branch dirty
    kind="$(_source_kind "$PLUGIN_DIR")"
    ver="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/pyproject.toml" 2>/dev/null || echo 0.0.0)"
    commit="null"; branch="null"; dirty="false"
    if [[ "$kind" == "local" ]]; then
        local c b d
        read -r c b d <<< "$(_git_info "$REPO_ROOT")"
        commit="\"$c\""; branch="\"$b\""; dirty="$d"
    fi
    local tmp="$manifest_path.tmp"
    cat > "$tmp" << MANIFEST
{
  "schema_version": 3,
  "service": "agent-codespaces",
  "deployed_at": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "deployed_by": "$(hostname)-$(uname -s | tr '[:upper:]' '[:lower:]')",
  "source": {
    "kind": "$kind",
    "path": "$PLUGIN_DIR",
    "repo": "copilot-extensions",
    "plugin": "agent-codespaces",
    "version": "$ver",
    "commit": $commit,
    "branch": $branch,
    "dirty": $dirty
  },
  "venv": "$VENV_DIR",
  "runtime": "python"
}
MANIFEST
    mv -f "$tmp" "$manifest_path"
    _ok "Deploy manifest written (source: $kind)"
}

# -- Actions ---------------------------------------------------------------

do_install() {
    _header "$SERVICE_NAME Install"

    # Create directories
    mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"

    # Deploy venv
    deploy_venv || return 1

    # Deploy package
    deploy_package || return 1

    # Deploy binstub
    deploy_binstub

    # Write manifest
    write_deploy_manifest

    # Verify (import from the venv -- no PYTHONPATH)
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

    # Stop managed SSH ControlMaster connections before removing files. They
    # multiplex connections to CodeSpaces via sockets under
    # ~/.agent-codespaces/sockets. Close each via `ssh -O exit` (best-effort),
    # then kill any lingering ssh master referencing the socket dir.
    local socket_dir="$INSTALL_DIR/sockets"
    if [[ -d "$socket_dir" ]]; then
        for sock in "$socket_dir"/*; do
            [[ -e "$sock" ]] || continue
            ssh -o "ControlPath=$sock" -O exit placeholder >/dev/null 2>&1 || true
        done
    fi
    if command -v pkill &>/dev/null; then
        pkill -f "ControlPath=$INSTALL_DIR/sockets" 2>/dev/null && \
            _changed "Stopped managed SSH ControlMaster processes" || true
    fi

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

    # Package (installed into the venv)
    if "$VENV_PYTHON" -c 'import agent_codespaces' 2>/dev/null; then
        _ok "Package: agent_codespaces importable in venv"
    else
        _fail "Package not importable in venv"
    fi

    # ssh-manager
    if "$VENV_PYTHON" -c 'import ssh_manager' 2>/dev/null; then
        _ok "ssh-manager: importable in venv"
    else
        _fail "ssh-manager not importable in venv"
    fi

    # Console script
    if [[ -x "$VENV_BIN" ]]; then
        _ok "Console script: $VENV_BIN"
    else
        _fail "Console script missing: $VENV_BIN"
    fi

    # Binstub
    local stub_path="$LOCAL_BIN/agent-codespaces"
    if [[ -f "$stub_path" ]]; then
        _ok "Binstub: $stub_path"
    else
        _warn "Binstub not found at $stub_path"
    fi

    # Version (from the installed package)
    if [[ -x "$VENV_BIN" ]]; then
        local ver_info
        ver_info="$("$VENV_BIN" version 2>/dev/null || true)"
        [[ -n "$ver_info" ]] && _ok "Version: $ver_info"
    fi

    # Deploy manifest + source footprint (local checkout vs marketplace)
    local manifest="$INSTALL_DIR/deploy-manifest.json"
    if [[ -f "$manifest" ]]; then
        local _kind _ver _dep
        _kind=$(grep -o '"kind": *"[^"]*"' "$manifest" | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
        _ver=$(grep -o '"version": *"[^"]*"' "$manifest" | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
        [[ -n "$_kind" ]] && _ok "Source: $_kind ($_ver)"
        _dep=$(grep -o '"deployed_at": *"[^"]*"' "$manifest" | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
        [[ -n "$_dep" ]] && _ok "Deployed: $_dep"
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
