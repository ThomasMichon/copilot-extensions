#!/usr/bin/env bash
# agent-worktrees:native-seed-setup-v1
# Dot-source only from the copied normalized setup.

. "${BASH_SOURCE[0]%/*}/native-seed-boundary.sh"

_aw_native_setup_require_contract() {
    aw_native_seed_supports_posix_setup "$LAUNCH_SEED_NATIVE_ROOT"
}

_aw_native_setup_prepare() {
    local -a AW_NATIVE_SEED_PHASE_RUNTIME=("${AW_NATIVE_SEED_RUNTIME[@]}")
    aw_native_seed_prepare "$@"
}

_aw_native_setup_finish() {
    local code=$?
    trap - EXIT
    aw_native_seed_return "$LAUNCH_SEED_NATIVE_ROOT" \
        'Native setup return is unconfirmed; inspect the retained launch.' \
        --phase returned --exit-code "$code" "${AW_NATIVE_SETUP_CALLBACK[@]}"
    exit "$code"
}

aw_native_setup_begin() {
    [[ "$LAUNCH_SEED_PREPARATION" == seed ]] || return 3
    [[ -n "$LAUNCH_SEED_RECORD" && "$LAUNCH_SEED_NATIVE_ROOT" == /* ]] || return 3
    [[ -z "$(builtin trap -p EXIT)" ]] || return 3
    if [[ -n "$LAUNCH_ATTEMPT_ID$LAUNCH_RESOLUTION_ID$LAUNCH_PARENT_ID" ]]; then
        [[ -n "$LAUNCH_ATTEMPT_ID" && -n "$LAUNCH_RESOLUTION_ID" ]] || return 3
    fi
    # Discover on the selected runtime before provider-derived birth or exec syntax.
    _aw_native_setup_require_contract || {
        printf 'Current runtime cannot own native POSIX setup; no backend started.\n' >&2
        return 3
    }
    local -a arguments=(
        --phase setup --output-format nul --environment-reply execution-hint
        --record "$LAUNCH_SEED_RECORD" --pid "$$"
    )
    [[ -z "$LAUNCH_SEED_ID" ]] || arguments+=(--seed-id "$LAUNCH_SEED_ID")
    [[ -z "$LAUNCH_SEED_PRESERVE_ID" ]] || arguments+=(--preserve-seed-id "$LAUNCH_SEED_PRESERVE_ID")
    [[ -z "$LAUNCH_NATIVE_RESUME_SESSION" ]] || arguments+=(--native-resume-session "$LAUNCH_NATIVE_RESUME_SESSION")
    [[ -z "$LAUNCH_ATTEMPT_ID" ]] || arguments+=(--launch-attempt-id "$LAUNCH_ATTEMPT_ID")
    [[ -z "$LAUNCH_RESOLUTION_ID" ]] || arguments+=(--launch-resolution-id "$LAUNCH_RESOLUTION_ID")
    [[ -z "$LAUNCH_PARENT_ID" ]] || arguments+=(--launch-parent-id "$LAUNCH_PARENT_ID")
    _aw_native_setup_prepare "$LAUNCH_SEED_NATIVE_ROOT" "${arguments[@]}" -- "$@" || return 3
    unset AGENT_WORKTREES_LAUNCH_EXECUTION
    AW_NATIVE_SETUP_CALLBACK=(
        --record "$LAUNCH_SEED_RECORD"
        --launch-attempt-id "$AW_NATIVE_SEED_ATTEMPT" --launch-resolution-id "$AW_NATIVE_SEED_RESOLUTION"
        --operation-id "$AW_NATIVE_SEED_OPERATION"
    )
    [[ -z "$AW_NATIVE_SEED_PARENT" ]] || AW_NATIVE_SETUP_CALLBACK+=(--launch-parent-id "$AW_NATIVE_SEED_PARENT")
    LAUNCH_SEED_ID="$AW_NATIVE_SEED_SETUP_ID"
    LAUNCH_ATTEMPT_ID="$AW_NATIVE_SEED_ATTEMPT"
    LAUNCH_RESOLUTION_ID="$AW_NATIVE_SEED_RESOLUTION"
    LAUNCH_PARENT_ID="$AW_NATIVE_SEED_OPERATION"
    trap _aw_native_setup_finish EXIT
}

_aw_native_setup_backend_environment() {
    local name declaration attributes
    builtin printf '%s\0' agent-worktrees.backend-environment.v1
    for name in PYTHONPATH PYTHONHOME VIRTUAL_ENV __PYVENV_LAUNCHER__; do
        declaration="$(declare -p "$name" 2>/dev/null)" || declaration=""
        attributes="${declaration#declare -}"
        attributes="${attributes%% *}"
        if [[ "$attributes" == *x* && "$attributes" != *a* && "$attributes" != *A* && "$declaration" == *=* ]]; then
            builtin printf '%s\0' x "${!name}"
        else
            builtin printf '\0\0'
        fi
    done
    builtin printf '%s\0' complete
}

aw_native_setup_backend() {
    local code=0 completion="" status="" index entry hint=""
    # Bash waits in the foreground with the original terminal. Only its direct
    # child exec-replaces Python; the finite provider timeout must never own it.
    trap : INT
    if [[ -z "$LAUNCH_SEED_ID" ]]; then
        "$@" || code=$?
        return "$code"
    fi
    _aw_native_setup_require_contract || return 3
    completion="$(_aw_native_seed_control mktemp "${BASH_SOURCE[0]%/*}/.native-seed-exec.XXXXXXXX")" || return 3
    local -a preparation=(
        --phase prepare --output-format nul --environment-reply execution-hint
        --record "$LAUNCH_SEED_RECORD" --seed-id "$LAUNCH_SEED_ID"
        --launch-attempt-id "$LAUNCH_ATTEMPT_ID" --launch-resolution-id "$LAUNCH_RESOLUTION_ID"
        --launch-parent-id "$LAUNCH_PARENT_ID"
    )
    if ! _aw_native_setup_prepare "$LAUNCH_SEED_NATIVE_ROOT" "${preparation[@]}" -- "$@"; then
        _aw_native_seed_control rm -f -- "$completion"
        return 3
    fi
    local -a callback=(
        --record "$LAUNCH_SEED_RECORD"
        --launch-attempt-id "$AW_NATIVE_SEED_ATTEMPT" --launch-resolution-id "$AW_NATIVE_SEED_RESOLUTION"
        --operation-id "$AW_NATIVE_SEED_OPERATION" --launch-parent-id "$AW_NATIVE_SEED_PARENT"
    )
    for entry in ${AW_NATIVE_SEED_ENV[@]+"${AW_NATIVE_SEED_ENV[@]}"}; do
        case "$entry" in
            AGENT_WORKTREES_LAUNCH_EXECUTION=*) hint="${entry#*=}" ;;
        esac
    done
    if [[ -n "$hint" ]]; then
        local AGENT_WORKTREES_LAUNCH_EXECUTION="$hint"
        export AGENT_WORKTREES_LAUNCH_EXECUTION
        local -a command=(
            "${AW_NATIVE_SEED_RUNTIME[@]}" -m agent_worktrees.launch_seed_native
            --contract-version 1 --root "$LAUNCH_SEED_NATIVE_ROOT" --phase exec
            --completion-file "$completion" --backend-environment-fd 3
            "${callback[@]}" -- "${AW_NATIVE_SEED_ARGV[@]}"
        )
        _aw_native_seed_control env -u PYTHONPATH -u PYTHONHOME -u VIRTUAL_ENV -u __PYVENV_LAUNCHER__ \
            "${command[@]}" 3< <(_aw_native_setup_backend_environment) || code=$?
        for ((index=0; index<220; index++)); do
            [[ ! -s "$completion" ]] || break
            _aw_native_seed_control sleep 0.05
        done
        IFS= read -r status < "$completion" || status=""
    else
        code=3
    fi
    if [[ "$status" == 0 ]]; then
        aw_native_seed_return "$LAUNCH_SEED_NATIVE_ROOT" \
            'Native backend return is unconfirmed; inspect the retained handoff.' \
            --phase returned --exit-code "$code" "${callback[@]}"
    else
        if [[ "$status" != 3 ]]; then
            aw_native_seed_phase "$LAUNCH_SEED_NATIVE_ROOT" --phase interrupted \
                "${callback[@]}" >/dev/null || true
        fi
        printf 'Native backend exec is unconfirmed; inspect the retained handoff.\n' >&2
    fi
    _aw_native_seed_control rm -f -- "$completion"
    return "$code"
}
