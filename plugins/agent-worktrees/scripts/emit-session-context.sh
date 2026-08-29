#!/usr/bin/env bash
# Pure aggregate-mode context producer; direct sessionStart hooks remain separate.
set -uo pipefail

root="${COPILOT_PLUGIN_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd -P)}"
script="$root/scripts/emit_session_context.py"
python="$(command -v python3 || command -v python || true)"
if [[ -z "$python" || ! -f "$script" ]]; then
    printf '{}'
    exit 0
fi
PYTHONPATH="" "$python" "$script" || printf '{}'
