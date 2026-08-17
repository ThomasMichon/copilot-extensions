#!/usr/bin/env bash
# agent-dispatch-solo/scenario.sh -- Tier-P (programmatic) F1 solo scenario.
#
# Installs ONLY agent-dispatch on a fresh box and asserts its STANDALONE
# install/behave contract: payload present, self-provisioning runtime, login-shell
# binstub with a real package version, and idempotent read entry points that work
# against an empty solo queue. This is distinct from agent-dispatch-cutover, which
# validates graceful daemon cutover during updates.
#
# Name-free / public F1. Env: CR_MARKETPLACE_REPO / CR_MARKETPLACE_NAME /
# CR_UV_INDEX + the lib's vars. MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
UV_INDEX="${CR_UV_INDEX:-}"
PLUGIN="agent-dispatch"
INSTALLED_ROOT="$HOME/.copilot/installed-plugins/$MARKETPLACE_NAME"
INSTALL_DIR="$HOME/.agent-dispatch"

: "${CR_SCENARIO_NAME:=agent-dispatch-solo}"
export CR_SCENARIO_NAME
cr_init
cr_meta "plugin" "$PLUGIN"
cr_meta "base" "fresh-standalone"

_apply_uv_index_fixture() {
    [ -n "$UV_INDEX" ] || return 0
    export UV_INDEX_URL="$UV_INDEX" UV_DEFAULT_INDEX="$UV_INDEX" UV_EXTRA_INDEX_URL="${UV_EXTRA_INDEX_URL:-$UV_INDEX}"
    mkdir -p "$HOME/.config/uv"
    printf '[[index]]\nurl = "%s"\ndefault = true\n' "$UV_INDEX" > "$HOME/.config/uv/uv.toml"
    info "uv-index fixture applied: uv -> $UV_INDEX"
}

_installer_path() {
    local p=""
    if [ -f "$INSTALL_DIR/payload-dir" ]; then
        p="$(tr -d ' \t\r\n' < "$INSTALL_DIR/payload-dir")/scripts/install.sh"
        [ -f "$p" ] && { printf '%s' "$p"; return 0; }
    fi
    p="$INSTALLED_ROOT/$PLUGIN/scripts/install.sh"
    [ -f "$p" ] && { printf '%s' "$p"; return 0; }
    p="$(ls "$HOME"/.copilot/installed-plugins/*/"$PLUGIN"/scripts/install.sh 2>/dev/null | head -n1)"
    [ -n "$p" ] && [ -f "$p" ] && { printf '%s' "$p"; return 0; }
    return 1
}

_resolve_runtime_python() {
    local root="$INSTALL_DIR" ver="" p=""
    if [ -f "$root/current-version" ]; then
        ver="$(tr -d ' \t\r\n' < "$root/current-version")"
    fi
    if [ -n "$ver" ]; then
        for p in "$root/versions/$ver/bin/python" "$root/versions/$ver/Scripts/python.exe"; do
            [ -x "$p" ] || [ -f "$p" ] && { printf '%s' "$p"; return 0; }
        done
    fi
    p="$(ls -1d "$root"/versions/*/bin/python 2>/dev/null | sort | tail -1)"
    [ -n "$p" ] && [ -x "$p" ] && { printf '%s' "$p"; return 0; }
    p="$(ls -1d "$root"/versions/*/Scripts/python.exe 2>/dev/null | sort | tail -1)"
    [ -n "$p" ] && [ -f "$p" ] && { printf '%s' "$p"; return 0; }
    [ -x "$root/.venv/bin/python" ] && { printf '%s' "$root/.venv/bin/python"; return 0; }
    [ -f "$root/.venv/Scripts/python.exe" ] && { printf '%s' "$root/.venv/Scripts/python.exe"; return 0; }
    return 1
}

_expected_version() {
    local py="${1:-}"
    if [ -n "$py" ] && [ -f "$INSTALL_DIR/deploy-manifest.json" ]; then
        "$py" -c 'import json, os; print(json.load(open(os.path.expanduser("~/.agent-dispatch/deploy-manifest.json"), encoding="utf-8"))["source"].get("version", ""))' 2>/dev/null && return 0
    fi
    grep -m1 -E '^[[:space:]]*version[[:space:]]*=' "$INSTALLED_ROOT/$PLUGIN/pyproject.toml" 2>/dev/null | sed -E 's/.*=[[:space:]]*"([^"]+)".*/\1/'
}

_has_runtime_artifact() {
    [ -d "$INSTALL_DIR/versions" ] || [ -e "$INSTALL_DIR/.venv" ]
}

# =========================================================================
phase 0 "environment (fresh machine)"
envdump
if [ -d "$INSTALL_DIR" ] || [ -d "$HOME/.local/bin" ]; then
    fail "environment is NOT clean -- pre-existing ~/.agent-dispatch or ~/.local/bin"
else
    pass "clean slate: no ~/.agent-dispatch, no ~/.local/bin"
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
if [ -d "$INSTALLED_ROOT/agent-worktrees" ] || [ -d "$INSTALLED_ROOT/agent-bridge" ] || [ -d "$INSTALLED_ROOT/agent-codespaces" ]; then
    fail "unexpected companion payload installed -- scenario must install ONLY $PLUGIN"
else
    pass "no companion agent runtime payloads installed"
fi

# =========================================================================
phase 2 "runtime self-provisions"
_apply_uv_index_fixture
mkdir -p "$HOME/ad-solo-repo"
(
    cd "$HOME/ad-solo-repo" \
    && git init -q \
    && git config user.email t@e \
    && git config user.name t \
    && echo '# ad solo' > README.md \
    && git add -A \
    && git commit -qm init
)
PLUGIN_ARG=()
[ -d "$INSTALLED_ROOT/$PLUGIN" ] && PLUGIN_ARG=( --plugin-dir "$INSTALLED_ROOT/$PLUGIN" )
( cd "$HOME/ad-solo-repo" && capture "session-first" -- copilot -p "Reply with the single word: ready." --allow-all-tools "${PLUGIN_ARG[@]}" ) || true
sleep 8
if bash -lc 'command -v agent-dispatch >/dev/null 2>&1'; then
    capture "binstub-provision" -- bash -lc 'agent-dispatch --version' || true
else
    installer="$(_installer_path || true)"
    if [ -n "$installer" ]; then
        capture "installer-provision" -- bash "$installer" provision || true
    else
        jam "path-binstub" "no login-shell binstub and no installer found after first session" "session-start should stamp ~/.local/bin/agent-dispatch or leave an installer payload"
    fi
fi
RUNTIME_PY="$(_resolve_runtime_python || true)"
if [ -n "$RUNTIME_PY" ] && _has_runtime_artifact && "$RUNTIME_PY" -c 'import agent_dispatch' >/dev/null 2>&1; then
    pass "runtime provisioned: versions/.venv artifact + agent_dispatch import OK ($RUNTIME_PY)"
    cr_meta "runtime_python" "$RUNTIME_PY"
else
    if [ -z "$UV_INDEX" ] && grep -qiE 'HandshakeFailure|pythonhosted|SSL|TLS|certificate|Could not resolve|connection' "$CR_LOGDIR"/*.log 2>/dev/null; then
        jam "toolchain-uv" "runtime not provisioned: uv could not reach its index (public PyPI/TLS blocked)" "re-run with CR_UV_INDEX=<internal index-url>"
    else
        jam "path-binstub" "agent-dispatch runtime NOT provisioned by session + binstub/installer provision" "session-start should stamp the binstub; first use/installer provision should build ~/.agent-dispatch"
    fi
fi

# =========================================================================
phase 3 "binstub on PATH + reports the real installed version"
if bash -lc 'command -v agent-dispatch >/dev/null'; then
    pass "agent-dispatch resolves on a fresh login-shell PATH"
else
    fail "agent-dispatch NOT on login-shell PATH"
fi
_cli_ver_raw="$(bash -lc 'agent-dispatch --version' 2>/dev/null || true)"
_cli_ver="$(printf '%s' "$_cli_ver_raw" | head -n1 | sed -E 's/^agent-dispatch[[:space:]]+//; s/[[:space:]]+$//')"
_pkg_ver=""
_expected_ver=""
if [ -z "${RUNTIME_PY:-}" ]; then RUNTIME_PY="$(_resolve_runtime_python || true)"; fi
if [ -n "$RUNTIME_PY" ]; then
    _pkg_ver="$($RUNTIME_PY -c 'import agent_dispatch; print(agent_dispatch.__version__)' 2>/dev/null || true)"
    _expected_ver="$(_expected_version "$RUNTIME_PY" 2>/dev/null || true)"
fi
cr_meta "cli_version" "$_cli_ver"
cr_meta "package_version" "$_pkg_ver"
if [ -n "$(printf '%s' "$_cli_ver" | tr -d ' \t\r\n')" ]; then
    pass "agent-dispatch --version -> $_cli_ver_raw"
else
    fail "agent-dispatch --version printed NOTHING"
fi
case "$_pkg_ver" in
    ""|0.0.0|0.0.0+dev|0.0.0*)
        fail "in-package agent_dispatch.__version__ is fallback/stale: '${_pkg_ver:-<empty>}'"
        ;;
    *)
        pass "in-package agent_dispatch.__version__ is real: $_pkg_ver"
        ;;
esac
if [ -n "$_cli_ver" ] && [ -n "$_pkg_ver" ] && [ "$_cli_ver" = "$_pkg_ver" ]; then
    pass "--version matches in-package __version__"
else
    fail "--version ('$_cli_ver') does not match in-package __version__ ('$_pkg_ver')"
fi
if [ -n "$_expected_ver" ] && [ -n "$_pkg_ver" ] && [ "$_expected_ver" = "$_pkg_ver" ]; then
    pass "runtime version matches installed manifest/source version ($_expected_ver)"
else
    fail "runtime version ('$_pkg_ver') does not match installed version ('$_expected_ver')"
fi

# =========================================================================
phase 4 "standalone read verbs exit 0 solo"
installer="$(_installer_path || true)"
if [ -n "$installer" ]; then
    if capture "read-installer-status" -- bash "$installer" status; then
        pass "installer status exits 0 solo"
    else
        jam "dispatch-config" "installer status failed solo (see cr-logs/read-installer-status.log)" "status is the idempotent installer entry point and should not require companion plugins"
    fi
else
    jam "path-binstub" "installer status unavailable: no installer path found" "payload-dir / installed plugin should expose scripts/install.sh"
fi

if capture "read-version" -- bash -lc 'agent-dispatch --version'; then
    pass "agent-dispatch --version exits 0 solo"
else
    jam "dispatch-config" "agent-dispatch --version failed solo (see cr-logs/read-version.log)" "--version should not need a coordinator or queue state"
fi

# inbox is intentionally first among coordinator-backed reads: it is a read verb
# that uses the CLI's lazy local autostart path. The coordinator endpoint is then
# discovered dynamically via the rendezvous file; no fixed port is assumed.
if capture "read-inbox-empty" -- bash -lc 'agent-dispatch inbox --machine clean-room-solo'; then
    if grep -qx '\[\]' "$CR_LOGDIR/read-inbox-empty.log" 2>/dev/null; then
        pass "agent-dispatch inbox --machine clean-room-solo exits 0 and reports an empty queue"
    else
        fail "agent-dispatch inbox exited 0 but did not report an empty queue (see cr-logs/read-inbox-empty.log)"
    fi
else
    jam "dispatch-config" "agent-dispatch inbox crashed/failed solo against an empty queue (see cr-logs/read-inbox-empty.log)" "an empty standalone queue should report cleanly, not crash"
fi

if capture "read-health" -- bash -lc 'agent-dispatch health'; then
    pass "agent-dispatch health exits 0 solo after dynamic coordinator rendezvous"
    _health_ver="$(grep -m1 '"version"' "$CR_LOGDIR/read-health.log" 2>/dev/null | sed -E 's/.*"version"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')"
    if [ -n "$_pkg_ver" ] && [ "$_health_ver" = "$_pkg_ver" ]; then
        pass "coordinator /health version matches installed package version ($_health_ver)"
    else
        fail "coordinator /health version ('$_health_ver') does not match package version ('$_pkg_ver')"
    fi
else
    jam "dispatch-config" "agent-dispatch health failed solo after inbox autostart (see cr-logs/read-health.log)" "health should discover the local coordinator via rendezvous, not assume a fixed endpoint"
fi

# =========================================================================
cr_finalize
