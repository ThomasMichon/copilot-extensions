#!/usr/bin/env bash
# Emit the exact payload-owned agent-index invocation into session context.
set -eu

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
self_root="$(cd "$script_dir/.." && pwd -P)"
context_root="${COPILOT_PLUGIN_ROOT:-$self_root}"
context_root="$(cd "$context_root" 2>/dev/null && pwd -P)" || {
    printf '%s\n' '{}'
    exit 0
}
[ "$context_root" = "$self_root" ] || {
    printf '%s\n' '{}'
    exit 0
}

command_path="$self_root/bin/agent-index"
availability="unavailable"
[ -x "$command_path" ] && availability="ready"
py="$(command -v python3 || command -v python || true)"
[ -n "$py" ] || {
    printf '%s\n' '{}'
    exit 0
}

COMMAND_PATH="$command_path" AVAILABILITY="$availability" "$py" <<'PY'
import json
import os

catalog = {
    "schema": "copilot-extensions.session-command-catalog",
    "version": 1,
    "plugin": "agent-index",
    "payload": {"provenance": "payload-local"},
    "commands": [
        {
            "id": "agent-index",
            "argv": [os.environ["COMMAND_PATH"]],
            "shell": "direct",
            "purpose": "Search and operate the semantic index",
            "availability": os.environ["AVAILABILITY"],
        }
    ],
}
context = (
    "## agent-index session command catalog\n\n"
    "Invoke the exact `argv` below. Do not search `PATH` or substitute a "
    "same-named command from another payload.\n\n"
    "```json\n"
    + json.dumps(catalog, sort_keys=True)
    + "\n```"
)
print(json.dumps({"additionalContext": context}))
PY
