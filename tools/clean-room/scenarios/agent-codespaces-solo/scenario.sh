#!/usr/bin/env bash
# agent-codespaces-solo/scenario.sh -- Tier-P (programmatic) F1 solo scenario.
#
# Installs ONLY agent-codespaces on a fresh box and asserts its DEGRADE-SAFE
# contract. agent-codespaces shells the agent-worktrees binstub at every
# AW-touch point (account map, L2 leases, worktree claims, --project, state-root)
# and is degrade-safe by construction: those shell-outs FALL OPEN to ambient
# behavior when agent-worktrees is absent (resolve_owner_worktree / gh_account /
# coordination fail-open). So installed alone it must still provision, expose a
# binstub with a real version, and answer its read verbs (list/status/leases/
# validate) rather than hard-fail on a missing base.
#
# This is the PROGRAMMATIC (Tier-P) install/degrade-safe scenario -- distinct from
# the downstream Tier-E *capability* eval of the same name (the 6-step CodeSpace
# workflow), which needs a live CodeSpace + agent and is judged.
#
# Name-free / public F1. Env: CR_MARKETPLACE_REPO / CR_MARKETPLACE_NAME /
# CR_UV_INDEX + the lib's vars. MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
UV_INDEX="${CR_UV_INDEX:-}"
PLUGIN="agent-codespaces"
INSTALLED_ROOT="$HOME/.copilot/installed-plugins/$MARKETPLACE_NAME"

: "${CR_SCENARIO_NAME:=agent-codespaces-solo}"
export CR_SCENARIO_NAME
cr_init
cr_meta "plugin" "$PLUGIN"
cr_meta "base"   "without-agent-worktrees"

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
if [ -d "$HOME/.agent-codespaces" ] || [ -d "$HOME/.local/bin" ]; then
    fail "environment is NOT clean -- pre-existing ~/.agent-codespaces or ~/.local/bin"
else
    pass "clean slate: no ~/.agent-codespaces, no ~/.local/bin"
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
if [ -d "$INSTALLED_ROOT/agent-worktrees" ]; then
    info "agent-worktrees payload IS present (dependency pull) -- degrade-safe still asserted below"
    cr_meta "agent_worktrees_present" "yes"
else
    pass "agent-worktrees NOT installed -- the without-base condition holds"
    cr_meta "agent_worktrees_present" "no"
fi

# =========================================================================
phase 2 "runtime provisions on first session"
_apply_uv_index_fixture
mkdir -p "$HOME/cs-repo" && ( cd "$HOME/cs-repo" && git init -q && git config user.email t@e && git config user.name t && echo '# cs' > README.md && git add -A && git commit -qm init )
PLUGIN_ARG=()
[ -d "$INSTALLED_ROOT/$PLUGIN" ] && PLUGIN_ARG=( --plugin-dir "$INSTALLED_ROOT/$PLUGIN" )
( cd "$HOME/cs-repo" && capture "session-first" -- copilot -p "Reply with the single word: ready." --allow-all-tools "${PLUGIN_ARG[@]}" ) || true
sleep 8
if [ -d "$HOME/.agent-codespaces" ] && { [ -d "$HOME/.agent-codespaces/versions" ] || [ -x "$HOME/.agent-codespaces/.venv/bin/python" ] || [ -e "$HOME/.local/bin/agent-codespaces" ]; }; then
    pass "agent-codespaces runtime deployed after first session"
else
    if [ -z "$UV_INDEX" ] && grep -qiE 'HandshakeFailure|pythonhosted|SSL|TLS|certificate' "$CR_LOGDIR/session-first.log" 2>/dev/null; then
        jam "toolchain-uv" "first session: uv could not reach its index (public PyPI TLS-blocked)" "re-run with CR_UV_INDEX=<internal index-url>"
    else
        jam "path-binstub" "agent-codespaces runtime NOT deployed by first session (bootstrap-check no-op, #1236)" "first-install should deploy, not just reconcile"
    fi
fi

# =========================================================================
phase 3 "binstub on PATH + reports a REAL version"
if bash -lc 'command -v agent-codespaces >/dev/null'; then
    pass "agent-codespaces resolves on a fresh login-shell PATH"
else
    fail "agent-codespaces NOT on login-shell PATH"
fi
_ver="$(agent-codespaces --version 2>/dev/null || agent-codespaces version 2>/dev/null)"
if [ -n "$(printf '%s' "$_ver" | tr -d ' \t\r\n')" ]; then
    pass "agent-codespaces version -> $(printf '%s' "$_ver" | head -1)"
else
    fail "agent-codespaces --version/version printed NOTHING (unstamped build-info defect)"
fi

# =========================================================================
phase 4 "DEGRADE-SAFE: read verbs fall open WITHOUT an agent-worktrees base"
# list/status/leases/validate touch AW (account map / leases / state-root) and
# must FALL OPEN to ambient behavior when AW is absent, not hard-fail.
_ok=0
for verb in "list" "status" "leases" "validate"; do
    if capture "read-$verb" -- bash -lc "agent-codespaces $verb"; then
        pass "agent-codespaces $verb exits 0 (degrade-safe with no agent-worktrees base)"
        _ok=1
    else
        if grep -qiE 'No module|Traceback|resolve_owner_worktree|repos\.yaml' "$CR_LOGDIR/read-$verb.log" 2>/dev/null; then
            jam "codespace-config" "agent-codespaces $verb CRASHED on a missing agent-worktrees base (not fail-open)" "AW touch points (account map/leases/state-root) must fall open to ambient behavior when AW is absent"
        else
            info "agent-codespaces $verb non-zero (see cr-logs/read-$verb.log) -- may need gh auth / a real venue"
        fi
    fi
done
[ $_ok -eq 1 ] || fail "no agent-codespaces read verb (list/status/leases/validate) exited 0 without the base"

# =========================================================================
cr_finalize
