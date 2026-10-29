#!/usr/bin/env bash
# Linux native custody uses the same whole-file flock as OperationLock.

assert_native_operation_lock() {
    local path="${1:-}" opened expected evidence
    if [[ "$(uname -s)" != Linux || "$path" != /* || ! -f "$path" || -L "$path" ]]; then
        printf '%s\n' 'Native operation custody requires an absolute plain Linux lock.' >&2
        return 1
    fi
    expected="$(readlink -e -- "$path")" || return 1
    if [[ "$expected" != "$path" ]]; then
        printf '%s\n' 'Native operation lock path must be normalized and unlinked.' >&2
        return 1
    fi
    opened="$(stat -Lc '%d:%i' /proc/self/fd/9)" || return 1
    expected="$(stat -Lc '%d:%i' -- "$path")" || return 1
    if [[ "$opened" != "$expected" ]]; then
        printf '%s\n' 'Native operation lock file was replaced.' >&2
        return 1
    fi
    evidence="$(LC_ALL=C grep '^lock:' /proc/self/fdinfo/9)" || {
        printf '%s\n' 'Native descriptor has no kernel lock evidence.' >&2
        return 1
    }
    if [[ "$evidence" == *$'\n'* ]] || ! LC_ALL=C grep -Eq \
        '^lock:[[:space:]]+[0-9]+:[[:space:]]+FLOCK[[:space:]]+ADVISORY[[:space:]]+WRITE[[:space:]]+-?[0-9]+[[:space:]]+[^[:space:]]+[[:space:]]+0[[:space:]]+EOF[[:space:]]*$' <<<"$evidence"; then
        printf '%s\n' 'Native descriptor does not hold exclusive whole-file flock custody.' >&2
        return 1
    fi
}

enter_native_operation_lock() {
    local path="${1:-}" parent code
    if [[ "$(uname -s)" != Linux || "$path" != /* || -L "$path" || -L /proc/self/fd/9 ]]; then
        printf '%s\n' 'Native operation admission requires Linux, a plain absolute lock and free FD 9.' >&2
        return 1
    fi
    parent="${path%/*}"
    if [[ -z "$parent" ]]; then parent=/; fi
    if [[ ! -d "$parent" || "$(readlink -e -- "$parent")" != "$parent" ]]; then
        printf '%s\n' 'Native operation lock requires an existing unlinked parent.' >&2
        return 1
    fi
    if [[ ! -e "$path" ]]; then
        if ! (umask 077; set -o noclobber; : > "$path"); then
            if [[ ! -f "$path" || -L "$path" ]]; then
                printf '%s\n' 'Native operation lock creation failed.' >&2
                return 1
            fi
        fi
    fi
    if [[ ! -f "$path" || -L "$path" ]]; then
        printf '%s\n' 'Native operation lock must remain an ordinary file.' >&2
        return 1
    fi
    exec 9<>"$path" || return 1
    if flock --exclusive --nonblock --conflict-exit-code 75 9; then
        code=0
    else
        code=$?
    fi
    if [[ "$code" != 0 ]]; then
        exec 9>&-
        return "$code"
    fi
    if ! assert_native_operation_lock "$path"; then
        exec 9>&-
        return 1
    fi
    __single_instance_native_identity="$(stat -Lc '%d:%i' /proc/self/fd/9)" || {
        exec 9>&-
        return 1
    }
    __single_instance_native_evidence="$(LC_ALL=C grep '^lock:' /proc/self/fdinfo/9)" || {
        exec 9>&-
        return 1
    }
}

leave_native_operation_lock() {
    local identity evidence code
    identity="$(stat -Lc '%d:%i' /proc/self/fd/9)" || return 1
    evidence="$(LC_ALL=C grep '^lock:' /proc/self/fdinfo/9)" || {
        printf '%s\n' 'Native cleanup refuses a descriptor without its acquired lock evidence.' >&2
        return 1
    }
    if [[ -z "${__single_instance_native_identity:-}" || -z "${__single_instance_native_evidence:-}" ||
        "$identity" != "$__single_instance_native_identity" || "$evidence" != "$__single_instance_native_evidence" ]]; then
        printf '%s\n' 'Native cleanup refuses an unrelated descriptor reference.' >&2
        return 1
    fi
    if assert_native_operation_lock "${1:-}"; then code=0; else code=$?; fi
    # Closing this reference must not unlock an outstanding inherited reference.
    exec 9>&-
    unset __single_instance_native_identity __single_instance_native_evidence
    return "$code"
}
