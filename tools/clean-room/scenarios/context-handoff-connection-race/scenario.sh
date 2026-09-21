#!/usr/bin/env bash
# context-handoff-connection-race/scenario.sh -- Tier-P F1 repro rig for
# github/copilot-agent-runtime#22266: a single session that discovers
# `context-handoff` from TWO sources at once -- the marketplace-installed
# plugin copy AND a second copy present as a project-level extension in the
# working repo -- launches BOTH as real connections, and the second one hits
# validate_external_tools' same-session tool-name clash
# (generate_handoff_prompt/save_handoff_prompt/consume_handoff/trigger_handoff
# already registered by another connection).
#
# This is the mechanism actually confirmed against a live machine (both
# connections legitimately host-spawned by the SAME session, from two
# discovery sources for one plugin id) -- NOT a manually-invoked standalone
# extension_bootstrap.mjs process from outside the harness (that path was
# tried first and failed here for an unrelated, informative reason: a
# standalone-spawned process's stdio is not wired to any real host, so its
# `connect` RPC just times out). Reproducing the SAME-SESSION double-discovery
# needs no stdio trickery at all: it is the CLI's own normal extension-
# discovery + launch flow, run against a deliberately-duplicated source.
#
# This is a REPRO rig, not a regression gate: a clean box is used specifically
# so the mechanism can be shown to reproduce independent of the machine/session
# state that first surfaced it. PASS here means the clash WAS observed; a miss
# is reported via `jam` since it means either the guard changed upstream, or
# this container's assumptions about the runtime's on-disk layout/log naming
# have drifted -- both are actionable, neither is silently ignored.
#
# Name-free / public F1. Asserts on the clash OUTCOME (extension log text),
# not exact CLI spelling or install paths (those are discovered, not
# hardcoded).
# Env: CR_MARKETPLACE_REPO / CR_MARKETPLACE_NAME.
# MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
PLUGIN="context-handoff"
INSTALLED_ROOT="$HOME/.copilot/installed-plugins/$MARKETPLACE_NAME"
EXT_LOG_DIR="$HOME/.copilot/logs/extensions"

: "${CR_SCENARIO_NAME:=context-handoff-connection-race}"
export CR_SCENARIO_NAME
cr_init
cr_meta "plugin" "$PLUGIN"
cr_meta "validates" "same-session double-discovery tool-name clash reproduces (copilot-agent-runtime#22266)"

# =========================================================================
phase 0 "environment (fresh machine)"
envdump
if [ -d "$INSTALLED_ROOT/$PLUGIN" ]; then
    fail "environment is NOT clean -- $PLUGIN already installed"
else
    pass "clean slate: no pre-existing $PLUGIN install"
fi

# =========================================================================
phase 1 "install $PLUGIN (marketplace source)"
mkdir -p "$HOME/.copilot"
cat > "$HOME/.copilot/settings.json" <<JSON
{
  "extraKnownMarketplaces": { "$MARKETPLACE_NAME": { "source": { "source": "github", "repo": "$MARKETPLACE_REPO" } } },
  "enabledPlugins": { "$PLUGIN@$MARKETPLACE_NAME": true }
}
JSON
capture "marketplace-add" -- copilot plugin marketplace add "$MARKETPLACE_REPO" || true
capture "install" -- copilot plugin install "$PLUGIN@$MARKETPLACE_NAME" || true
INSTALLED_EXT_DIR="$INSTALLED_ROOT/$PLUGIN/extensions/$PLUGIN"
if [ -d "$INSTALLED_EXT_DIR" ]; then
    pass "$PLUGIN payload present on disk ($INSTALLED_EXT_DIR)"
else
    jam "npm-registry" "$PLUGIN payload NOT installed (see cr-logs/install.log)" "check marketplace source + node/npm feed"
fi

# =========================================================================
phase 2 "duplicate the extension as a project-level source in the working repo"
# The double-discovery condition: the SAME tool names, discoverable from a
# SECOND source the session's own git root trusts. Copying the installed
# payload verbatim (not hand-authoring a facsimile) keeps the tool names and
# registration behavior byte-identical to production, so any clash is the
# real one, not an artifact of a simplified fixture.
mkdir -p "$HOME/ch-repro" && ( cd "$HOME/ch-repro" && git init -q \
    && git config user.email t@e && git config user.name t \
    && echo '# ch-repro' > README.md && git add -A && git commit -qm init )
PROJECT_EXT_DIR="$HOME/ch-repro/.github/extensions/$PLUGIN"
if [ -d "$INSTALLED_EXT_DIR" ]; then
    mkdir -p "$(dirname "$PROJECT_EXT_DIR")"
    cp -r "$INSTALLED_EXT_DIR" "$PROJECT_EXT_DIR"
    pass "duplicated $PLUGIN's extension payload into $PROJECT_EXT_DIR (project-level source)"
else
    info "phase 2 skipped: no installed payload to duplicate (see phase 1 jam)"
fi

# =========================================================================
phase 3 "run one session against BOTH sources; both extensions launch"
mkdir -p "$EXT_LOG_DIR"
BEFORE_LOGS="$CR_LOGDIR/ext-logs-before.txt"
ls -1 "$EXT_LOG_DIR" 2>/dev/null > "$BEFORE_LOGS"

PLUGIN_ARG=()
[ -d "$INSTALLED_ROOT/$PLUGIN" ] && PLUGIN_ARG=( --plugin-dir "$INSTALLED_ROOT/$PLUGIN" )
( cd "$HOME/ch-repro" && capture "session" -- copilot -p "Reply with the single word: ready." \
    --allow-all-tools "${PLUGIN_ARG[@]}" ) || true
sleep 3   # let any launched extension subprocess finish writing its own log

NEW_LOGS="$(comm -13 <(sort "$BEFORE_LOGS") <(ls -1 "$EXT_LOG_DIR" 2>/dev/null | sort) | grep -i "$PLUGIN" || true)"
NEW_LOG_COUNT="$(printf '%s\n' "$NEW_LOGS" | grep -c . || true)"
cr_meta "new_context_handoff_extension_logs" "$NEW_LOG_COUNT"
if [ "$NEW_LOG_COUNT" -ge 2 ]; then
    pass "session launched $NEW_LOG_COUNT separate $PLUGIN extension connections (installed + project source both discovered)"
elif [ "$NEW_LOG_COUNT" -eq 1 ]; then
    jam "single-discovery" "only 1 $PLUGIN extension connection was launched -- the duplicate source was not discovered as a distinct candidate" \
        "check whether project-level extension discovery requires an explicit trust/add-dir step in this CLI version"
else
    jam "no-launch" "no $PLUGIN extension logs appeared for this session at all" \
        "check cr-logs/session.log for a plugin-load failure before extensions ever start"
fi

# =========================================================================
phase 4 "assert the tool-name clash on the second (losing) connection"
CLASH_FOUND=0
if [ -n "$NEW_LOGS" ]; then
    while IFS= read -r log_name; do
        [ -n "$log_name" ] || continue
        if grep -q "already registered by another connection" "$EXT_LOG_DIR/$log_name" 2>/dev/null; then
            CLASH_FOUND=1
            info "clash confirmed in $log_name"
        fi
    done <<< "$NEW_LOGS"
fi
if [ "$CLASH_FOUND" -eq 1 ]; then
    pass "same-session double-discovery clash REPRODUCED: a second connection was rejected with 'already registered by another connection'"
else
    jam "no-repro" "no new $PLUGIN extension log contained the expected tool-name clash text" \
        "either the guard/discovery order changed upstream (good news -- update/retire this scenario), or this container's log-naming assumption drifted; inspect cr-logs and $EXT_LOG_DIR directly"
fi

cr_finalize
