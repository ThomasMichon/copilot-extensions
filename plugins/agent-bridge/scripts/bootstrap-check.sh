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
ScriptDir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PluginDir="$(cd "$ScriptDir/.." && pwd)"
py="$(command -v python3 || command -v python || true)"; [ -n "$py" ] || exit 0
name="$("$py" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("name",""))' "$PluginDir/plugin.json" 2>/dev/null)"
[ -n "$name" ] || exit 0
InstallDir="$HOME/.$name"
Manifest="$InstallDir/deploy-manifest.json"
[ -f "$Manifest" ] || exit 0
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
echo "[$name] runtime $deployed -> $current; reconciling in background (log: $InstallDir/reconcile.log)..."
reconcile_log="$InstallDir/reconcile.log"
status_file="$InstallDir/reconcile-status.json"
now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
# Observability (#167): capture the otherwise-silent background reconcile.
nohup bash "${target[@]}" >"$reconcile_log" 2>&1 &
launched_pid=$!
printf '{"at":"%s","from":"%s","to":"%s","launched_pid":%s,"log":"%s"}\n' \
  "$now" "$deployed" "$current" "$launched_pid" "$reconcile_log" >"$status_file" 2>/dev/null || true
exit 0