#!/usr/bin/env bash
# Reconcile registered local marketplace sources on session start.

set -uo pipefail

context_only=0
[[ "${1:-}" == "--context-only" ]] && context_only=1
await_context=0
if [[ "${1:-}" == "--await-context" ]]; then
    context_only=1
    await_context=1
fi
side_effect_only=0
[[ "${1:-}" == "--side-effect-only" ]] && side_effect_only=1
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
context_file="$context_dir/marketplace-overrides-${launch_key:-none}"

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
        printf '%s' "$output"
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
PY="${AW_PY:-}"
[[ -x "$PY" ]] || publish '{}'

out="$(printf '%s' "$payload" | PYTHONPATH="" "$PY" -m agent_worktrees reconcile-marketplaces \
    --stdin --session-start 2>/dev/null || true)"
if [[ -n "$out" ]]; then publish "$out"; else publish '{}'; fi
