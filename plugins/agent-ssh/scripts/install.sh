#!/usr/bin/env bash
# Install/update the agent-ssh runtime (Linux / WSL / macOS).
# Usage: ./install.sh [install|update|status|uninstall] [--force] [--install-dir DIR]

set -euo pipefail

_ok()   { printf '  [OK]   %s\n' "$1"; }
_skip() { printf '  [SKIP] %s\n' "$1"; }
_fail() { printf '  [FAIL] %s\n' "$1" >&2; }
_step() { printf '  ...    %s\n' "$1"; }

ACTION="install"
FORCE=0
INSTALL_DIR=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        install|update|status|uninstall) ACTION="$1"; shift ;;
        --force) FORCE=1; shift ;;
        --install-dir) INSTALL_DIR="$2"; shift 2 ;;
        *) _fail "unknown argument: $1"; exit 2 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PKG_SRC_DIR="$PLUGIN_DIR/src/agent_ssh"
INSTALL_DIR="${INSTALL_DIR:-$HOME/.agent-ssh}"
VENV_DIR="$INSTALL_DIR/.venv"
LOCAL_BIN="$HOME/.local/bin"
VENV_PYTHON="$VENV_DIR/bin/python"
STUB="$LOCAL_BIN/agent-ssh"
MANIFEST_PATH="$INSTALL_DIR/deploy-manifest.json"

# === install-contract:v3 versioned-venv (agent-ssh: .venv-as-symlink) ===
# Immutable per-version runtime (#581): build into versions/<version> and make the
# `.venv` path a symlink into it, so the binstub + manifest resolve through the
# link. CLI (no daemon). LINK_DIR = stable `.venv`; VENV_DIR = the versions/<v>
# slot. Legacy mode: LINK_DIR == VENV_DIR. Gated behind AGENT_SSH_VERSIONED
# (default ON); scripts/versioned_runtime.py owns the swap + migration + gc.
LINK_DIR="$VENV_DIR"
LINK_PYTHON="$VENV_PYTHON"
VERSIONED_RUNTIME=0
SRC_VERSION=""
if [[ "${COPILOT_EXT_NO_VERSIONED:-}" != "1" && ! "${AGENT_SSH_VERSIONED:-}" =~ ^(0|false|no|off)$ ]]; then
    if [[ -f "$PLUGIN_DIR/pyproject.toml" ]]; then
        SRC_VERSION="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/pyproject.toml" | head -n1)"
    fi
    if [[ -n "$SRC_VERSION" ]]; then
        VERSIONED_RUNTIME=1
        VENV_DIR="$INSTALL_DIR/versions/$SRC_VERSION"
        VENV_PYTHON="$VENV_DIR/bin/python"
    fi
fi

_versioned_activate() {
    # CLI (no daemon): health-gate the slot, swap the `.venv` symlink onto it
    # (first migration moves a legacy real `.venv` aside), gc keeping current +
    # previous-good. Returns non-zero on failure. No-op in legacy mode.
    [[ "$VERSIONED_RUNTIME" == 1 ]] || return 0
    local vr="$SCRIPT_DIR/versioned_runtime.py"
    local py="$VENV_DIR/bin/python"
    [[ -x "$py" ]] || py="$LINK_DIR/bin/python"
    [[ -x "$py" ]] || return 0
    if ! "$VENV_PYTHON" -c 'import agent_ssh' 2>/dev/null; then
        _fail "Fresh runtime slot failed its health gate (versions/$SRC_VERSION) -- not activating"
        return 1
    fi
    local prev
    prev="$("$py" "$vr" --root "$INSTALL_DIR" --link-name ".venv" current 2>/dev/null || echo "")"
    if ! "$py" "$vr" --root "$INSTALL_DIR" --link-name ".venv" activate "$SRC_VERSION" --replace-nonlink; then
        _fail "Failed to activate versioned venv (.venv -> versions/$SRC_VERSION)"
        return 1
    fi
    _ok "Runtime version $SRC_VERSION active (.venv -> versions/$SRC_VERSION)"
    if [[ -n "$prev" ]]; then
        "$LINK_DIR/bin/python" "$vr" --root "$INSTALL_DIR" --link-name ".venv" gc --protect-pids --keep "$prev" 2>&1 | sed 's/^/  gc: /' || true
    else
        "$LINK_DIR/bin/python" "$vr" --root "$INSTALL_DIR" --link-name ".venv" gc --protect-pids 2>&1 | sed 's/^/  gc: /' || true
    fi
    return 0
}
# === end install-contract:v3 versioned-venv ===

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

if [[ "$ACTION" == "status" ]]; then
    echo '=== agent-ssh status ==='
    [[ -x "$LINK_PYTHON" ]] && _ok "Venv: $LINK_DIR" || _skip "Venv missing: $LINK_DIR"
    [[ -x "$STUB" ]] && _ok "Binstub: $STUB" || _skip "Binstub missing: $STUB"
    [[ -f "$MANIFEST_PATH" ]] && _ok "Deploy manifest: $MANIFEST_PATH" || _skip "Deploy manifest missing"
    exit 0
fi

if [[ "$ACTION" == "uninstall" ]]; then
    rm -f "$STUB"
    rm -rf "$INSTALL_DIR"
    _ok 'agent-ssh runtime removed'
    exit 0
fi

echo ''
echo '=== agent-ssh install ==='
echo ''

if [[ ! -d "$PKG_SRC_DIR" ]]; then
    _fail "Package source not found at $PKG_SRC_DIR"
    exit 1
fi

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

mkdir -p "$INSTALL_DIR" "$LOCAL_BIN"
_ok "Directories: $INSTALL_DIR"

# -- Deploy the session-start hook (version-gated runtime reconcile) --
# hooks.json runs ~/.agent-ssh/bin/bootstrap-check.sh at session start; it
# re-runs this installer only when the deployed version drifts from the payload.
BIN_HOOK_DIR="$INSTALL_DIR/bin"
mkdir -p "$BIN_HOOK_DIR"
for h in bootstrap-check.ps1 bootstrap-check.sh; do
    [ -f "$SCRIPT_DIR/$h" ] && cp -f "$SCRIPT_DIR/$h" "$BIN_HOOK_DIR/$h"
done
_ok "Session-start hook: $BIN_HOOK_DIR/bootstrap-check.sh"

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

if [[ "$HAVE_UV" -eq 1 ]]; then
    if ! uv pip install --python "$VENV_PYTHON" "$PLUGIN_DIR" --quiet 2>/dev/null; then
        _fail 'Failed to install agent-ssh package into venv'
        exit 1
    fi
else
    if ! "$VENV_PYTHON" -m pip install --quiet "$PLUGIN_DIR" 2>/dev/null; then
        _fail 'Failed to install agent-ssh package into venv'
        exit 1
    fi
fi
_ok 'Package installed: agent-ssh'

# Versioned layout (#581): health-gate the slot + swap the `.venv` symlink.
_versioned_activate || exit 1

cat > "$STUB" << 'STUBEOF'
#!/usr/bin/env bash
export PYTHONUTF8=1
exec "$HOME/.agent-ssh/.venv/bin/python" -m agent_ssh "$@"
STUBEOF
chmod +x "$STUB"
_ok "Binstub: $STUB"

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
  "service": "agent-ssh",
  "deployed_at": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "deployed_by": "$(hostname)-$(uname -s | tr '[:upper:]' '[:lower:]')",
  "source": {
    "kind": "$KIND",
    "path": "$PLUGIN_DIR",
    "repo": "copilot-extensions",
    "plugin": "agent-ssh",
    "version": "$VER",
    "commit": $COMMIT,
    "branch": $BRANCH,
    "dirty": $DIRTY
  },
  "venv": "$LINK_DIR",
  "runtime": "python"
}
EOF
mv -f "$TMP" "$MANIFEST_PATH"
_ok "Deploy manifest written (source: $KIND)"

echo ''
if "$LINK_PYTHON" -c 'import agent_ssh' 2>/dev/null; then
    _ok 'Verification: module imports successfully'
else
    _fail 'Verification: module import failed'
    exit 1
fi

case ":$PATH:" in
    *":$LOCAL_BIN:"*) _ok "PATH: $LOCAL_BIN is on PATH" ;;
    *) _step "Add $LOCAL_BIN to your PATH (e.g. in ~/.bashrc): export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

echo ''
echo '=== agent-ssh install complete ==='
echo '  Try: agent-ssh version'
exit 0
