#!/usr/bin/env bash
# Installation driver contract: pull-requests-first-use-v1
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mode="$(bash "$here/payload-mode.sh")"
export PYTHONUTF8=1
unset PYTHONHOME PYTHONPATH

select_python() (
    local expected="${1:-}" provider="$here/_bootstrap_python/bootstrap-uv.sh"
    local capture="$here/native_capture.sh" uv
    exec 5>&- 6>&- 7>&- 8>&- 9>&-
    if [[ ! -f "$capture" ]]; then
        capture="$here/../../../libs/agent-procutil/src/agent_procutil/native_capture.sh"
    fi
    if [[ ! -f "$provider" || ! -f "$capture" ]]; then
        printf '%s\n' '[agent-pull-requests] Python admission support is unavailable; use explicit setup.' >&2
        return 126
    fi
    uv="$(command -v uv)" || {
        printf '%s\n' '[agent-pull-requests] uv is unavailable; prepare the command prerequisites through explicit setup.' >&2
        return 126
    }
    . "$capture"
    if [[ -n "$expected" ]]; then
        if ! capture_native_process 10 "$BASH" -c \
            '. "$1"; resolve_uv_runtime_python "$2" "$3"' \
            pull-requests-runtime-admission "$provider" "$expected" "$uv"; then
            printf '%s\n' '[agent-pull-requests] application runtime admission failed; use explicit setup.' >&2
            return 126
        fi
    elif ! capture_native_process 10 "$BASH" "$here/_bootstrap_python/select.sh" Inspect; then
        printf '%s\n' '[agent-pull-requests] command bootstrap Python is unavailable; use explicit setup.' >&2
        return 126
    fi
    printf '%s\n' "$__native_capture_output"
)

resolve_ready_runtime() {
    local result
    AGENT_RT_PY=""
    AGENT_RT_RESOLVE_TRACE_MODE=ready
    set +e
    # shellcheck source=/dev/null
    . "$here/resolve-runtime.sh"
    result=$?
    set -e
    if [[ "$result" -ne 0 ]]; then
        printf '%s\n' '[agent-pull-requests] runtime resolver failed; use explicit setup.' >&2
        exit 126
    fi
}

if [[ "$mode" == legacy ]]; then
    export AGENT_RT_ROOT="${HOME:?}/.agent-pull-requests"
    if [[ ! -f "$here/resolve-runtime.sh" ]] ||
       ! grep -Fq '# Trace contract: normal/read-only/ready-v1' "$here/resolve-runtime.sh"; then
        printf '%s\n' 'Read-only runtime resolver is unavailable; update or repair the owning payload.' >&2
        exit 126
    fi
    resolve_ready_runtime
    if [[ -z "${AGENT_RT_PY:-}" ]]; then
        if [[ "${AGENT_PULL_REQUESTS_NO_SELFPROVISION+x}" ]]; then
            printf '%s\n' 'Runtime not provisioned (AGENT_PULL_REQUESTS_NO_SELFPROVISION set)' >&2
            exit 126
        fi
        if [[ "${AGENT_PULL_REQUESTS_PINNED_CONTEXT+x}" ]]; then
            printf '%s\n' 'Pinned invocation requires a prepared owning runtime; provision independently.' >&2
            exit 126
        fi
        payload="$(dirname -- "$here")"
        first_use="$payload/libs/update-driver/scripts/first-use.sh"
        if [[ ! -f "$first_use" ]]; then
            printf '%s\n' 'Packaged installation driver is unavailable; update or repair the owning payload.' >&2
            exit 126
        fi
        bash "$first_use" "$HOME/.copilot-extensions/update-driver" \
            "$payload" "$AGENT_RT_ROOT" "$(pwd -P)" --wait || exit "$?"
        resolve_ready_runtime
    fi
    if [[ -z "${AGENT_RT_PY:-}" ]]; then
        printf '%s\n' 'Owning installer returned without a resolvable runtime' >&2
        exit 126
    fi
    AGENT_RT_PY="$(select_python "$AGENT_RT_PY")" || exit 126
    exec "$AGENT_RT_PY" -m agent_pull_requests "$@"
fi
[[ "$mode" == cell ]] || { printf '%s\n' 'Unknown native installation mode' >&2; exit 126; }
python="$(select_python)" || exit 126
exec "$python" -I "$here/cell_invoke.py" -- "$@"
