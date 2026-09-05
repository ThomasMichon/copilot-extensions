#!/usr/bin/env bash
set -euo pipefail

_aw_launch_recovery=""
if [[ "${1:-}" == "--recovery" ]]; then
    _aw_launch_recovery="--recovery"
    shift
fi
if [[ "${1:-}" == "--" ]]; then
    shift
fi
if [[ $# -eq 0 ]]; then
    printf '%s\n' 'ERROR: launch-command.sh requires a command.' >&2
    exit 2
fi

_aw_machine_settings_helper="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/reconcile-machine-settings.sh"
if [[ -f "$_aw_machine_settings_helper" ]]; then
    # shellcheck disable=SC1090
    . "$_aw_machine_settings_helper" "$_aw_launch_recovery"
fi

_aw_uses_default_setup=""
if [[ "${1##*/}" == "bash" ]] &&
    [[ "${2:-}" != "" ]] &&
    [[ "${2##*/}" == "default-setup.sh" ]]; then
    _aw_uses_default_setup=1
fi
if [[ -z "$_aw_uses_default_setup" ]]; then
    unset AGENT_WORKTREES_MACHINE_SETTINGS_RECONCILED
fi
unset _aw_uses_default_setup

exec "$@"
