#!/usr/bin/env bash
# Bootstrap the agent-worktrees runtime.
# Creates ~/.agent-worktrees/ with venv, package, wrappers, binstub.
# Idempotent -- safe to re-run for repairs or upgrades.
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

# Stamp build info so --version reflects this deployment
_repo_root="$(cd "$PLUGIN_DIR/../.." && pwd)"
_commit="$(git -C "$_repo_root" rev-parse HEAD 2>/dev/null || echo unknown)"
_branch="$(git -C "$_repo_root" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
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

# Deploy default session setup scripts to <install>/scripts/. The launch plan
# emitted by `agent-worktrees resolve` references
# ~/.agent-worktrees/scripts/default-setup.* when a repo has no setup script of
# its own; without these the bridge cannot spawn a session.
SCRIPTS_DST_DIR="$INSTALL_DIR/scripts"
mkdir -p "$SCRIPTS_DST_DIR"
for name in default-setup.ps1 default-setup.sh; do
    src="$SCRIPT_DIR/$name"
    if [[ -f "$src" ]]; then
        cp "$src" "$SCRIPTS_DST_DIR/$name"
        chmod +x "$SCRIPTS_DST_DIR/$name" 2>/dev/null || true
        ok "Setup script: $name"
    else
        fail "Setup script not found: $src"
    fi
done

# ── 6. Deploy binstub ─────────────────────────────────────────────────

stub_path="$LOCAL_BIN/agent-worktrees"
cat > "$stub_path" << 'STUB'
#!/usr/bin/env bash
export PYTHONUTF8=1
export PYTHONPATH="$HOME/.agent-worktrees/lib${PYTHONPATH:+:$PYTHONPATH}"
unset PYTHONHOME
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

# Check PATH and add ~/.local/bin if missing
if echo "$PATH" | tr ':' '\n' | grep -qx "$LOCAL_BIN"; then
    ok "PATH: $LOCAL_BIN is on PATH"
else
    # Add to current session
    export PATH="$LOCAL_BIN:$PATH"
    ok "PATH: Added $LOCAL_BIN to current session"

    # Persist in shell profile
    shell_profile=""
    if [[ -f "$HOME/.bashrc" ]]; then
        shell_profile="$HOME/.bashrc"
    elif [[ -f "$HOME/.profile" ]]; then
        shell_profile="$HOME/.profile"
    elif [[ -f "$HOME/.zshrc" ]]; then
        shell_profile="$HOME/.zshrc"
    fi

    if [[ -n "$shell_profile" ]]; then
        # Only add if not already present
        if ! grep -q "\.local/bin" "$shell_profile" 2>/dev/null; then
            echo '' >> "$shell_profile"
            echo '# Added by agent-worktrees init' >> "$shell_profile"
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$shell_profile"
            ok "PATH: Added to $shell_profile (persistent)"
        else
            ok "PATH: Already in $shell_profile"
        fi
    else
        echo "  [WARN] No shell profile found -- add 'export PATH=\"\$HOME/.local/bin:\$PATH\"' manually"
    fi
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
