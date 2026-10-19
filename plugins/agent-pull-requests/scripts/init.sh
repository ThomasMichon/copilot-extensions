#!/usr/bin/env bash
# Back-compat bootstrap entrypoint: delegate to the canonical install script.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mode="$(bash "$here/payload-mode.sh")"
if [[ "$mode" == cell ]]; then
    if [[ "$#" != 0 ]]; then
        printf '%s\n' 'Cell bootstrap cannot use legacy installer arguments' >&2
        exit 126
    fi
    exec bash "$here/cell-install.sh" bootstrap
fi
[[ "$mode" == legacy ]] || { printf '%s\n' 'Unknown native installation mode' >&2; exit 126; }
exec "$here/install.sh" install "$@"
