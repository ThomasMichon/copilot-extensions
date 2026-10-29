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
if [[ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]]; then exit 0; fi
py="$(command -v python3 || command -v python || true)"; [ -n "$py" ] || exit 0
case "$py" in
  /*|[A-Za-z]:/*|[A-Za-z]:\\*|\\\\*) ;;
  *) py="$PWD/$py" ;;
esac
name="$("$py" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("name",""))' "$PluginDir/plugin.json" 2>/dev/null)"
[ -n "$name" ] || exit 0
InstallDir="$HOME/.$name"
assert_bootstrap_publication() {
  local action_rc=0
  "$py" -I -B "$ScriptDir/action_controls.py" \
      --root "$InstallDir" --action automatic-runtime-reconcile --automatic >/dev/null || action_rc=$?
  if [[ "$action_rc" != 0 ]]; then
    printf '[%s] Automatic runtime reconciliation skipped; retry on a later session start after resolving the reported action policy.\n' "$name" >&2
    [[ "$action_rc" != 3 ]] || exit 3
    exit 126
  fi
  local helper="$ScriptDir/installation-context/plugin-publication-probe.sh"
  if [[ -f "$helper" ]]; then
    bash "$helper" "$PluginDir" "$name" "$InstallDir" && return 0
  else
    local policy="$InstallDir/runtime-deployment.json" ancestor="$InstallDir"
    while [[ ! -e "$ancestor" && ! -L "$ancestor" ]]; do ancestor="$(dirname -- "$ancestor")"; done
    if [[ -d "$ancestor" && -r "$ancestor" && -x "$ancestor" &&
          ! -e "$policy" && ! -L "$policy" ]]; then return 0; fi
    printf '[%s] Runtime publication refused: deployment policy requires a capable installation helper.\n' "$name" >&2
  fi
  printf '[%s] Runtime publication refused: bootstrap admission did not succeed. Use explicit installation management to repair it.\n' "$name" >&2
  exit 3
}
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
    assert_bootstrap_publication
    stamp_rc=0
    bash "$installer" stamp >/dev/null || stamp_rc=$?
    if [[ "$stamp_rc" != 0 ]]; then
      printf '[%s] Automatic stamp did not complete (exit %s); no background retry attempted.\n' "$name" "$stamp_rc" >&2
      exit "$stamp_rc"
    fi
  fi
  exit 0
fi
deployed="$("$py" -c 'import json,sys;print(json.load(open(sys.argv[1]))["source"].get("version",""))' "$Manifest" 2>/dev/null)"
current="$deployed"
pyproj="$PluginDir/pyproject.toml"
if [ -f "$pyproj" ]; then
  v="$(grep -m1 -E '^[[:space:]]*version[[:space:]]*=' "$pyproj" | sed -E 's/.*=[[:space:]]*"([^"]+)".*/\1/')"
  [ -n "$v" ] && current="$v"
fi
# --- selected completed runtime readiness (reference: agent-bridge) ---
provisioned=0
if [ "$deployed" = "$current" ]; then
  expected_slot="$("$py" -I -B - "$ScriptDir" "$PluginDir" "$Manifest" "$current" "$name" <<'EXPECTED_SLOT'
import json
import pathlib
import sys

sys.path.insert(0, sys.argv[1])
payload, manifest = pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
current, name = sys.argv[4:]
deployment = json.loads(manifest.read_text(encoding="utf-8-sig"))
from versioned_runtime_readiness import deployment_runtime_version
print(deployment_runtime_version(payload, current, name, deployment))
EXPECTED_SLOT
)" || expected_slot=""
  # Legacy installations have no selector. A link must not hide a broken or
  # different selection in the marker-only layout.
  if [ "$expected_slot" = "$current" ] &&
     [ ! -e "$InstallDir/current-version" ] && [ ! -L "$InstallDir/current-version" ] &&
     [ ! -e "$PluginDir/runtime-source.json" ] && [ ! -L "$PluginDir/runtime-source.json" ] &&
     { [ -e "$InstallDir/.venv" ] || [ -e "$InstallDir/venv" ]; }; then
    provisioned=1
  elif [ -f "$InstallDir/current-version" ] && [ -f "$ScriptDir/resolve-runtime.sh" ]; then
    selected="$(tr -d ' \t\r\n' < "$InstallDir/current-version")"
    if [ -n "$expected_slot" ] && [ "$selected" = "$expected_slot" ]; then
      # Subshell scopes resolver variables and read-only suppresses durable
      # traces. A healthy fallback must never stand in for this exact selection.
      resolved="$(
        AGENT_RT_ROOT="$InstallDir"
        AGENT_RT_RESOLVE_TRACE_MODE=read-only
        . "$ScriptDir/resolve-runtime.sh"
        printf '%s' "$AGENT_RT_PY"
      )"
      if [ -n "$resolved" ] &&
         { [ "$resolved" = "$InstallDir/versions/$selected/bin/python" ] ||
           [ "$resolved" = "$InstallDir/versions/$selected/Scripts/python.exe" ]; }; then
        provisioned=1
      fi
    fi
  fi
fi
if [ "$provisioned" = 1 ]; then exit 0; fi
# --- end selected completed runtime readiness ---

if [ -f "$PluginDir/scripts/init.sh" ]; then
  target=("$PluginDir/scripts/init.sh")
elif [ -f "$PluginDir/scripts/install.sh" ]; then
  target=("$PluginDir/scripts/install.sh" install)
else
  exit 0
fi

assert_bootstrap_publication

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
    assert_bootstrap_publication
    kill "$lockPid" 2>/dev/null || true
  fi
fi

echo "[$name] runtime $deployed -> $current; reconciling in background..." >&2
cd "$HOME" || { echo "reconcile launch failed: HOME is unavailable" >&2; exit 1; }
assert_bootstrap_publication
nohup bash -c '
  "$1" -I -B "$2" --root "$3" --action automatic-runtime-reconcile --automatic >/dev/null || exit $?
  shift 3
  exec bash "$@"
' agent-dispatch-reconcile "$py" "$ScriptDir/action_controls.py" "$InstallDir" \
    "${target[@]}" >"$InstallDir/reconcile.log" 2>&1 &
echo $! > "$lockFile" 2>/dev/null || true
exit 0