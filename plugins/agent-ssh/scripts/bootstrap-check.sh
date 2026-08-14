#!/usr/bin/env bash
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

InstallDir="$HOME/.agent-ssh"
Manifest="$InstallDir/deploy-manifest.json"

[ -f "$Manifest" ] || exit 0

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

echo "[agent-ssh] runtime $deployed -> $current; reconciling in background..."
nohup bash "$init" >/dev/null 2>&1 &

exit 0
