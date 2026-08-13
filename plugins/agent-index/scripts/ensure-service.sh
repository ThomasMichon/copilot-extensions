#!/usr/bin/env bash
# Session-start USER-MODE service ensure (agent-index-specific), POSIX peer of
# ensure-service.ps1. Guarantees the indexer daemon (and, on host, the durable
# embedding engine) is running as a user process -- via systemd --user (already
# user-mode, no elevation) or a nohup start -- never elevated. Fast + timeout-
# safe: a healthy daemon returns immediately; an unhealthy one kicks a BACKGROUND
# `install.sh ensure` and returns without blocking session start.
set -u
INSTALL_DIR="$HOME/.agent-index"
# Only act on a box where agent-index is actually deployed.
[ -f "$INSTALL_DIR/deploy-manifest.json" ] || exit 0

# Fast health probe on the LIVE routing endpoint (active.json ephemeral port).
healthy=0
if command -v python3 >/dev/null 2>&1; then
    if python3 - "$INSTALL_DIR/active.json" <<'PY' 2>/dev/null
import json, sys, urllib.request
try:
    p = json.load(open(sys.argv[1]))["active"]["port"]
    urllib.request.urlopen("http://127.0.0.1:%d/health" % int(p), timeout=2).read()
    sys.exit(0)
except Exception:
    sys.exit(1)
PY
    then healthy=1; fi
fi
[ "$healthy" = "1" ] && exit 0

inst="$(cd "$(dirname "$0")" && pwd)/install.sh"
[ -f "$inst" ] || exit 0
printf '%s\n' "[agent-index] daemon not healthy -- ensuring (user-mode) in background..." >&2
nohup bash "$inst" ensure >/dev/null 2>&1 &
exit 0
