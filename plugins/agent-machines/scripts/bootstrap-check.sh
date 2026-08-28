#!/usr/bin/env bash
# agent-machines session-start hook -- version-gated runtime reconcile.
#
# Runs at session start (via hooks.json). Ensures the installed agent-machines
# binstub/venv matches the plugin source version, so a `copilot plugin update`
# that bumps the payload is picked up automatically -- without ever running
# machine *restoration* itself.
#
# Fast path: compare the deployed version to the source version (plugin
# pyproject.toml). Legacy deployments read ~/.agent-machines/deploy-manifest.json;
# an explicit validated installation context may redirect that read to its
# plugin root. Namespaced writes remain blocked until the context-aware
# installer is operative.
#
# Deployed to ~/.agent-machines/bin/ by scripts/init.sh. Only reconciles
# staleness -- first install is the one-time agent-machines-setup step.

ScriptDir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PluginDir="$(cd "$ScriptDir/.." && pwd)"
legacy_mutation_allowed() {
  local probe="$ScriptDir/installation-context/legacy-entrypoint-probe.sh"
  [ -f "$probe" ] || {
    echo "[agent-machines] legacy mutation probe is unavailable; skipping reconcile." >&2
    return 1
  }
  bash "$probe" --payload-root "$PluginDir" --legacy-root "$HOME/.agent-machines"
}
ContextSelected=0
InstallDir="$HOME/.agent-machines"
if [ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]; then
  resolver="$ScriptDir/installation-context/installation-context.sh"
  query="$ScriptDir/installation-context/json-query.awk"
  if [ ! -f "$resolver" ] || [ ! -f "$query" ]; then
    echo "[agent-machines] installation context is selected but its validator is unavailable; skipping reconcile." >&2
    exit 0
  fi
  durableHome="$COPILOT_EXTENSIONS_CONTEXT"
  for _part in 1 2 3 4 5; do durableHome="$(dirname -- "$durableHome")"; done
  validated="$(mktemp)" || exit 0
  if ! bash "$resolver" validate \
      --context "$COPILOT_EXTENSIONS_CONTEXT" \
      --durable-home "$durableHome" >"$validated"; then
    rm -f -- "$validated"
    echo "[agent-machines] installation context is invalid; skipping reconcile without legacy fallback." >&2
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
    echo "[agent-machines] installation context is invalid; skipping reconcile without legacy fallback." >&2
    exit 0
  fi
  if [ "$contextPlugin" = agent-machines ]; then
    resolved="$(mktemp)" || exit 0
    if ! bash "$resolver" resolve \
        --context "$COPILOT_EXTENSIONS_CONTEXT" \
        --plugin-id agent-machines \
        --payload-root "$PluginDir" \
        --durable-home "$durableHome" >"$resolved"; then
      rm -f -- "$resolved"
      echo "[agent-machines] installation context is invalid; skipping reconcile without legacy fallback." >&2
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
      echo "[agent-machines] installation context returned no plugin root; skipping reconcile without legacy fallback." >&2
      exit 0
    fi
    ContextSelected=1
  fi
fi
Manifest="$InstallDir/deploy-manifest.json"
Binstub="$HOME/.local/bin/agent-machines"

# Not provisioned yet -> do the cheap FIRST install ('stamp') so the binstub is
# on PATH this session; the self-provisioning binstub then builds the venv on
# first use (#1393). hooks.json runs the PAYLOAD copy, so this script's own dir
# is the plugin's scripts/ dir even on a fresh box. Fires only when init.sh
# declares a 'stamp' action; else a safe no-op.
if [ ! -f "$Manifest" ]; then
  if [ "$ContextSelected" = 1 ]; then
    echo "[agent-machines] selected context has no deploy manifest; namespaced install remains non-operative." >&2
    exit 0
  fi
  _init="$ScriptDir/init.sh"
  if [ -f "$_init" ] && grep -q 'stamp)' "$_init" 2>/dev/null; then
    legacy_mutation_allowed || exit 0
    bash "$_init" stamp >/dev/null 2>&1 || true
  fi
  exit 0
fi

py="$(command -v python3 || command -v python || true)"
[ -n "$py" ] || exit 0

deployed="$("$py" -c 'import json,sys;print(json.load(open(sys.argv[1]))["source"].get("version",""))' "$Manifest" 2>/dev/null)"
if [ "$ContextSelected" = 1 ]; then
  pluginDir="$PluginDir"
else
  pluginDir="$("$py" -c 'import json,sys;print(json.load(open(sys.argv[1]))["source"]["path"])' "$Manifest" 2>/dev/null)"
fi
[ -n "$pluginDir" ] && [ -d "$pluginDir" ] || exit 0

current="$deployed"
pyproj="$pluginDir/pyproject.toml"
if [ -f "$pyproj" ]; then
  v="$(grep -m1 -E '^[[:space:]]*version[[:space:]]*=' "$pyproj" | sed -E 's/.*=[[:space:]]*"([^"]+)".*/\1/')"
  [ -n "$v" ] && current="$v"
fi
if [ "$ContextSelected" = 1 ]; then
  if [ "$deployed" = "$current" ]; then exit 0; fi
  echo "[agent-machines] selected context runtime $deployed -> $current; context-aware install is not active yet." >&2
  exit 0
fi

# Up to date and binstub present -> fast no-op.
if [ -x "$Binstub" ] && [ "$deployed" = "$current" ]; then exit 0; fi

init="$pluginDir/scripts/init.sh"
[ -f "$init" ] || exit 0

legacy_mutation_allowed || exit 0
echo "[agent-machines] runtime $deployed -> $current; reconciling in background..."
nohup bash "$init" >/dev/null 2>&1 &

exit 0
