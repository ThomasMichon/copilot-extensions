#!/usr/bin/env bash
# Session-start runtime bootstrap + reconcile -- generic, self-locating. Invoked
# (via hooks.json) from the plugin's scripts/ dir. Derives the install dir from
# plugin.json's name (~/.<name>). Two jobs, both grace-window-cheap:
#   1. FIRST install (unprovisioned): if the installer supports a cheap 'stamp'
#      action, splat the self-provisioning binstub now (deferring the venv build
#      to the binstub's first use) so the CLI is on PATH this session -- no venv
#      build on the hook. Installers without 'stamp' keep the old no-op.
#   2. RECONCILE (already provisioned): re-run the legacy installer in the
#      BACKGROUND only when the deployed version drifts. An explicit validated
#      installation context redirects manifest inspection only; namespaced
#      writes remain blocked until context-aware installers become operative.
ScriptDir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PluginDir="$(cd "$ScriptDir/.." && pwd)"
legacy_mutation_allowed() {
  local probe="$ScriptDir/installation-context/legacy-entrypoint-probe.sh"
  [ -f "$probe" ] || {
    echo "[$name] legacy mutation probe is unavailable; skipping reconcile." >&2
    return 1
  }
  bash "$probe" --payload-root "$PluginDir" --legacy-root "$HOME/.$name"
}
py="$(command -v python3 || command -v python || true)"; [ -n "$py" ] || exit 0
name="$("$py" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("name",""))' "$PluginDir/plugin.json" 2>/dev/null)"
[ -n "$name" ] || exit 0
ContextSelected=0
InstallDir="$HOME/.$name"
if [ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]; then
  resolver="$ScriptDir/installation-context/installation-context.sh"
  query="$ScriptDir/installation-context/json-query.awk"
  if [ ! -f "$resolver" ] || [ ! -f "$query" ]; then
    echo "[$name] installation context is selected but its validator is unavailable; skipping reconcile." >&2
    exit 0
  fi
  durableHome="$COPILOT_EXTENSIONS_CONTEXT"
  for _part in 1 2 3 4 5; do durableHome="$(dirname -- "$durableHome")"; done
  validated="$(mktemp)" || exit 0
  if ! bash "$resolver" validate \
      --context "$COPILOT_EXTENSIONS_CONTEXT" \
      --durable-home "$durableHome" >"$validated"; then
    rm -f -- "$validated"
    echo "[$name] installation context is invalid; skipping reconcile without legacy fallback." >&2
    exit 0
  fi
  encoded="$(LC_ALL=C awk -f "$query" -v mode=hex -v query_path=pluginId "$validated" 2>/dev/null || true)"
  rm -f -- "$validated"
  contextPlugin=""
  while [ -n "$encoded" ]; do
    [ "${#encoded}" -ge 2 ] || { contextPlugin=""; break; }
    printf -v byte '%b' "\\x${encoded:0:2}"
    contextPlugin+="$byte"
    encoded="${encoded:2}"
  done
  if [ -z "$contextPlugin" ]; then
    echo "[$name] installation context is invalid; skipping reconcile without legacy fallback." >&2
    exit 0
  fi
  if [ "$contextPlugin" = "$name" ]; then
    resolved="$(mktemp)" || exit 0
    if ! bash "$resolver" resolve \
        --context "$COPILOT_EXTENSIONS_CONTEXT" \
        --plugin-id "$name" \
        --payload-root "$PluginDir" \
        --durable-home "$durableHome" >"$resolved"; then
      rm -f -- "$resolved"
      echo "[$name] installation context is invalid; skipping reconcile without legacy fallback." >&2
      exit 0
    fi
    encoded="$(LC_ALL=C awk -f "$query" -v mode=hex -v query_path=pluginRoot "$resolved" 2>/dev/null || true)"
    rm -f -- "$resolved"
    InstallDir=""
    while [ -n "$encoded" ]; do
      [ "${#encoded}" -ge 2 ] || { InstallDir=""; break; }
      printf -v byte '%b' "\\x${encoded:0:2}"
      InstallDir+="$byte"
      encoded="${encoded:2}"
    done
    if [ -z "$InstallDir" ]; then
      echo "[$name] installation context returned no plugin root; skipping reconcile without legacy fallback." >&2
      exit 0
    fi
    ContextSelected=1
  fi
fi
Manifest="$InstallDir/deploy-manifest.json"
if [ ! -f "$Manifest" ]; then
  if [ "$ContextSelected" = 1 ]; then
    echo "[$name] selected context has no deploy manifest; namespaced install remains non-operative." >&2
    exit 0
  fi
  # Not provisioned yet -- do the cheap FIRST install (stamp) so the binstub is
  # callable this session; it self-provisions the runtime on first use. Fully
  # back-compatible: only fires when the installer declares a 'stamp' action.
  installer=""
  for candidate in "$PluginDir/scripts/init.sh" "$PluginDir/scripts/install.sh"; do
    if [ -f "$candidate" ] && grep -qE '^[[:space:]]*([[:alnum:]_-]+\|)*stamp(\|[[:alnum:]_-]+)*\)' "$candidate" 2>/dev/null; then installer="$candidate"; break; fi
  done
  if [ -n "$installer" ]; then
    legacy_mutation_allowed || exit 0
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
if [ "$ContextSelected" = 1 ]; then
  if [ "$deployed" = "$current" ]; then exit 0; fi
  echo "[$name] selected context runtime $deployed -> $current; context-aware install is not active yet." >&2
  exit 0
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
legacy_mutation_allowed || exit 0
echo "[$name] runtime $deployed -> $current; reconciling in background..."
nohup bash "${target[@]}" >/dev/null 2>&1 &
exit 0