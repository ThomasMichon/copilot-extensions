#!/usr/bin/env bash
# agent-dispatch-supervisor-self-update/scenario.sh -- Tier-P F1 scenario.
#
# Validates the `supervise serve` singleton daemon's own live version-staleness
# check (#2259): unlike the coordinator's analogous self-update loop, this one
# is DEFAULT-ON / opt-out, because this harness has no launch-path protocol to
# flip an opt-in env var before a daemon's first boot -- an opt-in gate here
# would simply never activate for a real operator. Default-on behavior gets
# real process-level validation here (a real spawn, a real single-instance
# lease release/reacquire, a genuinely different live successor pid) before it
# ships to everyone, plus the fail-safe/opt-out negative controls that prove a
# healthy daemon never spuriously self-terminates.
#
# The heavy orchestration lives in a portable stdlib-only probe
# (fixtures/supervisor_self_update_probe.py) so it is verifiable off-Docker too.
#
# Name-free / public F1. Asserts on daemon-process OUTCOMES, not exact CLI
# spelling. Env: CR_MARKETPLACE_REPO / CR_MARKETPLACE_NAME / CR_UV_INDEX /
# CR_SUPERVISOR_SELF_UPDATE_CHECKS. MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
UV_INDEX="${CR_UV_INDEX:-}"
CHECKS="${CR_SUPERVISOR_SELF_UPDATE_CHECKS:-default-on-handoff,opt-out-disables,no-marker-fail-safe}"
PLUGIN="agent-dispatch"
INSTALLED_ROOT="$HOME/.copilot/installed-plugins/$MARKETPLACE_NAME"

: "${CR_SCENARIO_NAME:=agent-dispatch-supervisor-self-update}"
export CR_SCENARIO_NAME
cr_init
cr_meta "plugin" "$PLUGIN"
cr_meta "validates" "service-lifecycle-supervision (supervisor self-update is default-on, not opt-in)"

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
    local root="$HOME/.agent-dispatch" ver="" p=""
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
if [ -d "$HOME/.agent-dispatch" ] || [ -d "$HOME/.local/bin" ]; then
    fail "environment is NOT clean -- pre-existing ~/.agent-dispatch or ~/.local/bin"
else
    pass "clean slate: no ~/.agent-dispatch, no ~/.local/bin"
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
mkdir -p "$HOME/ad-repo" && ( cd "$HOME/ad-repo" && git init -q && git config user.email t@e && git config user.name t && echo '# ad' > README.md && git add -A && git commit -qm init )
PLUGIN_ARG=()
[ -d "$INSTALLED_ROOT/$PLUGIN" ] && PLUGIN_ARG=( --plugin-dir "$INSTALLED_ROOT/$PLUGIN" )
( cd "$HOME/ad-repo" && capture "session-first" -- copilot -p "Reply with the single word: ready." --allow-all-tools "${PLUGIN_ARG[@]}" ) || true
sleep 8
if command -v agent-dispatch >/dev/null 2>&1; then
    capture "binstub-provision" -- bash -lc 'agent-dispatch --help' || true
fi
SLOT_PY="$(_resolve_slot_python || true)"
if [ -n "$SLOT_PY" ] && "$SLOT_PY" -c 'import agent_dispatch' >/dev/null 2>&1; then
    pass "runtime provisioned: slot python OK ($SLOT_PY)"
    cr_meta "slot_python" "$SLOT_PY"
else
    if [ -z "$UV_INDEX" ] && grep -qiE 'HandshakeFailure|pythonhosted|SSL|TLS|certificate' "$CR_LOGDIR"/*.log 2>/dev/null; then
        jam "toolchain-uv" "runtime not provisioned: uv could not reach its index (public PyPI TLS-blocked)" "re-run with CR_UV_INDEX=<internal index-url>"
    else
        jam "path-binstub" "agent-dispatch runtime/venv NOT provisioned after first session" "supervisor self-update battery cannot run without a built slot"
    fi
fi

# =========================================================================
phase 3 "supervisor self-update battery (default-on, opt-out, fail-safe)"
if [ -z "${SLOT_PY:-}" ]; then
    jam "dispatch-cutover" "no slot python -- cannot run the supervisor self-update probe" "fix provisioning (phase 2) first"
else
    capture "supervisor-self-update-probe" -- "$SLOT_PY" "$_SELF_DIR/fixtures/supervisor_self_update_probe.py" --python "$SLOT_PY" --source "$INSTALLED_ROOT/$PLUGIN" --checks "$CHECKS" || true
    _log="$CR_LOGDIR/supervisor-self-update-probe.log"
    _seen=0
    while IFS= read -r line; do
        _seen=1
        _name="$(printf '%s' "$line" | awk '{print $2}')"
        _stat="$(printf '%s' "$line" | awk '{print $3}')"
        _rest="$(printf '%s' "$line" | cut -d' ' -f4-)"
        if [ "$_stat" = "PASS" ]; then
            pass "supervisor-self-update/$_name: $_rest"
        else
            jam "dispatch-cutover" "supervisor-self-update/$_name FAILED: $_rest" "the supervisor self-update check is DEFAULT-ON; see cr-logs/supervisor-self-update-probe.log"
        fi
    done < <(grep '^PROBE: ' "$_log" 2>/dev/null)
    if [ "$_seen" -eq 0 ]; then
        jam "dispatch-cutover" "supervisor self-update probe emitted no PROBE lines (crash before assertions)" "see cr-logs/supervisor-self-update-probe.log"
    fi
    _summary="$(grep '^PROBE-SUMMARY:' "$_log" 2>/dev/null | tail -1)"
    [ -n "$_summary" ] && info "$_summary"
fi

# =========================================================================
cr_finalize
