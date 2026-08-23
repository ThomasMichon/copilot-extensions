#!/usr/bin/env bash
# agent-bridge-concurrent-daemon/scenario.sh -- Tier-P F1 daemon single-instance guard.
#
# Validates the guard that prevents the duplicate-daemon failure mode at its ROOT:
# when a second `agent-bridge start` races the first -- the classic case is a
# concurrent sessionStart-hook reinstall spawning a second daemon while one is
# live -- the guard must REFUSE the duplicate instead of standing a colliding
# daemon up beside the first. It complements agent-bridge-concurrent-relay: that
# scenario proves the relay recovers if a duplicate slips through; this one proves
# the duplicate is refused before it can bind at all.
#
# The guard is app-level + OS-agnostic (an exclusive OS byte-range lock the kernel
# frees on holder death), so this Linux clean-room exercises the real thing. The
# contention orchestration lives in a portable stdlib-only probe
# (fixtures/singleton_guard_probe.py) that drives the REAL
# agent_bridge.singleton.SingleInstance with cross-process holders, so it is
# verifiable off-Docker too (any built agent-bridge venv).
#
# FIDELITY: a fully live "two installed daemons race the installer end-to-end"
# assertion is a Tier-E, box-in-the-loop concern for the wider chaos rig. This
# Tier-P probe proves the guard MECHANISM the duplicate-prevention is built on:
# a live holder refuses a second acquire (naming the holder pid); a DEAD holder's
# lock is reclaimed (no stale wedge); and an active/passive pair coexists by port.
#
# Name-free / public F1. Asserts on guard OUTCOMES, not exact CLI spelling.
# Env: CR_MARKETPLACE_REPO / CR_MARKETPLACE_NAME / CR_UV_INDEX / CR_GUARD_CHECKS.
# MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
UV_INDEX="${CR_UV_INDEX:-}"
CHECKS="${CR_GUARD_CHECKS:-duplicate-refused,dead-holder-reclaimed,passive-coexist-by-port}"
PLUGIN="agent-bridge"
INSTALLED_ROOT="$HOME/.copilot/installed-plugins/$MARKETPLACE_NAME"

: "${CR_SCENARIO_NAME:=agent-bridge-concurrent-daemon}"
export CR_SCENARIO_NAME
cr_init
cr_meta "plugin" "$PLUGIN"
cr_meta "validates" "single-instance daemon guard refuses a duplicate (and reclaims a dead holder)"

_apply_uv_index_fixture() {
    [ -n "$UV_INDEX" ] || return 0
    export UV_INDEX_URL="$UV_INDEX" UV_DEFAULT_INDEX="$UV_INDEX" UV_EXTRA_INDEX_URL="${UV_EXTRA_INDEX_URL:-$UV_INDEX}"
    mkdir -p "$HOME/.config/uv"
    printf '[[index]]\nurl = "%s"\ndefault = true\n' "$UV_INDEX" > "$HOME/.config/uv/uv.toml"
    info "uv-index fixture applied: uv -> $UV_INDEX"
}

# Resolve the built versioned-slot python (the daemon runs from the immutable
# slot, not the .venv link). Prefer the current-version marker; else newest slot.
_resolve_slot_python() {
    local root="$HOME/.agent-bridge" ver="" p=""
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
if [ -d "$HOME/.agent-bridge" ] || [ -d "$HOME/.local/bin" ]; then
    fail "environment is NOT clean -- pre-existing ~/.agent-bridge or ~/.local/bin"
else
    pass "clean slate: no ~/.agent-bridge, no ~/.local/bin"
fi

# =========================================================================
phase 1 "install $PLUGIN"
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
phase 2 "runtime provisions on first session"
_apply_uv_index_fixture
mkdir -p "$HOME/ab-repo" && ( cd "$HOME/ab-repo" && git init -q && git config user.email t@e && git config user.name t && echo '# ab' > README.md && git add -A && git commit -qm init )
PLUGIN_ARG=()
[ -d "$INSTALLED_ROOT/$PLUGIN" ] && PLUGIN_ARG=( --plugin-dir "$INSTALLED_ROOT/$PLUGIN" )
( cd "$HOME/ab-repo" && capture "session-first" -- copilot -p "Reply with the single word: ready." --allow-all-tools "${PLUGIN_ARG[@]}" ) || true
sleep 8
capture "provision" -- bash -lc '
  command -v agent-bridge >/dev/null 2>&1 && agent-bridge version >/dev/null 2>&1
  bash "'"$INSTALLED_ROOT/$PLUGIN"'/scripts/install.sh" provision 2>&1 || true
' || true
SLOT_PY="$(_resolve_slot_python || true)"
if [ -n "$SLOT_PY" ] && "$SLOT_PY" -c 'import agent_bridge.singleton' >/dev/null 2>&1; then
    pass "runtime provisioned: slot python + agent_bridge.singleton import OK ($SLOT_PY)"
    cr_meta "slot_python" "$SLOT_PY"
else
    if [ -z "$UV_INDEX" ] && grep -qiE 'HandshakeFailure|pythonhosted|SSL|TLS|certificate' "$CR_LOGDIR"/*.log 2>/dev/null; then
        jam "toolchain-uv" "runtime not provisioned: uv could not reach its index (public PyPI TLS-blocked)" "re-run with CR_UV_INDEX=<internal index-url>"
    else
        jam "path-binstub" "agent-bridge runtime/venv NOT provisioned after first session" "guard probe cannot run without a built slot"
    fi
fi

# =========================================================================
phase 3 "single-instance daemon guard under concurrent start"
if [ -n "${SLOT_PY:-}" ] && "$SLOT_PY" -c 'import agent_bridge.singleton' >/dev/null 2>&1; then
    PROBE="$_SELF_DIR/fixtures/singleton_guard_probe.py"
    if capture "guard-probe" -- "$SLOT_PY" "$PROBE" --checks "$CHECKS"; then
        pass "single-instance guard checks PASSED ($CHECKS)"
    else
        while IFS= read -r line; do
            case "$line" in
                "PROBE: "*" FAIL "*)
                    jam "daemon-guard" "guard check FAILED: $line" \
                        "a duplicate daemon was NOT refused (or a dead holder wedged startup) -- port collision + relay breakage would follow"
                    ;;
                "PROBE: "*" PASS "*) info "$line" ;;
            esac
        done < "$CR_LOGDIR/guard-probe.log" 2>/dev/null
    fi
else
    info "guard probe skipped: no built slot python with agent_bridge.singleton (provisioning jam above)"
fi

cr_finalize
