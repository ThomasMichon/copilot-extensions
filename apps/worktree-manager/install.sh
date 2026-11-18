#!/bin/sh
slot=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit
unset UV_PROJECT UV_PROJECT_ENVIRONMENT UV_NO_SYNC UV_NO_PROJECT UV_WORKING_DIR UV_FROZEN PYTHONPATH PYTHONHOME VIRTUAL_ENV
if [ -z "${UV_CACHE_DIR:-}" ]; then
    root=$(CDPATH= cd -- "$slot/../.." && pwd) || exit
    UV_CACHE_DIR="$root/cache/uv"
fi
if command -v cygpath >/dev/null 2>&1; then
    slot=$(cygpath -w "$slot") || exit
    UV_CACHE_DIR=$(cygpath -w "$UV_CACHE_DIR") || exit
fi
export UV_CACHE_DIR
if [ ! -f "$slot/uv.lock" ]; then
    uv lock --managed-python --project "$slot" || exit
fi
exec uv run --locked --no-dev --no-editable --link-mode hardlink --managed-python --project "$slot" python -I -X utf8 -B "$slot/install.py" "$@"
