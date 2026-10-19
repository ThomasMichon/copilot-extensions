#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for python in python3 python; do
    if command -v "$python" >/dev/null 2>&1 &&
        "$python" -I -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
        cd "${HOME:?Cell installer requires an existing home working directory}"
        if [[ "$#" == 1 && "$1" == bootstrap ]]; then
            exec "$python" -I "$script_dir/cell_invoke.py" --bootstrap
        fi
        exec "$python" -I "$script_dir/cell_install.py" "$@"
    fi
done
printf '%s\n' 'Cell installer requires an existing Python 3.10+ interpreter' >&2
exit 2
