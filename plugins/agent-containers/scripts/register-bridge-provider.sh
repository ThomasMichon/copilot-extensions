#!/usr/bin/env bash
# register-bridge-provider -- drop this plugin's agent-bridge namespace-provider
# manifest into the providers.d registry so agent-bridge discovers it
# DECLARATIVELY (no imperative "bridge register" call).
#
# Generic + self-locating: byte-identical across provider plugins. The plugin
# ships its own `references/bridge-provider.json` template (namespace /
# restricted / description); this hook resolves the plugin's ABSOLUTE binstub
# and injects it as the manifest `command`, so the agent-bridge daemon -- which
# cannot import the provider nor see its binstub on PATH -- can still drive it
# over a process boundary.
#
# Safe + best-effort: if the binstub isn't provisioned yet (fresh install), exit
# 0 and let a later session drop it. Never blocks the session; never raises.
set -uo pipefail

ScriptDir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PluginDir="$(cd "$ScriptDir/.." && pwd)"

py="$(command -v python3 || command -v python || true)"
[ -n "$py" ] || exit 0

name="$("$py" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("name",""))' "$PluginDir/plugin.json" 2>/dev/null)"
[ -n "$name" ] || name="$(basename "$PluginDir")"
[ -n "$name" ] || exit 0

template="$PluginDir/references/bridge-provider.json"
[ -f "$template" ] || exit 0

# Binstub location is a fixed agent-* runtime convention ($HOME/.local/bin/<name>).
binstub="$HOME/.local/bin/$name"
[ -x "$binstub" ] || exit 0

# Resolve providers.d honoring agent-bridge's config-dir contract.
if [ -n "${AGENT_BRIDGE_PROVIDERS_DIR:-}" ]; then
  dir="$AGENT_BRIDGE_PROVIDERS_DIR"
else
  dir="${AGENT_BRIDGE_CONFIG_DIR:-$HOME/.agent-bridge}/providers.d"
fi
mkdir -p "$dir" 2>/dev/null || exit 0

"$py" - "$template" "$binstub" "$dir/$name.json" <<'PY'
import json, os, sys

template, binstub, out = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(template, encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    sys.exit(0)

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
