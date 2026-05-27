#!/usr/bin/env bash
# Bootstrap hook — runs on session start via hooks.json
# Auto-updates the agent-worktrees runtime payload when stale.
# If not installed, prints a hint (full install requires interactive setup).

set -euo pipefail

INSTALL_DIR="$HOME/.agent-worktrees"
LIB_DIR="$INSTALL_DIR/lib"
PKG_DST="$LIB_DIR/agent_worktrees"
VENV_PYTHON="$INSTALL_DIR/.venv/bin/python"
MANIFEST="$INSTALL_DIR/deploy-manifest.json"

# --- Not installed: hint only ---
if [[ ! -f "$VENV_PYTHON" ]]; then
    echo ''
    echo -e '\033[33m[agent-worktrees] Runtime not installed.\033[0m'
    echo -e "\033[90m  Ask Copilot to 'set up agent-worktrees' to bootstrap the runtime.\033[0m"
    echo ''
    exit 0
fi

# --- Installed: check if package is stale ---
if [[ ! -f "$MANIFEST" ]]; then exit 0; fi

plugin_dir="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('plugin_source',''))" "$MANIFEST" 2>/dev/null || true)"
if [[ -z "$plugin_dir" || ! -d "$plugin_dir" ]]; then exit 0; fi

PKG_SRC="$plugin_dir/src/agent_worktrees"
if [[ ! -d "$PKG_SRC" ]]; then exit 0; fi

deployed_commit="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('commit',''))" "$MANIFEST" 2>/dev/null || true)"
current_commit="$(git -C "$plugin_dir" rev-parse HEAD 2>/dev/null || true)"

if [[ -z "$deployed_commit" || -z "$current_commit" || "$deployed_commit" == "$current_commit" ]]; then
    exit 0
fi

# Stale — re-deploy package
echo -e '\033[90m[agent-worktrees] Updating runtime payload...\033[0m'
rm -rf "$PKG_DST"
mkdir -p "$LIB_DIR"
cp -r "$PKG_SRC" "$PKG_DST"

python3 -c "
import json, sys
from datetime import datetime, timezone
m = json.load(open(sys.argv[1]))
m['commit'] = sys.argv[2]
m['deployed_at'] = datetime.now(timezone.utc).isoformat()
m['dirty'] = False
json.dump(m, open(sys.argv[1], 'w'), indent=2)
" "$MANIFEST" "$current_commit" 2>/dev/null || true

echo -e '\033[90m[agent-worktrees] Runtime updated.\033[0m'
exit 0
