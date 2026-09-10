#!/usr/bin/env bash
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
# OPT-IN GATE (reference implementation -- fan out to sibling plugins as a
# follow-up, see tools/check-bootstrap-sync.py FAMILIES): gated the same way as
# the .ps1 counterpart -- requires a checked-in
# <project>/.copilot-extensions/config.yaml with a top-level
# `background_reconcile: true` line (plain regex match, no yaml parser -- this
# hook has no python/venv yet). No opt-in -> no background spawn. Deliberate
# behavior change: previously every session silently self-healed drift.
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

# OPT-IN GATE: drift exists, so we'd normally reconcile -- but only when the
# current project has explicitly opted in. COPILOT_PROJECT_DIR is the
# session's project checkout, injected by the CLI at session start; fall back
# to cwd if unset.
ProjectDir="${COPILOT_PROJECT_DIR:-$(pwd)}"
optInFile="$ProjectDir/.copilot-extensions/config.yaml"
optedIn=0
if [ -f "$optInFile" ] && grep -qE '^[[:space:]]*background_reconcile:[[:space:]]*true[[:space:]]*$' "$optInFile" 2>/dev/null; then
  optedIn=1
fi
if [ "$optedIn" -ne 1 ]; then
  echo "[$name] runtime $deployed -> $current; background reconcile SKIPPED (no opt-in -- add 'background_reconcile: true' to $optInFile to enable)" >&2
  exit 0
fi

if [ -f "$PluginDir/scripts/init.sh" ]; then
  target=("$PluginDir/scripts/init.sh")
elif [ -f "$PluginDir/scripts/install.sh" ]; then
  target=("$PluginDir/scripts/install.sh" install)
else
  exit 0
fi
echo "[$name] runtime $deployed -> $current; reconciling in background (log: $InstallDir/reconcile.log)..." >&2
reconcile_log="$InstallDir/reconcile.log"
status_file="$InstallDir/reconcile-status.json"
now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
# Observability (#167): capture the otherwise-silent background reconcile.
nohup bash "${target[@]}" >"$reconcile_log" 2>&1 &
launched_pid=$!
printf '{"at":"%s","from":"%s","to":"%s","launched_pid":%s,"log":"%s"}\n' \
  "$now" "$deployed" "$current" "$launched_pid" "$reconcile_log" \
  >"$status_file" 2>/dev/null || true
exit 0