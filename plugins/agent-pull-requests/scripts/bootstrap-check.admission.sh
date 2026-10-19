#!/usr/bin/env bash
# Native admission wrapper for the unchanged shared bootstrap-check reconciler.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
emit_session_start_json() {
    printf '{}'
}
trap 'emit_session_start_json' EXIT
if ! mode="$(bash "$here/payload-mode.sh")"; then
    exit 126
fi
if [[ "$mode" == legacy ]]; then
    bash "$here/bootstrap-check.sh" >/dev/null
    exit $?
fi
[[ "$mode" == cell ]] || { printf '%s\n' 'Unknown native installation mode' >&2; exit 126; }
# Provision and activation are on demand through the owning payload, not session start.
