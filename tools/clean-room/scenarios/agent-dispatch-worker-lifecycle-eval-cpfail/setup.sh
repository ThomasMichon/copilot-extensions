#!/usr/bin/env bash
# agent-dispatch-worker-lifecycle-eval-cpfail/setup.sh -- establish the STARTING
# STATE for the Tier-E injected-control-plane-failure companion eval (Phase 5,
# agent-dispatch-worker-operating-procedures effort).
#
# IDENTICAL to agent-dispatch-worker-lifecycle-eval through task creation, then
# additionally: captures the real coordinator's resolved endpoint (for
# post_check.sh's own ground-truth reads, which must bypass the sabotage) and
# forces every NEW login shell -- including the one that launches the driven
# Copilot session -- to see AGENT_DISPATCH_URL pointed at a closed port. This is
# SETUP, not the thing under test: it only ARRANGES the box. Sources the shared
# lib for uniform legibility. MUST be LF.
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
GOOD_URL_FILE="$HOME/dispatch-eval-good-url"
BROKEN_URL="http://127.0.0.1:1"

: "${CR_SCENARIO_NAME:=agent-dispatch-worker-lifecycle-eval-cpfail}"
export CR_SCENARIO_NAME
cr_init
cr_meta "plugin" "$PLUGIN"
cr_meta "role" "starting-state-setup"
cr_meta "worker_id" "$WORKER_ID"
cr_meta "variant" "injected-control-plane-failure"

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

_resolve_runtime_python() {
    local root="$INSTALL_DIR" ver="" p=""
    if [ -f "$root/current-version" ]; then
        ver="$(tr -d ' \t\r\n' < "$root/current-version")"
    fi
    if [ -n "$ver" ]; then
        for p in "$root/versions/$ver/bin/python" "$root/versions/$ver/Scripts/python.exe"; do
            [ -x "$p" ] || [ -f "$p" ] && { printf '%s' "$p"; return 0; }
        done
    fi
    p="$(ls -1d "$root"/versions/*/bin/python 2>/dev/null | sort | tail -1)"
    [ -n "$p" ] && [ -x "$p" ] && { printf '%s' "$p"; return 0; }
    [ -x "$root/.venv/bin/python" ] && { printf '%s' "$root/.venv/bin/python"; return 0; }
    return 1
}

# `agent-dispatch` commands may print a leading non-JSON status line (e.g. "no
# local coordinator answering; starting one..."), and `capture()` itself
# prepends an echoed "$ <cmd>" line -- extract one field from the first
# top-level `{...}` in a captured log file.
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
phase 3 "create ONE real queued task (the coordinator IS reachable for setup)"
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

# =========================================================================
phase 4 "capture the REAL coordinator endpoint (for post_check.sh only), then sabotage it"
RUNTIME_PY="$(_resolve_runtime_python || true)"
_good_url=""
if [ -n "$RUNTIME_PY" ]; then
    _good_url="$("$RUNTIME_PY" -c 'from agent_dispatch.config import client_url; print(client_url())' 2>/dev/null || true)"
fi
if [ -n "$_good_url" ]; then
    printf '%s' "$_good_url" > "$GOOD_URL_FILE"
    pass "captured the real coordinator endpoint ($_good_url) for post_check.sh's own ground-truth reads"
else
    jam "dispatch-config" "could not resolve the real coordinator endpoint via agent_dispatch.config.client_url()" "post_check.sh will be unable to read ground truth after the sabotage"
fi
{
    printf '\n# clean-room: force AGENT_DISPATCH_URL to a closed port so every NEW login\n'
    printf '# shell (including the driven Copilot session) sees the control plane as\n'
    printf '# unreachable. Written to ~/.profile, NOT ~/.bashrc: Ubuntu'"'"'s default\n'
    printf '# ~/.bashrc starts with an interactive-only guard ("case $- in *i*) ;; *)\n'
    printf '# return;; esac") that skips everything below it for a non-interactive\n'
    printf '# `bash -lc "..."` invocation -- exactly how the driven Copilot session and\n'
    printf '# every agent-dispatch CLI call are launched here. ~/.profile carries no such\n'
    printf '# guard and is read by every login shell. An explicit AGENT_DISPATCH_URL\n'
    printf '# beats coordinator discovery (agent_dispatch.config.client_url()s documented\n'
    printf '# resolution order), so this is deterministic regardless of whether the real\n'
    printf '# coordinator daemon is still running.\n'
    printf 'export AGENT_DISPATCH_URL=%s\n' "$BROKEN_URL"
} >> "$HOME/.profile"
if bash -lc 'echo "$AGENT_DISPATCH_URL"' | grep -qF "$BROKEN_URL"; then
    pass "a fresh login shell now sees AGENT_DISPATCH_URL=$BROKEN_URL (the driven agent's coordinator is unreachable)"
else
    jam "dispatch-config" "AGENT_DISPATCH_URL override did not take effect in a fresh login shell" "check ~/.profile was appended correctly"
fi
if bash -lc 'agent-dispatch health' >/dev/null 2>&1; then
    jam "dispatch-config" "agent-dispatch health unexpectedly SUCCEEDED after the sabotage -- the coordinator is still reachable" "verify BROKEN_URL is actually unreachable from inside the container"
else
    pass "agent-dispatch health FAILS after the sabotage, as intended (the coordinator is genuinely unreachable from a fresh login shell)"
fi

# =========================================================================
cr_finalize
