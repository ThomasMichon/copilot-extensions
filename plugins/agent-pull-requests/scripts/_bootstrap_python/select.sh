#!/usr/bin/env bash
set -euo pipefail
script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
. "$script_dir/bootstrap-uv.sh"
resolve_uv_bootstrap_python "$script_dir/../bootstrap-python-version.txt" "${1:-Inspect}"
