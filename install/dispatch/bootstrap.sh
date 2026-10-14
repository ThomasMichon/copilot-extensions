#!/usr/bin/env bash
# install/dispatch/bootstrap.sh -- the STABLE, public install entry point
# for the agent-dispatch standalone package.
#
#   curl -fsSL https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/install/dispatch/bootstrap.sh | bash -s -- install
#
# This is the file any published URL, bookmarked command, or another
# plugin's hardcoded installer reference should point at -- see
# docs/patterns/standalone-service-layout.md's "public-install-surface
# indirection". It never moves and never changes shape. It does nothing but
# resolve and exec the CURRENT real bootstrap
# (services/dispatch/bootstrap.sh today); when that package moves to a new
# top-level location in a future reorganization, only REAL_PATH below needs
# updating -- every external caller's URL stays byte-for-byte the same.
#
# Every argument is forwarded verbatim to the real bootstrap script.

set -euo pipefail

REPO_OWNER="ThomasMichon"
REPO_NAME="copilot-extensions"
REF="${AGENT_DISPATCH_SERVICE_REF:-main}"

# <-- The ONLY line that should ever need updating when the real package moves.
REAL_PATH="services/dispatch/bootstrap.sh"

# Prefer a local sibling copy when this proxy is itself running from a
# cloned checkout (a developer invoking it directly, or a test) rather than
# a bare `curl | bash` fetch -- avoids a redundant network round-trip and
# lets this proxy be exercised offline.
SCRIPT_SOURCE="${BASH_SOURCE[0]:-$0}"
if [ -f "$SCRIPT_SOURCE" ]; then
    SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_SOURCE")" && pwd)"
    LOCAL_REAL="$SCRIPT_DIR/../../$REAL_PATH"
    if [ -f "$LOCAL_REAL" ]; then
        exec bash "$LOCAL_REAL" "$@"
    fi
fi

REAL_URL="https://raw.githubusercontent.com/$REPO_OWNER/$REPO_NAME/$REF/$REAL_PATH"
echo "install/dispatch/bootstrap.sh: fetching the real bootstrap ($REAL_PATH, $REF)..." >&2
TMP_FILE="$(mktemp "${TMPDIR:-/tmp}/agent-dispatch-install-proxy.XXXXXX")"
trap 'rm -f "$TMP_FILE"' EXIT
if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$REAL_URL" -o "$TMP_FILE"
elif command -v wget >/dev/null 2>&1; then
    wget -qO "$TMP_FILE" "$REAL_URL"
else
    echo "install/dispatch/bootstrap.sh: need curl or wget to fetch $REAL_URL" >&2
    exit 1
fi
exec bash "$TMP_FILE" "$@"
