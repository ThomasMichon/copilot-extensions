#!/usr/bin/env bash
set -euo pipefail

unavailable() {
    printf '%s\n' '[agent-worktrees] SessionEnd client unavailable; closure is unconfirmed.' >&2
    exit 0
}
scripts="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || unavailable
payload="$(cat)" || exit 0
[[ -f "$scripts/managed-session.sh" ]] || exit 0
. "$scripts/managed-session.sh"
python="$(managed_worktrees_python "$scripts")" || exit 0
[[ -n "$python" && -f "$scripts/managed_session.py" ]] || exit 0
printf '%s' "$payload" | "$python" -I -S -B "$scripts/managed_session.py" --check || exit 0
runtime_root="$HOME/.agent-worktrees"
if [[ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]]; then
    runtime_root="$("$python" -I -S -B "$scripts/registry_root.py")" || unavailable
    [[ "$runtime_root" == /* ]] || unavailable
fi
[[ -f "$scripts/resolve-runtime.sh" && -f "$scripts/hook_client.py" ]] || unavailable
AGENT_RT_ROOT="$runtime_root"
AGENT_RT_RESOLVE_TRACE_MODE=read-only
. "$scripts/resolve-runtime.sh"
[[ -x "${AW_PY:-}" ]] || unavailable
printf '%s' "$payload" | PYTHONPATH="" "$AW_PY" -I -B -X utf8 "$scripts/hook_client.py" sessionEnd || unavailable
exit 0
