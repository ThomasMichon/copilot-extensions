#!/usr/bin/env bash
# register-nudge -- sessionStart additionalContext hook (hooks.json). See
# register-nudge.ps1 for the parity.
#
# First-run ONBOARDING nudge: when a session is in a git repo that is NOT a
# registered agent-worktrees project, emit a ONE-TIME (per repo) additionalContext
# nudge inviting `agent-worktrees register <name>`. Nudge ONLY -- it NEVER
# registers/adopts anything (install-vs-adopt boundary): the register is the
# operator's explicit act.
#
# Grace-window-cheap + resolver-free: pure shell + a heuristic read of
# projects.yaml, so it works on a tools-half box (the self-provisioned runtime,
# no full-launcher resolver). Fail-open: emits `{}` (a no-op) on ANY uncertainty
# so it never nags wrongly, and it writes only machine-local runtime state
# (~/.agent-worktrees/.register-nudged/), never the repo.
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
context_file="$context_dir/register-nudge-${launch_key:-none}"

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

emit_empty() { publish '{}'; }

# Only nudge when agent-worktrees is actually available to register with (the
# self-provisioning tool binstub is on PATH or deployed).
if ! command -v agent-worktrees >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/agent-worktrees" ]; then
    emit_empty
fi

# Must be inside a git work tree.
top="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$top" ] || emit_empty

# Derive the repo name: strip a `<repo>.worktrees/<id>` worktree suffix, then
# take the basename.
base="$top"
case "$base" in
    *.worktrees/*) base="${base%%.worktrees/*}" ;;
esac
name="$(basename "$base" 2>/dev/null || true)"
[ -n "$name" ] || emit_empty

# Already registered? (heuristic: the repo name is a project key in
# projects.yaml.) If so -- or if we cannot be sure it is NOT registered -- stay
# silent.
projects="$HOME/.agent-worktrees/projects.yaml"
if [ -f "$projects" ] && grep -qE "^[[:space:]]+${name}:[[:space:]]*$" "$projects" 2>/dev/null; then
    emit_empty
fi

# Once-per-repo gating: skip if we have already nudged for this repo path.
marker_dir="$HOME/.agent-worktrees/.register-nudged"
key="$(printf '%s' "$top" | cksum 2>/dev/null | cut -d' ' -f1)"
[ -n "$key" ] || key="$name"
marker="$marker_dir/$key"
[ -f "$marker" ] && emit_empty
mkdir -p "$marker_dir" 2>/dev/null || true
: > "$marker" 2>/dev/null || true

msg="This repo ($name) is not a registered agent-worktrees project. To enable isolated, concurrent worktree sessions (create/finalize + the PR flow), register it once from the repo root: agent-worktrees register $name . This is an onboarding nudge only -- nothing has been registered, and agent-worktrees never auto-adopts a repo."

# JSON-encode the message (backslashes then quotes) and emit the object.
esc=${msg//\\/\\\\}
esc=${esc//\"/\\\"}
publish "$(printf '{"additionalContext": "%s"}' "$esc")"
