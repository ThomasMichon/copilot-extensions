#!/usr/bin/env bash
# agent-index-solo/scenario.sh -- Tier-P (programmatic) F1 solo scenario.
#
# Installs ONLY agent-index on a fresh box and asserts its standalone
# install/behave contract: payload, self-provisioned SERVICE runtime, login-shell
# binstub with a real version, direct MCP tool registration, read-only agent-mcp
# bridge config, and status/health reads. It deliberately does NOT require the
# durable embedding-engine runtime (~/.agent-index/engine) or any model/index
# build; empty/degraded read results are acceptable, crashes are not.
#
# Distinct from agent-index-cutover, which validates zdd graceful service cutover.
# Name-free / public F1. Env: CR_MARKETPLACE_REPO / CR_MARKETPLACE_NAME /
# CR_UV_INDEX + the lib's vars. MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
UV_INDEX="${CR_UV_INDEX:-}"
PLUGIN="agent-index"
INSTALLED_ROOT="$HOME/.copilot/installed-plugins/$MARKETPLACE_NAME"

: "${CR_SCENARIO_NAME:=agent-index-solo}"
export CR_SCENARIO_NAME
cr_init
cr_meta "plugin" "$PLUGIN"
cr_meta "base" "solo-standalone"
cr_meta "engine_required" "no"

_apply_uv_index_fixture() {
    [ -n "$UV_INDEX" ] || return 0
    export UV_INDEX_URL="$UV_INDEX" UV_DEFAULT_INDEX="$UV_INDEX" UV_EXTRA_INDEX_URL="${UV_EXTRA_INDEX_URL:-$UV_INDEX}"
    mkdir -p "$HOME/.config/uv"
    printf '[[index]]\nurl = "%s"\ndefault = true\n' "$UV_INDEX" > "$HOME/.config/uv/uv.toml"
    info "uv-index fixture applied: uv -> $UV_INDEX"
}

_runtime_python() {
    if [ -x "$HOME/.agent-index/.venv/bin/python" ]; then
        printf '%s' "$HOME/.agent-index/.venv/bin/python"
        return 0
    fi
    local p
    p="$(ls -1d "$HOME"/.agent-index/versions/*/bin/python 2>/dev/null | sort | tail -1)"
    [ -n "$p" ] && [ -x "$p" ] && { printf '%s' "$p"; return 0; }
    return 1
}

_log_has_toolchain_uv() {
    grep -qiE 'HandshakeFailure|pythonhosted|SSL|TLS|certificate|uv.*index|No matching distribution|failed to download' "$CR_LOGDIR"/*.log 2>/dev/null
}

_read_ok() {
    local label="$1" desc="$2" cmd="$3"
    if capture "$label" -- bash -lc "$cmd"; then
        pass "$desc exits 0"
    else
        if grep -qiE 'Traceback|ModuleNotFoundError|No module named|command not found|ImportError' "$CR_LOGDIR/$label.log" 2>/dev/null; then
            jam "index-config" "$desc crashed solo (see cr-logs/$label.log)" "read/status verbs must return an empty/degraded result, not crash"
        else
            jam "index-config" "$desc exited non-zero solo (see cr-logs/$label.log)" "read/status verbs must degrade cleanly with no built index"
        fi
    fi
}

# =========================================================================
phase 0 "environment (fresh machine)"
envdump
if [ -d "$HOME/.agent-index" ] || [ -d "$HOME/.local/bin" ]; then
    fail "environment is NOT clean -- pre-existing ~/.agent-index or ~/.local/bin"
else
    pass "clean slate: no ~/.agent-index, no ~/.local/bin"
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
if [ -f "$INSTALLED_ROOT/$PLUGIN/mcp/agent-index.yaml" ] \
   && [ "$(find "$INSTALLED_ROOT/$PLUGIN/mcp/tools" -maxdepth 1 -name 'agent_index_*.md' 2>/dev/null | wc -l | tr -d ' ')" -ge 4 ] \
   && ! grep -q 'agent_index_reindex' "$INSTALLED_ROOT/$PLUGIN/mcp/agent-index.yaml" 2>/dev/null; then
    pass "read-only agent-mcp bridge config present (four read tools; no reindex tool)"
else
    jam "index-config" "read-only agent-mcp bridge config missing or exposes reindex" "payload should include mcp/agent-index.yaml with search/similar/clusters/status only"
fi

# =========================================================================
phase 2 "runtime provisions the service runtime"
_apply_uv_index_fixture
# Host role gives this solo box the local service/runtime and direct MCP surface.
# AGENT_INDEX_NO_ENGINE_DEPS is a guardrail: this scenario never needs torch/model deps.
mkdir -p "$HOME/.agent-index"
printf 'role: host\n' > "$HOME/.agent-index/config.yaml"
export AGENT_INDEX_ROLE=host AGENT_INDEX_NO_ENGINE_DEPS=1
mkdir -p "$HOME/ai-repo" && ( cd "$HOME/ai-repo" && git init -q && git config user.email t@e && git config user.name t && echo '# ai' > README.md && git add -A && git commit -qm init )
PLUGIN_ARG=()
[ -d "$INSTALLED_ROOT/$PLUGIN" ] && PLUGIN_ARG=( --plugin-dir "$INSTALLED_ROOT/$PLUGIN" )
( cd "$HOME/ai-repo" && capture "session-first" -- copilot -p "Reply with the single word: ready." --allow-all-tools "${PLUGIN_ARG[@]}" ) || true
sleep 5
# The sessionStart hook should stamp the binstub; the explicit provision action is
# the deterministic first-use fallback that builds the SERVICE runtime only.
capture "provision" -- bash "$INSTALLED_ROOT/$PLUGIN/scripts/install.sh" provision || true
if [ -d "$HOME/.agent-index/versions" ] || [ -x "$HOME/.agent-index/.venv/bin/python" ]; then
    pass "service runtime provisioned (~/.agent-index/versions or .venv present)"
else
    if [ -z "$UV_INDEX" ] && _log_has_toolchain_uv; then
        jam "toolchain-uv" "service runtime not provisioned: uv could not reach its package index" "re-run with CR_UV_INDEX=<internal index-url>"
    else
        jam "path-binstub" "service runtime NOT provisioned after session + installer provision" "binstub/provision should build ~/.agent-index/versions or ~/.agent-index/.venv"
    fi
fi
if [ -x "$HOME/.local/bin/agent-index" ]; then
    pass "agent-index binstub deployed to ~/.local/bin"
else
    jam "path-binstub" "~/.local/bin/agent-index missing after provision" "installer provision should deploy the self-provisioning binstub"
fi
if [ -x "$HOME/.agent-index/engine/.venv/bin/python" ]; then
    info "durable engine runtime exists, but this scenario did not require it"
else
    pass "durable engine/model runtime NOT required for solo pass (~/.agent-index/engine absent)"
fi

# =========================================================================
phase 3 "binstub on PATH + reports a REAL version"
if capture "which-binstub" -- bash -lc 'command -v agent-index'; then
    pass "agent-index resolves on a fresh login-shell PATH"
else
    jam "path-binstub" "agent-index is NOT on login-shell PATH" "install should place ~/.local/bin on login-shell PATH and deploy the binstub there"
fi
if capture "version-flag" -- bash -lc 'agent-index --version'; then
    if grep -Eq '[0-9]+\.[0-9]+' "$CR_LOGDIR/version-flag.log" 2>/dev/null; then
        pass "agent-index --version reports a real version ($(tail -n1 "$CR_LOGDIR/version-flag.log" | tr -d '\r'))"
    else
        jam "path-binstub" "agent-index --version did not print a real non-empty version" "the binstub must exec the installed package, not an unstamped placeholder"
    fi
else
    jam "path-binstub" "agent-index --version failed (see cr-logs/version-flag.log)" "the binstub must self-provision/exec successfully"
fi

# =========================================================================
phase 4 "standalone read/status/health verbs degrade cleanly with no built index"
# Ensure the local service shell is running; this starts only the lightweight
# service runtime. The durable engine/model remains optional and unbuilt.
capture "service-ensure" -- bash "$INSTALLED_ROOT/$PLUGIN/scripts/install.sh" ensure || true
sleep 3
_read_ok "read-status" "agent-index status" "agent-index status"
_read_ok "read-version" "agent-index version" "agent-index version"
_read_ok "read-engine-status" "agent-index engine status" "agent-index engine status"
_read_ok "read-role" "agent-index role --json" "agent-index role --json"

if [ -f "$HOME/.agent-index/active.json" ]; then
    if capture "read-health" -- python3 - "$HOME/.agent-index/active.json" <<'PY'
import json, sys, urllib.request
with open(sys.argv[1], encoding="utf-8") as f:
    port = int(json.load(f)["active"]["port"])
with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as resp:
    body = resp.read().decode("utf-8", "replace")
    print(body)
    raise SystemExit(0 if resp.status == 200 else 1)
PY
    then
        pass "service /health read exits 0"
    else
        jam "index-config" "service /health read failed after ensure (see cr-logs/read-health.log)" "the lightweight service shell should answer health without a built engine/index"
    fi
else
    jam "index-config" "service ensure did not publish ~/.agent-index/active.json" "the standalone host service should publish a health endpoint without a built engine/index"
fi

RUNTIME_PY="$(_runtime_python || true)"
if [ -n "$RUNTIME_PY" ]; then
    if capture "mcp-tool-surface" -- "$RUNTIME_PY" -c '
import asyncio
import agent_index.mcp_app as app
expected = {"agent_index_search", "agent_index_find_similar", "agent_index_clusters", "agent_index_status", "agent_index_reindex"}
tools = asyncio.run(app.mcp.list_tools())
names = {tool.name for tool in tools}
print("\n".join(sorted(names)))
missing = expected - names
raise SystemExit(0 if not missing else 1)
'; then
        pass "direct agent-index mcp registers the expected five-tool surface"
    else
        jam "index-config" "direct MCP tool registration failed (see cr-logs/mcp-tool-surface.log)" "host service runtime should include the MCP surface without starting a model/index build"
    fi
else
    jam "path-binstub" "no service runtime python available for MCP tool-surface check" "phase 2 provision should leave ~/.agent-index/.venv or versions/<ver>/bin/python"
fi

# =========================================================================
cr_finalize
