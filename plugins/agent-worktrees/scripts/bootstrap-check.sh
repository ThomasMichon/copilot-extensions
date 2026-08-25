#!/usr/bin/env bash
# Bootstrap hook -- runs on session start via hooks.json. hooks.json runs the
# PLUGIN PAYLOAD copy first, falling back to the deployed ~/.agent-worktrees/bin
# copy. Two jobs, both grace-window-cheap:
#   1. FIRST install (runtime not provisioned yet): fire the installer's cheap
#      'stamp' action so the self-provisioning agent-worktrees TOOL binstub lands
#      on PATH THIS session; the binstub builds the versioned venv on first use
#      (#1236/#1393). No venv build on the hook. Only fires when run from the
#      plugin payload (install.sh is a sibling) and the installer declares a
#      'stamp' action; otherwise a setup hint (deployed-copy fallback).
#   2. RECONCILE (already provisioned via the full launcher install): refresh the
#      deployed lib-copy package when the source commit drifts.

set -euo pipefail

ScriptDir="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || echo '')"
INSTALL_DIR="$HOME/.agent-worktrees"
LIB_DIR="$INSTALL_DIR/lib"
PKG_DST="$LIB_DIR/agent_worktrees"
_awresolve="$INSTALL_DIR/bin/resolve-runtime.sh"
[ -f "$_awresolve" ] && . "$_awresolve"
VENV_PYTHON="${AW_PY:-}"
MANIFEST="$INSTALL_DIR/deploy-manifest.json"

# Is the tools-half runtime already provisioned? (versioned-venv marker model,
# #581/#1393 -- a `.venv` link OR a current-version marker whose slot python
# exists.) A tools-half box has no full-launcher resolve-runtime.sh (so AW_PY is
# empty) yet IS provisioned; this keeps it from being mistaken for "not
# installed" and re-stamped/nagged every session.
_aw_provisioned() {
    [ -e "$INSTALL_DIR/.venv" ] && return 0
    if [ -f "$INSTALL_DIR/current-version" ]; then
        local cv; cv="$(tr -d '[:space:]' < "$INSTALL_DIR/current-version" 2>/dev/null || true)"
        if [ -n "$cv" ] && { [ -x "$INSTALL_DIR/versions/$cv/bin/python" ] || [ -f "$INSTALL_DIR/versions/$cv/Scripts/python.exe" ]; }; then
            return 0
        fi
    fi
    return 1
}

# --- FIRST install (nothing provisioned yet): stamp so the tool binstub is on
#     PATH this session and self-provisions the runtime on first use. ---
if [[ ! -x "$VENV_PYTHON" ]] && ! _aw_provisioned; then
    _installer="${ScriptDir:+$ScriptDir/install.sh}"
    if [[ -n "$_installer" && -f "$_installer" ]] && grep -qE '^[[:space:]]*stamp\)' "$_installer" 2>/dev/null; then
        bash "$_installer" stamp >/dev/null 2>&1 || true
        exit 0
    fi
    # Deployed-copy fallback on a still-unprovisioned box -> setup hint.
    echo ''
    echo -e '\033[33m[agent-worktrees] Runtime not installed.\033[0m'
    echo -e "\033[90m  Ask Copilot to 'set up agent-worktrees' to bootstrap the runtime.\033[0m"
    echo ''
    exit 0
fi

# Provisioned via the tools-half (versioned slot) but the full-launcher resolver
# isn't deployed -> nothing to reconcile via the legacy lib-copy path; no-op.
if [[ ! -x "$VENV_PYTHON" ]]; then
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

# Stale -- re-deploy package
echo -e '\033[90m[agent-worktrees] Updating runtime payload...\033[0m'
rm -rf "$PKG_DST"
mkdir -p "$LIB_DIR"
cp -r "$PKG_SRC" "$PKG_DST"

# Stamp build info so --version reflects the update
_branch="$(git -C "$plugin_dir" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
_ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
_src="$(echo "$plugin_dir" | tr '\\' '/')"
_ver="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$plugin_dir/pyproject.toml" 2>/dev/null || echo 0.0.0)"
cat > "$PKG_DST/_build_info.py" <<PYEOF
"""Build provenance -- auto-generated at deploy time. Do not edit."""

from __future__ import annotations

BUILD_INFO: dict[str, str] = {
    "version": "$_ver",
    "commit": "$current_commit",
    "branch": "$_branch",
    "build_timestamp": "$_ts",
    "source": "$_src",
}
PYEOF

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
