#!/usr/bin/env bash
# agent-mcp-multiplexer/scenario.sh -- Tier-P F1 process-topology scenario.
#
# Validates the #744 multiplexer's actual process TOPOLOGY on a fresh box: N
# concurrent `agent-mcp forward` sessions against one stdio-echo upstream
# collapse onto exactly one resident `serve` host plus N thin forwarders --
# never N heavy `bridge` interpreters -- while each session still answers
# `initialize`/`tools/list` correctly through the multiplexer. Also asserts the
# always-optional direct-bridge fallback (`AGENT_MCP_NO_MULTIPLEX=1` spawns
# zero resident hosts) and the host's bounded idle self-eviction.
#
# FIDELITY: the RAM footprint delta is already proven by
# `plugins/agent-mcp/examples/multiplexer_ab.py` (a Linux /proc A/B). This
# scenario proves the discrete TOPOLOGY invariant that RAM win depends on
# (one host, not N), plus the fallback and eviction properties, deterministically.
# The heavy orchestration lives in a portable stdlib-only probe
# (fixtures/multiplexer_topology_probe.py) so it is verifiable off-Docker too.
#
# Name-free / public F1. Env: CR_MARKETPLACE_REPO / CR_MARKETPLACE_NAME /
# CR_UV_INDEX / CR_MULTIPLEXER_CHECKS. MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
UV_INDEX="${CR_UV_INDEX:-}"
CHECKS="${CR_MULTIPLEXER_CHECKS:-topology-collapse,direct-fallback,idle-self-eviction}"
PLUGIN="agent-mcp"
INSTALLED_ROOT="$HOME/.copilot/installed-plugins/$MARKETPLACE_NAME"
PLUGIN_DIR="$INSTALLED_ROOT/$PLUGIN"

: "${CR_SCENARIO_NAME:=agent-mcp-multiplexer}"
export CR_SCENARIO_NAME
cr_init
cr_meta "plugin" "$PLUGIN"
cr_meta "base" "without-agent-bridge"
cr_meta "validates" "work-coalescing-singleton / graceful-composition (#744 multiplexer topology)"

_apply_uv_index_fixture() {
    [ -n "$UV_INDEX" ] || return 0
    export UV_INDEX_URL="$UV_INDEX" UV_DEFAULT_INDEX="$UV_INDEX" UV_EXTRA_INDEX_URL="${UV_EXTRA_INDEX_URL:-$UV_INDEX}"
    mkdir -p "$HOME/.config/uv"
    printf '[[index]]\nurl = "%s"\ndefault = true\n' "$UV_INDEX" > "$HOME/.config/uv/uv.toml"
    info "uv-index fixture applied: uv -> $UV_INDEX"
}

# Resolve the built versioned-slot python (the multiplexer host/forwarders run
# from the immutable slot). Prefer the current-version marker; else newest slot.
_resolve_slot_python() {
    local root="$HOME/.agent-mcp" ver="" p=""
    [ -f "$root/current-version" ] && ver="$(tr -d ' \t\r\n' < "$root/current-version")"
    if [ -n "$ver" ] && [ -x "$root/versions/$ver/bin/python" ]; then
        printf '%s' "$root/versions/$ver/bin/python"; return 0
    fi
    p="$(ls -1d "$root"/versions/*/bin/python 2>/dev/null | sort | tail -1)"
    [ -n "$p" ] && [ -x "$p" ] && { printf '%s' "$p"; return 0; }
    [ -x "$root/.venv/bin/python" ] && { printf '%s' "$root/.venv/bin/python"; return 0; }
    return 1
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

# =========================================================================
phase 2 "runtime provisions on first session"
_apply_uv_index_fixture
mkdir -p "$HOME/mcp-repo" && ( cd "$HOME/mcp-repo" && git init -q && git config user.email t@e && git config user.name t && echo '# mcp' > README.md && git add -A && git commit -qm init )
PLUGIN_ARG=()
[ -d "$PLUGIN_DIR" ] && PLUGIN_ARG=( --plugin-dir "$PLUGIN_DIR" )
( cd "$HOME/mcp-repo" && capture "session-first" -- copilot -p "Reply with the single word: ready." --allow-all-tools "${PLUGIN_ARG[@]}" ) || true
sleep 8
capture "provision" -- bash -lc '
  command -v agent-mcp >/dev/null 2>&1 && agent-mcp --version >/dev/null 2>&1
  bash "'"$PLUGIN_DIR"'/scripts/init.sh" provision 2>&1 || true
' || true
SLOT_PY="$(_resolve_slot_python || true)"
if [ -n "$SLOT_PY" ] && "$SLOT_PY" -c 'import agent_mcp' >/dev/null 2>&1; then
    pass "runtime provisioned: slot python + agent_mcp import OK ($SLOT_PY)"
    cr_meta "slot_python" "$SLOT_PY"
else
    if [ -z "$UV_INDEX" ] && grep -qiE 'HandshakeFailure|pythonhosted|SSL|TLS|certificate' "$CR_LOGDIR"/*.log 2>/dev/null; then
        jam "toolchain-uv" "runtime not provisioned: uv could not reach its index (public PyPI TLS-blocked)" "re-run with CR_UV_INDEX=<internal index-url>"
    else
        jam "path-binstub" "agent-mcp runtime/venv NOT provisioned after first session" "multiplexer battery cannot run without a built slot"
    fi
fi

# =========================================================================
phase 3 "multiplexer process-topology battery"
if [ -z "${SLOT_PY:-}" ]; then
    jam "mcp-multiplexer" "no slot python -- cannot run the topology probe" "fix provisioning (phase 2) first"
elif ! [ -d /proc ]; then
    jam "mcp-multiplexer" "no /proc on this host -- the topology probe is Linux-only" "run this scenario on a Linux clean-room image"
else
    capture "topology-probe" -- "$SLOT_PY" "$_SELF_DIR/fixtures/multiplexer_topology_probe.py" --python "$SLOT_PY" --checks "$CHECKS" || true
    _log="$CR_LOGDIR/topology-probe.log"
    _seen=0
    while IFS= read -r line; do
        _seen=1
        _name="$(printf '%s' "$line" | awk '{print $2}')"
        _stat="$(printf '%s' "$line" | awk '{print $3}')"
        _rest="$(printf '%s' "$line" | cut -d' ' -f4-)"
        if [ "$_stat" = "PASS" ]; then
            pass "multiplexer/$_name: $_rest"
        else
            jam "mcp-multiplexer" "multiplexer/$_name FAILED: $_rest" "the multiplexer must collapse N sessions onto one host, one host, and fall back/evict correctly; see cr-logs/topology-probe.log"
        fi
    done < <(grep '^PROBE: ' "$_log" 2>/dev/null)
    if [ "$_seen" -eq 0 ]; then
        jam "mcp-multiplexer" "topology probe emitted no PROBE lines (crash before assertions)" "see cr-logs/topology-probe.log"
    fi
    _summary="$(grep '^PROBE-SUMMARY:' "$_log" 2>/dev/null | tail -1)"
    [ -n "$_summary" ] && info "$_summary"
fi

# =========================================================================
cr_finalize
