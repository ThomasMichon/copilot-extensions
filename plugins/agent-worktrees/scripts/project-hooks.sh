#!/usr/bin/env bash
# Project hooks runner -- runs on session start via hooks.json
# Discovers and executes per-project session-start hooks from the
# project config directory (~/.{project}/hooks/session-start.sh).

set -euo pipefail

payload=""
if [[ ! -t 0 ]]; then payload="$(cat)"; fi

# Prefer the resident monitor's warm project resolution. The deployed CLI
# remains the bounded fallback when the monitor is unavailable.
_awresolve="$HOME/.agent-worktrees/bin/resolve-runtime.sh"
[ -f "$_awresolve" ] && . "$_awresolve"
PYTHON="${AW_PY:-}"
if [[ ! -x "$PYTHON" ]]; then exit 0; fi
hook=""
resolved_by_monitor=0
client="$HOME/.agent-worktrees/bin/hook_client.py"
if [[ -f "$client" ]]; then
    resolved="$(
        printf '%s' "$payload" |
            PYTHONPATH="" "$PYTHON" "$client" projectResolve 2>/dev/null || true
    )"
    if [[ "$resolved" == *$'\n'* ]]; then
        resolved_status="${resolved##*$'\n'}"
        resolved_hook="${resolved%%$'\n'*}"
        if [[ "$resolved_status" == "0" ]]; then
            resolved_by_monitor=1
            [[ "$resolved_hook" != "-" ]] && hook="$resolved_hook"
        fi
    fi
fi
if (( resolved_by_monitor )) && [[ -z "$hook" ]]; then exit 0; fi
if (( ! resolved_by_monitor )); then
    project="$(PYTHONPATH="" "$PYTHON" -m agent_worktrees get project 2>/dev/null || true)"
    if [[ -z "$project" ]]; then exit 0; fi
    hook="$HOME/.$project/hooks/session-start.sh"
fi
if [[ ! -f "$hook" ]]; then exit 0; fi

if [[ -n "$payload" ]]; then
    printf '%s' "$payload" | bash "$hook" || true
else
    bash "$hook" || true
fi

exit 0
