#!/usr/bin/env bash
# Session-start runtime bootstrap + reconcile -- generic, self-locating. Invoked
# (via hooks.json) from the plugin's scripts/ dir. Derives the install dir from
# plugin.json's name (~/.<name>). Two jobs, both grace-window-cheap:
#   1. FIRST install (unprovisioned): if the installer supports a cheap 'stamp'
#      action, splat the self-provisioning binstub now (deferring the venv build
#      to the binstub's first use) so the CLI is on PATH this session -- no venv
#      build on the hook. Installers without 'stamp' keep the old no-op.
#   2. RECONCILE (already provisioned): re-run the installer in the BACKGROUND
#      only when the deployed version drifts. Reconciles the TOOL, never state.
#
# NO OPT-IN GATE (agent-bridge-unified-zdd-cutover Phase 0): background
# reconcile used to require a checked-in, per-project opt-in flag in
# <project>/.copilot-extensions/config.yaml, because a raw reconcile could
# race a live daemon/session. Now that every reconcile-capable plugin's
# update path is always-ZDD (safe to run unattended), that justification is
# gone; the gate was removed rather than kept as a redundant consent
# checkbox. Frequency/trigger stays deliberately bounded -- still only once
# per session start, only on a real version drift. What DOES still bound
# concurrency is the single-flight + stale-reap guard below (a lock file,
# not the removed opt-in) -- see its own comment.
ScriptDir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PluginDir="$(cd "$ScriptDir/.." && pwd)"
session_start_json_emitted=0
emit_session_start_json() {
  if [ "${session_start_json_emitted:-0}" -eq 0 ]; then
    printf '{}'
    session_start_json_emitted=1
  fi
}
trap 'emit_session_start_json' EXIT
name="budget-guidance"
InstallDir="$HOME/.$name"
Manifest="$InstallDir/deploy-manifest.json"
if [ ! -f "$Manifest" ]; then
  # Not provisioned yet -- do the cheap FIRST install (stamp) so the binstub is
  # callable this session; it self-provisions the runtime on first use. Fully
  # back-compatible: only fires when the installer declares a 'stamp' action.
  installer=""
  for candidate in "$PluginDir/scripts/init.sh" "$PluginDir/scripts/install.sh"; do
    if [ -f "$candidate" ] && grep -qE '^[[:space:]]*([[:alnum:]_-]+\|)*stamp(\|[[:alnum:]_-]+)*\)' "$candidate" 2>/dev/null; then installer="$candidate"; break; fi
  done
  if [ -n "$installer" ]; then
    bash "$installer" stamp >/dev/null 2>&1 || true
  fi
  exit 0
fi
deployed="$(
  awk '
    /"source"[[:space:]]*:[[:space:]]*\{/ { in_source = 1; next }
    in_source && /"version"[[:space:]]*:/ {
      value = $0
      sub(/^.*"version"[[:space:]]*:[[:space:]]*"/, "", value)
      sub(/".*$/, "", value)
      print value
      exit
    }
    in_source && /^[[:space:]]*\}/ { exit }
  ' "$Manifest" 2>/dev/null
)"
[ -n "$deployed" ] || {
  echo "[$name] deploy manifest has no readable source version; skipping reconcile." >&2
  exit 0
}
current="$deployed"
pyproj="$PluginDir/pyproject.toml"
if [ -f "$pyproj" ]; then
  v="$(grep -m1 -E '^[[:space:]]*version[[:space:]]*=' "$pyproj" | sed -E 's/.*=[[:space:]]*"([^"]+)".*/\1/')"
  [ -n "$v" ] && current="$v"
fi
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

if [ -f "$PluginDir/scripts/init.sh" ]; then
  target=("$PluginDir/scripts/init.sh")
elif [ -f "$PluginDir/scripts/install.sh" ]; then
  target=("$PluginDir/scripts/install.sh" install)
else
  exit 0
fi

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

echo "[$name] runtime $deployed -> $current; reconciling in background..." >&2
nohup bash "${target[@]}" >/dev/null 2>&1 &
echo $! > "$lockFile" 2>/dev/null || true
exit 0