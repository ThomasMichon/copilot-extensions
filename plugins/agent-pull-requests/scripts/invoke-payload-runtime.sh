#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mode="$(bash "$here/payload-mode.sh")"
if [[ "$mode" == legacy ]]; then
    export AGENT_RT_ROOT="${HOME:?}/.agent-pull-requests"
    . "$here/resolve-runtime.sh"
    if [[ -z "${AGENT_RT_PY:-}" ]]; then
        if [[ "${AGENT_PULL_REQUESTS_NO_SELFPROVISION+x}" ]]; then
            printf '%s\n' 'Runtime not provisioned (AGENT_PULL_REQUESTS_NO_SELFPROVISION set)' >&2
            exit 126
        fi
        bash "$here/install.sh" provision >&2
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
