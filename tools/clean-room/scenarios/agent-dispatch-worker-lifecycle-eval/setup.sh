#!/usr/bin/env bash
# agent-dispatch-worker-lifecycle-eval/setup.sh -- establish the STARTING STATE
# for the Tier-E CLI-capable worker-lifecycle eval (Phase 5,
# agent-dispatch-worker-operating-procedures effort).
#
# This is SETUP, not the thing under test: it only ARRANGES the box (installs
# agent-dispatch solo and first-session-provisions it, git-init's a worker
# worktree with a fake `origin` remote so the repo lane resolves without
# agent-worktrees, and creates exactly ONE real, trivially-completable task
# QUEUED on the live coordinator) so the driven agent then faces a real
# "claim, work, and correctly close out this task using only agent-dispatch's
# own documented CLI" test. Its phases are setup TELEMETRY (pass/info), never
# the eval verdict -- the verdict comes from the driven-agent transcript +
# clean-room-judge. Sources the shared lib for uniform legibility. MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
UV_INDEX="${CR_UV_INDEX:-}"
PLUGIN="agent-dispatch"
INSTALLED_ROOT="$HOME/.copilot/installed-plugins/$MARKETPLACE_NAME"
INSTALL_DIR="$HOME/.agent-dispatch"
WORKER_DIR="$HOME/dispatch-eval-worker"
WORKER_ID="clean-room-eval-worker"
TASK_ID_FILE="$HOME/dispatch-eval-task-id"

: "${CR_SCENARIO_NAME:=agent-dispatch-worker-lifecycle-eval}"
export CR_SCENARIO_NAME
cr_init
cr_meta "plugin" "$PLUGIN"
cr_meta "role" "starting-state-setup"
cr_meta "worker_id" "$WORKER_ID"
cr_meta "variant" "happy-path"

_apply_uv_index_fixture() {
    [ -n "$UV_INDEX" ] || return 0
    export UV_INDEX_URL="$UV_INDEX" UV_DEFAULT_INDEX="$UV_INDEX" UV_EXTRA_INDEX_URL="${UV_EXTRA_INDEX_URL:-$UV_INDEX}"
    mkdir -p "$HOME/.config/uv"
    printf '[[index]]\nurl = "%s"\ndefault = true\n' "$UV_INDEX" > "$HOME/.config/uv/uv.toml"
    info "uv-index fixture applied: uv -> $UV_INDEX"
}

_installer_path() {
    local p=""
    if [ -f "$INSTALL_DIR/payload-dir" ]; then
        p="$(tr -d ' \t\r\n' < "$INSTALL_DIR/payload-dir")/scripts/install.sh"
        [ -f "$p" ] && { printf '%s' "$p"; return 0; }
    fi
    p="$INSTALLED_ROOT/$PLUGIN/scripts/install.sh"
    [ -f "$p" ] && { printf '%s' "$p"; return 0; }
    p="$(ls "$HOME"/.copilot/installed-plugins/*/"$PLUGIN"/scripts/install.sh 2>/dev/null | head -n1)"
    [ -n "$p" ] && [ -f "$p" ] && { printf '%s' "$p"; return 0; }
    return 1
}

# `agent-dispatch` commands may print a leading non-JSON status line (e.g.
# "no local coordinator answering; starting one...") before the JSON object.
# Extract one field from the first top-level `{...}` in a captured log file.
_json_field() {  # <log-file> <field-name>
    python3 -c '
import json, sys
content = open(sys.argv[1], encoding="utf-8").read()
content = content[content.index("{"):]
value = json.loads(content).get(sys.argv[2])
print(value if value is not None else "")
' "$1" "$2" 2>/dev/null
}

# =========================================================================
phase 0 "environment (fresh machine)"
envdump
if [ -d "$INSTALL_DIR" ] || [ -d "$HOME/.local/bin" ]; then
    info "pre-existing ~/.agent-dispatch or ~/.local/bin (box not pristine, continuing)"
else
    pass "clean slate: no ~/.agent-dispatch, no ~/.local/bin"
fi

# =========================================================================
phase 1 "install ONLY $PLUGIN (the starting state)"
mkdir -p "$HOME/.copilot"
cat > "$HOME/.copilot/settings.json" <<JSON
{
  "sandbox": { "enabled": false },
  "experimental": true,
  "extraKnownMarketplaces": { "$MARKETPLACE_NAME": { "source": { "source": "github", "repo": "$MARKETPLACE_REPO" } } },
  "enabledPlugins": { "$PLUGIN@$MARKETPLACE_NAME": true }
}
JSON
capture "marketplace-add" -- copilot plugin marketplace add "$MARKETPLACE_REPO" || true
capture "install" -- copilot plugin install "$PLUGIN@$MARKETPLACE_NAME" || true
if [ -d "$INSTALLED_ROOT/$PLUGIN" ]; then
    pass "$PLUGIN payload present on disk"
else
    jam "npm-registry" "$PLUGIN payload NOT installed (see cr-logs/install.log)" "check marketplace source + node/npm feed"
fi

# =========================================================================
phase 2 "first-session provision (binstub on PATH)"
_apply_uv_index_fixture
mkdir -p "$WORKER_DIR"
(
    cd "$WORKER_DIR" \
    && git init -q \
    && git config user.email t@e \
    && git config user.name t \
    && git remote add origin https://github.com/clean-room/dispatch-eval-worker.git \
    && printf 'line one\nline two\nline three\nline four\nline five\n' > README.md \
    && git add -A \
    && git commit -qm init
)
pass "worker worktree git-init'd at $WORKER_DIR with a fake origin remote (repo lane resolves without agent-worktrees)"
PLUGIN_ARG=()
[ -d "$INSTALLED_ROOT/$PLUGIN" ] && PLUGIN_ARG=( --plugin-dir "$INSTALLED_ROOT/$PLUGIN" )
( cd "$WORKER_DIR" && capture "session-provision" -- copilot -p "Reply with the single word: ready." --allow-all --experimental "${PLUGIN_ARG[@]}" ) || true
sleep 8
if ! bash -lc 'command -v agent-dispatch >/dev/null 2>&1'; then
    installer="$(_installer_path || true)"
    [ -n "$installer" ] && capture "installer-provision" -- bash "$installer" provision || true
fi
if bash -lc 'command -v agent-dispatch >/dev/null 2>&1'; then
    capture "binstub-version" -- bash -lc 'agent-dispatch --version' || true
    pass "agent-dispatch binstub resolves on a fresh login-shell PATH"
else
    jam "path-binstub" "agent-dispatch binstub NOT on PATH after provision" "see agent-dispatch-solo (#649) -- setup cannot proceed"
fi

# =========================================================================
phase 3 "create ONE real queued task (the coordinator is reachable throughout)"
_create_out="$CR_LOGDIR/create-task.log"
mkdir -p "$CR_LOGDIR"
if ( cd "$WORKER_DIR" && bash -lc 'agent-dispatch create "Count README.md lines" --prompt "In this worktree'"'"'s checkout, count the number of lines in README.md and report that exact integer as the completion result-ref. This is the entire task; no other action is required."' ) > "$_create_out" 2>&1; then
    _task_id="$(_json_field "$_create_out" id)"
    if [ -n "$_task_id" ]; then
        printf '%s' "$_task_id" > "$TASK_ID_FILE"
        cr_meta "task_id" "$_task_id"
        pass "created ONE queued task $_task_id in the worker worktree's repo lane"
    else
        jam "dispatch-config" "agent-dispatch create succeeded but no 'id' field in its output (see cr-logs/create-task.log)" "verify the create JSON shape"
    fi
else
    jam "dispatch-config" "agent-dispatch create FAILED (see cr-logs/create-task.log)" "verify the repo lane resolves (git origin) and the coordinator is reachable"
fi
if [ -f "$TASK_ID_FILE" ]; then
    _status_out="$CR_LOGDIR/verify-queued.log"
    if ( cd "$WORKER_DIR" && bash -lc "agent-dispatch show $(cat "$TASK_ID_FILE")" ) > "$_status_out" 2>&1; then
        if grep -q '"status": *"queued"' "$_status_out"; then
            pass "task $(cat "$TASK_ID_FILE") confirmed QUEUED (unclaimed) before the agent turn"
        else
            info "task status is not literally 'queued' in show output (see cr-logs/verify-queued.log) -- verify starting state"
        fi
    fi
fi

# =========================================================================
cr_finalize
