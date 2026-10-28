#!/usr/bin/env bash
# agent-worktrees:native-seed-reply-v1
# Dot-source only. A Bash subshell probes EOF; reply environment is never applied.

_aw_native_seed_read_reply() {
    local LC_ALL=C
    local field count index name value eof_diagnostics
    local -a header=() environment=() argv=()
    for ((index=0; index<7; index++)); do
        IFS= read -r -d '' field || return 3
        header[index]="$field"
    done
    [[ "${header[0]}" == agent-worktrees.native-seed-reply.v1 ]] || return 3
    for index in 1 2 3; do
        [[ "${header[index]}" =~ ^[0-9a-f]{32}$ ]] || return 3
    done
    [[ -z "${header[4]}" || "${header[4]}" =~ ^[0-9a-f]{32}$ ]] || return 3
    count="${header[6]}"
    # Canonical decimal only: never put provider text into shell arithmetic.
    [[ "$count" == -1 || "$count" =~ ^(0|[1-9][0-9]{0,8})$ ]] || return 3
    for ((index=0; index<count; index++)); do
        IFS= read -r -d '' name || return 3
        IFS= read -r -d '' value || return 3
        [[ -n "$name" && "$name" != *=* ]] || return 3
        environment+=("$name=$value")
    done
    IFS= read -r -d '' count || return 3
    [[ "$count" =~ ^[1-9][0-9]{0,8}$ ]] || return 3
    for ((index=0; index<count; index++)); do
        IFS= read -r -d '' value || return 3
        argv+=("$value")
    done
    [[ -n "${argv[0]}" ]] || return 3
    IFS= read -r -d '' field || return 3
    [[ "$field" == complete ]] || return 3
    # Refuse closed stdin before command substitution can reuse fd 0 for its pipe.
    : 2>/dev/null 3<&0 || return 3
    # Bash also returns 1 for EAGAIN/EBADF. Capture only the probe's diagnostics.
    eof_diagnostics="$(
        extra=
        eof_status=0
        { IFS= read -r -d '' extra || eof_status=$?; } 2>&1
        [[ "$eof_status" == 1 && -z "$extra" ]]
    )" || return 3
    [[ -z "$eof_diagnostics" ]] || return 3
    AW_NATIVE_SEED_ATTEMPT="${header[1]}"
    AW_NATIVE_SEED_RESOLUTION="${header[2]}"
    AW_NATIVE_SEED_OPERATION="${header[3]}"
    AW_NATIVE_SEED_PARENT="${header[4]}"
    AW_NATIVE_SEED_SETUP_ID="${header[5]}"
    AW_NATIVE_SEED_ENV_COUNT="${header[6]}"
    AW_NATIVE_SEED_ENV=(${environment[@]+"${environment[@]}"})
    AW_NATIVE_SEED_ARGV=("${argv[@]}")
}

aw_native_seed_read_reply() {
    local nocasematch=0 tracing=0 status=0
    case $- in *x*) tracing=1; set +x ;; esac
    AW_NATIVE_SEED_ATTEMPT=""
    AW_NATIVE_SEED_RESOLUTION=""
    AW_NATIVE_SEED_OPERATION=""
    AW_NATIVE_SEED_PARENT=""
    AW_NATIVE_SEED_SETUP_ID=""
    AW_NATIVE_SEED_ENV_COUNT=""
    AW_NATIVE_SEED_ENV=()
    AW_NATIVE_SEED_ARGV=()
    if shopt -q nocasematch; then
        nocasematch=1
        shopt -u nocasematch
    fi
    _aw_native_seed_read_reply 2>/dev/null || status=3
    if [[ "$nocasematch" == 1 ]]; then shopt -s nocasematch; fi
    if [[ "$tracing" == 1 ]]; then set -x; fi
    if [[ "$status" != 0 ]]; then
        printf 'Native preparation reply is invalid; no backend started.\n' >&2
        return 3
    fi
}
