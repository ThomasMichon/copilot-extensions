#!/usr/bin/env bash
# agent-bridge-concurrent-flip/scenario.sh -- Tier-P F1 version-flip coherence.
#
# Validates the invariant that makes a concurrent plugin UPDATE safe: while
# multiple installers race to flip the active runtime version -- the exact effect
# of several new Copilot sessions each firing the sessionStart-hook reinstall at
# once -- a reader resolving the runtime must ALWAYS land on a valid, existing
# version slot, never a torn/half-written current-version marker or a pointer to a
# slot that isn't there. It is the third leg of the concurrent-update triad:
# agent-bridge-concurrent-relay (the relay recovers), agent-bridge-concurrent-daemon
# (a duplicate daemon is refused), and this (the version flip stays coherent).
#
# The immutable-runtime layout manager is app-level + OS-agnostic (atomic
# current-version publish via os.replace + a current -> last-known-good -> newest
# resolver), so this Linux clean-room exercises the real thing. The flip storm
# lives in a portable stdlib-only probe (fixtures/version_flip_probe.py) that
# drives the REAL versioned_runtime module with cross-process flippers, so it is
# verifiable off-Docker too (any built agent-bridge venv).
#
# FIDELITY: a fully live "two real installers race the sessionStart hook + a live
# daemon end-to-end" assertion is a Tier-E, box-in-the-loop concern for the wider
# chaos rig. This Tier-P probe proves the version-resolution MECHANISM the
# update-safety is built on: coherent resolution + a never-torn marker under a
# storm of racing flips.
#
# Name-free / public F1. Asserts on resolver OUTCOMES, not exact CLI spelling.
# Env: CR_MARKETPLACE_REPO / CR_MARKETPLACE_NAME / CR_UV_INDEX / CR_FLIP_CHECKS.
# MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
UV_INDEX="${CR_UV_INDEX:-}"
CHECKS="${CR_FLIP_CHECKS:-flip-storm-coherent-resolution,marker-never-torn}"
PLUGIN="agent-bridge"
INSTALLED_ROOT="$HOME/.copilot/installed-plugins/$MARKETPLACE_NAME"

: "${CR_SCENARIO_NAME:=agent-bridge-concurrent-flip}"
export CR_SCENARIO_NAME
cr_init
cr_meta "plugin" "$PLUGIN"
cr_meta "validates" "version-slot flip stays coherent under a storm of racing installers"

_apply_uv_index_fixture() {
    [ -n "$UV_INDEX" ] || return 0
    export UV_INDEX_URL="$UV_INDEX" UV_DEFAULT_INDEX="$UV_INDEX" UV_EXTRA_INDEX_URL="${UV_EXTRA_INDEX_URL:-$UV_INDEX}"
    mkdir -p "$HOME/.config/uv"
    printf '[[index]]\nurl = "%s"\ndefault = true\n' "$UV_INDEX" > "$HOME/.config/uv/uv.toml"
    info "uv-index fixture applied: uv -> $UV_INDEX"
}

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

# Locate the immutable-runtime layout manager the probe drives (shipped in the
# installed plugin payload's scripts/ dir).
_resolve_runtime_module() {
    local m="$INSTALLED_ROOT/$PLUGIN/scripts/versioned_runtime.py"
    [ -f "$m" ] && { printf '%s' "$m"; return 0; }
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
RUNTIME_MODULE="$(_resolve_runtime_module || true)"
if [ -n "$SLOT_PY" ] && [ -n "$RUNTIME_MODULE" ]; then
    pass "runtime provisioned: slot python ($SLOT_PY) + versioned_runtime.py present"
    cr_meta "slot_python" "$SLOT_PY"
else
    if [ -z "$UV_INDEX" ] && grep -qiE 'HandshakeFailure|pythonhosted|SSL|TLS|certificate' "$CR_LOGDIR"/*.log 2>/dev/null; then
        jam "toolchain-uv" "runtime not provisioned: uv could not reach its index (public PyPI TLS-blocked)" "re-run with CR_UV_INDEX=<internal index-url>"
    else
        jam "path-binstub" "agent-bridge runtime/versioned_runtime.py NOT available after first session" "flip probe cannot run without a built slot + the layout manager"
    fi
fi

# =========================================================================
phase 3 "version-slot flip coherence under racing installers"
if [ -n "${SLOT_PY:-}" ] && [ -n "${RUNTIME_MODULE:-}" ]; then
    PROBE="$_SELF_DIR/fixtures/version_flip_probe.py"
    if capture "flip-probe" -- "$SLOT_PY" "$PROBE" --runtime-module "$RUNTIME_MODULE" --checks "$CHECKS"; then
        pass "version-flip coherence checks PASSED ($CHECKS)"
    else
        while IFS= read -r line; do
            case "$line" in
                "PROBE: "*" FAIL "*)
                    jam "version-flip" "flip check FAILED: $line" \
                        "a racing update left the runtime resolution incoherent (torn marker or dangling slot) -- a session could resolve a broken runtime"
                    ;;
                "PROBE: "*" PASS "*) info "$line" ;;
            esac
        done < "$CR_LOGDIR/flip-probe.log" 2>/dev/null
    fi
else
    info "flip probe skipped: no built slot python + versioned_runtime.py (provisioning jam above)"
fi

cr_finalize
