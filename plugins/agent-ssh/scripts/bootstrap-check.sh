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

# agent-ssh session-start hook -- version-gated runtime reconcile.
#
# Runs at session start (via hooks.json). Ensures the installed agent-ssh
# binstub/venv matches the plugin source version, so a `copilot plugin update`
# that bumps the payload is picked up automatically -- without any manual
# reinstall.
#
# Fast path: compare the deployed version (~/.agent-ssh/deploy-manifest.json) to
# the source version (plugin pyproject.toml). If they match and the venv exists,
# exit immediately. Otherwise re-run the plugin's own installer (scripts/init ->
# canonical install) in the BACKGROUND so session start never blocks on a venv
# build; the versioned-venv swap is atomic, so concurrent use stays safe.
#
# Deployed to ~/.agent-ssh/bin/ by scripts/install.sh. Only reconciles staleness.
#
# NO OPT-IN GATE (agent-bridge-unified-zdd-cutover Phase 0): background
# reconcile used to require a checked-in, per-project opt-in flag in
# <project>/.copilot-extensions/config.yaml, because a raw reconcile could
# race a live session. Now that every reconcile-capable plugin's update
# path is always-ZDD (safe to run unattended), that justification is gone;
# the gate was removed rather than kept as a redundant consent checkbox.
# Frequency/trigger stays deliberately bounded -- still only once per
# session start, only on a real version drift. What DOES still bound
# concurrency is the single-flight + stale-reap guard below (a lock file,
# not the removed opt-in).

session_start_json_emitted=0
emit_session_start_json() {
  if [ "${session_start_json_emitted:-0}" -eq 0 ]; then
    printf '{}'
    session_start_json_emitted=1
  fi
}
trap 'emit_session_start_json' EXIT

if [ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]; then
  exit 0
fi

InstallDir="$HOME/.agent-ssh"
Manifest="$InstallDir/deploy-manifest.json"

# FIRST install (no deploy manifest yet): do the cheap 'stamp' so the binstub is
# on PATH THIS session and self-provisions the runtime on first use. Without this
# a fresh box never provisions, because the reconcile logic below is manifest-gated
# -- the stamp writes no manifest, so it (idempotently) re-runs until first use
# builds the runtime. Mirrors agent-logger's bootstrap-check. Self-locate the
# installer from this script (the sessionStart hook runs the plugin-shipped copy)
# and only fire when the installer actually declares a 'stamp' action.
if [ ! -f "$Manifest" ]; then
  _bc_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  _bc_installer="$_bc_dir/install.sh"
  if [ -f "$_bc_installer" ] && grep -qE '(\||[[:space:]])stamp[|)]' "$_bc_installer" 2>/dev/null; then
    bash "$_bc_installer" stamp >/dev/null 2>&1 || true
  fi
  exit 0
fi

py="$(command -v python3 || command -v python || true)"
[ -n "$py" ] || exit 0

pluginDir="$("$py" -c 'import json,sys;print(json.load(open(sys.argv[1]))["source"]["path"])' "$Manifest" 2>/dev/null)"
deployed="$("$py" -c 'import json,sys;print(json.load(open(sys.argv[1]))["source"].get("version",""))' "$Manifest" 2>/dev/null)"
[ -n "$pluginDir" ] && [ -d "$pluginDir" ] || exit 0

current="$deployed"
pyproj="$pluginDir/pyproject.toml"
if [ -f "$pyproj" ]; then
  v="$(grep -m1 -E '^[[:space:]]*version[[:space:]]*=' "$pyproj" | sed -E 's/.*=[[:space:]]*"([^"]+)".*/\1/')"
  [ -n "$v" ] && current="$v"
fi

# Up to date and runtime present -> fast no-op.
# "Provisioned" no longer implies a .venv: the marker runtime model (#581) publishes
# the active slot via a current-version marker (POSIX keeps a .venv symlink, but a
# marker+slot is authoritative). Treat a marker whose slot python exists as
# provisioned too, so a current runtime is a clean no-op, not a needless rebuild.
provisioned=0
[ -e "$InstallDir/.venv" ] && provisioned=1
if [ "$provisioned" = 0 ] && [ -f "$InstallDir/current-version" ]; then
  cv="$(tr -d '[:space:]' < "$InstallDir/current-version")"
  # ...and only when it names the CURRENT payload version (the marker is authoritative
  # for the active slot; a stale marker must not suppress reconcile).
  if [ -n "$cv" ] && [ "$cv" = "$current" ] && { [ -x "$InstallDir/versions/$cv/bin/python" ] || [ -f "$InstallDir/versions/$cv/Scripts/python.exe" ]; }; then provisioned=1; fi
fi
if [ "$provisioned" = 1 ] && [ "$deployed" = "$current" ]; then exit 0; fi

init="$pluginDir/scripts/init.sh"
[ -f "$init" ] || exit 0

# --- Good boot-citizen guard: single-flight + stale-reap ---
# This hook fires on EVERY new session now that the opt-in gate is gone
# (agent-bridge-unified-zdd-cutover Phase 0 review finding): without this,
# a slow or wedged reconcile gets re-spawned every session, stacking
# orphaned background installers. If a prior reconcile is still running:
#   YOUNG (<10m) -> already in flight; do nothing (never stack).
#   STALE (>=10m) -> wedged; reap it, then relaunch (self-heals a one-off
#                    wedge instead of poisoning every future session).
lockFile="$InstallDir/reconcile.lock"
staleSeconds=600
if [ -f "$lockFile" ]; then
  lockPid="$(tr -d '[:space:]' < "$lockFile" 2>/dev/null)"
  if [ -n "$lockPid" ] && kill -0 "$lockPid" 2>/dev/null; then
    lockMtime="$(stat -c %Y "$lockFile" 2>/dev/null || stat -f %m "$lockFile" 2>/dev/null || echo 0)"
    nowSecs="$(date -u +%s)"
    if [ "$lockMtime" -gt 0 ] && [ $((nowSecs - lockMtime)) -lt "$staleSeconds" ]; then
      exit 0
    fi
    kill "$lockPid" 2>/dev/null || true
  fi
fi

echo "[agent-ssh] runtime $deployed -> $current; reconciling in background..." >&2
(cd "$HOME" && exec nohup bash "$init" >/dev/null 2>&1) &
echo $! > "$lockFile" 2>/dev/null || true

exit 0
