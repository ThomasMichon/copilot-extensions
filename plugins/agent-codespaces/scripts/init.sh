#!/usr/bin/env bash
# Bootstrap the agent-codespaces runtime (delegates to install.sh).
# Backwards-compatible shim -- the canonical, self-contained install flow lives
# in install.sh (the uv pip install model). See docs/install-contract.md.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/install.sh" install
