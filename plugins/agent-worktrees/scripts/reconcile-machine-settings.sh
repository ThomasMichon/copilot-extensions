#!/usr/bin/env bash

if [[ "${1:-}" == "--recovery" ]] ||
    [[ "${AGENT_WORKTREES_MACHINE_SETTINGS_RECONCILED:-}" == "1" ]]; then
    return 0
fi

if ! command -v agent-machines >/dev/null 2>&1; then
    export AGENT_WORKTREES_MACHINE_SETTINGS_RECONCILED=1
    return 0
fi

_aw_machine_settings_output=""
if _aw_machine_settings_output="$(
    agent-machines restore \
        --all-projects \
        --only copilot.settings \
        --apply \
        --json 2>&1
)"; then
    export AGENT_WORKTREES_MACHINE_SETTINGS_RECONCILED=1
    unset _aw_machine_settings_output
    return 0
else
    _aw_machine_settings_rc=$?
fi

printf '%s\n' \
    'ERROR: agent-machines failed to reconcile Copilot settings before launch.' >&2
if [[ -n "$_aw_machine_settings_output" ]]; then
    printf '%s\n' "$_aw_machine_settings_output" >&2
fi
unset _aw_machine_settings_output
return "$_aw_machine_settings_rc"
