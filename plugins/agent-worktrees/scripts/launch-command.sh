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
    # Stage 3 (copilot_invoked): the config-driven launch-template and legacy
    # tools/setup/setup.sh paths never reach default-setup.sh's own precise
    # exec-point emitter, so this wrapper -- the one seam EVERY resolved
    # command passes through -- emits a coarser "attempted" mark for them
    # instead (best-effort/detached; distinct from the confirmed event
    # default-setup.sh emits for its own path).
    _aw_resolve="$HOME/.agent-worktrees/bin/resolve-runtime.sh"
    if [[ -f "$_aw_resolve" ]]; then
        # shellcheck disable=SC1090
        . "$_aw_resolve"
        if [[ -x "${AW_PY:-}" ]]; then
            _aw_wt="$(PYTHONPATH="" "$AW_PY" -I -m agent_worktrees get worktree-id 2>/dev/null || true)"
            if [[ -n "$_aw_wt" ]]; then
                ( PYTHONPATH="" "$AW_PY" -I -m agent_worktrees activity-log \
                    copilot_invocation_attempted --worktree-id "$_aw_wt" \
                    --source launcher >/dev/null 2>&1 & ) || true
            fi
        fi
    fi
fi
unset _aw_uses_default_setup

exec "$@"
