#!/usr/bin/env bash

reconcile_installer_instances() (
    local helper_root="$1" registry_root="${2:-}" runtime_root="${3:-}" helper
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
    if ! unset -f daemon_instance_reconcile_contract reconcile_daemon_instances 2>/dev/null; then
        printf '%s\n' '[installer-instance-gc] deferred: native-contract-unavailable' >&2
        return 0
    fi
    if ! . "$helper"; then
        printf '%s\n' '[installer-instance-gc] deferred: native-helper-load-failed' >&2
        return 0
    fi
    if ! declare -F daemon_instance_reconcile_contract >/dev/null ||
        ! declare -F reconcile_daemon_instances >/dev/null ||
        [[ "$(daemon_instance_reconcile_contract)" != 'copilot-daemon-reconcile-v1' ]]; then
        printf '%s\n' '[installer-instance-gc] deferred: native-contract-unavailable' >&2
        return 0
    fi
    if ! reconcile_daemon_instances 128 5 "$registry_root" "$runtime_root" false >/dev/null; then
        printf '%s\n' '[installer-instance-gc] deferred: native-reconcile-failed' >&2
    fi
    return 0
)
