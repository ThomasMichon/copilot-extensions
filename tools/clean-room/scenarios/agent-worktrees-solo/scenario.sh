#!/usr/bin/env bash
# agent-worktrees-solo/scenario.sh -- Tier-P (programmatic) F1 solo scenario.
#
# Installs ONLY agent-worktrees on a fresh box and asserts the WORKTREE BASE
# itself stands up self-sufficiently and its CLI surface actually works: the
# first session stamps the binstub, first use provisions the runtime, the binstub
# is on PATH and reports a real version, its read verbs enumerate, and a worktree ROUND-TRIPS
# (register -> create -> finalize). agent-worktrees is the base other plugins
# degrade against, so this is the anchor of the P1 solo set.
#
# Name-free (public F1): the plugin + marketplace are the public suite. Asserts
# on filesystem/CLI OUTCOMES, not exact subcommand spelling, so it stays robust
# across copilot/plugin versions.
#
# Env: CR_MARKETPLACE_REPO (default ThomasMichon/copilot-extensions)
#      CR_MARKETPLACE_NAME (default copilot-extensions)
#      CR_UV_INDEX (opt-in uv-index fixture; see the lib/generic scenario)
#      + the lib's CR_REPORT / CR_LOGDIR / CR_UNTIL / CR_SCENARIO_NAME.
#
# MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
UV_INDEX="${CR_UV_INDEX:-}"
PLUGIN="agent-worktrees"
INSTALLED_ROOT="$HOME/.copilot/installed-plugins/$MARKETPLACE_NAME"
if [ -d "$MARKETPLACE_REPO" ]; then
    MARKETPLACE_SOURCE="{ \"source\": { \"source\": \"directory\", \"path\": \"$MARKETPLACE_REPO\" } }"
else
    MARKETPLACE_SOURCE="{ \"source\": { \"source\": \"github\", \"repo\": \"$MARKETPLACE_REPO\" } }"
fi

: "${CR_SCENARIO_NAME:=agent-worktrees-solo}"
export CR_SCENARIO_NAME
cr_init
cr_meta "plugin" "$PLUGIN"

_apply_uv_index_fixture() {
    [ -n "$UV_INDEX" ] || return 0
    export UV_INDEX_URL="$UV_INDEX" UV_DEFAULT_INDEX="$UV_INDEX" UV_EXTRA_INDEX_URL="${UV_EXTRA_INDEX_URL:-$UV_INDEX}"
    mkdir -p "$HOME/.config/uv"
    printf '[[index]]\nurl = "%s"\ndefault = true\n' "$UV_INDEX" > "$HOME/.config/uv/uv.toml"
    info "uv-index fixture applied: uv -> $UV_INDEX"
}

# =========================================================================
phase 0 "environment (fresh machine)"
envdump
if [ -d "$HOME/.agent-worktrees" ] || [ -d "$HOME/.local/bin" ]; then
    fail "environment is NOT clean -- pre-existing ~/.agent-worktrees or ~/.local/bin"
else
    pass "clean slate: no ~/.agent-worktrees, no ~/.local/bin"
fi

# =========================================================================
phase 1 "install ONLY $PLUGIN"
mkdir -p "$HOME/.copilot"
cat > "$HOME/.copilot/settings.json" <<JSON
{
  "extraKnownMarketplaces": { "$MARKETPLACE_NAME": $MARKETPLACE_SOURCE },
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
phase 2 "first session stamps binstub; first use provisions runtime"
_apply_uv_index_fixture
mkdir -p "$HOME/wt-repo" && ( cd "$HOME/wt-repo" && git init -q && git config user.email t@e && git config user.name t && echo '# wt' > README.md && git add -A && git commit -qm init )
PLUGIN_ARG=()
[ -d "$INSTALLED_ROOT/$PLUGIN" ] && PLUGIN_ARG=( --plugin-dir "$INSTALLED_ROOT/$PLUGIN" )
( cd "$HOME/wt-repo" && capture "session-first" -- copilot -p "Reply with the single word: ready." --allow-all-tools "${PLUGIN_ARG[@]}" ) || true
if [ -e "$HOME/.local/bin/agent-worktrees" ]; then
    pass "first session stamped ~/.local/bin/agent-worktrees"
    if capture "first-use-version" -- bash -lc 'agent-worktrees --version'; then
        first_use_ok=1
    else
        first_use_ok=0
        jam "path-binstub" "first binstub use failed (see cr-logs/first-use-version.log)" "stamped binstub should invoke install provision and then dispatch"
    fi
else
    first_use_ok=0
    jam "path-binstub" "first session did not stamp ~/.local/bin/agent-worktrees" "payload bootstrap-check should run install.sh stamp"
fi
sleep 3
_current="$(tr -d ' \t\r\n' < "$HOME/.agent-worktrees/current-version" 2>/dev/null || true)"
_slot="$HOME/.agent-worktrees/versions/$_current"
if [ "$first_use_ok" -eq 1 ] && [ -n "$_current" ] && [ -x "$_slot/bin/python" ] && [ -f "$_slot/.install-complete.json" ]; then
    pass "agent-worktrees runtime deployed on first binstub use"
else
    if [ -z "$UV_INDEX" ] && grep -qiE 'HandshakeFailure|pythonhosted|SSL|TLS|certificate' "$CR_LOGDIR/first-use-version.log" 2>/dev/null; then
        jam "toolchain-uv" "first use: uv could not reach its index (public PyPI TLS-blocked)" "re-run with CR_UV_INDEX=<internal index-url>"
    else
        jam "path-binstub" "runtime NOT deployed by first binstub use (see cr-logs/first-use-version.log)" "stamped binstub should invoke install provision"
    fi
fi

# =========================================================================
phase 3 "binstub on PATH + reports a REAL version"
if bash -lc 'command -v agent-worktrees >/dev/null'; then
    pass "agent-worktrees resolves on a fresh login-shell PATH"
else
    fail "agent-worktrees NOT on login-shell PATH (~/.local/bin not exported at login)"
fi
_ver="$(bash -lc 'agent-worktrees --version' 2>/dev/null)"
if [ -n "$(printf '%s' "$_ver" | tr -d ' \t\r\n')" ]; then
    pass "agent-worktrees --version -> $(printf '%s' "$_ver" | head -1)"
else
    fail "agent-worktrees --version printed NOTHING (unstamped build-info defect)"
fi

# =========================================================================
phase 4 "read verbs enumerate (repos / projects / list)"
if capture "read-repos-list" -- bash -lc "agent-worktrees repos list"; then
    pass "agent-worktrees repos list exits 0"
else
    fail "agent-worktrees repos list failed (see cr-logs/read-repos-list.log)"
fi
for probe in help version; do
    if [ "$probe" = help ]; then verb="--help"; else verb="--version"; fi
    if capture "read-$probe" -- bash -lc "agent-worktrees $verb"; then
        pass "agent-worktrees $verb exits 0"
    else
        fail "agent-worktrees $verb failed (see cr-logs/read-$probe.log)"
    fi
done

# =========================================================================
phase 5 "worktree round-trips (register -> create -> finalize)"
if bash -lc 'command -v agent-worktrees >/dev/null'; then
    capture "register" -- bash -lc "cd '$HOME/wt-repo' && agent-worktrees register wt-repo" || true
    if [ -f "$HOME/.agent-worktrees/projects.yaml" ] && grep -qi wt-repo "$HOME/.agent-worktrees/projects.yaml" 2>/dev/null; then
        pass "register: wt-repo recognized (projects.yaml written)"
    else
        fail "register: no projects.yaml entry for wt-repo"
    fi
    # create (programmatic, no launch) -> capture the id -> finalize it.
    capture "create" -- bash -lc "cd '$HOME/wt-repo' && agent-worktrees create --json" || true
    _wt_id="$(grep -oE '"id"[[:space:]]*:[[:space:]]*"[^"]+"' "$CR_LOGDIR/create.log" 2>/dev/null | head -1 | sed -E 's/.*"id"[^"]*"([^"]+)".*/\1/')"
    if [ -n "$_wt_id" ]; then
        pass "create: worktree carved ($_wt_id)"
        capture "finalize" -- bash -lc "cd '$HOME/wt-repo' && agent-worktrees finalize '$_wt_id' --json" || true
        if grep -qiE 'finaliz|prune|safe to' "$CR_LOGDIR/finalize.log" 2>/dev/null; then
            pass "finalize: $_wt_id round-tripped (create -> finalize)"
        else
            fail "finalize: $_wt_id did not finalize cleanly (see cr-logs/finalize.log)"
        fi
    else
        jam "repo-config" "create: no worktree id returned (see cr-logs/create.log)" "create --json should print the new worktree id + path without launching"
    fi
else
    jam "path-binstub" "cannot round-trip: agent-worktrees binstub unavailable (see Phase 2/3)" "provision the runtime first"
fi

# =========================================================================
cr_finalize
