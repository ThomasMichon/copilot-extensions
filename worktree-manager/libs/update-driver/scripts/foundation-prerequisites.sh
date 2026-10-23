#!/usr/bin/env bash
# Fixed acquisition participant; the producer owns admission and completion.
set -euo pipefail
[[ "$#" == 1 && "$(uname -s)" == Linux ]] || exit 2
bootstrap="$1"
helper="$(CDPATH= cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/installer-engine.sh"
unset UPDATE_DRIVER_WITNESS
_step() { printf '%s\n' "$*" >&2; }
_ok() { printf '%s\n' "$*" >&2; }
_warn() { printf '%s\n' "$*" >&2; }
_fail() { printf '%s\n' "$*" >&2; }
source "$helper"
export UV_PYTHON_INSTALL_DIR="$bootstrap/python" UV_CACHE_DIR="$bootstrap/cache"
export UV_NO_PROGRESS=1 UV_PYTHON=3.11 UV_PYTHON_PREFERENCE=only-managed
uv="$(ensure_uv "$bootstrap")"
[[ -n "$uv" ]] || exit 1
new_signed_venv "$uv" "$bootstrap/venv" 3.11
# Paths are fixed by the owner; this is data, never an acquisition argv recipe.
printf '%s\0' "$uv" >"$bootstrap/uv-path"
sync -f -- "$bootstrap/uv-path"
