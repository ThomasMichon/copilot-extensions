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
ContextActive=0
ContextPath=""
ContextMarketplaceId=""
InstallDir="$HOME/.agent-machines"
ProfileHome=""
uid="$(id -u 2>/dev/null || true)"
if [ -n "$uid" ] && command -v getent >/dev/null 2>&1; then
  ProfileHome="$(getent passwd "$uid" 2>/dev/null | cut -d: -f6)"
fi
if [ -z "$ProfileHome" ] && [ -n "$uid" ] && [ -r /etc/passwd ]; then
  ProfileHome="$(awk -F: -v uid="$uid" '$3 == uid { print $6; exit }' /etc/passwd)"
fi
if [ -z "$ProfileHome" ]; then
  echo "[agent-machines] canonical profile home is unavailable; skipping reconcile." >&2
  exit 0
fi
PolicyPath="$ProfileHome/.copilot-extensions/installation-mode.json"
  resolver="$ScriptDir/installation-context/installation-context.sh"
  query="$ScriptDir/installation-context/json-query.awk"
  if [ ! -f "$resolver" ] || [ ! -f "$query" ]; then
    echo "[agent-machines] installation context is selected but its validator is unavailable; skipping reconcile." >&2
    exit 0
  fi
  statusArgs=(
    status
    --payload-root "$PluginDir"
    --plugin-id agent-machines
    --legacy-root "$HOME/.agent-machines" # marketplace-isolation: allow legacy compatibility root
  )
  if [ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]; then
    statusArgs+=(--context "$COPILOT_EXTENSIONS_CONTEXT")
    contextDurableHome="$COPILOT_EXTENSIONS_CONTEXT"
    for _part in 1 2 3 4 5; do
      contextDurableHome="$(dirname -- "$contextDurableHome")"
    done
    statusArgs+=(--durable-home "$contextDurableHome")
  fi
  if ! resolved="$(bash "$resolver" "${statusArgs[@]}" 2>/dev/null)"; then
    echo "[agent-machines] installation status is invalid; skipping reconcile without legacy fallback." >&2
    exit 0
  fi
  jsonValue() {
    LC_ALL=C awk -f "$query" -v mode=get -v query_path="$1" <<<"$resolved" 2>/dev/null
  }
  sep=$'\034'
  jsonPath() {
    local value="" part
    for part in "$@"; do
      [ -z "$value" ] || value+="$sep"
      value+="$part"
    done
    printf '%s' "$value"
  }
  status="$(jsonValue status || true)"
  reason="$(jsonValue reason || true)"
  actualMode="$(jsonValue actualMode || true)"
  desiredMode="$(jsonValue desiredMode || true)"
  simplePolicyLegacy=0
  if [ -z "${COPILOT_EXTENSIONS_CONTEXT:-}" ] &&
     [ "$status" = provenance-blocked ] &&
     [ "$(jsonValue "$(jsonPath policy state)" || true)" = valid ] &&
     [ "$(jsonValue "$(jsonPath policy enabled)" || true)" = false ]; then
    marketplacesPath="$(jsonPath installationMode marketplaces)"
    marketplacesType="$(LC_ALL=C awk -f "$query" -v mode=type -v query_path="$marketplacesPath" "$PolicyPath" 2>/dev/null || true)"
    if [ -z "$marketplacesType" ] ||
       { [ "$marketplacesType" = object ] &&
         [ "$(LC_ALL=C awk -f "$query" -v mode=len -v query_path="$marketplacesPath" "$PolicyPath" 2>/dev/null || true)" = 0 ]; }; then
      simplePolicyLegacy=1
    fi
  fi
  if { [ "$status" = ready ] && [ "$actualMode" = legacy ] && [ "$desiredMode" = legacy ]; } ||
     [ "$simplePolicyLegacy" = 1 ]; then
    if [ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]; then
      echo "[agent-machines] requested installation context is not active; skipping reconcile without legacy fallback." >&2
      exit 0
    fi
  elif { [ "$status" = ready ] && [ "$reason" = namespaced-active ]; } ||
       [ "$status" = deactivation-required ]; then
    [ "$actualMode" = namespaced ] || exit 0
    InstallDir="$(jsonValue runtimeRoot || true)"
    ContextPath="$(jsonValue context || true)"
    ContextMarketplaceId="$(jsonValue marketplaceId || true)"
    if [ -z "$InstallDir" ] || [ -z "$ContextPath" ] || [ -z "$ContextMarketplaceId" ]; then
      echo "[agent-machines] active installation context is incomplete; skipping reconcile." >&2
      exit 0
    fi
    ContextSelected=1
    if [ "$status" = ready ] && [ "$reason" = namespaced-active ]; then
      ContextActive=1
    fi
  else
    echo "[agent-machines] installation governance blocks reconcile without legacy fallback: status=$status reason=$reason." >&2
    exit 0
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
    if [ "$ContextActive" = 1 ]; then
      init="$PluginDir/scripts/init.sh"
      if [ -f "$init" ]; then
        echo "[agent-machines] active cell has no runtime; reconciling in background..."
        nohup bash "$init" cell-provision \
          --context "$ContextPath" \
          --expected-marketplace-id "$ContextMarketplaceId" >/dev/null 2>&1 &
      fi
    fi
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

if [ "$ContextSelected" = 1 ]; then
  manifestValues="$("$py" -c '
import json, sys
def strict(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value
with open(sys.argv[1], "r", encoding="utf-8") as stream:
    manifest = json.load(stream, object_pairs_hook=strict)
source = manifest.get("source")
runtime = manifest.get("runtime")
installation = manifest.get("installation")
selected = runtime.get("selectedBy") if isinstance(runtime, dict) else None
valid = (
    type(manifest.get("schema_version")) is int
    and manifest["schema_version"] == 4
    and manifest.get("service") == "agent-machines"
    and isinstance(source, dict)
    and source.get("repo") == "copilot-extensions"
    and source.get("plugin") == "agent-machines"
    and all(isinstance(source.get(key), str) and source[key]
            for key in ("kind", "path", "version"))
    and type(source.get("dirty")) is bool
    and isinstance(runtime, dict)
    and runtime.get("kind") == "python"
    and all(isinstance(runtime.get(key), str) and runtime[key]
            for key in ("version", "path", "interpreter"))
    and isinstance(selected, dict)
    and all(isinstance(selected.get(key), str) and selected[key]
            for key in ("kind", "path", "version"))
    and isinstance(installation, dict)
    and installation.get("marketplaceId") == sys.argv[2]
    and installation.get("pluginId") == "agent-machines"
    and installation.get("context") == sys.argv[3]
)
if not valid:
    raise ValueError("invalid manifest")
print("\x1c".join((
    source["version"], source["path"], runtime["version"],
    runtime["path"], runtime["interpreter"], selected["version"],
)))
' "$Manifest" "$ContextMarketplaceId" "$ContextPath" 2>/dev/null)" || {
    echo "[agent-machines] active cell deploy manifest is invalid; skipping reconcile without legacy fallback." >&2
    exit 0
  }
  IFS=$'\034' read -r deployed sourcePath activeVersion runtimePath runtimeInterpreter selectedByVersion <<<"$manifestValues"
  CurrentMarker="$InstallDir/current-version"
  expectedRuntimePath="$InstallDir/versions/$activeVersion"
  expectedInterpreter="$expectedRuntimePath/bin/python"
  if [ -z "$deployed" ] || [ -z "$sourcePath" ] ||
     [ -z "$activeVersion" ] || [ "$runtimePath" != "$expectedRuntimePath" ] ||
     [ "$runtimeInterpreter" != "$expectedInterpreter" ] ||
     [ "$selectedByVersion" != "$activeVersion" ] ||
     [ ! -f "$CurrentMarker" ] || [ -L "$CurrentMarker" ] ||
     [ "$(cat "$CurrentMarker" 2>/dev/null)" != "$activeVersion" ] ||
     [ ! -x "$runtimeInterpreter" ]; then
    echo "[agent-machines] active cell deploy manifest is invalid; skipping reconcile without legacy fallback." >&2
    exit 0
  fi
  current="$deployed"
  pyproj="$PluginDir/pyproject.toml"
  if [ -f "$pyproj" ]; then
    v="$(grep -m1 -E '^[[:space:]]*version[[:space:]]*=' "$pyproj" | sed -E 's/.*=[[:space:]]*"([^"]+)".*/\1/')"
    [ -n "$v" ] && current="$v"
  fi
  if { [ "$deployed" = "$current" ] && [ "$sourcePath" = "$PluginDir" ]; } ||
     [ "$ContextActive" != 1 ]; then
    exit 0
  fi
  init="$PluginDir/scripts/init.sh"
  [ -f "$init" ] || exit 0
  echo "[agent-machines] active cell payload $deployed -> $current (runtime $activeVersion); reconciling in background..."
  nohup bash "$init" cell-provision \
    --context "$ContextPath" \
    --expected-marketplace-id "$ContextMarketplaceId" >/dev/null 2>&1 &
  exit 0
fi

deployed="$("$py" -c 'import json,sys;print(json.load(open(sys.argv[1]))["source"].get("version",""))' "$Manifest" 2>/dev/null)"
pluginDir="$("$py" -c 'import json,sys;print(json.load(open(sys.argv[1]))["source"]["path"])' "$Manifest" 2>/dev/null)"
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

legacy_mutation_allowed || exit 0
echo "[agent-machines] runtime $deployed -> $current; reconciling in background..."
nohup bash "$init" >/dev/null 2>&1 &

exit 0
