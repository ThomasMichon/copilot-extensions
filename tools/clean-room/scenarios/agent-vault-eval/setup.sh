#!/usr/bin/env bash
# agent-vault-eval/setup.sh -- establish the STARTING STATE for the Tier-E eval.
#
# This is SETUP, not the thing under test: it only ARRANGES the box (installs
# agent-vault solo and first-session-provisions it, no .kdbx configured) so the
# driven agent then faces a real "set it up from the docs and list my vault"
# task. Its phases are setup TELEMETRY (pass/info), never the eval verdict --
# the verdict comes from the driven-agent transcript + clean-room-judge.
#
# Sources the shared lib for uniform legibility. MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
UV_INDEX="${CR_UV_INDEX:-}"
PLUGIN="agent-vault"
INSTALLED_ROOT="$HOME/.copilot/installed-plugins/$MARKETPLACE_NAME"

: "${CR_SCENARIO_NAME:=agent-vault-eval}"
export CR_SCENARIO_NAME
cr_init
cr_meta "plugin" "$PLUGIN"
cr_meta "role"   "starting-state-setup"

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
if [ -d "$HOME/.agent-vault" ] || [ -d "$HOME/.local/bin" ]; then
    info "pre-existing ~/.agent-vault or ~/.local/bin (box not pristine, continuing)"
else
    pass "clean slate: no ~/.agent-vault, no ~/.local/bin"
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
phase 2 "first-session provision (binstub on PATH; NO .kdbx)"
_apply_uv_index_fixture
PLUGIN_ARG=()
[ -d "$INSTALLED_ROOT/$PLUGIN" ] && PLUGIN_ARG=( --plugin-dir "$INSTALLED_ROOT/$PLUGIN" )
capture "session-provision" -- copilot -p "Reply with the single word: ready." --allow-all-tools "${PLUGIN_ARG[@]}" || true
sleep 6
# Trigger first-use so the binstub self-provisions the runtime, if the session
# hook alone did not. (This is setup: we want the binstub callable so the eval
# audits USE-from-docs, not the provision mechanism -- that is agent-vault-solo.)
capture "first-use" -- bash -lc 'VAULT_NONINTERACTIVE=1 agent-vault vault list' || true
if bash -lc 'command -v agent-vault >/dev/null'; then
    pass "agent-vault binstub resolves on a fresh login-shell PATH"
else
    jam "path-binstub" "agent-vault binstub NOT on PATH after provision" "see agent-vault-solo (#649) -- setup cannot proceed"
fi
if [ ! -e "$HOME/.config/agent-vault" ] && [ -z "${KPDB:-}" ]; then
    pass "no .kdbx configured (the intended starting state for the eval)"
else
    info "a vault/KPDB may be present -- the eval expects NONE configured"
fi

# =========================================================================
cr_finalize
