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

context_only=0
[[ "${1:-}" == "--context-only" ]] && context_only=1
await_context=0
if [[ "${1:-}" == "--await-context" ]]; then
    context_only=1
    await_context=1
fi
side_effect_only=0
[[ "${1:-}" == "--side-effect-only" ]] && side_effect_only=1
wt_id="${WORKTREE_ID:-}"
payload=""
if [[ ! -t 0 ]]; then
    payload="$(cat)"
fi
producer_version=""
if [[ -n "${COPILOT_PLUGIN_ROOT:-}" && -f "$COPILOT_PLUGIN_ROOT/plugin.json" ]]; then
    producer_version="$(
        sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
            "$COPILOT_PLUGIN_ROOT/plugin.json" | head -n 1
    )"
fi
if [[ -z "$producer_version" && -f "$HOME/.agent-worktrees/current-version" ]]; then
    producer_version="$(tr -d ' \t\r\n' < "$HOME/.agent-worktrees/current-version")"
fi
identity_python="$(command -v python3 || command -v python || true)"
launch_key=""
if [[ -n "$identity_python" && -n "$producer_version" ]]; then
    launch_key="$(
        printf '%s' "$payload" | "$identity_python" -c '
import hashlib, json, math, os, sys
try:
    payload = json.load(sys.stdin)
    session_id = payload.get("sessionId")
    cwd = payload.get("cwd")
    source = payload.get("source", "")
    timestamp = payload.get("timestamp")
    version = sys.argv[1]
    if (
        not isinstance(session_id, str) or not session_id
        or not isinstance(cwd, str) or not os.path.isabs(cwd)
        or not isinstance(source, str) or not version
        or isinstance(timestamp, bool)
        or not isinstance(timestamp, (int, float))
        or not math.isfinite(timestamp)
    ):
        raise ValueError
    timestamp_text = (
        str(timestamp) if isinstance(timestamp, int) else format(timestamp, ".17g")
    )
    identity = json.dumps(
        [session_id, os.path.realpath(cwd), source, version, timestamp_text],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    print(hashlib.sha256(identity).hexdigest(), end="")
except Exception:
    pass
' "$producer_version" 2>/dev/null
    )"
fi
context_dir="$HOME/.agent-worktrees/.session-context"
context_file="$context_dir/register-session-${launch_key:-none}"

publish() {
    local output="$1"
    if (( ! context_only )) && [[ -n "$launch_key" ]]; then
        mkdir -p "$context_dir" 2>/dev/null || true
        {
            printf '%s\n' "$launch_key"
            printf '%s' "$output"
        } > "$context_file.tmp" 2>/dev/null &&
            mv -f "$context_file.tmp" "$context_file" 2>/dev/null || true
    fi
    if (( side_effect_only )); then
        printf '{}'
    else
        printf '%s\n' "$output"
    fi
    exit 0
}

if (( context_only )); then
    attempts=0
    while (( await_context && attempts < 60 )) &&
        [[ -n "$launch_key" && ! -f "$context_file" ]]; do
        sleep 0.05
        attempts=$((attempts + 1))
    done
    [[ -n "$launch_key" && -f "$context_file" ]] || publish '{}'
    stored_key="$(sed -n '1p' "$context_file" 2>/dev/null || true)"
    [[ "$stored_key" == "$launch_key" ]] || publish '{}'
    stored="$(sed '1d' "$context_file" 2>/dev/null || true)"
    [[ -n "$stored" ]] || stored='{}'
    publish "$stored"
fi

_awresolve="$HOME/.agent-worktrees/bin/resolve-runtime.sh"
[ -f "$_awresolve" ] && . "$_awresolve"
PYTHON="${AW_PY:-}"
if [[ ! -x "$PYTHON" ]]; then
    _log SKIP "venv python not found"
    publish '{}'
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

def parse_context(raw):
    if not raw.strip():
        return ""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    context = value.get("additionalContext") if isinstance(value, dict) else None
    return context.strip() if isinstance(context, str) else ""

catalog_context = parse_context(sys.argv[1])
registration_context = parse_context(sys.argv[2])
if not registration_context:
    print("{}")
    raise SystemExit(0)

contexts = []
for context in (catalog_context, registration_context):
    if context and context not in contexts:
        contexts.append(context)
print(json.dumps({"additionalContext": "\n\n".join(contexts)}) if contexts else "{}")
' "$catalog_json" "$registration_json" 2>/dev/null)"; then
    merged_json="{}"
fi
publish "$merged_json"
