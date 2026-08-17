#!/usr/bin/env bash
# agent-logger-solo/scenario.sh -- Tier-P (programmatic) F1 solo scenario.
#
# Installs ONLY agent-logger on a fresh box and asserts the standalone contract:
# the payload lands alone, session-start stamps the self-provisioning binstub,
# first CLI use builds the runtime, --version is real/non-empty, and read/dry-run
# verbs answer without sibling plugins. Chronicle is a scheduled tick that writes
# manifests when enabled; this scenario exercises status/read surfaces and avoids
# a manifest-writing tick.
#
# Name-free / public F1. Asserts on CLI/filesystem OUTCOMES, not exact spelling.
# Env: CR_MARKETPLACE_REPO / CR_MARKETPLACE_NAME / CR_UV_INDEX + the lib's vars.
# MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
UV_INDEX="${CR_UV_INDEX:-}"
PLUGIN="agent-logger"
INSTALLED_ROOT="$HOME/.copilot/installed-plugins/$MARKETPLACE_NAME"

: "${CR_SCENARIO_NAME:=agent-logger-solo}"
export CR_SCENARIO_NAME
cr_init
cr_meta "plugin" "$PLUGIN"
cr_meta "base"   "standalone-agent-logger"

_apply_uv_index_fixture() {
    [ -n "$UV_INDEX" ] || return 0
    export UV_INDEX_URL="$UV_INDEX" UV_DEFAULT_INDEX="$UV_INDEX" UV_EXTRA_INDEX_URL="${UV_EXTRA_INDEX_URL:-$UV_INDEX}"
    mkdir -p "$HOME/.config/uv"
    printf '[[index]]\nurl = "%s"\ndefault = true\n' "$UV_INDEX" > "$HOME/.config/uv/uv.toml"
    info "uv-index fixture applied: uv -> $UV_INDEX"
}

_payload_siblings() {
    [ -d "$INSTALLED_ROOT" ] || return 0
    local d base out=""
    for d in "$INSTALLED_ROOT"/*; do
        [ -d "$d" ] || continue
        base="${d##*/}"
        [ "$base" = "$PLUGIN" ] && continue
        out="$out $base"
    done
    printf '%s' "${out# }"
}

_expect_no_sibling_payloads() {
    local siblings
    siblings="$(_payload_siblings)"
    if [ -z "$siblings" ]; then
        pass "no sibling plugin payloads under $INSTALLED_ROOT"
        cr_meta "sibling_payloads" "none"
        return 0
    fi
    fail "unexpected sibling plugin payload(s): $siblings"
    cr_meta "sibling_payloads" "$siblings"
    return 1
}

_runtime_present() {
    [ -d "$HOME/.agent-logger/versions" ] || [ -e "$HOME/.agent-logger/.venv" ]
}

_classify_provision_failure() {
    if [ -z "$UV_INDEX" ] && grep -qiE 'HandshakeFailure|pythonhosted|SSL|TLS|certificate|Could not fetch|Failed to download|uv.*(error|failed)' "$CR_LOGDIR/session-first.log" "$CR_LOGDIR/provision-version.log" 2>/dev/null; then
        jam "toolchain-uv" "agent-logger runtime did not provision because uv/index access failed (see cr-logs/provision-version.log)" "re-run with CR_UV_INDEX=<internal index-url>"
    else
        jam "path-binstub" "agent-logger runtime did not provision from the login-shell binstub (see cr-logs/session-first.log and provision-version.log)" "session-start should stamp ~/.local/bin/agent-logger; first use should run installer provision"
    fi
}

_run_read() {
    local label="$1" desc="$2"; shift 2
    if capture "$label" -- bash -lc "$*"; then
        pass "$desc exits 0 standalone"
    else
        jam "logger-config" "$desc failed standalone (see cr-logs/$label.log)" "read/idempotent verbs must not require sibling plugins or private sink configuration"
    fi
}

# =========================================================================
phase 0 "environment (fresh machine)"
envdump
if [ -d "$HOME/.agent-logger" ] || [ -d "$HOME/.local/bin" ]; then
    fail "environment is NOT clean -- pre-existing ~/.agent-logger or ~/.local/bin"
else
    pass "clean slate: no ~/.agent-logger, no ~/.local/bin"
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
_expect_no_sibling_payloads || true

# =========================================================================
phase 2 "runtime self-provisions on first use"
_apply_uv_index_fixture
mkdir -p "$HOME/logger-repo" && ( cd "$HOME/logger-repo" && git init -q && git config user.email t@e && git config user.name t && echo '# logger' > README.md && git add -A && git commit -qm init )
PLUGIN_ARG=()
[ -d "$INSTALLED_ROOT/$PLUGIN" ] && PLUGIN_ARG=( --plugin-dir "$INSTALLED_ROOT/$PLUGIN" )
( cd "$HOME/logger-repo" && capture "session-first" -- copilot -p "Reply with the single word: ready." --allow-all-tools "${PLUGIN_ARG[@]}" ) || true
sleep 8
if [ -x "$HOME/.local/bin/agent-logger" ]; then
    pass "session-start stamped ~/.local/bin/agent-logger"
else
    fail "session-start did NOT stamp ~/.local/bin/agent-logger"
fi
capture "provision-version" -- bash -lc 'agent-logger --version' || true
if _runtime_present; then
    pass "agent-logger runtime present (~/.agent-logger/versions or ~/.agent-logger/.venv)"
else
    _classify_provision_failure
fi

# =========================================================================
phase 3 "binstub on PATH + reports a REAL version"
if bash -lc 'command -v agent-logger >/dev/null'; then
    pass "agent-logger resolves on a fresh login-shell PATH"
else
    fail "agent-logger NOT on login-shell PATH"
fi
_ver="$(bash -lc 'agent-logger --version' 2>/dev/null || true)"
if printf '%s\n' "$_ver" | grep -Eq '^agent-logger [0-9]'; then
    pass "agent-logger --version -> $(printf '%s' "$_ver" | head -1)"
else
    fail "agent-logger --version printed no real version (got: $(printf '%s' "$_ver" | tr -d '\r' | head -1))"
fi

# =========================================================================
phase 4 "STANDALONE: read/dry-run verbs answer without sibling plugins"
_expect_no_sibling_payloads || true
_run_read "read-config" "agent-logger config" 'agent-logger config'
_run_read "read-organization" "agent-logger organization" 'agent-logger organization'
_run_read "read-chronicle-status" "agent-logger chronicle status" 'agent-logger chronicle status'
_run_read "read-session-sync-status" "session-sync status" 'session-sync status'
_run_read "read-session-sync-dry-run" "session-sync run --dry-run --verbose" 'session-sync run --dry-run --verbose'
_run_read "read-origin-backfill-local-dry-run" "agent-logger origin backfill-local --dry-run" 'agent-logger origin backfill-local --dry-run --source "$HOME/.copilot"'

# =========================================================================
cr_finalize
