#!/usr/bin/env bash
set -euo pipefail
scripts="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
. "$scripts/installer-bootstrap.sh"
support="$(_containers_installer_support)"
. "$support/bootstrap-uv.sh"
unset UV_CONFIG_FILE
export UV_NO_CONFIG=1
invoke_uv_bootstrap_environment "${1:?UV executable required}" venv \
    --python 3.11 --managed-python "${2:?candidate slot required}"
