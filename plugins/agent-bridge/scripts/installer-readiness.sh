#!/usr/bin/env bash
set -uo pipefail

plugin_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export AGENT_BRIDGE_NO_SELFPROVISION=1
output="$("$plugin_root/bin/agent-bridge" installer-readiness 2>/dev/null)"
exit_code=$?
if [[ -n "$output" ]]; then
    printf '%s\n' "$output"
    exit "$exit_code"
fi

printf '%s\n' '{"schema":"copilot-extensions.module-readiness","version":1,"module":"agent-bridge/runtime","state":"failed","detail":"The agent-bridge runtime is not installed or could not run its readiness probe. Run the declared installer update."}'
exit 1
