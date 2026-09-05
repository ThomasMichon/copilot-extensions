#!/usr/bin/env bash
# init.sh -- base/client-only compatibility shim; never provisions a host service.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/install.sh" install "$@"
