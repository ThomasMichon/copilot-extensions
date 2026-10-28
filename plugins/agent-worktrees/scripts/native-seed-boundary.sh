#!/usr/bin/env bash
# agent-worktrees:native-seed-boundary-v1
# Dot-source only. Finite providers never own a backend's lifetime.

# shellcheck source=native-seed-reply.sh
. "${BASH_SOURCE[0]%/*}/native-seed-reply.sh"

_aw_native_seed_run_phase() (
    local helper="" result=0 index deadline="$1"
    shift
    [[ "$deadline" =~ ^[1-9][0-9]{0,8}$ ]] || return 3
    trap 'if [[ -n "$helper" ]]; then
              kill -KILL -- "-$helper" 2>/dev/null || true
              wait "$helper" 2>/dev/null || true
          fi' EXIT
    trap 'exit 3' HUP INT TERM
    # A separate job group contains only the finite provider and its children.
    set -m || return 3
    "$@" </dev/null 2>/dev/null &
    helper=$!
    set +m
    for ((index=0; index<200; index++)); do
        (( SECONDS < deadline )) || break
        kill -0 "$helper" 2>/dev/null || break
        sleep 0.05
    done
    if kill -0 "$helper" 2>/dev/null; then
        return 3
    fi
    wait "$helper" || result=$?
    [[ "$result" == 0 ]] || return 3
    # A provider that left descendants holding the pipe is not complete.
    ! kill -0 -- "-$helper" 2>/dev/null || return 3
) 2>/dev/null

aw_native_seed_phase() (
    case $- in *x*) set +x ;; esac
    set +a
    local LC_ALL=C
    local root="${1:-}" version="" slot python bootstrap
    [[ $# -ge 2 && "$root" == /* && -f "$root/current-version" ]] || return 3
    shift
    shopt -u nocasematch
    IFS= read -r version < "$root/current-version" || [[ -n "$version" ]] || return 3
    [[ "$version" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]{0,255}$ ]] || return 3
    slot="$root/versions/$version"
    python="$slot/bin/python"
    bootstrap="$slot/.runtime-bootstrap/runtime_launcher.py"
    [[ -x "$python" && -f "$bootstrap" && -f "$slot/.install-complete.json" ]] || return 3
    _aw_native_seed_run_phase "${_aw_native_seed_deadline:-$((SECONDS + 10))}" env \
        -u PYTHONPATH -u PYTHONHOME -u VIRTUAL_ENV -u __PYVENV_LAUNCHER__ \
        "$python" -I -S -B -X utf8 "$bootstrap" \
        -m agent_worktrees.launch_seed_native --contract-version 1 --root "$root" "$@"
)

_aw_native_seed_prepare_phase() {
    local _aw_native_seed_completion="$1"
    local _aw_native_seed_control="$2" _aw_native_seed_deadline="$3" phase_status=0
    export -n _aw_native_seed_completion _aw_native_seed_control _aw_native_seed_deadline
    shift 3
    aw_native_seed_phase "$@" || phase_status=3
    # This file contains only status, never reply/argument/environment bytes.
    printf '%s\n' "$phase_status" 2>/dev/null >| "$_aw_native_seed_control" || return 3
    [[ "$phase_status" == 0 ]] || return 3
    printf '%s\0' "$_aw_native_seed_completion"
}

_aw_native_seed_collect_reply() {
    local status=0 phase_status="" index
    aw_native_seed_read_reply "$1" "$3" 2>/dev/null || status=3
    # Rejection cannot depend on EOF from an out-of-group stdout writer.
    # Keep the pipe open while the provider owner settles, with a 1s grace.
    for ((index=0; index<220; index++)); do
        IFS= builtin read -r phase_status < "$2" || phase_status=""
        [[ "$phase_status" != 0 && "$phase_status" != 3 ]] || break
        (( SECONDS < $3 + 1 )) || break
        sleep 0.05
    done
    [[ "$phase_status" == 0 ]] || status=3
    [[ "$status" == 0 ]] || _aw_native_seed_clear_reply
    return "$status"
}

aw_native_seed_prepare() {
    local tracing=0 status=0
    case $- in *x*) tracing=1; set +x ;; esac
    _aw_native_seed_clear_reply
    # Bash 3.2 cannot wait for process substitutions. Only the finite phase
    # owner emits this per-call trailer, after its real provider wait/cleanup.
    local _aw_native_seed_completion="agent-worktrees.native-phase-complete.v1:$$:$RANDOM:$RANDOM:$RANDOM:$RANDOM"
    local _aw_native_seed_deadline=$((SECONDS + 10)) _aw_native_seed_control=""
    export -n _aw_native_seed_completion _aw_native_seed_deadline _aw_native_seed_control
    _aw_native_seed_control="$(mktemp "${BASH_SOURCE[0]%/*}/.native-seed-phase.XXXXXXXX" 2>/dev/null)" || status=3
    export -n _aw_native_seed_control
    if [[ "$status" == 0 ]]; then
        _aw_native_seed_collect_reply "$_aw_native_seed_completion" "$_aw_native_seed_control" "$_aw_native_seed_deadline" \
            < <(_aw_native_seed_prepare_phase "$_aw_native_seed_completion" "$_aw_native_seed_control" "$_aw_native_seed_deadline" "$@") \
            2>/dev/null || status=3
        rm -f -- "$_aw_native_seed_control" 2>/dev/null || status=3
    fi
    if [[ "$status" != 0 ]]; then
        _aw_native_seed_clear_reply
        printf 'Native preparation is unconfirmed; no backend started.\n' >&2
    fi
    if [[ "$tracing" == 1 ]]; then set -x; fi
    return "$status"
}
