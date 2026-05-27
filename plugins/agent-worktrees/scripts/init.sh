#!/usr/bin/env bash
# Bootstrap the agent-worktrees runtime.
# Creates ~/.agent-worktrees/ with venv, package, wrappers, binstub.
# Idempotent — safe to re-run for repairs or upgrades.
set -euo pipefail

# ── Output helpers ─────────────────────────────────────────────────────

ok()   { echo "  [OK]   $1"; }
skip() { echo "  [SKIP] $1"; }
fail() { echo "  [FAIL] $1" >&2; }
step() { echo "  ...    $1"; }

# ── Paths ──────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BIN_SRC_DIR="$PLUGIN_DIR/bin"
PKG_SRC_DIR="$PLUGIN_DIR/src/agent_worktrees"

INSTALL_DIR="${1:-$HOME/.agent-worktrees}"
LIB_DIR="$INSTALL_DIR/lib"
BIN_DIR="$INSTALL_DIR/bin"
VENV_DIR="$INSTALL_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
LOCAL_BIN="$HOME/.local/bin"

# ── Preflight checks ──────────────────────────────────────────────────

echo ''
echo '=== agent-worktrees init ==='
echo ''

if [[ ! -d "$PKG_SRC_DIR" ]]; then
    fail "Package source not found at $PKG_SRC_DIR"
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

# ── 1. Create directories ─────────────────────────────────────────────

mkdir -p "$INSTALL_DIR" "$LIB_DIR" "$BIN_DIR" "$LOCAL_BIN"
ok "Directories: $INSTALL_DIR"

# ── 2. Create venv ────────────────────────────────────────────────────

if [[ ! -f "$VENV_PYTHON" ]]; then
    if command -v uv &>/dev/null; then
        step "Creating venv via uv..."
        if ! uv venv "$VENV_DIR" --allow-existing 2>/dev/null; then
            step "uv failed — falling back to python -m venv"
            $PYTHON_CMD -m venv "$VENV_DIR"
        fi
    else
        step "Creating venv via python -m venv..."
        $PYTHON_CMD -m venv "$VENV_DIR"
    fi

    if [[ ! -f "$VENV_PYTHON" ]]; then
        fail "Venv creation failed — $VENV_PYTHON not found"
        exit 1
    fi
    ok "Venv created"
else
    skip "Venv already exists"
fi

# ── 3. Install dependencies ───────────────────────────────────────────

if command -v uv &>/dev/null; then
    uv pip install --python "$VENV_PYTHON" pyyaml 2>/dev/null
else
    "$VENV_PYTHON" -m pip install --quiet pyyaml 2>/dev/null
fi
ok "Dependencies: pyyaml"

# ── 4. Deploy package (file copy) ─────────────────────────────────────

PKG_DST="$LIB_DIR/agent_worktrees"
rm -rf "$PKG_DST"
cp -r "$PKG_SRC_DIR" "$PKG_DST"
ok "Package deployed to $PKG_DST"

# ── 5. Deploy wrappers & bootstrap scripts ────────────────────────────

for name in launch-session.sh; do
    src="$BIN_SRC_DIR/$name"
    if [[ -f "$src" ]]; then
        cp "$src" "$BIN_DIR/$name"
        chmod +x "$BIN_DIR/$name"
        ok "Wrapper: $name"
    else
        fail "Wrapper not found: $src"
    fi
done

for name in bootstrap-check.ps1 bootstrap-check.sh; do
    src="$SCRIPT_DIR/$name"
    if [[ -f "$src" ]]; then
        cp "$src" "$BIN_DIR/$name"
        chmod +x "$BIN_DIR/$name"
        ok "Bootstrap: $name"
    fi
done

# ── 6. Deploy binstub ─────────────────────────────────────────────────

stub_path="$LOCAL_BIN/agent-worktrees"
cat > "$stub_path" << 'STUB'
#!/usr/bin/env bash
exec "$HOME/.agent-worktrees/.venv/bin/python" -m agent_worktrees "$@"
STUB
chmod +x "$stub_path"
ok "Binstub: $stub_path"

# -- 6b. Install terminal multiplexer (optional) ----------------------

if ! command -v tmux &>/dev/null; then
    step "tmux not found -- attempting install..."
    if command -v apt-get &>/dev/null; then
        sudo apt-get install -y -qq tmux 2>/dev/null && ok "tmux installed" || step "tmux install failed -- install manually: apt install tmux"
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y -q tmux 2>/dev/null && ok "tmux installed" || step "tmux install failed -- install manually: dnf install tmux"
    elif command -v brew &>/dev/null; then
        brew install tmux 2>/dev/null && ok "tmux installed" || step "tmux install failed -- install manually: brew install tmux"
    else
        step "tmux not found -- install with your package manager for session persistence"
    fi
else
    ok "tmux: already installed"
fi

# ── 7. Write deploy manifest ──────────────────────────────────────────

manifest_path="$INSTALL_DIR/deploy-manifest.json"
repo_root="$(git -C "$PLUGIN_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
commit=""
if [[ -n "$repo_root" ]]; then
    commit="$(git -C "$repo_root" rev-parse HEAD 2>/dev/null || true)"
fi

cat > "$manifest_path" << MANIFEST
{
  "service": "agent-worktrees",
  "commit": "${commit:-unknown}",
  "deployed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "runtime": "python",
  "plugin_source": "$PLUGIN_DIR",
  "install_dir": "$INSTALL_DIR"
}
MANIFEST
ok "Manifest: $manifest_path"

# ── 8. Verify ─────────────────────────────────────────────────────────

echo ''
export PYTHONPATH="$LIB_DIR"
import_check="$("$VENV_PYTHON" -c 'import agent_worktrees; print("OK")' 2>/dev/null || true)"
if [[ "$import_check" == "OK" ]]; then
    ok "Verification: module imports successfully"
else
    fail "Verification: module import failed"
    exit 1
fi

# Check PATH
if echo "$PATH" | tr ':' '\n' | grep -qx "$LOCAL_BIN"; then
    ok "PATH: $LOCAL_BIN is on PATH"
else
    echo "  [WARN] $LOCAL_BIN is not on PATH — add it to use agent-worktrees globally"
fi

echo ''
echo '=== Init complete ==='
echo ''
echo "  Runtime:  $INSTALL_DIR"
echo "  Binstub:  agent-worktrees"
echo ''
echo "  Next: cd into a repo and run \"agent-worktrees register <name>\""
echo ''
exit 0
