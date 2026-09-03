#!/usr/bin/env bash
# Session-start USER-MODE service ensure (agent-index-specific), POSIX peer of
# ensure-service.ps1. Guarantees the indexer daemon (and, on host, the durable
# embedding engine) is running as a user process -- via systemd --user (already
# user-mode, no elevation) or a nohup start -- never elevated. Fast + timeout-
# safe: namespaced mode coalesces a background cell-runtime ensure; legacy mode
# kicks a background `install.sh ensure`. Neither path waits for service startup.
set -u
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

# Session start is repository-scoped activation. Merely enabling the plugin
# must not start a machine-global daemon in an unrelated repository.
repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$repo_root" ] || exit 0
repo_config="$repo_root/.agent-index/config.yaml"
[ -f "$repo_config" ] || exit 0
me="$(printf '%s' "${AGENT_INDEX_MACHINE:-$(hostname -s)}" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
py="$(command -v python3 || command -v python || true)"
[ -n "$py" ] || exit 0
role="$(CDPATH= cd -- "$script_dir" &&
    "$py" -I -X utf8 "$script_dir/resolve-activation-role.py" \
        --config "$repo_config" --machine "$me" 2>/dev/null || true)"
[ "$role" = "host" ] || exit 0

if [ -f "$script_dir/runtime-gate.sh" ]; then
    bash "$script_dir/runtime-gate.sh" __cell-service-ensure >/dev/null 2>&1
    cell_status=$?
    if [ "$cell_status" -eq 0 ]; then exit 0; fi
    if [ "$cell_status" -ne 10 ]; then exit 0; fi
fi

INSTALL_DIR="$HOME/.agent-index"
# Only act on a box where agent-index is actually deployed.
[ -f "$INSTALL_DIR/deploy-manifest.json" ] || exit 0

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
probe_python="${pybin:-$py}"
if [ -n "$probe_python" ]; then
    if (CDPATH= cd -- "$INSTALL_DIR" &&
        "$probe_python" -I -X utf8 - "$INSTALL_DIR/active.json" <<'PY' 2>/dev/null
import json, sys, urllib.request
try:
    with open(sys.argv[1], encoding="utf-8") as stream:
        p = json.load(stream)["active"]["port"]
    value = json.loads(
        urllib.request.urlopen(
            "http://127.0.0.1:%d/health" % int(p), timeout=2
        ).read().decode("utf-8")
    )
    sys.exit(0 if value.get("status") == "ok" and value.get("promoted") is not False else 1)
except Exception:
    sys.exit(1)
PY
    ); then healthy=1; fi
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
