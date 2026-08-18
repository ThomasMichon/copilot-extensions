#!/usr/bin/env bash
# suite-assembly-eval/setup.sh -- establish the STARTING STATE for the F1-E eval.
#
# Installs the copilot-extensions HARNESS CORE (agent-worktrees base + agent-bridge)
# and first-session-provisions it (binstubs on PATH), and seeds a throwaway git
# repo at /home/operator/demo-repo. It does NOT register the repo or create a
# worktree -- that assembly is what the driven agent must do from the docs.
#
# SETUP, not the thing under test: its phases are telemetry (pass/info), never
# the eval verdict. MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
UV_INDEX="${CR_UV_INDEX:-}"
INSTALLED_ROOT="$HOME/.copilot/installed-plugins/$MARKETPLACE_NAME"
DEMO_REPO="$HOME/demo-repo"

: "${CR_SCENARIO_NAME:=suite-assembly-eval}"
export CR_SCENARIO_NAME
cr_init
cr_meta "plugins" "agent-worktrees,agent-bridge"
cr_meta "role"    "starting-state-setup"

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
    info "pre-existing ~/.agent-worktrees or ~/.local/bin (box not pristine, continuing)"
else
    pass "clean slate: no ~/.agent-worktrees, no ~/.local/bin"
fi

# =========================================================================
phase 1 "install the harness core (agent-worktrees base + agent-bridge)"
mkdir -p "$HOME/.copilot"
cat > "$HOME/.copilot/settings.json" <<JSON
{
  "extraKnownMarketplaces": { "$MARKETPLACE_NAME": { "source": { "source": "github", "repo": "$MARKETPLACE_REPO" } } },
  "enabledPlugins": {
    "agent-worktrees@$MARKETPLACE_NAME": true,
    "agent-bridge@$MARKETPLACE_NAME": true
  }
}
JSON
capture "marketplace-add" -- copilot plugin marketplace add "$MARKETPLACE_REPO" || true
capture "install-worktrees" -- copilot plugin install "agent-worktrees@$MARKETPLACE_NAME" || true
capture "install-bridge"    -- copilot plugin install "agent-bridge@$MARKETPLACE_NAME" || true
_ok=1
[ -d "$INSTALLED_ROOT/agent-worktrees" ] && pass "agent-worktrees payload present" || { _ok=0; jam "npm-registry" "agent-worktrees payload NOT installed (see cr-logs/install-worktrees.log)" "check marketplace source + node/npm feed"; }
[ -d "$INSTALLED_ROOT/agent-bridge" ]    && pass "agent-bridge payload present"    || { _ok=0; jam "npm-registry" "agent-bridge payload NOT installed (see cr-logs/install-bridge.log)" "check marketplace source + node/npm feed"; }

# =========================================================================
phase 2 "first session (agent-bridge self-provisions; agent-worktrees awaits setup)"
_apply_uv_index_fixture
PLUGIN_ARG=()
[ -d "$INSTALLED_ROOT/agent-worktrees" ] && PLUGIN_ARG=( --plugin-dir "$INSTALLED_ROOT/agent-worktrees" )
[ -d "$INSTALLED_ROOT/agent-bridge" ]    && PLUGIN_ARG+=( --plugin-dir "$INSTALLED_ROOT/agent-bridge" )
capture "session-provision" -- copilot -p "Reply with the single word: ready." --allow-all-tools "${PLUGIN_ARG[@]}" || true
sleep 8
capture "bridge-first-use" -- bash -lc 'agent-bridge --version' || true
if bash -lc 'command -v agent-bridge >/dev/null'; then
    pass "agent-bridge binstub self-provisioned onto PATH"
else
    info "agent-bridge binstub not on PATH yet (the agent may need to provision it)"
fi
# agent-worktrees does NOT self-provision on first session (issue #691) -- it
# guides an explicit setup. Its binstub being absent here is the INTENDED starting
# state for this assembly eval (the agent must run the documented setup), NOT a jam.
if bash -lc 'command -v agent-worktrees >/dev/null'; then
    info "agent-worktrees binstub already on PATH (unexpected but fine)"
    cr_meta "wt_preprovisioned" "yes"
else
    info "agent-worktrees binstub NOT on PATH -- the intended starting state (awaits documented setup; #691)"
    cr_meta "wt_preprovisioned" "no"
fi

# =========================================================================
phase 3 "seed a throwaway git repo (NOT registered)"
if [ ! -d "$DEMO_REPO/.git" ]; then
    mkdir -p "$DEMO_REPO"
    ( cd "$DEMO_REPO" && git init -q && git config user.email t@e && git config user.name t && echo '# demo' > README.md && git add -A && git commit -qm init )
fi
[ -d "$DEMO_REPO/.git" ] && pass "demo git repo seeded at $DEMO_REPO (unregistered)" || jam "repo-config" "failed to seed $DEMO_REPO" "git init failed"
if [ -f "$HOME/.agent-worktrees/projects.yaml" ] && grep -qi 'demo-repo' "$HOME/.agent-worktrees/projects.yaml" 2>/dev/null; then
    info "demo-repo already registered -- the eval expects it UNREGISTERED"
else
    pass "demo-repo not yet registered (the intended starting state)"
fi

# =========================================================================
cr_finalize
