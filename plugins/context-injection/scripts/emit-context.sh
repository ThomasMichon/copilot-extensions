#!/usr/bin/env bash
set -uo pipefail

root="${COPILOT_PLUGIN_ROOT:-${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-}}}"
[ -n "$root" ] || { printf '{}'; exit 0; }

python_bin="$(command -v python3 || command -v python || true)"
if [ -z "$python_bin" ]; then
    printf '%s\n' '[context-injection] Python is unavailable; context aggregation disabled' >&2
    printf '{}'
    exit 0
fi

tmp="$(mktemp "${TMPDIR:-/tmp}/context-injection.XXXXXXXX")" || {
    printf '{}'
    exit 0
}
trap 'rm -f "$tmp"' EXIT
if "$python_bin" "$root/scripts/aggregate_context.py" >"$tmp"; then
    cat "$tmp"
else
    printf '%s\n' '[context-injection] aggregator failed; direct context retained' >&2
    printf '{}'
fi
