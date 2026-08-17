#!/usr/bin/env bash
# agent-machines-solo/scenario.sh -- Tier-P (programmatic) F1 solo scenario.
#
# Installs ONLY agent-machines on a fresh box and asserts the standalone
# machine-state reconciler CLI stands up without sibling plugins or requirement
# packages. agent-machines is not a daemon: session start stamps a login-shell
# binstub, the binstub self-provisions (or the installer provision action is used
# as a fallback), and restore is an explicit CLI preview/apply operation.
#
# Name-free / public F1. Env: CR_MARKETPLACE_REPO / CR_MARKETPLACE_NAME /
# CR_UV_INDEX + the lib's vars. MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
UV_INDEX="${CR_UV_INDEX:-}"
PLUGIN="agent-machines"
INSTALLED_ROOT="$HOME/.copilot/installed-plugins/$MARKETPLACE_NAME"

: "${CR_SCENARIO_NAME:=agent-machines-solo}"
export CR_SCENARIO_NAME
cr_init
cr_meta "plugin" "$PLUGIN"
cr_meta "base" "without-agent-worktrees-or-requirement-packages"
cr_meta "read_verbs" "discover --json; plan --json; validate --json; restore (default dry-run); --help"

_apply_uv_index_fixture() {
    [ -n "$UV_INDEX" ] || return 0
    export UV_INDEX_URL="$UV_INDEX" UV_DEFAULT_INDEX="$UV_INDEX" UV_EXTRA_INDEX_URL="${UV_EXTRA_INDEX_URL:-$UV_INDEX}"
    mkdir -p "$HOME/.config/uv"
    printf '[[index]]\nurl = "%s"\ndefault = true\n' "$UV_INDEX" > "$HOME/.config/uv/uv.toml"
    info "uv-index fixture applied: uv -> $UV_INDEX"
}

_runtime_ready() {
    [ -d "$HOME/.agent-machines/versions" ] || [ -x "$HOME/.agent-machines/.venv/bin/python" ]
}

_login_binstub_ready() {
    bash -lc 'command -v agent-machines >/dev/null'
}

_has_traceback() {
    grep -qiE 'Traceback|Unhandled exception|ModuleNotFoundError|ImportError' "$1" 2>/dev/null
}

_has_uv_failure() {
    grep -qiE 'HandshakeFailure|pythonhosted|SSL|TLS|certificate|Failed to install|uv .*failed|Could not resolve|No solution found' "$1" 2>/dev/null
}

# =========================================================================
phase 0 "environment (fresh machine)"
envdump
if [ -d "$HOME/.agent-machines" ] || [ -d "$HOME/.local/bin" ]; then
    jam "machines-config" "environment is NOT clean -- pre-existing ~/.agent-machines or ~/.local/bin" "start the scenario in a fresh base image"
else
    pass "clean slate: no ~/.agent-machines, no ~/.local/bin"
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
    jam "machines-config" "agent-worktrees payload is present; solo install pulled a sibling plugin" "agent-machines solo should install only agent-machines"
else
    pass "no agent-worktrees payload -- standalone condition holds"
fi

# =========================================================================
phase 2 "runtime self-provisions (session stamp + binstub/provision fallback)"
_apply_uv_index_fixture
mkdir -p "$HOME/machines-repo" && (
    cd "$HOME/machines-repo" &&
    git init -q &&
    git config user.email t@e &&
    git config user.name t &&
    echo '# machines' > README.md &&
    git add -A &&
    git commit -qm init
)
PLUGIN_ARG=()
[ -d "$INSTALLED_ROOT/$PLUGIN" ] && PLUGIN_ARG=( --plugin-dir "$INSTALLED_ROOT/$PLUGIN" )
( cd "$HOME/machines-repo" && capture "session-first" -- copilot -p "Reply with the single word: ready." --allow-all-tools "${PLUGIN_ARG[@]}" ) || true
sleep 8

if ! _runtime_ready; then
    if _login_binstub_ready; then
        capture "runtime-first-use" -- bash -lc 'agent-machines version' || true
    elif [ -f "$INSTALLED_ROOT/$PLUGIN/scripts/init.sh" ]; then
        capture "runtime-provision-fallback" -- bash "$INSTALLED_ROOT/$PLUGIN/scripts/init.sh" provision || true
    else
        jam "path-binstub" "no login-shell binstub and no installer provision fallback found" "session stamp should publish ~/.local/bin/agent-machines or payload scripts/init.sh should be present"
    fi
fi

if _runtime_ready; then
    pass "agent-machines runtime deployed (~/.agent-machines/versions or .venv present)"
else
    _evidence="$CR_LOGDIR/runtime-first-use.log"
    [ -f "$_evidence" ] || _evidence="$CR_LOGDIR/runtime-provision-fallback.log"
    [ -f "$_evidence" ] || _evidence="$CR_LOGDIR/session-first.log"
    if [ -z "$UV_INDEX" ] && _has_uv_failure "$_evidence"; then
        jam "toolchain-uv" "runtime provisioning could not use uv/index (see $_evidence)" "re-run with CR_UV_INDEX=<internal index-url>"
    else
        jam "path-binstub" "runtime NOT deployed after session + binstub/provision fallback (see $_evidence)" "session stamp and self-provisioning binstub must converge ~/.agent-machines"
    fi
fi

# =========================================================================
phase 3 "binstub on PATH + reports a REAL version"
if capture "which-agent-machines" -- bash -lc 'command -v agent-machines'; then
    pass "agent-machines resolves on a fresh login-shell PATH"
else
    jam "path-binstub" "agent-machines NOT on login-shell PATH (see cr-logs/which-agent-machines.log)" "~/.local/bin must be exported for login shells"
fi
if capture "version" -- bash -lc 'agent-machines --version'; then
    _ver="$(tr -d ' \t\r\n' < "$CR_LOGDIR/version.log" 2>/dev/null || true)"
    if [ -n "$_ver" ]; then
        pass "agent-machines --version -> $(head -1 "$CR_LOGDIR/version.log")"
    else
        jam "path-binstub" "agent-machines --version printed NOTHING (see cr-logs/version.log)" "runtime build metadata should stamp a real version"
    fi
else
    jam "path-binstub" "agent-machines --version failed (see cr-logs/version.log)" "binstub should exec the provisioned runtime"
fi

# =========================================================================
phase 4 "standalone read/preview verbs handle absent config"
if capture "read-help" -- bash -lc 'agent-machines --help'; then
    if _has_traceback "$CR_LOGDIR/read-help.log"; then
        jam "machines-config" "agent-machines --help emitted a traceback (see cr-logs/read-help.log)" "help must not import/crash on absent machine config"
    else
        pass "agent-machines --help exits 0 without traceback"
    fi
else
    jam "machines-config" "agent-machines --help failed (see cr-logs/read-help.log)" "help should be standalone"
fi

_read_ok=0
for verb in "discover --json" "plan --json" "validate --json"; do
    _label="read-${verb%% *}"
    if capture "$_label" -- bash -lc "agent-machines $verb"; then
        if _has_traceback "$CR_LOGDIR/$_label.log"; then
            jam "machines-config" "agent-machines $verb emitted a traceback (see cr-logs/$_label.log)" "read verbs must degrade cleanly when ~/.agent-worktrees and requirement packages are absent"
        else
            pass "agent-machines $verb exits 0 without traceback"
            _read_ok=1
        fi
    else
        if _has_traceback "$CR_LOGDIR/$_label.log"; then
            jam "machines-config" "agent-machines $verb crashed on absent config (see cr-logs/$_label.log)" "return cleanly or print a clear validator/config message"
        else
            jam "machines-config" "agent-machines $verb returned non-zero on absent config (see cr-logs/$_label.log)" "read verbs should exit 0 with an empty discovered package set"
        fi
    fi
done
[ "$_read_ok" -eq 1 ] || jam "machines-config" "no read verb (discover/plan/validate) exited 0" "at least one standalone read verb should succeed without requirement packages"

# There is no restore --dry-run flag; restore defaults to a dry-run preview and
# --apply opts into mutation. With no discovered requirement packages, it should
# exit 0; if a future validator blocks it, the message must be explicit rather
# than a Python traceback.
if capture "restore-preview-empty" -- bash -lc 'agent-machines restore'; then
    if _has_traceback "$CR_LOGDIR/restore-preview-empty.log"; then
        jam "machines-config" "agent-machines restore emitted a traceback on absent config (see cr-logs/restore-preview-empty.log)" "restore's default dry-run should degrade cleanly with an empty package union"
    else
        pass "agent-machines restore (default dry-run) exits 0 without traceback on absent config"
    fi
else
    if _has_traceback "$CR_LOGDIR/restore-preview-empty.log"; then
        jam "machines-config" "agent-machines restore crashed on absent config (see cr-logs/restore-preview-empty.log)" "restore must refuse via validator/config messaging, not traceback"
    elif grep -qiE 'restore refused: validator reported errors|manifest error|validator' "$CR_LOGDIR/restore-preview-empty.log" 2>/dev/null; then
        pass "agent-machines restore emitted a clear validator/config refusal without traceback"
    else
        jam "machines-config" "agent-machines restore returned non-zero without a clear validator message (see cr-logs/restore-preview-empty.log)" "empty/absent config should exit 0 or explain validator refusal"
    fi
fi

# =========================================================================
cr_finalize
