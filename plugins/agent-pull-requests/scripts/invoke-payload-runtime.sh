#!/usr/bin/env bash
# Installation driver contract: pull-requests-first-use-v1
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mode="$(bash "$here/payload-mode.sh")"
if [[ "$mode" == legacy ]]; then
    export AGENT_RT_ROOT="${HOME:?}/.agent-pull-requests"
    if [[ ! -f "$here/resolve-runtime.sh" ]] ||
       ! grep -Fq '# Trace contract: normal/read-only/ready-v1' "$here/resolve-runtime.sh"; then
        printf '%s\n' 'Read-only runtime resolver is unavailable; update or repair the owning payload.' >&2
        exit 126
    fi
    AGENT_RT_RESOLVE_TRACE_MODE=ready
    . "$here/resolve-runtime.sh"
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
        . "$here/resolve-runtime.sh"
    fi
    if [[ -z "${AGENT_RT_PY:-}" ]]; then
        printf '%s\n' 'Owning installer returned without a resolvable runtime' >&2
        exit 126
    fi
    exec "$AGENT_RT_PY" -m agent_pull_requests "$@"
fi
[[ "$mode" == cell ]] || { printf '%s\n' 'Unknown native installation mode' >&2; exit 126; }
for python in python3 python; do
    if command -v "$python" >/dev/null 2>&1 &&
        "$python" -I -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
        exec "$python" -I "$here/cell_invoke.py" -- "$@"
    fi
done
printf '%s\n' 'Governed invocation requires an existing Python 3.10+ interpreter' >&2
exit 126
