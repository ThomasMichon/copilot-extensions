#!/usr/bin/env bash
set -uo pipefail

plugin_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export AGENT_CONTAINERS_NO_SELFPROVISION=1
output="$("$plugin_root/bin/agent-containers" installer-readiness 2>/dev/null)"
exit_code=$?
if [[ -n "$output" ]]; then
    printf '%s\n' "$output"
    exit "$exit_code"
fi

printf '%s\n' '{"schema":"copilot-extensions.module-readiness","version":1,"module":"agent-containers/runtime","state":"failed","detail":"The agent-containers runtime is not installed or could not run its readiness probe. Run the declared installer init action."}'
exit 1
