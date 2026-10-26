#!/usr/bin/env bash
set -euo pipefail
root="$1" plugin="$2" library="$3" mode="$4"
source "$library/single-instance-lease/src/single_instance_lease/operation.sh"
source "$library/update-driver/src/update_driver/foundation.sh"

if [[ "$mode" == replace-once || "$mode" == replace-always ]]; then
    stat() {
        command stat "$@" || return "$?"
        if [[ "${@: -1}" == "$root/foundation-operation.json" &&
            ( "$mode" == replace-always || -f "$root/replacement.json" ) ]]; then
            if [[ "$mode" == replace-always ]]; then
                cp -- "$root/foundation-operation.json" "$root/replacement.json" || return 1
            fi
            mv -T -- "$root/replacement.json" "$root/foundation-operation.json" || return 1
            printf 'replaced\n' >>"$root/replacements"
        fi
    }
    read_native_foundation_progress "$root"
    _native_foundation_encode
    exit
fi
if [[ "$mode" == read ]]; then
    read_native_foundation_progress "$root"
    if [[ "$__foundation_present" == 1 ]]; then _native_foundation_encode; fi
    exit
fi
enter_native_operation_lock "$root/execution.lock"
case "$mode" in
    roundtrip)
        start_native_foundation_progress "$root" "$plugin"
        operation="$__foundation_document"
        printf '%s\n' "$operation" >"$root/admitted.json"
        complete_native_foundation_progress "$root" "$operation" "$plugin" 0
        cp -- "$root/foundation-operation.json" "$root/completed.json"
        start_native_foundation_progress "$root" "$plugin"
        complete_native_foundation_progress "$root" "$__foundation_document" "$plugin" -7 builder_failed
        cp -- "$root/foundation-operation.json" "$root/failed.json"
        start_native_foundation_progress "$root" "$plugin"
        complete_native_foundation_progress "$root" "$__foundation_document" "$plugin" null cleanup_unproved
        if start_native_foundation_progress "$root" "$plugin"; then exit 10; fi
        ;;
    freeze)
        start_native_foundation_progress "$root" "$plugin"
        freeze_native_foundation_source "$root" "$plugin" "$root/frozen" "${__foundation_value[source_hash]}"
        ;;
    refuse-freeze)
        if freeze_native_foundation_source "$root" "$plugin" "$5" "$6"; then exit 18; fi
        ;;
    stale-source)
        start_native_foundation_progress "$root" "$plugin"
        operation="$__foundation_document"
        printf changed >"$plugin/source.py"
        if complete_native_foundation_progress "$root" "$operation" "$plugin" 0; then exit 11; fi
        if freeze_native_foundation_source "$root" "$plugin" "$root/frozen" "${__foundation_value[source_hash]}"; then exit 12; fi
        ;;
    foreign)
        leave_native_operation_lock "$root/execution.lock"
        if start_native_foundation_progress "$root" "$plugin"; then exit 13; fi
        if freeze_native_foundation_source "$root" "$plugin" "$root/frozen" "$5"; then exit 14; fi
        if recover_native_foundation_after_restart "$root"; then exit 15; fi
        exit
        ;;
    refuse)
        if start_native_foundation_progress "$root" "$plugin"; then exit 16; fi
        ;;
    reconcile) reconcile_native_foundation_progress "$root" ;;
    recover) recover_native_foundation_after_restart "$root" ;;
    near-limit-complete)
        read_native_foundation_progress "$root"
        complete_native_foundation_progress "$root" "$__foundation_document" "$plugin" 7 builder_failed
        ;;
    protocol)
        for path in "$root/corpus/"*.json; do
            cp -- "$path" "$root/foundation-operation.json"
            name="${path##*/}"
            if read_native_foundation_progress "$root"; then
                printf '%s yes\n' "$name"
                _native_foundation_encode >"${path%.json}.native"
            else
                printf '%s no\n' "$name"
            fi
        done
        ;;
    *) exit 17 ;;
esac
leave_native_operation_lock "$root/execution.lock"
