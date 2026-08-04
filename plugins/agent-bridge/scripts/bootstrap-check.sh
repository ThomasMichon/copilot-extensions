#!/usr/bin/env bash
# Session-start runtime reconcile -- generic, self-locating; byte-identical across
# agent-* runtime plugins. Invoked (via hooks.json) from the plugin's scripts/
# dir. Derives the install dir from plugin.json's name (~/.<name>) and re-runs the
# installer in the BACKGROUND only when the deployed version drifts from the
# payload. Reconciles the TOOL, never machine state/config.
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
echo "[$name] runtime $deployed -> $current; reconciling in background..."
nohup bash "${target[@]}" >/dev/null 2>&1 &
exit 0