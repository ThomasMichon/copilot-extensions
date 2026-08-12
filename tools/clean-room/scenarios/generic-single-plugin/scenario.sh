#!/usr/bin/env bash
# generic-single-plugin/scenario.sh -- the REFERENCE clean-room scenario.
#
# Runs INSIDE the disposable container as the unprivileged `operator` from a
# stock login shell. It reproduces a naive operator's fresh-machine experience
# and turns every "I believe / mixed reports" question about the plugin
# install -> bootstrap -> operate flow into a hard PASS/FAIL/INFO line.
#
# It is the substrate-generalisation proof for the scenario contract (design
# Sec.6): it sources the shared lib and defines its stages purely through the
# helper API (phase/pass/fail/info/capture/envdump/jam), so the runner stays
# name-free and every scenario reports uniformly.
#
# It asserts on FILESYSTEM OUTCOMES (venv present, binstub on PATH, plugins on
# disk, project registered) rather than exact CLI syntax, so it stays robust if
# a `copilot plugin ...` subcommand spelling changes -- it records the CLI
# surface it saw and captures all output for triage.
#
# Configurable via env (defaults reproduce today's Layer-0 check):
#   CR_MARKETPLACE_REPO  owner/name of the marketplace repo (GitHub)
#                        default: ThomasMichon/copilot-extensions
#   CR_MARKETPLACE_NAME  marketplace id used in <plugin>@<name> sources
#                        default: copilot-extensions
#   CR_PRIMARY_PLUGIN    the single plugin a naive user installs first
#                        default: agent-codespaces
#   CR_EXPECT_DEPS       space-separated plugins that SHOULD arrive with it
#                        default: "agent-bridge agent-worktrees"
#   CR_UV_INDEX          OPT-IN uv index (uv-index fixture). When set, the deploy
#                        stage points uv at this internal index so provisioning
#                        succeeds on a governed box. Default UNSET -> the
#                        governed public-PyPI TLS block surfaces as a toolchain-uv
#                        jam (that surfacing is the point; mirrors the npm arg).
#   CR_REPORT / CR_LOGDIR / CR_UNTIL / CR_SCENARIO_NAME  (see the lib)
#
# CR_LIB: absolute path to clean-room-lib.sh (the runner sets this; falls back to
# a path relative to this script for a hand-run).
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/clean-room-lib.sh
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
PRIMARY_PLUGIN="${CR_PRIMARY_PLUGIN:-agent-codespaces}"
EXPECT_DEPS="${CR_EXPECT_DEPS:-agent-bridge agent-worktrees}"
UV_INDEX="${CR_UV_INDEX:-}"
INSTALLED_ROOT="$HOME/.copilot/installed-plugins/$MARKETPLACE_NAME"

: "${CR_SCENARIO_NAME:=generic-single-plugin}"
export CR_SCENARIO_NAME
cr_init
cr_meta "marketplace_repo" "$MARKETPLACE_REPO"
cr_meta "primary_plugin"   "$PRIMARY_PLUGIN"

# Point uv at an internal index when the opt-in fixture is supplied (design
# Sec.3/7 uv-index fixture): uv does NOT read pip.conf, so on a governed box its
# default public PyPI index is TLS-blocked and every `uv pip install` fails. This
# mirrors the runner's npm build-arg -- opt-in, applied only for the deploy stage.
_apply_uv_index_fixture() {
    [ -n "$UV_INDEX" ] || return 0
    export UV_INDEX_URL="$UV_INDEX"
    export UV_DEFAULT_INDEX="$UV_INDEX"
    export UV_EXTRA_INDEX_URL="${UV_EXTRA_INDEX_URL:-$UV_INDEX}"
    mkdir -p "$HOME/.config/uv"
    cat > "$HOME/.config/uv/uv.toml" <<TOML
# clean-room uv-index fixture (opt-in, CR_UV_INDEX) -- governed-feed unjam.
[[index]]
url = "$UV_INDEX"
default = true
TOML
    info "uv-index fixture applied: uv -> $UV_INDEX (UV_INDEX_URL + ~/.config/uv/uv.toml)"
}

# =========================================================================
phase 0 "environment (must look like a fresh machine)"
envdump
info "whoami=$(whoami) HOME=$HOME"
info "copilot: $(command -v copilot || echo MISSING) ($(copilot --version 2>/dev/null | head -1))"
info "uv: $(command -v uv || echo MISSING)  git: $(command -v git || echo MISSING)  node: $(node --version 2>/dev/null)"
info "login-shell PATH: $(bash -lc 'echo $PATH')"
if [ -d "$HOME/.agent-codespaces" ] || [ -d "$HOME/.agent-worktrees" ] || [ -d "$HOME/.local/bin" ]; then
    fail "environment is NOT clean -- pre-existing ~/.agent-* or ~/.local/bin"
else
    pass "clean slate: no ~/.agent-*, no ~/.local/bin"
fi
# Record the CLI surface so we know what subcommands exist on this version.
copilot --help        >"$CR_LOGDIR/copilot-help.log"        2>&1 || true
copilot plugin --help >"$CR_LOGDIR/copilot-plugin-help.log" 2>&1 || true

# =========================================================================
phase 1 "register marketplace + install ONE plugin ($PRIMARY_PLUGIN)"
# Seed the marketplace declaratively (most robust across CLI versions); also try
# the CLI verb and record which path worked.
mkdir -p "$HOME/.copilot"
cat > "$HOME/.copilot/settings.json" <<JSON
{
  "extraKnownMarketplaces": {
    "$MARKETPLACE_NAME": { "source": { "source": "github", "repo": "$MARKETPLACE_REPO" } }
  },
  "enabledPlugins": {
    "$PRIMARY_PLUGIN@$MARKETPLACE_NAME": true
  }
}
JSON
info "wrote ~/.copilot/settings.json (marketplace=$MARKETPLACE_NAME repo=$MARKETPLACE_REPO)"

capture "marketplace-add" -- copilot plugin marketplace add "$MARKETPLACE_REPO" || true
capture "install-primary" -- copilot plugin install "$PRIMARY_PLUGIN@$MARKETPLACE_NAME" || true

if [ -d "$INSTALLED_ROOT/$PRIMARY_PLUGIN" ]; then
    pass "$PRIMARY_PLUGIN payload present on disk ($INSTALLED_ROOT/$PRIMARY_PLUGIN)"
else
    jam "npm-registry" "install-primary: $PRIMARY_PLUGIN payload NOT installed (see cr-logs/install-primary.log)" \
        "check the marketplace source + node/npm feed reachability"
fi

# =========================================================================
phase 2 "dependency chain (does one install pull the rest?)"
for dep in $EXPECT_DEPS; do
    if [ -d "$INSTALLED_ROOT/$dep" ]; then
        pass "dependency present: $dep (auto-pulled)"
    else
        fail "dependency ABSENT: $dep (installing $PRIMARY_PLUGIN did not bring it)"
    fi
done
info "installed-plugins dir: $(ls -1 "$INSTALLED_ROOT" 2>/dev/null | tr '\n' ' ' || echo '(none)')"

# =========================================================================
phase 3 "runtime bootstrap on first session (venv + binstub)"
# Firing a session runs the plugins' sessionStart hooks. On a clean machine the
# bootstrap-check hook currently EXITS if there is no deploy-manifest yet -- this
# phase measures whether a first session actually deploys the runtime.
_apply_uv_index_fixture
mkdir -p "$HOME/harness-repo" && ( cd "$HOME/harness-repo" && git init -q && git config user.email t@e && git config user.name t && echo '# harness' > README.md && git add -A && git commit -qm init )
_plugin_dir_args() {
    local args=()
    for p in "$PRIMARY_PLUGIN" $EXPECT_DEPS; do
        [ -d "$INSTALLED_ROOT/$p" ] && args+=( --plugin-dir "$INSTALLED_ROOT/$p" )
    done
    printf '%s\n' "${args[@]}"
}
mapfile -t PLUGIN_ARGS < <(_plugin_dir_args)
( cd "$HOME/harness-repo" && capture "session-first" -- \
    copilot -p "Reply with the single word: ready." --allow-all-tools "${PLUGIN_ARGS[@]}" ) || true
# Give any backgrounded installer a moment.
sleep 8
if [ -x "$HOME/.agent-codespaces/.venv/bin/python" ] || [ -d "$HOME/.agent-codespaces/versions" ]; then
    pass "agent-codespaces runtime venv deployed after first session"
else
    # Classify: on a governed box without the uv-index fixture, provisioning is
    # blocked at uv; otherwise it is the fresh-machine bootstrap-check no-op (#1236).
    if [ -z "$UV_INDEX" ] && grep -qiE 'HandshakeFailure|pythonhosted|SSL|TLS|self.signed|certificate' "$CR_LOGDIR/session-first.log" 2>/dev/null; then
        jam "toolchain-uv" "session-first: uv could not reach its index (public PyPI TLS-blocked on a governed box)" \
            "re-run with CR_UV_INDEX=<internal pip index-url> (the uv-index fixture)"
    else
        jam "path-binstub" "agent-codespaces runtime venv NOT deployed by first session (bootstrap-check no-op'd on fresh machine, #1236)" \
            "first-install should deploy the runtime, not just reconcile"
    fi
fi

# =========================================================================
phase 4 "binstub reachability from a stock login shell"
if [ -e "$HOME/.local/bin/agent-codespaces" ]; then
    pass "binstub deployed: ~/.local/bin/agent-codespaces"
else
    fail "binstub missing: ~/.local/bin/agent-codespaces"
fi
if bash -lc 'command -v agent-codespaces >/dev/null'; then
    pass "agent-codespaces resolves on a fresh login-shell PATH"
else
    fail "agent-codespaces NOT on login-shell PATH (~/.local/bin not exported at login)"
fi
# Cross-plugin shell-out that agent-codespaces relies on:
if bash -lc 'command -v agent-worktrees >/dev/null'; then
    pass "agent-worktrees resolves on PATH (cross-plugin shell-outs will work)"
else
    fail "agent-worktrees NOT on PATH (agent-codespaces account/state-root shell-outs fail open)"
fi

# =========================================================================
phase 5 "plugin loading in headless copilot -p (enabledPlugins vs --plugin-dir)"
# (a) rely on enabledPlugins in settings.json (NO --plugin-dir)
( cd "$HOME/harness-repo" && capture "load-enabledonly" -- \
    copilot -p "List your available skills that mention 'codespace'. If none, say NONE." --allow-all-tools ) || true
# (b) explicit --plugin-dir staging
( cd "$HOME/harness-repo" && capture "load-plugindir" -- \
    copilot -p "List your available skills that mention 'codespace'. If none, say NONE." --allow-all-tools "${PLUGIN_ARGS[@]}" ) || true
if grep -qiE 'codespace' "$CR_LOGDIR/load-enabledonly.log" 2>/dev/null; then
    info "headless -p appears to honor enabledPlugins (codespace skill surfaced WITHOUT --plugin-dir)"
else
    info "headless -p did NOT surface a codespace skill from enabledPlugins alone (expected: needs --plugin-dir)"
fi
if grep -qiE 'codespace' "$CR_LOGDIR/load-plugindir.log" 2>/dev/null; then
    pass "headless -p surfaces codespace skills WITH --plugin-dir staging"
else
    fail "headless -p did not surface codespace skills even WITH --plugin-dir (see log)"
fi

# =========================================================================
phase 6 "register the current repo as a harness project"
if command -v agent-worktrees >/dev/null 2>&1; then
    ( cd "$HOME/harness-repo" && capture "register" -- agent-worktrees register harness-repo ) || true
    if [ -f "$HOME/.agent-worktrees/projects.yaml" ] && grep -qi harness-repo "$HOME/.agent-worktrees/projects.yaml" 2>/dev/null; then
        pass "harness-repo registered (projects.yaml written)"
    else
        fail "harness-repo registration did not produce a projects.yaml entry"
    fi
else
    jam "repo-config" "cannot register: agent-worktrees binstub unavailable (see Phase 3/4)" \
        "provision the runtime first (first-session deploy / setup)"
fi

# =========================================================================
cr_finalize
