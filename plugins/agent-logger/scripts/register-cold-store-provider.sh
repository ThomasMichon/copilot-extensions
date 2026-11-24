#!/usr/bin/env bash
# register-cold-store-provider -- drop this plugin's agent-bridge
# cold-store-provider manifest into the cold-store-providers.d registry so
# agent-bridge discovers it DECLARATIVELY (no imperative "bridge register"
# call). Twin of agent-codespaces'/agent-containers' `register-bridge-
# provider.sh`, but for the **cold-store** provider kind (capability-keyed,
# distinct directory, `session-fetch` verb) rather than a `namespace:`
# address-space provider -- see `docs/architecture.md`'s cold-store-provider
# subsection for the full contract.
#
# Generic + self-locating: the plugin ships its own
# `references/cold-store-provider.json` template (capability/description);
# this hook resolves the payload-local shim and injects it as the manifest
# `command`, so the agent-bridge daemon can drive the exact same payload and
# installation context the current session loaded.
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

case "${OSTYPE:-}" in
    msys*|cygwin*)
        host="$(cygpath -u "${SYSTEMROOT:-${SystemRoot:-}}")/System32/WindowsPowerShell/v1.0/powershell.exe"
        "$host" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass \
            -File "$(cygpath -w "$ScriptDir/register-cold-store-provider.ps1")" \
            </dev/null >/dev/null 2>&1 || true
        exit 0
        ;;
esac

selector="$ScriptDir/hook-bootstrap-python.sh"
[ -f "$selector" ] || exit 0
. "$selector"
py="$(logger_hook_python "$ScriptDir" 2>/dev/null)" || exit 0

template="$PluginDir/references/cold-store-provider.json"
[ -f "$template" ] || exit 0

# Resolve cold-store-providers.d honoring agent-bridge's config-dir contract.
if [ -n "${AGENT_BRIDGE_COLD_STORE_PROVIDERS_DIR:-}" ]; then
  dir="$AGENT_BRIDGE_COLD_STORE_PROVIDERS_DIR"
else
  dir="${AGENT_BRIDGE_CONFIG_DIR:-$HOME/.agent-bridge}/cold-store-providers.d"
fi
"$py" -I -B -X utf8 - "$template" "$PluginDir" "$dir" <<'PY'
import json, os, sys
from pathlib import Path

template, plugin_root, directory = sys.argv[1:]
try:
    with open(Path(plugin_root) / "plugin.json", encoding="utf-8") as f:
        name = json.load(f).get("name", "")
except Exception:
    name = ""
if not isinstance(name, str):
    sys.exit(0)
name = name or Path(plugin_root).name
binstub = (Path(plugin_root) / "bin" / name).as_posix()
if not os.path.isfile(binstub) or not os.access(binstub, os.X_OK):
    sys.exit(0)
out = str(Path(directory) / (name + ".json"))
try:
    with open(template, encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    sys.exit(0)

data["plugin_root"] = os.path.realpath(plugin_root)
data["command"] = [binstub]
payload = json.dumps(data, indent=2, sort_keys=True) + "\n"

try:
    os.makedirs(directory, exist_ok=True)
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
