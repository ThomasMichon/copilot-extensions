#!/usr/bin/env bash
# Canonical source: libs/hook-uv-python/hook-uv-python.sh. The copy in each
# adopting plugin's scripts/ directory is vendored byte-identically by
# tools/sync-hook-uv-python.py; edit only the canonical file.
#
# Source this file, then call `hook_uv_python <python arguments...>`. It runs
# the uv-managed CPython named below through `uv run`, and uv installs that
# Python when it is missing (UV_PYTHON_DOWNLOADS=never still disables the
# download). A system Python is never used as a fallback. When uv is not
# available, it prints a clear message and returns 127; the calling hook keeps
# its own no-op result. The version matches the repository's .python-version.
HOOK_UV_PYTHON_VERSION=3.11

hook_uv_python() (
    uv_path="$(command -v uv 2>/dev/null || true)"
    if [[ -z "$uv_path" ]]; then
        for candidate in "${HOME:-}/.local/bin/uv" "${HOME:-}/.cargo/bin/uv"; do
            if [[ -n "${HOME:-}" && -x "$candidate" ]]; then
                uv_path="$candidate"
                break
            fi
        done
    fi
    if [[ -z "$uv_path" ]]; then
        printf '%s\n' "uv is required to run this hook's Python, but 'uv' was not found on PATH." \
            'Install uv (https://docs.astral.sh/uv/getting-started/installation/). A system Python is never used.' >&2
        return 127
    fi
    # Inherited selectors must not redirect the interpreter or its imports.
    unset UV_PYTHON UV_PYTHON_PREFERENCE UV_MANAGED_PYTHON UV_NO_MANAGED_PYTHON \
        VIRTUAL_ENV CONDA_PREFIX PYTHONHOME PYTHONPATH UV_WORKING_DIR UV_WORKING_DIRECTORY \
        UV_ISOLATED UV_ENV_FILE UV_PROJECT UV_FROZEN UV_LOCKED UV_NO_SYNC
    exec "$uv_path" run --quiet --no-project --managed-python \
        --python "$HOOK_UV_PYTHON_VERSION" python "$@"
)