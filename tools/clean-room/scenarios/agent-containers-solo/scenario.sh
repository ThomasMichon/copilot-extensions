#!/usr/bin/env bash
# agent-containers-solo/scenario.sh -- Tier-P (programmatic) F1 solo scenario.
#
# Installs ONLY agent-containers on a fresh box and asserts its DEGRADE-SAFE
# contract. agent-containers shells the agent-worktrees binstub only at the
# optional knowledge-overlay config lookup; that lookup must FALL OPEN when
# agent-worktrees is absent. So installed alone it must still provision, expose a
# binstub with a real version, and answer read/idempotent no-venue verbs rather
# than hard-fail on a missing base.
#
# Name-free / public F1. Env: CR_MARKETPLACE_REPO / CR_MARKETPLACE_NAME /
# CR_UV_INDEX + the lib's vars. MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
UV_INDEX="${CR_UV_INDEX:-}"
PLUGIN="agent-containers"
INSTALLED_ROOT="$HOME/.copilot/installed-plugins/$MARKETPLACE_NAME"

: "${CR_SCENARIO_NAME:=agent-containers-solo}"
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

_resolve_runtime_python() {
    local root="$HOME/.agent-containers" ver="" p=""
    [ -f "$root/current-version" ] && ver="$(tr -d ' \t\r\n' < "$root/current-version")"
    if [ -n "$ver" ] && [ -x "$root/versions/$ver/bin/python" ]; then
        printf '%s' "$root/versions/$ver/bin/python"; return 0
    fi
    p="$(ls -1d "$root"/versions/*/bin/python 2>/dev/null | sort | tail -1)"
    [ -n "$p" ] && [ -x "$p" ] && { printf '%s' "$p"; return 0; }
    [ -x "$root/.venv/bin/python" ] && { printf '%s' "$root/.venv/bin/python"; return 0; }
    return 1
}

_check_read_verb() {
    local label="$1"; shift
    if capture "read-$label" -- bash -lc "$*"; then
        pass "agent-containers $* exits 0 (degrade-safe with no agent-worktrees base)"
        return 0
    fi
    if grep -qiE 'Traceback|resolve_owner_worktree|repos\.yaml|agent-worktrees|No module named' "$CR_LOGDIR/read-$label.log" 2>/dev/null; then
        jam "container-config" "agent-containers $* CRASHED on a missing agent-worktrees base" "optional agent-worktrees config lookup must fall open when the base is absent"
    else
        jam "container-config" "agent-containers $* exited non-zero in solo/no-venue mode" "read/idempotent verbs in this scenario should not require Docker, a container, or agent-worktrees"
    fi
    return 1
}

# =========================================================================
phase 0 "environment (fresh machine)"
envdump
if [ -d "$HOME/.agent-containers" ] || [ -d "$HOME/.local/bin" ]; then
    fail "environment is NOT clean -- pre-existing ~/.agent-containers or ~/.local/bin"
else
    pass "clean slate: no ~/.agent-containers, no ~/.local/bin"
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
mkdir -p "$HOME/ac-repo" && ( cd "$HOME/ac-repo" && git init -q && git config user.email t@e && git config user.name t && echo '# ac' > README.md && git add -A && git commit -qm init )
PLUGIN_ARG=()
[ -d "$INSTALLED_ROOT/$PLUGIN" ] && PLUGIN_ARG=( --plugin-dir "$INSTALLED_ROOT/$PLUGIN" )
( cd "$HOME/ac-repo" && capture "session-first" -- copilot -p "Reply with the single word: ready." --allow-all-tools "${PLUGIN_ARG[@]}" ) || true
sleep 8
# First call to the self-provisioning binstub builds the venv on demand if the
# session-start stamp placed it on PATH; the explicit installer provision is the
# deterministic fallback for bases where the stamp did not run yet.
capture "provision" -- bash -lc '
  if command -v agent-containers >/dev/null 2>&1; then agent-containers version >/dev/null 2>&1 || true; fi
  inst="'"$INSTALLED_ROOT/$PLUGIN"'/scripts/init.sh"
  if [ -f "$inst" ]; then bash "$inst" provision; else echo "installer missing: $inst" >&2; exit 127; fi
' || true
RUNTIME_PY="$(_resolve_runtime_python || true)"
if { [ -d "$HOME/.agent-containers/versions" ] || [ -x "$HOME/.agent-containers/.venv/bin/python" ]; } \
   && [ -n "$RUNTIME_PY" ] && "$RUNTIME_PY" -c 'import agent_containers' >/dev/null 2>&1; then
    pass "agent-containers runtime provisioned after first session/fallback ($RUNTIME_PY)"
    cr_meta "runtime_python" "$RUNTIME_PY"
else
    if [ -z "$UV_INDEX" ] && grep -qiE 'HandshakeFailure|pythonhosted|SSL|TLS|certificate|CERTIFICATE_VERIFY_FAILED|Connection reset|timed out' "$CR_LOGDIR"/*.log 2>/dev/null; then
        jam "toolchain-uv" "runtime not provisioned: uv could not reach its index (public PyPI TLS-blocked)" "re-run with CR_UV_INDEX=<internal index-url>"
    else
        jam "path-binstub" "agent-containers runtime NOT provisioned after first session/fallback" "session-start stamp or self-provisioning binstub should lead to scripts/init.sh provision"
    fi
fi

# =========================================================================
phase 3 "binstub on PATH + reports a REAL version"
if capture "path-agent-containers" -- bash -lc 'command -v agent-containers'; then
    pass "agent-containers resolves on a fresh login-shell PATH"
else
    fail "agent-containers NOT on login-shell PATH"
fi
capture "version" -- bash -lc 'agent-containers --version 2>/dev/null || agent-containers version 2>/dev/null' || true
_ver="$(grep -E '[0-9]+\.[0-9]+' "$CR_LOGDIR/version.log" 2>/dev/null | head -1 || true)"
if [ -n "$(printf '%s' "$_ver" | tr -d ' \t\r\n')" ]; then
    pass "agent-containers version -> $(printf '%s' "$_ver" | head -1)"
else
    fail "agent-containers --version/version printed no real version (unstamped build-info defect)"
fi

# =========================================================================
phase 4 "DEGRADE-SAFE: read verbs fall open WITHOUT an agent-worktrees base"
# These are the real no-Docker/no-venue read/idempotent verbs in agent-containers:
# leases reads advisory lease state; relay-profile loads config (including the
# optional agent-worktrees knowledge-overlay lookup); namespace-target-repo is the
# process-boundary provider query that is always empty for containers.
_ok=0
if _check_read_verb "leases" 'agent-containers leases'; then _ok=$((_ok+1)); fi
if _check_read_verb "relay-profile" 'agent-containers relay-profile'; then _ok=$((_ok+1)); fi
if _check_read_verb "namespace-target-repo" 'agent-containers namespace-target-repo smoke'; then _ok=$((_ok+1)); fi
[ "$_ok" -eq 3 ] || fail "not all agent-containers read verbs (leases/relay-profile/namespace-target-repo) exited 0 without the base"

# =========================================================================
cr_finalize
