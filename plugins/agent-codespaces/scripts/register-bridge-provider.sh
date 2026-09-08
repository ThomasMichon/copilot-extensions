#!/usr/bin/env bash
# register-bridge-provider -- drop this plugin's agent-bridge namespace-provider
# manifest into the providers.d registry so agent-bridge discovers it
# DECLARATIVELY (no imperative "bridge register" call).
#
# Generic + self-locating: byte-identical across provider plugins. The plugin
# ships its own `references/bridge-provider.json` template (namespace /
# restricted / description); this hook resolves the payload-local shim and
# injects it as the manifest `command`, so the agent-bridge daemon can drive the
# exact same payload and installation context the current session loaded.
#
# Safe + best-effort: if the payload-local shim is absent, exit 0 and let a
# later session drop it. Never blocks the session; never raises.
set -uo pipefail
session_start_json_emitted=0
emit_session_start_json() {
  if [ "${session_start_json_emitted:-0}" -eq 0 ]; then
    printf '{}'
    session_start_json_emitted=1
  fi
}
trap 'emit_session_start_json' EXIT

ScriptDir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PluginDir="$(cd "$ScriptDir/.." && pwd)"

py="$(command -v python3 || command -v python || true)"
[ -n "$py" ] || exit 0

name="$("$py" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("name",""))' "$PluginDir/plugin.json" 2>/dev/null)"
[ -n "$name" ] || name="$(basename "$PluginDir")"
[ -n "$name" ] || exit 0

template="$PluginDir/references/bridge-provider.json"
[ -f "$template" ] || exit 0

# Use the payload-local shim, not the mutable machine-global compatibility
# binstub, so providers stay bound to the exact payload root the current
# installation context selected.
binstub="$PluginDir/bin/$name"
[ -x "$binstub" ] || exit 0

# Resolve providers.d honoring agent-bridge's config-dir contract.
if [ -n "${AGENT_BRIDGE_PROVIDERS_DIR:-}" ]; then
  dir="$AGENT_BRIDGE_PROVIDERS_DIR"
else
  dir="${AGENT_BRIDGE_CONFIG_DIR:-$HOME/.agent-bridge}/providers.d"
fi
mkdir -p "$dir" 2>/dev/null || exit 0

"$py" - "$template" "$PluginDir" "$binstub" "$dir/$name.json" <<'PY'
import json, os, sys

template, plugin_root, binstub, out = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
try:
    with open(template, encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    sys.exit(0)

data["plugin_root"] = os.path.realpath(plugin_root)
data["command"] = [binstub]
payload = json.dumps(data, indent=2, sort_keys=True) + "\n"

try:
    if os.path.exists(out):
        with open(out, encoding="utf-8-sig") as f:
            if f.read() == payload:
                sys.exit(0)  # unchanged -- idempotent
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
    os.replace(tmp, out)
except Exception:
    sys.exit(0)
PY
exit 0
