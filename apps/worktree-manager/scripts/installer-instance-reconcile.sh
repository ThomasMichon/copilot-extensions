#!/usr/bin/env bash

_reconcile_installer_instances_managed() (
    local adapter_root="$1" helper_root="$2" registry_root="$3" runtime_root="$4" python
    if ! unset -f daemon_instance_reconcile_contract reconcile_daemon_instances 2>/dev/null; then
        printf '%s\n' '[installer-instance-gc] deferred: native-contract-unavailable' >&2
        return 0
    fi
    . "$helper_root/daemon_instances.sh" || {
        printf '%s\n' '[installer-instance-gc] deferred: native-helper-load-failed' >&2
        return 0
    }
    if ! declare -F daemon_instance_reconcile_contract >/dev/null ||
        ! declare -F reconcile_daemon_instances >/dev/null ||
        [[ "$(daemon_instance_reconcile_contract)" != 'copilot-daemon-reconcile-v1' ]]; then
        printf '%s\n' '[installer-instance-gc] deferred: native-contract-unavailable' >&2
        return 0
    fi
    if [[ ! -f "$adapter_root/bootstrap-uv.sh" ||
        ! -f "$adapter_root/installer-instance-python-version.txt" ]] ||
        ! . "$adapter_root/bootstrap-uv.sh"; then
        printf '%s\n' '[installer-instance-gc] deferred: managed-binding-unavailable' >&2
        return 0
    fi
    python="$(resolve_uv_bootstrap_python \
        "$adapter_root/installer-instance-python-version.txt" Inspect </dev/null)" || {
        printf '%s\n' '[installer-instance-gc] deferred: managed-python-unavailable' >&2
        return 0
    }
    export COPILOT_EXTENSIONS_DAEMON_PYTHON="$python"
    if ! reconcile_daemon_instances 128 5 "$registry_root" "$runtime_root" false >/dev/null; then
        printf '%s\n' '[installer-instance-gc] deferred: native-reconcile-failed' >&2
    fi
    return 0
)

reconcile_installer_instances() (
    local helper_root="$1" registry_root="${2:-}" runtime_root="${3:-}" helper adapter_root limiter
    if [[ -z "$registry_root" && ( "${COPILOT_EXTENSIONS_TEST_CONTAINED:-}" == 1 ||
        -n "${PYTEST_CURRENT_TEST:-}" ) ]]; then
        return 0
    fi
    if [[ -z "$runtime_root" ]]; then
        printf '%s\n' '[installer-instance-gc] deferred: runtime-scope-unavailable' >&2
        return 0
    fi
    helper="$helper_root/daemon_instances.sh"
    if [[ ! -f "$helper" || -L "$helper" ]]; then
        printf '%s\n' '[installer-instance-gc] deferred: native-helper-unavailable' >&2
        return 0
    fi
    adapter_root="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)" || return 0
    limiter="$(command -v timeout)" || {
        printf '%s\n' '[installer-instance-gc] deferred: native-deadline-unavailable' >&2
        return 0
    }
    # Kill the owned group at the hard deadline: an early TERM exit of its
    # direct worker must not cancel termination of resistant descendants.
    if ! "$limiter" --signal=KILL 5 "$BASH" --noprofile --norc -c '
        . "$1/installer-instance-reconcile.sh" || exit
        _reconcile_installer_instances_managed "$@"
    ' installer-instance-reconcile "$adapter_root" "$helper_root" "$registry_root" "$runtime_root"; then
        printf '%s\n' '[installer-instance-gc] deferred: native-reconcile-failed' >&2
    fi
    return 0
)
