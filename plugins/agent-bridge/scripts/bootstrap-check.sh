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

# agent-bridge session-start runtime reconcile (reference implementation).
# Invoked via hooks.json at session start. Derives the install dir from
# plugin.json's name (~/.<name>) and re-runs the installer in the BACKGROUND only
# when the deployed version drifts from the payload. Reconciles the TOOL, never
# machine state/config.
#
# NOTE ON SHARING: this file is NOT byte-identical across all agent-* plugins --
# three deploy-model families exist (see tools/check-bootstrap-sync.py). This
# copy carries the observability + venv-or-.venv behavior below.
#
# OBSERVABILITY (#167): the background reconcile is otherwise silent. This hook
# records each attempt to ~/.<name>/reconcile-status.json and tees the
# installer's output to ~/.<name>/reconcile.log so a failed auto-update is
# diagnosable.
#
# NO OPT-IN GATE (agent-bridge-unified-zdd-cutover Phase 0): background
# reconcile used to require a checked-in
# <project>/.copilot-extensions/config.yaml with a top-level, per-project
# opt-in flag, because a raw reconcile
# could race a live daemon/session. Now that agent-bridge's own update path
# is always-ZDD (spawn passive -> health-gate -> flip -> drain -> retire,
# safe to run unattended), that justification is gone; the gate was removed
# rather than kept as a redundant consent checkbox. Frequency/trigger stays
# deliberately bounded -- still only once per session start, only on a real
# version drift. What DOES still bound concurrency is the single-flight +
# stale-reap guard below (already present in the .ps1 sibling; this .sh
# now carries the same guard, not the removed opt-in). Staleness stays
# observable via `agent-bridge service status`, which now reports
# days-since-last-reconcile (see _print_reconcile_status in
# service_process_cli.py).
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
py="$(command -v python3 || command -v python || true)"; [ -n "$py" ] || exit 0
name="$("$py" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("name",""))' "$PluginDir/plugin.json" 2>/dev/null)"
[ -n "$name" ] || exit 0
InstallDir="$HOME/.$name"
Manifest="$InstallDir/deploy-manifest.json"
if [ ! -f "$Manifest" ]; then
  # Not provisioned yet -- do the cheap FIRST install (stamp) so the
  # self-provisioning binstub is on PATH this session; the binstub then builds
  # the venv on first use (#1393). Without this, a freshly `copilot plugin
  # install`-ed agent-bridge left NO binstub until a manual install -- and
  # agent-bridge is the `codespace:` dispatch transport, so the 3-plugin golden
  # path never got off the ground. Fires only when the installer declares a
  # 'stamp' action (a safe no-op otherwise).
  installer="$PluginDir/scripts/install.sh"
  if [ -f "$installer" ] && grep -qE '^[[:space:]]*stamp\)' "$installer" 2>/dev/null; then
    bash "$installer" stamp >/dev/null 2>&1 || true
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
# The stable runtime link is named '.venv' for most plugins but 'venv' for a
# few (agent-bridge); accept EITHER so this early-exit actually fires instead of
# re-launching the installer on every session start.
if { [ -e "$InstallDir/.venv" ] || [ -e "$InstallDir/venv" ]; } && [ "$deployed" = "$current" ]; then exit 0; fi

if [ -f "$PluginDir/scripts/init.sh" ]; then
  target=("$PluginDir/scripts/init.sh")
elif [ -f "$PluginDir/scripts/install.sh" ]; then
  target=("$PluginDir/scripts/install.sh" install)
else
  exit 0
fi
reconcile_log="$InstallDir/reconcile.log"
status_file="$InstallDir/reconcile-status.json"

# --- Good boot-citizen guard: single-flight + stale-reap (mirrors the .ps1
# counterpart) --- This hook fires on EVERY new session now that the opt-in
# gate is gone: without this, a slow or wedged reconcile gets re-spawned
# every session, stacking orphaned background installers. Reuses the
# existing reconcile-status.json (launched_pid/at) rather than a second
# lock file. If a prior reconcile PID is still alive:
#   YOUNG (<10m) -> already in flight; do nothing (never stack).
#   STALE (>=10m) -> wedged; reap it, then relaunch (self-heals a one-off
#                    wedge instead of poisoning every future session).
staleSeconds=600
if [ -f "$status_file" ]; then
  prevPid="$("$py" -c '
import json, sys
try:
    print(json.load(open(sys.argv[1])).get("launched_pid", "") or "")
except Exception:
    print("")
' "$status_file" 2>/dev/null)"
  if [ -n "$prevPid" ] && kill -0 "$prevPid" 2>/dev/null; then
    prevEpoch="$("$py" -c '
import json, sys, datetime
try:
    at = json.load(open(sys.argv[1])).get("at", "")
    dt = datetime.datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    print(int(dt.timestamp()))
except Exception:
    print(0)
' "$status_file" 2>/dev/null)"
    nowEpoch="$(date -u +%s)"
    if [ "$prevEpoch" -gt 0 ] && [ $((nowEpoch - prevEpoch)) -lt "$staleSeconds" ]; then
      exit 0
    fi
    kill "$prevPid" 2>/dev/null || true
  fi
fi

echo "[$name] runtime $deployed -> $current; reconciling in background (log: $InstallDir/reconcile.log)..." >&2
now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
# Observability (#167 + agent-bridge-unified-zdd-cutover Phase 0 review):
# record the attempt immediately (so the single-flight/staleness check
# above always sees it), THEN have the SAME nohup'd process overwrite the
# same status file with completion info once the installer actually exits
# -- otherwise "Last auto-reconcile" would report a launch timestamp even
# for a reconcile that failed or is still wedged, making real staleness
# look falsely healthy. Everything (install + completion write) runs
# inside one flat `bash -c`, so it stays nohup-protected end to end -- no
# extra un-nohup'd wrapper subshell that could die to a SIGHUP the nohup'd
# child itself would have survived.
nohup bash -c '
  reconcile_log="$1"; status_file="$2"; deployed="$3"; current="$4"; started_at="$5"
  shift 5
  bash "$@" >"$reconcile_log" 2>&1
  rc=$?
  completed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  success="false"; [ "$rc" -eq 0 ] && success="true"
  printf "{\"at\":\"%s\",\"from\":\"%s\",\"to\":\"%s\",\"launched_pid\":%s,\"log\":\"%s\",\"completed_at\":\"%s\",\"exit_code\":%d,\"success\":%s}\n" \
    "$started_at" "$deployed" "$current" "${BASHPID:-$$}" "$reconcile_log" "$completed_at" "$rc" "$success" \
    >"$status_file" 2>/dev/null || true
' _ "$reconcile_log" "$status_file" "$deployed" "$current" "$now" "${target[@]}" &
launched_pid=$!
printf '{"at":"%s","from":"%s","to":"%s","launched_pid":%s,"log":"%s"}\n' \
  "$now" "$deployed" "$current" "$launched_pid" "$reconcile_log" \
  >"$status_file" 2>/dev/null || true
exit 0