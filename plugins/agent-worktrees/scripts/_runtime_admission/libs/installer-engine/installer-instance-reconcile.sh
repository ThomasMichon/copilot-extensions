#!/usr/bin/env bash

reconcile_installer_instances() (
    local helper_root="$1" registry_root="${2:-}" helper
    if [[ -z "$registry_root" && ( "${COPILOT_EXTENSIONS_TEST_CONTAINED:-}" == 1 ||
        -n "${PYTEST_CURRENT_TEST:-}" ) ]]; then
        return 0
    fi
    helper="$helper_root/daemon_instances.sh"
    if [[ ! -f "$helper" || -L "$helper" ]]; then
        printf '%s\n' '[installer-instance-gc] deferred: native-helper-unavailable' >&2
        return 0
    fi
    if ! . "$helper"; then
        printf '%s\n' '[installer-instance-gc] deferred: native-helper-load-failed' >&2
        return 0
    fi
    if ! reconcile_daemon_instances 128 5 "$registry_root" >/dev/null; then
        printf '%s\n' '[installer-instance-gc] deferred: native-reconcile-failed' >&2
    fi
    return 0
)
