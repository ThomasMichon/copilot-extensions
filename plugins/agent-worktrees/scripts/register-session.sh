#!/usr/bin/env bash
# Register a Copilot session against the current worktree.
# Called from hooks.json on sessionStart.
#
# The Copilot CLI pipes {sessionId, cwd, ...} as a JSON payload on stdin.
# COPILOT_AGENT_SESSION_ID is NOT reliably set in the sessionStart hook
# environment, so the stdin payload is the authoritative source for the
# session id. We forward it to the Python command (--stdin), which parses
# it and resolves the worktree from cwd when WORKTREE_ID is absent.

set -euo pipefail

_LOG="${WORKTREE_SETUP_LOG:-/dev/null}"
_log() { printf '[%s] [%s] register-session: %s\n' "$(date '+%H:%M:%S')" "$1" "$2" >> "$_LOG" 2>/dev/null || true; }

wt_id="${WORKTREE_ID:-}"
payload=""
if [[ ! -t 0 ]]; then
    payload="$(cat)"
fi

_awresolve="$HOME/.agent-worktrees/bin/resolve-runtime.sh"
[ -f "$_awresolve" ] && . "$_awresolve"
PYTHON="${AW_PY:-}"
if [[ ! -x "$PYTHON" ]]; then
    _log SKIP "venv python not found"
    exit 0
fi

args=(-m agent_worktrees register-session --stdin --emit-context)
[[ -n "$wt_id" ]] && args+=(--worktree-id "$wt_id")

# Capture the registration context so a successful managed-worktree binding can
# carry the payload-local command catalog in the same sessionStart result. The
# current CLI keeps only one non-empty result when hooks race (#1234); without
# this narrow same-plugin merge, agents receive either the binding or the exact
# argv[0], but not reliably both.
registration_json=""
if registration_json="$(printf '%s' "$payload" | PYTHONPATH="" "$PYTHON" "${args[@]}" 2>/dev/null)"; then
    _log OK "registered session (wt=${wt_id:-<from-cwd>})"
else
    _log WARN "register-session failed (exit $?) wt=${wt_id:-<from-cwd>}"
fi

catalog_json=""
catalog_script=""
if [[ -n "${COPILOT_PLUGIN_ROOT:-}" ]]; then
    catalog_script="$COPILOT_PLUGIN_ROOT/scripts/emit-command-catalog.sh"
fi
if [[ -n "$catalog_script" && -f "$catalog_script" ]]; then
    catalog_json="$(bash "$catalog_script" 2>/dev/null || true)"
fi

merged_json=""
if ! merged_json="$(PYTHONPATH="" "$PYTHON" -c '
import json
import sys

contexts = []
for raw in sys.argv[1:]:
    if not raw.strip():
        continue
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        continue
    context = value.get("additionalContext") if isinstance(value, dict) else None
    if isinstance(context, str) and context.strip() and context not in contexts:
        contexts.append(context)
print(json.dumps({"additionalContext": "\n\n".join(contexts)}) if contexts else "{}")
' "$catalog_json" "$registration_json" 2>/dev/null)"; then
    merged_json="{}"
fi
printf '%s\n' "$merged_json"

exit 0
