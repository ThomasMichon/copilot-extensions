#!/usr/bin/env bash
# init.sh -- thin compatibility shim.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/install.sh" install "$@"
