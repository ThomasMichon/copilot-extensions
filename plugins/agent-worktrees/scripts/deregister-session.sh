#!/usr/bin/env bash
set -euo pipefail

unavailable() {
    printf '%s\n' '[agent-worktrees] SessionEnd client unavailable; closure is unconfirmed.' >&2
    exit 0
}
scripts="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || unavailable
payload="$(cat)" || exit 0
python="$(command -v python3 || command -v python || true)"
[[ -n "$python" && -f "$scripts/managed_session.py" ]] || exit 0
printf '%s' "$payload" | "$python" -I -B "$scripts/managed_session.py" --check || exit 0
runtime_root="$HOME/.agent-worktrees"
if [[ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]]; then
    owner="${AGENT_WORKTREES_PAYLOAD_ROOT:-${COPILOT_PLUGIN_ROOT:-${scripts%/*}}}"
    durable_home="$COPILOT_EXTENSIONS_CONTEXT"
    for _part in 1 2 3 4 5; do durable_home="$(dirname -- "$durable_home")"; done
    validation="$(bash "$scripts/installation-context/installation-context.sh" validate \
        --context "$COPILOT_EXTENSIONS_CONTEXT" --durable-home "$durable_home" \
        --expected-plugin-id agent-worktrees --expected-payload-root "$owner")" || unavailable
    runtime_root="$(LC_ALL=C awk -f "$scripts/installation-context/json-query.awk" \
        -v mode=get -v query_path=pluginRoot <<<"$validation")" || unavailable
    [[ "$runtime_root" == /* ]] || unavailable
fi
[[ -f "$scripts/resolve-runtime.sh" && -f "$scripts/hook_client.py" ]] || unavailable
AGENT_RT_ROOT="$runtime_root"
AGENT_RT_RESOLVE_TRACE_MODE=read-only
. "$scripts/resolve-runtime.sh"
[[ -x "${AW_PY:-}" ]] || unavailable
cache="$runtime_root/cache/python/unsealed-session-end-$$-$RANDOM"
if command -v cygpath >/dev/null 2>&1; then
    cache="$(cygpath -aw "$cache")"
fi
selected_cache="$("$AW_PY" -I -S -B -X utf8 -X "pycache_prefix=$cache" -c '
import runpy,sys
from pathlib import Path
helper = runpy.run_path(sys.argv[1])
print(helper["python_cache_arguments"](Path(sys.executable))[1].split("=", 1)[1])
' "$scripts/hook_runtime.py")" || unavailable
[[ -n "$selected_cache" ]] || unavailable
printf '%s' "$payload" | PYTHONPATH="" "$AW_PY" -I -X utf8 -X "pycache_prefix=$selected_cache" "$scripts/hook_client.py" sessionEnd || unavailable
exit 0
