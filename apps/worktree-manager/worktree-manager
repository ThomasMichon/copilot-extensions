#!/bin/sh
installer_uv=uv
slot=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit
unset UV_PROJECT UV_PROJECT_ENVIRONMENT UV_NO_SYNC UV_NO_PROJECT UV_WORKING_DIR UV_FROZEN PYTHONPATH PYTHONHOME VIRTUAL_ENV
if [ -z "${UV_CACHE_DIR:-}" ]; then
    root=$(CDPATH= cd -- "$slot/../.." && pwd) || exit
    UV_CACHE_DIR="$root/cache/uv"
fi
if [ ! -f "$slot/uv.lock" ] || [ ! -f "$slot/.venv/pyvenv.cfg" ] || {
    [ ! -f "$slot/.venv/bin/python" ] && [ ! -f "$slot/.venv/Scripts/python.exe" ];
}; then
    echo 'Application is not staged; run bin/setup --stage' >&2
    exit 1
fi
if command -v cygpath >/dev/null 2>&1; then
    slot=$(cygpath -w "$slot") || exit
    UV_CACHE_DIR=$(cygpath -w "$UV_CACHE_DIR") || exit
fi
export UV_CACHE_DIR
exec "$installer_uv" run --locked --no-dev --no-editable --link-mode hardlink --managed-python --no-sync --no-python-downloads --project "$slot" python -I -X utf8 -B "$slot/worktree-manager.py" "$@"
