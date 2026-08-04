#!/usr/bin/env bash
# agent-machines session-start hook -- version-gated runtime reconcile.
#
# Runs at session start (via hooks.json). Ensures the installed agent-machines
# binstub/venv matches the plugin source version, so a `copilot plugin update`
# that bumps the payload is picked up automatically -- without ever running
# machine *restoration* itself.
#
# Fast path: compare the deployed version (~/.agent-machines/deploy-manifest.json)
# to the source version (plugin pyproject.toml). If they match and the binstub
# exists, exit immediately. Otherwise re-run the plugin's own installer
# (scripts/init.sh) in the BACKGROUND so session start never blocks on a venv
# build; the versioned-venv swap is atomic, so concurrent use stays safe.
#
# Deployed to ~/.agent-machines/bin/ by scripts/init.sh. Only reconciles
# staleness -- first install is the one-time agent-machines-setup step.

InstallDir="$HOME/.agent-machines"
Manifest="$InstallDir/deploy-manifest.json"
Binstub="$HOME/.local/bin/agent-machines"

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

# Up to date and binstub present -> fast no-op.
if [ -x "$Binstub" ] && [ "$deployed" = "$current" ]; then exit 0; fi

init="$pluginDir/scripts/init.sh"
[ -f "$init" ] || exit 0

echo "[agent-machines] runtime $deployed -> $current; reconciling in background..."
nohup bash "$init" >/dev/null 2>&1 &

exit 0
