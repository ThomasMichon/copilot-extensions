#!/usr/bin/env bash
# agent-bridge-cutover/scenario.sh -- Tier-P F1 cutover-resilience scenario.
#
# Validates the BINDING INVARIANT of the correct-install-flows effort
# (dotfiles#1393) for agent-bridge: a version cutover must NEVER kill in-flight,
# non-resumable work. agent-bridge stands a new daemon up beside the old
# (`start --passive` on a fresh port), health-gates it, flips the routing table
# (active.json), drains the old at the TURN boundary, and retires it -- so a live
# interactive session is not hard-killed and clients follow the routing flip.
#
# The zdd cutover mechanism is app-level + OS-agnostic (the same Python runs on
# Linux and Windows), so this Linux clean-room exercises the real thing end-to-end.
# The heavy orchestration lives in a portable stdlib-only probe
# (fixtures/cutover_probe.py) so it is verifiable off-Docker too.
#
# FIDELITY: a fully live "turn survives the flip" assertion needs a real model/ACP
# child (Tier-E). This Tier-P probe proves the cutover MECHANISM the turn-survival
# guarantee is built on: the active/passive routing flip + old-daemon retirement,
# the drain gate (turn boundary), and cooperative recovery of an aborted cutover.
#
# Name-free / public F1. Asserts on daemon/routing OUTCOMES, not exact CLI spelling.
# Env: CR_MARKETPLACE_REPO / CR_MARKETPLACE_NAME / CR_UV_INDEX / CR_CUTOVER_CHECKS.
# MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
UV_INDEX="${CR_UV_INDEX:-}"
CHECKS="${CR_CUTOVER_CHECKS:-routing-flip-retire,drain-gate,breadcrumb-recover}"
PLUGIN="agent-bridge"
INSTALLED_ROOT="$HOME/.copilot/installed-plugins/$MARKETPLACE_NAME"

: "${CR_SCENARIO_NAME:=agent-bridge-cutover}"
export CR_SCENARIO_NAME
cr_init
cr_meta "plugin" "$PLUGIN"
cr_meta "validates" "graceful-daemon-cutover (no in-flight session killed on update)"

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
# First call to the self-provisioning binstub builds the venv on demand if the
# session-start stamp deferred it.
# First call to the self-provisioning binstub builds the venv on demand if the
# session-start stamp deferred it. Trigger it in a LOGIN shell so ~/.local/bin
# (where the binstub lands) is on PATH even if the scenario shell's PATH predates
# the install, and also drive the installer's explicit `provision` action as a
# deterministic fallback.
capture "provision" -- bash -lc '
  command -v agent-bridge >/dev/null 2>&1 && agent-bridge version >/dev/null 2>&1
  bash "'"$INSTALLED_ROOT/$PLUGIN"'/scripts/install.sh" provision 2>&1 || true
' || true
SLOT_PY="$(_resolve_slot_python || true)"
if [ -n "$SLOT_PY" ] && "$SLOT_PY" -c 'import agent_bridge; from zdd.cutover import CutoverOrchestrator' >/dev/null 2>&1; then
    pass "runtime provisioned: slot python + zdd import OK ($SLOT_PY)"
    cr_meta "slot_python" "$SLOT_PY"
else
    if [ -z "$UV_INDEX" ] && grep -qiE 'HandshakeFailure|pythonhosted|SSL|TLS|certificate' "$CR_LOGDIR"/*.log 2>/dev/null; then
        jam "toolchain-uv" "runtime not provisioned: uv could not reach its index (public PyPI TLS-blocked)" "re-run with CR_UV_INDEX=<internal index-url>"
    else
        jam "path-binstub" "agent-bridge runtime/venv (with zdd) NOT provisioned after first session" "cutover battery cannot run without a built slot"
    fi
fi

# =========================================================================
phase 3 "cutover resilience battery (thorny situations)"
if [ -z "${SLOT_PY:-}" ]; then
    jam "bridge-cutover" "no slot python -- cannot run the cutover probe" "fix provisioning (phase 2) first"
else
    capture "cutover-probe" -- "$SLOT_PY" "$_SELF_DIR/fixtures/cutover_probe.py" --python "$SLOT_PY" --checks "$CHECKS" || true
    _log="$CR_LOGDIR/cutover-probe.log"
    _seen=0
    while IFS= read -r line; do
        _seen=1
        _name="$(printf '%s' "$line" | awk '{print $2}')"
        _stat="$(printf '%s' "$line" | awk '{print $3}')"
        _rest="$(printf '%s' "$line" | cut -d' ' -f4-)"
        if [ "$_stat" = "PASS" ]; then
            pass "cutover/$_name: $_rest"
        else
            jam "bridge-cutover" "cutover/$_name FAILED: $_rest" "graceful cutover must not kill in-flight sessions; see cr-logs/cutover-probe.log"
        fi
    done < <(grep '^PROBE: ' "$_log" 2>/dev/null)
    if [ "$_seen" -eq 0 ]; then
        jam "bridge-cutover" "cutover probe emitted no PROBE lines (crash before assertions)" "see cr-logs/cutover-probe.log"
    fi
    _summary="$(grep '^PROBE-SUMMARY:' "$_log" 2>/dev/null | tail -1)"
    [ -n "$_summary" ] && info "$_summary"
fi

# =========================================================================
cr_finalize
