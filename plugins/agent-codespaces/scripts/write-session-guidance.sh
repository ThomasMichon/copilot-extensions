#!/usr/bin/env bash
# Side-effect-only sessionStart wrapper: invokes write_session_guidance.py.
# Never emits additionalContext; this raw hooks.json entry writes the
# exact-session guidance file. See scripts/write_session_guidance.py.
set -uo pipefail

root="${COPILOT_PLUGIN_ROOT:-${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd -P)}}}"
if [[ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]]; then
    root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
fi
script="$root/scripts/write_session_guidance.py"
helper="$root/scripts/hook-uv-python.sh"
if [[ ! -f "$helper" || ! -f "$script" ]]; then
    if [[ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]]; then
        printf '%s\n' 'CodeSpaces guidance refused: Python or writer is unavailable' >&2
        exit 126
    fi
    printf '{}'
    exit 0
fi
. "$helper"
if [[ -n "${COPILOT_EXTENSIONS_CONTEXT:-}" ]]; then
    hook_uv_python -I -X utf8 "$script"
    exit $?
fi
hook_uv_python "$script" || printf '{}'
