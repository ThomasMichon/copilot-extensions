#!/usr/bin/env bash
# Native admission wrapper for the unchanged shared bootstrap-check reconciler.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! mode="$(bash "$here/payload-mode.sh")"; then
    printf '{}'
    exit 126
fi
if [[ "$mode" == legacy ]]; then exec bash "$here/bootstrap-check.sh"; fi
[[ "$mode" == cell ]] || { printf '%s\n' 'Unknown native installation mode' >&2; exit 126; }
# Provision and activation are on demand through the owning payload, not session start.
printf '{}'
