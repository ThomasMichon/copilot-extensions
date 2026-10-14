#!/usr/bin/env bash
set -uo pipefail

root="${COPILOT_PLUGIN_ROOT:-${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)}}}"
script="$root/scripts/write_session_guidance.py"
python="$(command -v python3 || command -v python || true)"
if [[ -z "$python" || ! -f "$script" ]]; then
    printf 'budget-guidance: session cue writer or Python is unavailable\n' >&2
elif ! "$python" -I -S "$script" >/dev/null 2>/dev/null; then
    printf 'budget-guidance: session cue could not be written\n' >&2
fi
printf '{}'
exit 0
