#!/usr/bin/env bash
# agent-ssh-solo/scenario.sh -- Tier-P (programmatic) F1 solo scenario.
#
# Installs ONLY agent-ssh on a fresh box and asserts its STANDALONE contract:
# the plugin is a profile emitter/verifier plus transport-provider contract, not
# a degrade-safe add-on to agent-worktrees. It must provision, expose a binstub,
# and answer idempotent read verbs without any sibling plugin and without a live
# SSH target.
#
# Name-free / public F1. Env: CR_MARKETPLACE_REPO / CR_MARKETPLACE_NAME /
# CR_UV_INDEX + the lib's vars. MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
UV_INDEX="${CR_UV_INDEX:-}"
PLUGIN="agent-ssh"
INSTALLED_ROOT="$HOME/.copilot/installed-plugins/$MARKETPLACE_NAME"

: "${CR_SCENARIO_NAME:=agent-ssh-solo}"
export CR_SCENARIO_NAME
cr_init
cr_meta "plugin" "$PLUGIN"
cr_meta "base" "standalone-without-siblings"

_apply_uv_index_fixture() {
    [ -n "$UV_INDEX" ] || return 0
    export UV_INDEX_URL="$UV_INDEX" UV_DEFAULT_INDEX="$UV_INDEX" UV_EXTRA_INDEX_URL="${UV_EXTRA_INDEX_URL:-$UV_INDEX}"
    mkdir -p "$HOME/.config/uv"
    printf '[[index]]\nurl = "%s"\ndefault = true\n' "$UV_INDEX" > "$HOME/.config/uv/uv.toml"
    info "uv-index fixture applied: uv -> $UV_INDEX"
}

_runtime_ready() {
    [ -x "$HOME/.agent-ssh/.venv/bin/python" ] && return 0
    local py
    for py in "$HOME/.agent-ssh"/versions/*/bin/python "$HOME/.agent-ssh"/versions/*/Scripts/python.exe; do
        [ -x "$py" ] && return 0
    done
    return 1
}

_classify_runtime_failure() {
    local evidence="$1"
    if [ -z "$UV_INDEX" ] && grep -qiE 'HandshakeFailure|pythonhosted|SSL|TLS|self.signed|certificate|Failed to install agent-ssh package|uv pip install' "$CR_LOGDIR"/*.log 2>/dev/null; then
        jam "toolchain-uv" "$evidence: uv could not reach/use its package index" "re-run with CR_UV_INDEX=<internal index-url>"
    else
        jam "path-binstub" "$evidence: agent-ssh runtime/venv not provisioned" "first session or first-use binstub should provision; installer provision is the fallback"
    fi
}

# =========================================================================
phase 0 "environment (fresh machine)"
envdump
if [ -d "$HOME/.agent-ssh" ] || [ -d "$HOME/.local/bin" ]; then
    jam "path-binstub" "environment is not clean: pre-existing ~/.agent-ssh or ~/.local/bin" "run from a fresh clean-room container"
else
    pass "clean slate: no ~/.agent-ssh, no ~/.local/bin"
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

_unexpected=""
for payload in "$INSTALLED_ROOT"/*; do
    [ -d "$payload" ] || continue
    name="${payload##*/}"
    [ "$name" = "$PLUGIN" ] || _unexpected="$_unexpected $name"
done
if [ -z "$(printf '%s' "$_unexpected" | tr -d ' ')" ]; then
    pass "no sibling plugin payloads installed -- standalone condition holds"
else
    jam "ssh-contract" "installing agent-ssh pulled sibling plugin(s):$_unexpected" "agent-ssh solo must not depend on sibling plugin payloads"
fi

# =========================================================================
phase 2 "runtime provisions on first session / first use"
_apply_uv_index_fixture
mkdir -p "$HOME/ssh-repo" && ( cd "$HOME/ssh-repo" && git init -q && git config user.email t@e && git config user.name t && echo '# ssh' > README.md && git add -A && git commit -qm init )
PLUGIN_ARG=()
[ -d "$INSTALLED_ROOT/$PLUGIN" ] && PLUGIN_ARG=( --plugin-dir "$INSTALLED_ROOT/$PLUGIN" )
( cd "$HOME/ssh-repo" && capture "session-first" -- copilot -p "Reply with the single word: ready." --allow-all-tools "${PLUGIN_ARG[@]}" ) || true
sleep 8
if _runtime_ready; then
    pass "agent-ssh runtime deployed after first session"
else
    info "runtime not ready after session-start hook; trying login-shell binstub first-use trigger"
    if bash -lc 'command -v agent-ssh >/dev/null'; then
        capture "binstub-first-use" -- bash -lc 'agent-ssh --help' || true
        sleep 2
    else
        info "agent-ssh binstub is not on login-shell PATH yet"
    fi
    if _runtime_ready; then
        pass "agent-ssh runtime provisioned by login-shell binstub first use"
    else
        info "using installer provision fallback so later standalone read assertions can run"
        if [ -f "$INSTALLED_ROOT/$PLUGIN/scripts/install.sh" ]; then
            capture "installer-provision" -- bash "$INSTALLED_ROOT/$PLUGIN/scripts/install.sh" provision || true
        else
            jam "path-binstub" "installer provision fallback unavailable: $INSTALLED_ROOT/$PLUGIN/scripts/install.sh missing" "install should leave an executable plugin payload"
        fi
        if _runtime_ready; then
            jam "path-binstub" "agent-ssh runtime required installer provision fallback; first session/login-shell binstub did not self-provision" "session hook or installed binstub should make the runtime available without a manual installer call"
            pass "agent-ssh runtime available after installer provision fallback"
        else
            _classify_runtime_failure "phase 2"
        fi
    fi
fi

# =========================================================================
phase 3 "binstub on PATH + reports a REAL version"
if [ -e "$HOME/.local/bin/agent-ssh" ]; then
    pass "binstub deployed: ~/.local/bin/agent-ssh"
else
    jam "path-binstub" "binstub missing: ~/.local/bin/agent-ssh" "installer should deploy a login-shell binstub"
fi
if bash -lc 'command -v agent-ssh >/dev/null'; then
    pass "agent-ssh resolves on a fresh login-shell PATH"
else
    jam "path-binstub" "agent-ssh NOT on login-shell PATH" "ensure ~/.local/bin is exported for login shells"
fi
_ver="$(bash -lc 'agent-ssh --version 2>/dev/null || agent-ssh version 2>/dev/null')"
if [ -n "$(printf '%s' "$_ver" | tr -d ' \t\r\n')" ]; then
    pass "agent-ssh version -> $(printf '%s' "$_ver" | head -1)"
else
    jam "path-binstub" "agent-ssh --version/version printed NOTHING" "package metadata should stamp a non-empty version"
fi

# =========================================================================
phase 4 "STANDALONE: read verbs answer with no sibling plugin"
mkdir -p "$HOME/agent-ssh-fixtures"
REGISTRY="$HOME/agent-ssh-fixtures/direct-registry.yaml"
MACHINES="$HOME/agent-ssh-fixtures/machines.yaml"
cat > "$REGISTRY" <<YAML
transport: direct
machines:
  - name: cr-direct-example
    hostname: 127.0.0.1
    user: operator
    port: 22
    identity_file: ~/.ssh/id_ed25519
YAML
cat > "$MACHINES" <<YAML
control_plane:
  project: clean-room
machines:
  local:
    display_name: Local clean-room example
    role: test
    environment: linux
    hostname: localhost
    ssh:
      ready: false
      environments:
        - name: linux
          alias: cr-direct-example
          shell: bash
          user: operator
YAML
MODULE="$INSTALLED_ROOT/$PLUGIN/transports/direct/module.yaml"
[ -f "$MODULE" ] || MODULE="$INSTALLED_ROOT/$PLUGIN/contract/examples/direct.module.yaml"

_ok=0
if [ -f "$MODULE" ] && capture "read-emit-profile" -- bash -lc "agent-ssh emit-profile '$REGISTRY' --module '$MODULE' --print"; then
    pass "agent-ssh emit-profile exits 0 using an in-box/example direct transport"
    _ok=$((_ok + 1))
else
    jam "ssh-contract" "agent-ssh emit-profile failed (see cr-logs/read-emit-profile.log)" "direct transport profile emission should not require a live SSH target or sibling plugin"
fi
if capture "read-mesh-status" -- bash -lc "agent-ssh mesh-status --json --path '$MACHINES'"; then
    pass "agent-ssh mesh-status --json exits 0 from a local machines.yaml"
    _ok=$((_ok + 1))
else
    jam "ssh-contract" "agent-ssh mesh-status failed (see cr-logs/read-mesh-status.log)" "mesh-status is config-driven and read-only"
fi
if capture "read-verify-help" -- bash -lc 'agent-ssh verify --help'; then
    pass "agent-ssh verify --help exits 0 without probing a target"
    _ok=$((_ok + 1))
else
    jam "ssh-contract" "agent-ssh verify --help failed (see cr-logs/read-verify-help.log)" "verify help should load without an SSH target"
fi
if capture "read-explore-help" -- bash -lc 'agent-ssh explore --help'; then
    pass "agent-ssh explore --help exits 0 without probing a target"
    _ok=$((_ok + 1))
else
    jam "ssh-contract" "agent-ssh explore --help failed (see cr-logs/read-explore-help.log)" "explore help should load without an SSH target"
fi

if [ "$_ok" -eq 4 ]; then
    pass "standalone read verbs all exited 0 with no sibling plugin present"
else
    jam "ssh-contract" "only $_ok/4 standalone read verbs exited 0" "agent-ssh read surface must be self-contained"
fi

# =========================================================================
cr_finalize