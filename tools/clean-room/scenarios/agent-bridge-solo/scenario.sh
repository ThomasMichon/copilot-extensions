#!/usr/bin/env bash
# agent-bridge-solo/scenario.sh -- Tier-P (programmatic) F1 solo scenario.
#
# Installs ONLY agent-bridge on a fresh box (the "without agent-worktrees base"
# degenerate case) and asserts the DEGRADE-SAFE contract: agent-bridge is
# loose-coupled to agent-worktrees (no hard `import agent_worktrees` -- it reads
# AW's data files + shells binstubs when present, and falls open when absent), so
# installed alone it must still stand up and its read verbs must answer rather
# than crash on a missing base.
#
# Name-free / public F1. Asserts on CLI/filesystem OUTCOMES, not exact spelling.
#
# Env: CR_MARKETPLACE_REPO / CR_MARKETPLACE_NAME / CR_UV_INDEX + the lib's vars.
# MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
UV_INDEX="${CR_UV_INDEX:-}"
PLUGIN="agent-bridge"
INSTALLED_ROOT="$HOME/.copilot/installed-plugins/$MARKETPLACE_NAME"

: "${CR_SCENARIO_NAME:=agent-bridge-solo}"
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
if [ -d "$HOME/.agent-bridge" ] || [ -d "$HOME/.local/bin" ]; then
    fail "environment is NOT clean -- pre-existing ~/.agent-bridge or ~/.local/bin"
else
    pass "clean slate: no ~/.agent-bridge, no ~/.local/bin"
fi

# =========================================================================
phase 1 "install ONLY $PLUGIN (no agent-worktrees base)"
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
# Record whether agent-worktrees came along (it should NOT for a true solo test).
if [ -d "$INSTALLED_ROOT/agent-worktrees" ]; then
    info "agent-worktrees payload IS present (a dependency pull) -- degrade-safe still asserted below"
    cr_meta "agent_worktrees_present" "yes"
else
    pass "agent-worktrees NOT installed -- the without-base condition holds"
    cr_meta "agent_worktrees_present" "no"
fi

# =========================================================================
phase 2 "runtime provisions on first session"
_apply_uv_index_fixture
mkdir -p "$HOME/br-repo" && ( cd "$HOME/br-repo" && git init -q && git config user.email t@e && git config user.name t && echo '# br' > README.md && git add -A && git commit -qm init )
PLUGIN_ARG=()
[ -d "$INSTALLED_ROOT/$PLUGIN" ] && PLUGIN_ARG=( --plugin-dir "$INSTALLED_ROOT/$PLUGIN" )
( cd "$HOME/br-repo" && capture "session-first" -- copilot -p "Reply with the single word: ready." --allow-all-tools "${PLUGIN_ARG[@]}" ) || true
sleep 8
if [ -d "$HOME/.agent-bridge" ] && { [ -d "$HOME/.agent-bridge/versions" ] || [ -x "$HOME/.agent-bridge/.venv/bin/python" ] || [ -e "$HOME/.local/bin/agent-bridge" ]; }; then
    pass "agent-bridge runtime deployed after first session"
else
    if [ -z "$UV_INDEX" ] && grep -qiE 'HandshakeFailure|pythonhosted|SSL|TLS|certificate' "$CR_LOGDIR/session-first.log" 2>/dev/null; then
        jam "toolchain-uv" "first session: uv could not reach its index (public PyPI TLS-blocked)" "re-run with CR_UV_INDEX=<internal index-url>"
    else
        jam "path-binstub" "agent-bridge runtime NOT deployed by first session (bootstrap-check no-op, #1236)" "first-install should deploy, not just reconcile"
    fi
fi

# =========================================================================
phase 3 "binstub on PATH + reports a REAL version"
if bash -lc 'command -v agent-bridge >/dev/null'; then
    pass "agent-bridge resolves on a fresh login-shell PATH"
else
    fail "agent-bridge NOT on login-shell PATH"
fi
_ver="$(agent-bridge --version 2>/dev/null || agent-bridge version 2>/dev/null)"
if [ -n "$(printf '%s' "$_ver" | tr -d ' \t\r\n')" ]; then
    pass "agent-bridge version -> $(printf '%s' "$_ver" | head -1)"
else
    fail "agent-bridge --version/version printed NOTHING (unstamped build-info defect)"
fi

# =========================================================================
phase 4 "DEGRADE-SAFE: read verbs answer WITHOUT an agent-worktrees base"
# agents/machines/sessions read AW's repos.yaml/related.yaml when present and must
# fall open (not crash) when absent -- the loose-coupling contract.
_ok=0
for verb in "agents" "machines" "sessions" "status"; do
    if capture "read-$verb" -- bash -lc "agent-bridge $verb"; then
        pass "agent-bridge $verb exits 0 (degrade-safe with no agent-worktrees base)"
        _ok=1
    else
        # A crash referencing agent_worktrees / repos.yaml would be a hard-dependency violation.
        if grep -qiE 'agent_worktrees|No module|repos\.yaml|related\.yaml|Traceback' "$CR_LOGDIR/read-$verb.log" 2>/dev/null; then
            jam "bridge-service" "agent-bridge $verb CRASHED referencing agent-worktrees (hard dependency; not degrade-safe)" "agent-bridge must fall open when the AW base is absent (read data files / shell binstubs, no hard import)"
        else
            info "agent-bridge $verb non-zero (see cr-logs/read-$verb.log) -- may need the service running"
        fi
    fi
done
[ $_ok -eq 1 ] || fail "no agent-bridge read verb (agents/machines/sessions/status) exited 0 without the base"

# =========================================================================
cr_finalize
