#!/usr/bin/env bash
# agent-dispatch-hibernate-eval/setup.sh -- establish the STARTING STATE for the
# Tier-E hibernate-the-wait (suspend/resume) eval.
#
# This is SETUP, not the thing under test: it only ARRANGES the box so the driven
# agent then faces a real "my goal blocks on this external wait -- hand it off per
# the docs" task. It:
#   1. installs agent-dispatch solo and first-session-provisions it (binstub on
#      PATH) -- reused from agent-dispatch-solo;
#   2. git-init's a worker worktree at ~/hibernate-worker (resume handle
#      clean-room/hibernate-worker);
#   3. arms a CALLER-CONTROLLED signal ('hibernate-signal') via the shared lib --
#      a truly-blocking wait whose ONLY release is the harness (post_check.sh),
#      so hibernation is objectively observable.
# Its phases are setup TELEMETRY (pass/info), never the eval verdict -- the verdict
# comes from the driven-agent transcript + clean-room-judge. Sources the shared lib
# for uniform legibility. MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
UV_INDEX="${CR_UV_INDEX:-}"
PLUGIN="agent-dispatch"
INSTALLED_ROOT="$HOME/.copilot/installed-plugins/$MARKETPLACE_NAME"
INSTALL_DIR="$HOME/.agent-dispatch"
SIGNAL_NAME="hibernate-signal"
WORKER_DIR="$HOME/hibernate-worker"
WORKER_HANDLE="clean-room/hibernate-worker"

: "${CR_SCENARIO_NAME:=agent-dispatch-hibernate-eval}"
export CR_SCENARIO_NAME
cr_init
cr_meta "plugin" "$PLUGIN"
cr_meta "role"   "starting-state-setup"
cr_meta "signal_name" "$SIGNAL_NAME"
cr_meta "worker_handle" "$WORKER_HANDLE"

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
    && echo '# hibernate worker' > README.md \
    && git add -A \
    && git commit -qm init
)
pass "worker worktree git-init'd at $WORKER_DIR (handle $WORKER_HANDLE)"
PLUGIN_ARG=()
[ -d "$INSTALLED_ROOT/$PLUGIN" ] && PLUGIN_ARG=( --plugin-dir "$INSTALLED_ROOT/$PLUGIN" )
( cd "$WORKER_DIR" && capture "session-provision" -- copilot -p "Reply with the single word: ready." --allow-all-tools "${PLUGIN_ARG[@]}" ) || true
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
phase 3 "arm the caller-controlled signal (the async construct under test)"
cr_signal_arm "$SIGNAL_NAME"
_waitsh="$(cr_signal_wait_cmd "$SIGNAL_NAME")"
cr_meta "signal_wait_cmd" "$_waitsh"
# The eval prompt references this exact absolute path; assert it matches so a
# home-dir mismatch surfaces here (setup telemetry), not as a mysterious eval FAIL.
if [ "$_waitsh" = "/home/operator/.cr-signals/$SIGNAL_NAME-wait.sh" ] && [ -x "$_waitsh" ]; then
    pass "signal '$SIGNAL_NAME' armed; wait script at the prompt-referenced path ($_waitsh)"
else
    info "signal wait script is $_waitsh (prompt references /home/operator/.cr-signals/$SIGNAL_NAME-wait.sh) -- verify \$HOME"
fi
if cr_signal_waiter_present "$SIGNAL_NAME"; then
    info "a waiter for '$SIGNAL_NAME' is ALREADY running before the turn (unexpected)"
else
    pass "no waiter running yet -- a live waiter after the turn is the driven agent's suspend"
fi

# =========================================================================
cr_finalize
