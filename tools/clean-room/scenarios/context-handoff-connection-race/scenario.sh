#!/usr/bin/env bash
# context-handoff-connection-race/scenario.sh -- Tier-P F1 repro rig for
# github/copilot-agent-runtime#22266's confirmed half: a second extension
# connection manually joining a LIVE session (via the runtime's own
# extension_bootstrap.mjs, with a hand-set COPILOT_EXTENSION_PARENT_PID and
# SESSION_ID) collides with the session's already-registered context-handoff
# tool names and gets rejected by validate_external_tools.
#
# This is a REPRO rig, not a regression gate: a clean box is used specifically
# so the mechanism can be shown to reproduce independent of the machine/session
# state that first surfaced it (a Docker "fresh machine", same rationale as
# every other Tier-P scenario in this rig). PASS here means the clash WAS
# observed (the mechanism is confirmed, reproducibly); a miss is reported via
# `jam` since it means either the bug is fixed upstream, or this container's
# assumptions about the runtime's on-disk layout have drifted -- both are
# actionable, neither is silently ignored.
#
# Mechanism (see copilot-agent-runtime#22266 and
# src/runtime/src/protocol/sdk_connection_registry.rs's validate_external_tools):
# extension_bootstrap.mjs only checks that its OS parent pid matches whatever
# COPILOT_EXTENSION_PARENT_PID claims -- an env var the caller fully controls.
# Anything that sets it to its own true parent pid, points COPILOT_SDK_PATH at
# the real bundled SDK, and sets SESSION_ID to an already-live session can join
# that session from OUTSIDE the harness's own supervised launch path.
#
# Name-free / public F1. Asserts on the clash OUTCOME (stderr text), not exact
# CLI spelling or install paths (those are discovered, not hardcoded).
# Env: CR_MARKETPLACE_REPO / CR_MARKETPLACE_NAME / CR_HOLD_SECONDS.
# MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
HOLD_SECONDS="${CR_HOLD_SECONDS:-25}"
PLUGIN="context-handoff"
INSTALLED_ROOT="$HOME/.copilot/installed-plugins/$MARKETPLACE_NAME"
SESSION_STATE_DIR="$HOME/.copilot/session-state"

: "${CR_SCENARIO_NAME:=context-handoff-connection-race}"
export CR_SCENARIO_NAME
cr_init
cr_meta "plugin" "$PLUGIN"
cr_meta "validates" "manual-join tool-name clash reproduces against a live session (copilot-agent-runtime#22266)"

# =========================================================================
phase 0 "environment (fresh machine)"
envdump
if [ -d "$INSTALLED_ROOT/$PLUGIN" ]; then
    fail "environment is NOT clean -- $PLUGIN already installed"
else
    pass "clean slate: no pre-existing $PLUGIN install"
fi

# =========================================================================
phase 1 "install $PLUGIN"
mkdir -p "$HOME/.copilot"
cat > "$HOME/.copilot/settings.json" <<JSON
{
  "extraKnownMarketplaces": { "$MARKETPLACE_NAME": { "source": { "source": "github", "repo": "$MARKETPLACE_REPO" } } },
  "enabledPlugins": { "$PLUGIN@$MARKETPLACE_NAME": true }
}
JSON
capture "marketplace-add" -- copilot plugin marketplace add "$MARKETPLACE_REPO" || true
capture "install" -- copilot plugin install "$PLUGIN@$MARKETPLACE_NAME" || true
EXT_PATH="$INSTALLED_ROOT/$PLUGIN/extensions/$PLUGIN/extension.mjs"
if [ -f "$EXT_PATH" ]; then
    pass "$PLUGIN payload present on disk ($EXT_PATH)"
else
    jam "npm-registry" "$PLUGIN payload NOT installed (see cr-logs/install.log)" "check marketplace source + node/npm feed"
fi

# =========================================================================
phase 2 "start a real, long-lived session (context-handoff enabled) and capture its session id"
mkdir -p "$HOME/ch-repro" && ( cd "$HOME/ch-repro" && git init -q \
    && git config user.email t@e && git config user.name t \
    && echo '# ch-repro' > README.md && git add -A && git commit -qm init )

PLUGIN_ARG=()
[ -f "$EXT_PATH" ] && PLUGIN_ARG=( --plugin-dir "$INSTALLED_ROOT/$PLUGIN" )

# Snapshot existing session-state dirs so the new one can be identified by
# set difference rather than guessing "the newest" (which races a
# concurrently-running unrelated session on a shared box).
mkdir -p "$SESSION_STATE_DIR"
BEFORE_SESSIONS="$CR_LOGDIR/sessions-before.txt"
ls -1 "$SESSION_STATE_DIR" 2>/dev/null > "$BEFORE_SESSIONS"

# A held-open session: the prompt asks the agent to sleep so the process
# stays live for HOLD_SECONDS while phase 3/4 run concurrently against it.
LIVE_LOG="$CR_LOGDIR/live-session.log"
( cd "$HOME/ch-repro" && copilot -p "Run: sleep $HOLD_SECONDS" --allow-all-tools "${PLUGIN_ARG[@]}" \
    > "$LIVE_LOG" 2>&1 ) &
LIVE_PID=$!
cr_meta "live_session_pid" "$LIVE_PID"

# Wait for a new session-state directory to appear (bounded).
SESSION_ID=""
for _ in $(seq 1 40); do
    NEW_DIR="$(comm -13 <(sort "$BEFORE_SESSIONS") <(ls -1 "$SESSION_STATE_DIR" 2>/dev/null | sort) | head -1)"
    if [ -n "$NEW_DIR" ]; then SESSION_ID="$NEW_DIR"; break; fi
    sleep 0.5
done

if [ -n "$SESSION_ID" ]; then
    pass "live session started, session id captured: $SESSION_ID (pid $LIVE_PID)"
    cr_meta "session_id" "$SESSION_ID"
else
    jam "session-capture" "no new session-state directory appeared within 20s" \
        "check cr-logs/live-session.log for a startup failure"
fi

# =========================================================================
phase 3 "resolve the runtime's bootstrap entrypoint + bundled SDK path"
# The CLI extracts its versioned runtime payload to a per-platform cache dir
# (~/.cache/copilot/pkg/<platform>/<version>/ on Linux, mirroring
# %LOCALAPPDATA%\copilot\pkg\<platform>\<version>\ on Windows) -- NOT inside
# the npm package itself, which only ships the launcher. Prefer the slot
# whose version matches the currently-installed CLI so the manually-joined
# connection speaks the exact same protocol revision as the live session.
BOOTSTRAP_PATH=""
SDK_PATH=""
CLI_VERSION="$(copilot --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
CACHE_PKG_ROOT="$HOME/.cache/copilot/pkg"
if [ -n "$CLI_VERSION" ] && [ -d "$CACHE_PKG_ROOT" ]; then
    BOOTSTRAP_PATH="$(find "$CACHE_PKG_ROOT" -path "*/$CLI_VERSION/preloads/extension_bootstrap.mjs" 2>/dev/null | head -1)"
fi
# Fall back to whatever slot exists if the exact version isn't cached (e.g. an
# auto-updated CLI left only a newer slot behind).
if [ -z "$BOOTSTRAP_PATH" ] && [ -d "$CACHE_PKG_ROOT" ]; then
    BOOTSTRAP_PATH="$(find "$CACHE_PKG_ROOT" -type f -name extension_bootstrap.mjs 2>/dev/null | sort | tail -1)"
fi
if [ -n "$BOOTSTRAP_PATH" ]; then
    PKG_ROOT="$(dirname "$(dirname "$BOOTSTRAP_PATH")")"   # .../preloads/.. -> pkg root
    [ -d "$PKG_ROOT/copilot-sdk" ] && SDK_PATH="$PKG_ROOT/copilot-sdk"
fi

if [ -n "$BOOTSTRAP_PATH" ] && [ -n "$SDK_PATH" ]; then
    pass "resolved bootstrap ($BOOTSTRAP_PATH) and SDK path ($SDK_PATH)"
    cr_meta "bootstrap_path" "$BOOTSTRAP_PATH"
    cr_meta "sdk_path" "$SDK_PATH"
else
    jam "layout-drift" "could not resolve extension_bootstrap.mjs and/or copilot-sdk under the npm global install" \
        "the CLI package layout may have changed; update this scenario's discovery logic"
fi

# =========================================================================
phase 4 "manually join the live session with a second connection; assert the clash"
if [ -n "$SESSION_ID" ] && [ -n "$BOOTSTRAP_PATH" ] && [ -n "$SDK_PATH" ] && [ -f "$EXT_PATH" ]; then
    MANUAL_LOG="$CR_LOGDIR/manual-join.log"
    # $$ is this shell's own pid -- the true OS parent of the node process we
    # are about to spawn directly (not through the harness's own supervised
    # launch path), which is exactly what extension_bootstrap.mjs's
    # process.ppid check expects.
    COPILOT_EXTENSION_PARENT_PID="$$" \
    COPILOT_SDK_PATH="$SDK_PATH" \
    EXTENSION_PATH="$EXT_PATH" \
    SESSION_ID="$SESSION_ID" \
        node "$BOOTSTRAP_PATH" > "$MANUAL_LOG" 2>&1
    MANUAL_RC=$?
    info "manual second connection exited rc=$MANUAL_RC (log: $MANUAL_LOG)"

    if grep -q "already registered by another connection" "$MANUAL_LOG" 2>/dev/null; then
        pass "manual-join clash REPRODUCED: validate_external_tools rejected the second connection (see $MANUAL_LOG)"
    else
        jam "no-repro" "manual second connection did NOT hit the expected tool-name clash (rc=$MANUAL_RC)" \
            "either the guard changed upstream (good news -- update/retire this scenario), or a layout/timing assumption drifted; inspect $MANUAL_LOG"
    fi
else
    info "phase 4 skipped: a precondition from phases 2/3 was not satisfied (see jams above)"
fi

# Best-effort cleanup of the held-open session.
kill "${LIVE_PID:-0}" >/dev/null 2>&1 || true

cr_finalize
