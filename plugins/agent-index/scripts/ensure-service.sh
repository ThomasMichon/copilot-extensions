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

# A client runs NO local indexer daemon -- its MCP/CLI route to the designated
# host's service over SSH -- so there is nothing to keep alive here. Skip fast
# (no background install.sh spawn) on any non-host. Mirrors config.resolve_role
# precedence: a VALID AGENT_INDEX_ROLE env (host/client) wins; otherwise the
# config.yaml role:/engine: scalar; else client. An unrecognized env value is
# ignored (falls through), never treated as a role.
role=""
envrole="$(printf '%s' "${AGENT_INDEX_ROLE:-}" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
case "$envrole" in
    host|client) role="$envrole" ;;
    *) [ -f "$INSTALL_DIR/config.yaml" ] && role="$(sed -n 's/^[[:space:]]*\(role\|engine\)[[:space:]]*:[[:space:]]*"\?\([A-Za-z]\+\)"\?.*/\2/p' "$INSTALL_DIR/config.yaml" | head -n1 | tr '[:upper:]' '[:lower:]')" ;;
esac
case "$role" in host|engine|server|indexer) : ;; *) exit 0 ;; esac

# Fast health probe on the LIVE routing endpoint (active.json ephemeral port).
# Resolve the runtime's OWN slot python via the canonical marker-only resolver
# (uniform-runtime-resolution, #765); if no slot is resolvable yet, fall back to
# curl so a host without an installed runtime still probes.
pybin=""
_res="$INSTALL_DIR/bin/resolve-runtime.sh"
if [ -f "$_res" ]; then
    AGENT_RT_ROOT="$INSTALL_DIR"; AGENT_RT_PY=""
    . "$_res"
    [ -n "$AGENT_RT_PY" ] && pybin="$AGENT_RT_PY"
fi

healthy=0
if [ -n "$pybin" ]; then
    if "$pybin" - "$INSTALL_DIR/active.json" <<'PY' 2>/dev/null
import json, sys, urllib.request
try:
    p = json.load(open(sys.argv[1]))["active"]["port"]
    urllib.request.urlopen("http://127.0.0.1:%d/health" % int(p), timeout=2).read()
    sys.exit(0)
except Exception:
    sys.exit(1)
PY
    then healthy=1; fi
elif command -v curl >/dev/null 2>&1; then
    port="$(sed -n 's/.*"port"[: ]*\([0-9]\{1,\}\).*/\1/p' "$INSTALL_DIR/active.json" | head -n1)"
    [ -n "$port" ] && curl -fsS --max-time 2 "http://127.0.0.1:$port/health" >/dev/null 2>&1 && healthy=1
fi
[ "$healthy" = "1" ] && exit 0

inst="$(cd "$(dirname "$0")" && pwd)/install.sh"
[ -f "$inst" ] || exit 0
probe="$(cd "$(dirname "$0")" && pwd)/installation-context/legacy-entrypoint-probe.sh"
if [ ! -f "$probe" ]; then
    printf '%s\n' '[agent-index] legacy mutation probe is unavailable; skipping service ensure.' >&2
    exit 0
fi
bash "$probe" --payload-root "$(cd "$(dirname "$0")/.." && pwd)" \
    --legacy-root "$INSTALL_DIR" || exit 0
printf '%s\n' "[agent-index] daemon not healthy -- ensuring (user-mode) in background..." >&2
nohup bash "$inst" ensure >/dev/null 2>&1 &
exit 0
