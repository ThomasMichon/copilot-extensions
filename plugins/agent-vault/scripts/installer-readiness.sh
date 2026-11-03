#!/usr/bin/env bash
set -uo pipefail

plugin_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export AGENT_VAULT_NO_SELFPROVISION=1
output="$("$plugin_root/bin/agent-vault" installer-readiness 2>/dev/null)"
exit_code=$?
if [[ -n "$output" ]]; then
    printf '%s\n' "$output"
    exit "$exit_code"
fi

if [[ "$exit_code" == 3 ]]; then
    printf '%s\n' '{"schema":"copilot-extensions.module-readiness","version":1,"module":"agent-vault/runtime","state":"failed","detail":"Agent Vault activation configuration could not be resolved; correct its configuration, binding or payload."}'
else
    printf '%s\n' '{"schema":"copilot-extensions.module-readiness","version":1,"module":"agent-vault/runtime","state":"failed","detail":"The agent-vault runtime is not installed or could not run its readiness probe. Run the declared installer update."}'
fi
exit 1
