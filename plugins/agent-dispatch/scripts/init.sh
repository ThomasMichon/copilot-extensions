#!/usr/bin/env bash
# init.sh -- thin compatibility shim.
#
# The canonical installer is scripts/install.sh (a full lifecycle manager:
# install|update|status|start|stop|uninstall, matching agent-bridge). This
# bootstrap alias forwards to `install.sh install` so older references and the
# agent-worktrees reconciler's init fallback keep working. All flags pass
# through (e.g. --no-service, --install-dir DIR).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/install.sh" install "$@"
