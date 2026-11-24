#!/usr/bin/env bash
# Side-effect-only sessionStart wrapper: invokes write_session_guidance.py.
# Never emits additionalContext; this raw hooks.json entry writes the
# exact-session guidance file. See scripts/write_session_guidance.py.
set -uo pipefail

root="${COPILOT_PLUGIN_ROOT:-${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd -P)}}}"
case "${OSTYPE:-}" in
    msys*|cygwin*)
        host="$(cygpath -u "${SYSTEMROOT:-${SystemRoot:-}}")/System32/WindowsPowerShell/v1.0/powershell.exe"
        "$host" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass \
            -File "$(cygpath -w "$root/scripts/write-session-guidance.ps1")" \
            >/dev/null 2>&1 || true
        printf '{}'
        exit 0
        ;;
esac
script="$root/scripts/write_session_guidance.py"
python="$(command -v python3 || command -v python || true)"
if [[ -z "$python" || ! -f "$script" ]]; then
    printf '{}'
    exit 0
fi
PYTHONPATH="" "$python" "$script" || printf '{}'