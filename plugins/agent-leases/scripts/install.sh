#!/usr/bin/env bash
# Install or update the agent-leases runtime (Linux / WSL / macOS).

set -euo pipefail

_ok()   { printf '  [OK]   %s\n' "$1"; }
_fail() { printf '  [FAIL] %s\n' "$1" >&2; }
_step() { printf '  ...    %s\n' "$1"; }

ACTION="${1:-status}"
shift || true
[[ $# -eq 0 ]] || { _fail "Unexpected arguments: $*"; exit 2; }
case "$ACTION" in
    install|update|status|uninstall) ;;
    *) _fail "Unknown action: $ACTION"; exit 2 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL_DIR="$HOME/.agent-leases"
LOCAL_BIN="$HOME/.local/bin"
LINK_DIR="$INSTALL_DIR/.venv"
LINK_PYTHON="$LINK_DIR/bin/python"
STUB="$LOCAL_BIN/agent-leases"
MANIFEST_PATH="$INSTALL_DIR/deploy-manifest.json"

if [[ "$ACTION" == "status" ]]; then
    [[ -x "$LINK_PYTHON" && -f "$STUB" ]] || {
        _fail "agent-leases is not installed"; exit 1; }
    "$LINK_PYTHON" -m agent_leases --version
    [[ -f "$MANIFEST_PATH" ]] && _ok "Deploy manifest: $MANIFEST_PATH"
    exit 0
fi
if [[ "$ACTION" == "uninstall" ]]; then
    rm -f "$STUB"
    if [[ -d "$INSTALL_DIR" ]]; then
        rm -rf "$INSTALL_DIR"
    fi
    _ok "Removed agent-leases runtime"
    exit 0
fi

command -v uv >/dev/null 2>&1 || {
    _fail "uv is required to install agent-leases"; exit 1; }
command -v git >/dev/null 2>&1 || {
    _fail "git is required to run agent-leases"; exit 1; }

# === install-contract:v3 versioned-venv -- keep byte-identical across plugins ===
VENV_DIR="$LINK_DIR"
VENV_PYTHON="$LINK_PYTHON"
VERSIONED_RUNTIME=1
[[ "${COPILOT_EXT_NO_VERSIONED:-}" == "1" ]] && VERSIONED_RUNTIME=0
SRC_VERSION="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/pyproject.toml" | head -n1)"
if [[ "$VERSIONED_RUNTIME" -eq 1 && -n "$SRC_VERSION" ]]; then
    VENV_DIR="$INSTALL_DIR/versions/$SRC_VERSION"
    VENV_PYTHON="$VENV_DIR/bin/python"
else
    VERSIONED_RUNTIME=0
fi
# === end install-contract:v3 versioned-venv ===

mkdir -p "$INSTALL_DIR" "$LOCAL_BIN" "$INSTALL_DIR/bin"
for hook in bootstrap-check.ps1 bootstrap-check.sh; do
    cp -f "$SCRIPT_DIR/$hook" "$INSTALL_DIR/bin/$hook"
done

uv venv "$VENV_DIR" --allow-existing >/dev/null
uv pip install --python "$VENV_PYTHON" "$PLUGIN_DIR" --quiet

# === install-contract:v3 versioned-venv activate ===
if [[ "$VERSIONED_RUNTIME" -eq 1 ]]; then
    VR_SCRIPT="$SCRIPT_DIR/versioned_runtime.py"
    PREVIOUS="$("$VENV_PYTHON" "$VR_SCRIPT" --root "$INSTALL_DIR" --link-name '.venv' current 2>/dev/null || true)"
    "$VENV_PYTHON" -c 'import agent_leases'
    "$VENV_PYTHON" "$VR_SCRIPT" --root "$INSTALL_DIR" --link-name '.venv' \
        activate "$SRC_VERSION" --replace-nonlink >/dev/null
    if [[ -n "$PREVIOUS" ]]; then
        "$LINK_PYTHON" "$VR_SCRIPT" --root "$INSTALL_DIR" --link-name '.venv' \
            gc --protect-pids --keep "$PREVIOUS" >/dev/null || true
    else
        "$LINK_PYTHON" "$VR_SCRIPT" --root "$INSTALL_DIR" --link-name '.venv' \
            gc --protect-pids >/dev/null || true
    fi
fi
# === end install-contract:v3 versioned-venv activate ===

cat > "$STUB" <<'EOF'
#!/usr/bin/env bash
export PYTHONUTF8=1
exec "$HOME/.agent-leases/.venv/bin/python" -m agent_leases "$@"
EOF
chmod +x "$STUB"

# === install-contract:v3 source-kind -- keep byte-identical across plugins ===
_source_kind() {
    case "$(printf '%s' "$1" | tr '\\' '/')" in
        */.copilot/installed-plugins/*) printf 'marketplace' ;;
        *) printf 'local' ;;
    esac
}
# === end install-contract:v3 source-kind ===

KIND="$(_source_kind "$PLUGIN_DIR")"
COMMIT="null"
BRANCH="null"
DIRTY=false
if [[ "$KIND" == "local" ]]; then
    REPO_ROOT="$(cd "$PLUGIN_DIR/../.." && pwd)"
    COMMIT="\"$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)\""
    BRANCH="\"$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)\""
    [[ -n "$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null)" ]] && DIRTY=true
fi
TMP="$MANIFEST_PATH.tmp"
cat > "$TMP" <<EOF
{
  "schema_version": 3,
  "service": "agent-leases",
  "deployed_at": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "source": {
    "kind": "$KIND",
    "path": "$PLUGIN_DIR",
    "repo": "copilot-extensions",
    "plugin": "agent-leases",
    "version": "$SRC_VERSION",
    "commit": $COMMIT,
    "branch": $BRANCH,
    "dirty": $DIRTY
  },
  "venv": "$LINK_DIR",
  "runtime": "python"
}
EOF
mv -f "$TMP" "$MANIFEST_PATH"

"$LINK_PYTHON" -m agent_leases --version
_ok "Runtime: $INSTALL_DIR"
_ok "Binstub: $STUB"
_step "Configure ~/.agent-leases/config.json key 'origin' before acquiring leases"
