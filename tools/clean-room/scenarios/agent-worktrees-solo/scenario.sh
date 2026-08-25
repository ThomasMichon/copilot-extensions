#!/usr/bin/env bash
# agent-worktrees-solo/scenario.sh -- Tier-P (programmatic) F1 solo scenario.
#
# Installs ONLY agent-worktrees on a fresh box and asserts the WORKTREE BASE
# itself stands up self-sufficiently and its CLI surface actually works: the
# runtime provisions on first session, the binstub is on PATH and reports a real
# version, its read verbs enumerate, and a worktree ROUND-TRIPS
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
phase 2 "runtime self-provisions on first session / first use (stamp + venv)"
_apply_uv_index_fixture
mkdir -p "$HOME/wt-repo" && ( cd "$HOME/wt-repo" && git init -q && git config user.email t@e && git config user.name t && echo '# wt' > README.md && git add -A && git commit -qm init )
PLUGIN_ARG=()
[ -d "$INSTALLED_ROOT/$PLUGIN" ] && PLUGIN_ARG=( --plugin-dir "$INSTALLED_ROOT/$PLUGIN" )
( cd "$HOME/wt-repo" && capture "session-first" -- copilot -p "Reply with the single word: ready." --allow-all-tools "${PLUGIN_ARG[@]}" ) || true
sleep 8
# The versioned venv is deliberately DEFERRED to the tool binstub's first use
# (#1393): a first session STAMPS the self-provisioning ~/.local/bin/agent-worktrees
# binstub (grace-window-cheap, no venv build), and the binstub builds the venv on
# first invocation. So assert the stamp landed, then trigger first use and assert
# the runtime -- mirroring agent-ssh-solo / agent-machines-solo.
_aw_runtime_ready() { [ -d "$HOME/.agent-worktrees/versions" ] || [ -x "$HOME/.agent-worktrees/.venv/bin/python" ]; }
if [ -e "$HOME/.local/bin/agent-worktrees" ]; then
    pass "session-start stamped the self-provisioning binstub (~/.local/bin/agent-worktrees)"
else
    info "session-start did not stamp a binstub yet; still probing for a runtime"
fi
if ! _aw_runtime_ready && bash -lc 'command -v agent-worktrees >/dev/null'; then
    info "runtime not built yet -- triggering the binstub's first-use provision"
    capture "binstub-first-use" -- bash -lc 'agent-worktrees --version' || true
    sleep 3
fi
if _aw_runtime_ready; then
    pass "agent-worktrees runtime deployed after first session / first use"
else
    if [ -z "$UV_INDEX" ] && grep -qiE 'HandshakeFailure|pythonhosted|SSL|TLS|certificate' "$CR_LOGDIR/session-first.log" "$CR_LOGDIR/binstub-first-use.log" 2>/dev/null; then
        jam "toolchain-uv" "first session/use: uv could not reach its index (public PyPI TLS-blocked)" "re-run with CR_UV_INDEX=<internal index-url>"
    else
        jam "path-binstub" "runtime NOT deployed by first session or first-use binstub (#1236)" "first-install should stamp the binstub + self-provision on first use"
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
phase 4 "read verbs enumerate (repos list / projects / list)"
# Invoke through a login shell: the tool binstub lives in ~/.local/bin, which is
# on the login PATH (as an agent's shell-outs see it) but NOT a bare exec PATH.
_read_ok=0
for verb in "repos list" "projects" "list"; do
    _label="read-$(printf '%s' "$verb" | tr ' ' '-')"
    if capture "$_label" -- bash -lc "agent-worktrees $verb"; then
        pass "agent-worktrees $verb exits 0"
        _read_ok=1
    else
        info "agent-worktrees $verb non-zero (see cr-logs/$_label.log)"
    fi
done
[ $_read_ok -eq 1 ] || fail "no agent-worktrees read verb (repos list/projects/list) exited 0"

# =========================================================================
phase 5 "worktree round-trips (register -> create -> finalize)"
if bash -lc 'command -v agent-worktrees >/dev/null'; then
    ( cd "$HOME/wt-repo" && capture "register" -- bash -lc 'agent-worktrees register wt-repo' ) || true
    if [ -f "$HOME/.agent-worktrees/projects.yaml" ] && grep -qi wt-repo "$HOME/.agent-worktrees/projects.yaml" 2>/dev/null; then
        pass "register: wt-repo recognized (projects.yaml written)"
    else
        fail "register: no projects.yaml entry for wt-repo"
    fi
    # create (programmatic, no launch) -> capture the id -> finalize it.
    ( cd "$HOME/wt-repo" && capture "create" -- bash -lc 'agent-worktrees create --json' ) || true
    _wt_id="$(grep -oE '"id"[[:space:]]*:[[:space:]]*"[^"]+"' "$CR_LOGDIR/create.log" 2>/dev/null | head -1 | sed -E 's/.*"id"[^"]*"([^"]+)".*/\1/')"
    if [ -n "$_wt_id" ]; then
        pass "create: worktree carved ($_wt_id)"
        ( cd "$HOME/wt-repo" && capture "finalize" -- bash -lc "agent-worktrees finalize '$_wt_id' --json" ) || true
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
