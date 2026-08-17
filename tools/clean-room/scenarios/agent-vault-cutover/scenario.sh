#!/usr/bin/env bash
# agent-vault-cutover/scenario.sh -- Tier-P F1 cutover-resilience witness.
#
# agent-vault is a machine-local secret-store service: its daemon caches the
# KeePassXC master password with a TTL and serves credential fetches over a
# discoverable local endpoint. The graceful-daemon-cutover pattern names vault as
# a FUTURE "connection-owner" adopter -- a version cutover should not drop an
# in-flight credential fetch / the unlocked TTL cache.
#
# STATE OF THE WORLD (verified in code): agent-vault has the CLIENT-SIDE half of
# cutover -- the rendezvous **cutover fallback ladder** (`agent_vault.rendezvous.
# resolve`: override -> live rendezvous file -> legacy constant) -- but has NOT
# yet adopted the DAEMON-SIDE active/passive zdd cutover (no vendored `zdd`, no
# CutoverOrchestrator/`--passive`/routing-table flip). So this scenario:
#   * PROVES the rendezvous fallback ladder deterministically (a portable
#     stdlib-only probe run off the built slot python), and
#   * REPORTS the daemon-side zdd cutover as a classified, forward-looking gap
#     (INFO, not a probe failure) so the scenario stays green today and its
#     phase-3 battery lights up the moment vault vendors zdd. (issue #609)
#
# Name-free / public F1. Asserts on OUTCOMES, not exact CLI spelling.
# Env: CR_MARKETPLACE_REPO / CR_MARKETPLACE_NAME / CR_UV_INDEX / CR_LADDER_CHECKS.
# MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
UV_INDEX="${CR_UV_INDEX:-}"
LADDER_CHECKS="${CR_LADDER_CHECKS:-ladder-override,ladder-file,ladder-legacy,ladder-precedence,ladder-empty-raises}"
PLUGIN="agent-vault"
INSTALLED_ROOT="$HOME/.copilot/installed-plugins/$MARKETPLACE_NAME"

: "${CR_SCENARIO_NAME:=agent-vault-cutover}"
export CR_SCENARIO_NAME
cr_init
cr_meta "plugin" "$PLUGIN"
cr_meta "validates" "cutover fallback ladder today; forward-ready witness for graceful-daemon-cutover (daemon-side zdd not yet adopted, #609)"

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
    local root="$HOME/.agent-vault" ver="" p=""
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
if [ -d "$HOME/.agent-vault" ] || [ -d "$HOME/.local/bin" ]; then
    fail "environment is NOT clean -- pre-existing ~/.agent-vault or ~/.local/bin"
else
    pass "clean slate: no ~/.agent-vault, no ~/.local/bin"
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
phase 2 "runtime provisions on first session (built slot + agent_vault importable)"
_apply_uv_index_fixture
mkdir -p "$HOME/av-repo" && ( cd "$HOME/av-repo" && git init -q && git config user.email t@e && git config user.name t && echo '# av' > README.md && git add -A && git commit -qm init )
PLUGIN_ARG=()
[ -d "$INSTALLED_ROOT/$PLUGIN" ] && PLUGIN_ARG=( --plugin-dir "$INSTALLED_ROOT/$PLUGIN" )
( cd "$HOME/av-repo" && capture "session-first" -- copilot -p "Reply with the single word: ready." --allow-all-tools "${PLUGIN_ARG[@]}" ) || true
sleep 8
# Drive the self-provisioning binstub in a LOGIN shell (so ~/.local/bin is on
# PATH) and the installer's explicit `provision` action as a deterministic fallback.
capture "provision" -- bash -lc '
  command -v agent-vault >/dev/null 2>&1 && agent-vault --version >/dev/null 2>&1
  bash "'"$INSTALLED_ROOT/$PLUGIN"'/scripts/install.sh" provision 2>&1 || true
' || true
SLOT_PY="$(_resolve_slot_python || true)"
if [ -n "$SLOT_PY" ] && "$SLOT_PY" -c 'import agent_vault; from agent_vault import rendezvous' >/dev/null 2>&1; then
    pass "runtime provisioned: slot python + agent_vault.rendezvous import OK ($SLOT_PY)"
    cr_meta "slot_python" "$SLOT_PY"
else
    if [ -z "$UV_INDEX" ] && grep -qiE 'HandshakeFailure|pythonhosted|SSL|TLS|certificate' "$CR_LOGDIR"/*.log 2>/dev/null; then
        jam "toolchain-uv" "runtime not provisioned: uv could not reach its index (public PyPI TLS-blocked)" "re-run with CR_UV_INDEX=<internal index-url>"
    else
        jam "path-binstub" "agent-vault runtime/venv NOT provisioned after first session" "the cutover-ladder probe cannot run without a built slot"
    fi
fi

# =========================================================================
phase 3 "cutover fallback ladder (client-side resilience) + daemon-side gap"
if [ -z "${SLOT_PY:-}" ]; then
    jam "vault-cutover" "no slot python -- cannot run the ladder probe" "fix provisioning (phase 2) first"
else
    capture "cutover-probe" -- "$SLOT_PY" "$_SELF_DIR/fixtures/cutover_probe.py" --checks "$LADDER_CHECKS" || true
    _log="$CR_LOGDIR/cutover-probe.log"
    _seen=0
    while IFS= read -r line; do
        _seen=1
        _name="$(printf '%s' "$line" | awk '{print $2}')"
        _stat="$(printf '%s' "$line" | awk '{print $3}')"
        _rest="$(printf '%s' "$line" | cut -d' ' -f4-)"
        if [ "$_stat" = "PASS" ]; then
            pass "ladder/$_name: $_rest"
        else
            jam "vault-cutover" "ladder/$_name FAILED: $_rest" "the rendezvous cutover fallback ladder must resolve across an endpoint move; see cr-logs/cutover-probe.log"
        fi
    done < <(grep '^PROBE: ' "$_log" 2>/dev/null)
    if [ "$_seen" -eq 0 ]; then
        jam "vault-cutover" "ladder probe emitted no PROBE lines (crash before assertions)" "see cr-logs/cutover-probe.log"
    fi
    _summary="$(grep '^PROBE-SUMMARY:' "$_log" 2>/dev/null | tail -1)"
    [ -n "$_summary" ] && info "$_summary"

    # Forward-looking gap: is the DAEMON-SIDE active/passive zdd cutover adopted?
    # (import succeeds only once vault vendors zdd + implements the orchestrator.)
    if "$SLOT_PY" -c 'from zdd.cutover import CutoverOrchestrator' >/dev/null 2>&1; then
        info "daemon-side zdd cutover IS now adopted by agent-vault -- extend phase 3 with the active/passive battery (routing-flip-retire / drain-gate / breadcrumb-recover), as agent-bridge-cutover does"
        cr_meta "zdd_cutover_adopted" "yes"
    else
        info "daemon-side zdd active/passive cutover NOT yet adopted by agent-vault (no vendored zdd/CutoverOrchestrator) -- tracked in #609; a daemon restart still drops the TTL-cached master password. This witness asserts only the client-side rendezvous ladder today."
        cr_meta "zdd_cutover_adopted" "no"
    fi
fi

# =========================================================================
cr_finalize
