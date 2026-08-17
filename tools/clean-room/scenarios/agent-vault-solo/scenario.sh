#!/usr/bin/env bash
# agent-vault-solo/scenario.sh -- Tier-P (programmatic) F1 solo scenario.
#
# Installs ONLY agent-vault on a fresh box and asserts its standalone contract:
# local runtime self-provisioning, POSIX vault-askpass deployment, and safe read
# behavior when no KeePass database is configured. keepassxc-cli presence is
# recorded as info only; this scenario never requires an unlocked .kdbx.
#
# Name-free / public F1. Env: CR_MARKETPLACE_REPO / CR_MARKETPLACE_NAME /
# CR_UV_INDEX + the lib's vars. MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
UV_INDEX="${CR_UV_INDEX:-}"
PLUGIN="agent-vault"
INSTALLED_ROOT="$HOME/.copilot/installed-plugins/$MARKETPLACE_NAME"

: "${CR_SCENARIO_NAME:=agent-vault-solo}"
export CR_SCENARIO_NAME
cr_init
cr_meta "plugin" "$PLUGIN"
cr_meta "base"   "standalone"

_apply_uv_index_fixture() {
    [ -n "$UV_INDEX" ] || return 0
    export UV_INDEX_URL="$UV_INDEX" UV_DEFAULT_INDEX="$UV_INDEX" UV_EXTRA_INDEX_URL="${UV_EXTRA_INDEX_URL:-$UV_INDEX}"
    mkdir -p "$HOME/.config/uv"
    printf '[[index]]
url = "%s"
default = true
' "$UV_INDEX" > "$HOME/.config/uv/uv.toml"
    info "uv-index fixture applied: uv -> $UV_INDEX"
}

_has_uv_index_jam() {
    grep -qiE 'HandshakeFailure|pythonhosted|SSL|TLS|self.signed|certificate|No solution found|failed to download'         "$CR_LOGDIR/session-first.log" "$CR_LOGDIR/first-use-vault-list.log" 2>/dev/null
}

_clean_vault_message() {
    local log="$1"
    grep -qiE 'KeePass database path is not configured|set KPDB|Vault locked|vault unavailable|No named vaults configured|Vault service not running|service unreachable|could not start vault service|CLI not available or vault locked|cache:|vault:' "$log" 2>/dev/null
}

_crashy_message() {
    local log="$1"
    grep -qiE 'Traceback|ModuleNotFoundError|No module named|AttributeError|Unhandled|Exception:' "$log" 2>/dev/null
}

_run_read_verb() {
    local label="$1" display="$2" cmd="$3" log="$CR_LOGDIR/$label.log"
    if capture "$label" -- bash -lc "VAULT_NONINTERACTIVE=1 $cmd"; then
        pass "$display exits 0 without a configured .kdbx"
        return 0
    fi
    if _crashy_message "$log"; then
        jam "vault-config" "$display crashed solo without a configured .kdbx (see cr-logs/$label.log)" "read/idempotent verbs must report no vault configured or locked, not traceback"
        return 1
    fi
    if _clean_vault_message "$log"; then
        pass "$display reports a clean no-vault-configured/locked condition"
        return 0
    fi
    jam "vault-config" "$display failed with an unclear error without a configured .kdbx (see cr-logs/$label.log)" "return a clean no-vault-configured/locked message, or exit 0 for config-only reads"
    return 1
}

# =========================================================================
phase 0 "environment (fresh machine)"
envdump
if [ -d "$HOME/.agent-vault" ] || [ -d "$HOME/.local/bin" ]; then
    fail "environment is NOT clean -- pre-existing ~/.agent-vault or ~/.local/bin"
else
    pass "clean slate: no ~/.agent-vault, no ~/.local/bin"
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
if command -v keepassxc-cli >/dev/null 2>&1; then
    _kp="$(command -v keepassxc-cli)"
    info "keepassxc-cli present: $_kp"
    cr_meta "keepassxc_cli_present" "yes"
else
    info "keepassxc-cli absent; continuing because solo validation avoids real secret operations"
    cr_meta "keepassxc_cli_present" "no"
fi

# =========================================================================
phase 2 "runtime self-provisions through session hook + first-use binstub"
_apply_uv_index_fixture
mkdir -p "$HOME/vault-repo" && ( cd "$HOME/vault-repo" && git init -q && git config user.email t@e && git config user.name t && echo '# vault' > README.md && git add -A && git commit -qm init )
PLUGIN_ARG=()
[ -d "$INSTALLED_ROOT/$PLUGIN" ] && PLUGIN_ARG=( --plugin-dir "$INSTALLED_ROOT/$PLUGIN" )
( cd "$HOME/vault-repo" && capture "session-first" -- copilot -p "Reply with the single word: ready." --allow-all-tools "${PLUGIN_ARG[@]}" ) || true
sleep 8
if bash -lc 'command -v agent-vault >/dev/null'; then
    pass "agent-vault binstub resolves after the session hook"
    capture "first-use-vault-list" -- bash -lc 'VAULT_NONINTERACTIVE=1 agent-vault vault list' || true
else
    jam "path-binstub" "agent-vault binstub unavailable on a fresh login-shell PATH after first session" "sessionStart bootstrap should stamp ~/.local/bin/agent-vault"
fi
if [ -d "$HOME/.agent-vault" ] && { [ -d "$HOME/.agent-vault/versions" ] || [ -x "$HOME/.agent-vault/.venv/bin/python" ]; }; then
    pass "agent-vault runtime provisioned (~/.agent-vault/versions or ~/.venv present)"
else
    if [ -z "$UV_INDEX" ] && _has_uv_index_jam; then
        jam "toolchain-uv" "runtime not provisioned: uv could not reach its index (public PyPI TLS-blocked)" "re-run with CR_UV_INDEX=<internal index-url>"
    else
        jam "path-binstub" "agent-vault runtime NOT provisioned by session hook + first-use binstub" "the stamped binstub should run scripts/install.sh provision on first use"
    fi
fi

# =========================================================================
phase 3 "binstub on PATH + real --version + vault-askpass"
if bash -lc 'command -v agent-vault >/dev/null'; then
    pass "agent-vault resolves on a fresh login-shell PATH"
else
    jam "path-binstub" "agent-vault NOT on a fresh login-shell PATH" "install/provision must deploy ~/.local/bin/agent-vault"
fi
if capture "version" -- bash -lc 'agent-vault --version'; then
    _ver="$(grep -Eo '[0-9]+\.[0-9]+\.[0-9]+[^[:space:]]*' "$CR_LOGDIR/version.log" | head -n1)"
    if [ -n "$_ver" ]; then
        pass "agent-vault --version -> $_ver"
    else
        jam "path-binstub" "agent-vault --version exited 0 but printed no real version (see cr-logs/version.log)" "expose the installed package version, not empty output"
    fi
else
    jam "path-binstub" "agent-vault --version failed (see cr-logs/version.log)" "the binstub should dispatch to a CLI that supports a real --version"
fi
if [ -x "$HOME/.local/bin/vault-askpass" ]; then
    pass "vault-askpass helper deployed at ~/.local/bin/vault-askpass"
else
    jam "path-binstub" "vault-askpass helper missing at ~/.local/bin/vault-askpass" "scripts/install.sh provision should write the SUDO_ASKPASS helper"
fi
if bash -lc 'command -v vault-askpass >/dev/null'; then
    pass "vault-askpass resolves on a fresh login-shell PATH"
else
    jam "path-binstub" "vault-askpass NOT on a fresh login-shell PATH" "~/.local/bin should be in the login-shell PATH and contain vault-askpass"
fi

# =========================================================================
phase 4 "STANDALONE read verbs degrade cleanly without a configured .kdbx"
_run_read_verb "read-vault-list" "agent-vault vault list" "agent-vault vault list"
_run_read_verb "read-which-json" "agent-vault which --json" "agent-vault which --json"
_run_read_verb "read-cache-status-json" "agent-vault cache-status --json" "agent-vault cache-status --json"
_run_read_verb "read-ping" "agent-vault ping" "agent-vault ping"
_run_read_verb "read-list-root" "agent-vault list /" "agent-vault list /"

# =========================================================================
cr_finalize
