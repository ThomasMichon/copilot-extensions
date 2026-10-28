#!/usr/bin/env bash
# agent-worktrees:native-seed-setup-v1
# Dot-source only from the copied normalized setup.

. "${BASH_SOURCE[0]%/*}/native-seed-boundary.sh"

_aw_native_setup_require_contract() {
    _aw_native_seed_runtime "$LAUNCH_SEED_NATIVE_ROOT" || return 3
    local selected="${AW_NATIVE_SEED_RUNTIME[0]}"
    [[ "${AW_NATIVE_SETUP_CAPABLE_RUNTIME:-}" != "$selected" ]] || return 0
    aw_native_seed_supports_posix_setup "$LAUNCH_SEED_NATIVE_ROOT" || return 3
    _aw_native_seed_runtime "$LAUNCH_SEED_NATIVE_ROOT" || return 3
    [[ "${AW_NATIVE_SEED_RUNTIME[0]}" == "$selected" ]] || return 3
    AW_NATIVE_SETUP_CAPABLE_RUNTIME="$selected"
}

_aw_native_setup_finish() {
    local code=$?
    trap - EXIT
    aw_native_seed_phase "$LAUNCH_SEED_NATIVE_ROOT" --phase returned --exit-code "$code" \
        "${AW_NATIVE_SETUP_CALLBACK[@]}" >/dev/null || \
        printf 'Native setup return is unconfirmed; inspect the retained launch.\n' >&2
    exit "$code"
}

aw_native_setup_begin() {
    [[ "$LAUNCH_SEED_PREPARATION" == seed || "$LAUNCH_SEED_PREPARATION" == admission ]] || return 3
    [[ -n "$LAUNCH_SEED_RECORD" && "$LAUNCH_SEED_NATIVE_ROOT" == /* ]] || return 3
    if [[ -n "$LAUNCH_ATTEMPT_ID$LAUNCH_RESOLUTION_ID$LAUNCH_PARENT_ID" ]]; then
        [[ -n "$LAUNCH_ATTEMPT_ID" && -n "$LAUNCH_RESOLUTION_ID" ]] || return 3
    fi
    # Discover on the selected runtime before provider-derived birth or exec syntax.
    _aw_native_setup_require_contract || {
        printf 'Current runtime cannot own native POSIX setup; no backend started.\n' >&2
        return 3
    }
    local -a arguments=(--phase setup --output-format nul --record "$LAUNCH_SEED_RECORD" --pid "$$")
    [[ "$LAUNCH_SEED_PREPARATION" != admission ]] || arguments+=(--admission-only)
    [[ -z "$LAUNCH_SEED_ID" ]] || arguments+=(--seed-id "$LAUNCH_SEED_ID")
    [[ -z "$LAUNCH_SEED_PRESERVE_ID" ]] || arguments+=(--preserve-seed-id "$LAUNCH_SEED_PRESERVE_ID")
    [[ -z "$LAUNCH_NATIVE_RESUME_SESSION" ]] || arguments+=(--native-resume-session "$LAUNCH_NATIVE_RESUME_SESSION")
    [[ -z "$LAUNCH_ATTEMPT_ID" ]] || arguments+=(--launch-attempt-id "$LAUNCH_ATTEMPT_ID")
    [[ -z "$LAUNCH_RESOLUTION_ID" ]] || arguments+=(--launch-resolution-id "$LAUNCH_RESOLUTION_ID")
    [[ -z "$LAUNCH_PARENT_ID" ]] || arguments+=(--launch-parent-id "$LAUNCH_PARENT_ID")
    aw_native_seed_prepare "$LAUNCH_SEED_NATIVE_ROOT" "${arguments[@]}" -- "$@" || return 3
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

aw_native_setup_backend() {
    local code=0 completion="" status="" index
    # Bash waits in the foreground with the original terminal. Only its direct
    # child exec-replaces Python; the finite provider timeout must never own it.
    trap : INT
    if [[ -z "$LAUNCH_SEED_ID" ]]; then
        "$@" || code=$?
        return "$code"
    fi
    _aw_native_setup_require_contract || return 3
    completion="$(mktemp "${BASH_SOURCE[0]%/*}/.native-seed-exec.XXXXXXXX")" || return 3
    local -a preparation=(
        --phase prepare --output-format nul --record "$LAUNCH_SEED_RECORD" --seed-id "$LAUNCH_SEED_ID"
        --launch-attempt-id "$LAUNCH_ATTEMPT_ID" --launch-resolution-id "$LAUNCH_RESOLUTION_ID"
        --launch-parent-id "$LAUNCH_PARENT_ID"
    )
    if ! aw_native_seed_prepare "$LAUNCH_SEED_NATIVE_ROOT" "${preparation[@]}" -- "$@"; then
        rm -f -- "$completion"
        return 3
    fi
    local -a callback=(
        --record "$LAUNCH_SEED_RECORD"
        --launch-attempt-id "$AW_NATIVE_SEED_ATTEMPT" --launch-resolution-id "$AW_NATIVE_SEED_RESOLUTION"
        --operation-id "$AW_NATIVE_SEED_OPERATION" --launch-parent-id "$AW_NATIVE_SEED_PARENT"
    )
    if _aw_native_seed_runtime "$LAUNCH_SEED_NATIVE_ROOT"; then
        local -a command=(
            "${AW_NATIVE_SEED_RUNTIME[@]}" -m agent_worktrees.launch_seed_native
            --contract-version 1 --root "$LAUNCH_SEED_NATIVE_ROOT" --phase exec
            --completion-file "$completion" "${callback[@]}" -- "${AW_NATIVE_SEED_ARGV[@]}"
        )
        if [[ "$AW_NATIVE_SEED_ENV_COUNT" == -1 ]]; then
            "${command[@]}" || code=$?
        else
            env -i ${AW_NATIVE_SEED_ENV[@]+"${AW_NATIVE_SEED_ENV[@]}"} "${command[@]}" || code=$?
        fi
        for ((index=0; index<220; index++)); do
            [[ ! -s "$completion" ]] || break
            sleep 0.05
        done
        IFS= read -r status < "$completion" || status=""
    else
        code=3
    fi
    if [[ "$status" == 0 ]]; then
        aw_native_seed_phase "$LAUNCH_SEED_NATIVE_ROOT" --phase returned --exit-code "$code" \
            "${callback[@]}" >/dev/null || \
            printf 'Native backend return is unconfirmed; inspect the retained handoff.\n' >&2
    elif [[ "$status" != 3 ]]; then
        aw_native_seed_phase "$LAUNCH_SEED_NATIVE_ROOT" --phase interrupted \
            "${callback[@]}" >/dev/null || true
        printf 'Native backend exec is unconfirmed; inspect the retained handoff.\n' >&2
    fi
    rm -f -- "$completion"
    return "$code"
}
