#!/usr/bin/env bash
# Side-effect-only sessionStart wrapper: invokes write_session_guidance.py.
# Never emits additionalContext; this raw hooks.json entry writes the
# exact-session guidance file. See scripts/write_session_guidance.py.
set -uo pipefail

root="${COPILOT_PLUGIN_ROOT:-${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd -P)}}}"
script="$root/scripts/write_session_guidance.py"
helper="$root/scripts/hook-uv-python.sh"
if [[ ! -f "$helper" || ! -f "$script" ]]; then
    printf '{}'
    exit 0
fi
. "$helper"
hook_uv_python "$script" || printf '{}'
