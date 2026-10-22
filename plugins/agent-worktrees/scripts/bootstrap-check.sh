#!/usr/bin/env bash

# --- bootstrap-killswitch guard (vendored; see libs/bootstrap-killswitch/README.md) ---
# One shared, repo-wide switch (not per-plugin) that pauses EVERY adopting
# plugin's reconcile-on-session-start at once, for when an operator/agent is
# hand-diagnosing a venv/install and a background reconcile must not race it.
# Legacy/default installation ONLY: a namespaced marketplace cell
# (COPILOT_EXTENSIONS_CONTEXT set) reconciles through its own cell-scoped
# mechanism, never this global state file -- crossing that installation-cell
# boundary would let one marketplace's switch pause an unrelated,
# independently-owned cell's reconcile (visions/plugin-services/
# installation-cells). This guard is therefore a deliberate no-op under a
# cell context, same as this hook's own existing cell-context exit below.
if [ -z "${COPILOT_EXTENSIONS_CONTEXT:-}" ]; then
  _bks_guard_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  _bks_guard="$_bks_guard_dir/bootstrap-killswitch-guard.sh"
  if [ -f "$_bks_guard" ] && bash "$_bks_guard" check >&2; then
    printf '{}'
    exit 0
  fi
  unset _bks_guard_dir _bks_guard
fi
# --- end bootstrap-killswitch guard ---

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

# A selected installation context owns runtime resolution. The payload-local
# command validates it and provisions its cell on first use; this best-effort
# hook must never touch the legacy root while any explicit context is present.
if [[ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]]; then
    exit 0
fi

ScriptDir="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || echo '')"
INSTALL_DIR="$HOME/.agent-worktrees"
LIB_DIR="$INSTALL_DIR/lib"
PKG_DST="$LIB_DIR/agent_worktrees"
_awresolve="$INSTALL_DIR/bin/resolve-runtime.sh"
if [ -f "$_awresolve" ]; then
    AGENT_RT_RESOLVE_TRACE_MODE=read-only . "$_awresolve"
fi
VENV_PYTHON="${AW_PY:-}"
MANIFEST="$INSTALL_DIR/deploy-manifest.json"

assert_bootstrap_publication() {
    local helper="$ScriptDir/installation-context/installation-context.sh"
    local query="$ScriptDir/installation-context/json-query.awk"
    local policy="$INSTALL_DIR/runtime-deployment.json"
    if [[ ! -f "$helper" || ! -f "$query" ]]; then
        # Older deployed hooks retain standalone behavior only without a policy.
        local ancestor="$INSTALL_DIR"
        while [[ ! -e "$ancestor" && ! -L "$ancestor" ]]; do
            ancestor="$(dirname -- "$ancestor")"
        done
        if [[ -d "$ancestor" && -r "$ancestor" && -x "$ancestor" &&
              ! -e "$policy" && ! -L "$policy" ]]; then
            return 0
        fi
        printf '%s\n' '[agent-worktrees] Runtime publication refused: deployment policy requires a capable installation helper. Use explicit installation management to repair it.' >&2
        printf '{}'
        exit 3
    fi
    local probe="" code=0 payload
    payload="$(cd -P -- "$ScriptDir/.." && pwd)"
    probe="$(bash "$helper" probe-plugin-publication --payload-root "$payload" \
        --plugin-id agent-worktrees --legacy-root "$INSTALL_DIR")" || code=$?
    local allowed="" allowed_type="" path="" root=""
    allowed="$(LC_ALL=C awk -f "$query" -v mode=get -v query_path=$'deployment\034allowPluginPublication' <<<"$probe" 2>/dev/null || true)"
    allowed_type="$(LC_ALL=C awk -f "$query" -v mode=type -v query_path=$'deployment\034allowPluginPublication' <<<"$probe" 2>/dev/null || true)"
    path="$(LC_ALL=C awk -f "$query" -v mode=get -v query_path=$'deployment\034path' <<<"$probe" 2>/dev/null || true)"
    root="$(LC_ALL=C awk -f "$query" -v mode=get -v query_path=$'legacy\034root' <<<"$probe" 2>/dev/null || true)"
    if [[ "$code" == 0 && "$allowed_type" == boolean && "$allowed" == true &&
          "$root" == /* && "$path" == "$root/runtime-deployment.json" ]]; then
        local legacy="" legacy_code=0 mutation="" mutation_type="" legacy_root=""
        legacy="$(bash "$helper" probe-legacy --payload-root "$payload" \
            --plugin-id agent-worktrees --legacy-root "$INSTALL_DIR")" || legacy_code=$?
        mutation="$(LC_ALL=C awk -f "$query" -v mode=get -v query_path=allowMutation <<<"$legacy" 2>/dev/null || true)"
        mutation_type="$(LC_ALL=C awk -f "$query" -v mode=type -v query_path=allowMutation <<<"$legacy" 2>/dev/null || true)"
        legacy_root="$(LC_ALL=C awk -f "$query" -v mode=get -v query_path=$'legacy\034root' <<<"$legacy" 2>/dev/null || true)"
        if [[ "$legacy_code" == 0 && "$mutation_type" == boolean &&
              "$mutation" == true && "$legacy_root" == "$root" ]]; then
            return 0
        fi
        probe="$probe $legacy"
    fi
    printf '[agent-worktrees] Runtime publication refused: %s Use explicit installation management to install or repair this runtime.\n' "$probe" >&2
    printf '{}'
    exit 3
}

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
        assert_bootstrap_publication
        bash "$_installer" stamp >/dev/null 2>&1 || true
        printf '{}'
        exit 0
    fi
    # Deployed-copy fallback on a still-unprovisioned box -> setup hint. Every
    # sessionStart invocation of this fallback (including hooks.json's direct,
    # no-python last resort) must emit exactly `{}`; the hint is diagnostic,
    # not model-facing, so it goes to stderr only.
    echo "[agent-worktrees] Runtime not installed. Ask Copilot to 'set up agent-worktrees' to bootstrap the runtime." >&2
    printf '{}'
    exit 0
fi

# Provisioned via the tools-half (versioned slot) but the full-launcher resolver
# isn't deployed -> nothing to reconcile via the legacy lib-copy path; no-op.
if [[ ! -x "$VENV_PYTHON" ]]; then
    printf '{}'
    exit 0
fi

# --- Installed: check if package is stale ---
if [[ ! -f "$MANIFEST" ]]; then printf '{}'; exit 0; fi
plugin_dir="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('plugin_source',''))" "$MANIFEST" 2>/dev/null || true)"
if [[ -z "$plugin_dir" || ! -d "$plugin_dir" ]]; then printf '{}'; exit 0; fi

PKG_SRC="$plugin_dir/src/agent_worktrees"
if [[ ! -d "$PKG_SRC" ]]; then printf '{}'; exit 0; fi

deployed_commit="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('commit',''))" "$MANIFEST" 2>/dev/null || true)"
current_commit="$(git -C "$plugin_dir" rev-parse HEAD 2>/dev/null || true)"

if [[ -z "$deployed_commit" || -z "$current_commit" || "$deployed_commit" == "$current_commit" ]]; then
    printf '{}'
    exit 0
fi

assert_bootstrap_publication
# Stale -- re-deploy package. Progress notices are diagnostics, not model
# context: route them to stderr and keep stdout a single JSON object.
echo -e '\033[90m[agent-worktrees] Updating runtime payload...\033[0m' >&2
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

echo -e '\033[90m[agent-worktrees] Runtime updated.\033[0m' >&2
printf '{}'
exit 0
