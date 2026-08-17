#!/usr/bin/env bash
# agent-mcp-solo/scenario.sh -- Tier-P (programmatic) F1 solo scenario.
#
# Installs ONLY agent-mcp on a fresh box and asserts the STANDALONE MCP-wrapper
# contract. agent-mcp is invoked directly from an agent's mcp-servers config as
# a local stdio MCP wrapper around an upstream http|stdio|cli server; it imports
# no agent-bridge code, uses no resolver, and does not require a resident daemon
# (serve is optional warmth only). Installed alone it must still provision,
# expose a binstub with a real version, validate a bridge config, and return a
# clear missing-bridge error rather than a traceback.
#
# Name-free / public F1. Env: CR_MARKETPLACE_REPO / CR_MARKETPLACE_NAME /
# CR_UV_INDEX + the lib's vars. MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
UV_INDEX="${CR_UV_INDEX:-}"
PLUGIN="agent-mcp"
INSTALLED_ROOT="$HOME/.copilot/installed-plugins/$MARKETPLACE_NAME"
PLUGIN_DIR="$INSTALLED_ROOT/$PLUGIN"

: "${CR_SCENARIO_NAME:=agent-mcp-solo}"
export CR_SCENARIO_NAME
cr_init
cr_meta "plugin" "$PLUGIN"
cr_meta "base" "without-agent-bridge"
cr_meta "validates" "standalone-reachability / a-la-carte independence"

_apply_uv_index_fixture() {
    [ -n "$UV_INDEX" ] || return 0
    export UV_INDEX_URL="$UV_INDEX" UV_DEFAULT_INDEX="$UV_INDEX" UV_EXTRA_INDEX_URL="${UV_EXTRA_INDEX_URL:-$UV_INDEX}"
    mkdir -p "$HOME/.config/uv"
    printf '[[index]]\nurl = "%s"\ndefault = true\n' "$UV_INDEX" > "$HOME/.config/uv/uv.toml"
    info "uv-index fixture applied: uv -> $UV_INDEX"
}

_runtime_present() {
    [ -d "$HOME/.agent-mcp/versions" ] || [ -x "$HOME/.agent-mcp/.venv/bin/python" ]
}

_classify_provision_failure() {
    local evidence="$1"
    if [ -z "$UV_INDEX" ] && grep -qiE 'HandshakeFailure|pythonhosted|SSL|TLS|certificate|Failed to install agent-mcp package' "$CR_LOGDIR/$evidence.log" 2>/dev/null; then
        jam "toolchain-uv" "$evidence: agent-mcp runtime could not be built from the default uv index" "re-run with CR_UV_INDEX=<internal index-url>"
    else
        jam "path-binstub" "$evidence: agent-mcp runtime/binstub did not self-provision cleanly" "sessionStart should stamp ~/.local/bin/agent-mcp, or scripts/init.sh provision should build ~/.agent-mcp"
    fi
}

# =========================================================================
phase 0 "environment (fresh machine)"
envdump
if [ -d "$HOME/.agent-mcp" ] || [ -d "$HOME/.local/bin" ]; then
    fail "environment is NOT clean -- pre-existing ~/.agent-mcp or ~/.local/bin"
else
    pass "clean slate: no ~/.agent-mcp, no ~/.local/bin"
fi
if [ -d "$HOME/.agent-bridge" ]; then
    fail "environment is NOT standalone-clean -- pre-existing ~/.agent-bridge"
else
    pass "standalone-clean: no ~/.agent-bridge runtime state"
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
if [ -d "$PLUGIN_DIR" ]; then
    pass "$PLUGIN payload present on disk"
else
    jam "npm-registry" "$PLUGIN payload NOT installed (see cr-logs/install.log)" "check marketplace source + node/npm feed"
fi
if [ -d "$INSTALLED_ROOT/agent-bridge" ] || [ -d "$HOME/.agent-bridge" ]; then
    fail "agent-bridge was pulled/present, but agent-mcp must be a standalone exemplar"
    cr_meta "agent_bridge_present" "yes"
else
    pass "agent-bridge NOT installed or required -- standalone condition holds"
    cr_meta "agent_bridge_present" "no"
fi

# =========================================================================
phase 2 "runtime self-provisions"
_apply_uv_index_fixture
mkdir -p "$HOME/mcp-repo" && ( cd "$HOME/mcp-repo" && git init -q && git config user.email t@example.invalid && git config user.name test && echo '# mcp' > README.md && git add -A && git commit -qm init )
PLUGIN_ARG=()
[ -d "$PLUGIN_DIR" ] && PLUGIN_ARG=( --plugin-dir "$PLUGIN_DIR" )
( cd "$HOME/mcp-repo" && capture "session-first" -- copilot -p "Reply with the single word: ready." --allow-all-tools "${PLUGIN_ARG[@]}" ) || true
sleep 8
if bash -lc 'command -v agent-mcp >/dev/null'; then
    pass "sessionStart/login shell exposes agent-mcp binstub"
    capture "first-version-provisions" -- bash -lc 'agent-mcp --version' || true
else
    info "agent-mcp not on login-shell PATH after first session; trying installer provision for a direct runtime signal"
    if [ -f "$PLUGIN_DIR/scripts/init.sh" ]; then
        capture "installer-provision" -- bash "$PLUGIN_DIR/scripts/init.sh" provision || true
    else
        jam "path-binstub" "agent-mcp installer missing at $PLUGIN_DIR/scripts/init.sh" "plugin payload should include scripts/init.sh with provision support"
    fi
fi
if _runtime_present; then
    pass "agent-mcp runtime present (~/.agent-mcp/versions or ~/.agent-mcp/.venv)"
else
    if [ -f "$CR_LOGDIR/first-version-provisions.log" ]; then
        _classify_provision_failure "first-version-provisions"
    elif [ -f "$CR_LOGDIR/installer-provision.log" ]; then
        _classify_provision_failure "installer-provision"
    else
        jam "path-binstub" "no agent-mcp runtime and no provisioning log was produced" "ensure the session hook stamps the binstub or the installer supports provision"
    fi
fi

# =========================================================================
phase 3 "binstub on PATH + reports a REAL version"
if bash -lc 'command -v agent-mcp >/dev/null'; then
    pass "agent-mcp resolves on a fresh login-shell PATH"
else
    fail "agent-mcp NOT on login-shell PATH"
fi
_ver="$(bash -lc 'agent-mcp --version' 2>/dev/null || true)"
if [ -n "$(printf '%s' "$_ver" | tr -d ' \t\r\n')" ]; then
    pass "agent-mcp --version -> $(printf '%s' "$_ver" | head -1)"
else
    fail "agent-mcp --version printed NOTHING (unstamped build-info or provisioning defect)"
fi

# =========================================================================
phase 4 "STANDALONE: help/status/validate without sibling plugin or bridge"
if capture "agent-mcp-help" -- bash -lc 'agent-mcp --help'; then
    if [ -s "$CR_LOGDIR/agent-mcp-help.log" ]; then
        pass "agent-mcp --help exits 0 and is non-empty"
    else
        fail "agent-mcp --help exited 0 but printed nothing"
    fi
else
    jam "mcp-config" "agent-mcp --help failed (see cr-logs/agent-mcp-help.log)" "help must not require any bridge config or sibling plugin"
fi

if capture "agent-mcp-status" -- bash -lc 'agent-mcp status'; then
    pass "agent-mcp status exits 0 with no agent-bridge sibling"
else
    jam "mcp-config" "agent-mcp status failed without agent-bridge (see cr-logs/agent-mcp-status.log)" "status should only inspect local prerequisites and bridge config dirs"
fi
if grep -qiE 'Traceback|agent[_-]bridge' "$CR_LOGDIR/agent-mcp-status.log" 2>/dev/null; then
    jam "mcp-config" "agent-mcp status referenced agent-bridge or raised a traceback" "agent-mcp must stay a-la-carte and bridge-free"
else
    pass "status output has no traceback and no agent-bridge dependency signal"
fi

cat > "$HOME/sample-stdio.mcp.yaml" <<'YAML'
server:
  type: stdio
  command: ["${python}", "-c", "import sys; sys.exit(0)"]
auth:
  kind: none
YAML
if capture "validate-sample" -- bash -lc 'agent-mcp validate "$HOME/sample-stdio.mcp.yaml"'; then
    if grep -q '^OK:' "$CR_LOGDIR/validate-sample.log" 2>/dev/null; then
        pass "validate accepts a local sample stdio bridge config"
    else
        fail "validate sample exited 0 without an OK line"
    fi
else
    jam "mcp-config" "validate sample failed (see cr-logs/validate-sample.log)" "a minimal stdio bridge with auth:none should schema-check without starting upstream"
fi

if capture "validate-missing" -- bash -lc 'agent-mcp validate definitely-not-a-bridge'; then
    fail "validate unexpectedly accepted a nonexistent bridge name"
else
    if grep -qi 'Traceback' "$CR_LOGDIR/validate-missing.log" 2>/dev/null; then
        jam "mcp-config" "validate missing bridge raised a traceback" "missing bridge names should return a clean INVALID/no bridge message"
    elif grep -qiE 'INVALID:.*no bridge named|no bridge named' "$CR_LOGDIR/validate-missing.log" 2>/dev/null; then
        pass "validate nonexistent bridge returns a clear non-traceback error"
    else
        fail "validate nonexistent bridge was nonzero but not clearly explained"
    fi
fi

# =========================================================================
cr_finalize
