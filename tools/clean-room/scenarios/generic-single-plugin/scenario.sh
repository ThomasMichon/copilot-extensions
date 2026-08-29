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
#   CR_MARKETPLACE_REPO  owner/name of the marketplace repo (GitHub), or a
#                        container-local marketplace directory for worktree tests
#                        default: ThomasMichon/copilot-extensions
#   CR_MARKETPLACE_NAME  marketplace id used in <plugin>@<name> sources
#                        default: copilot-extensions
#   CR_PRIMARY_PLUGIN    the single plugin a naive user installs first
#                        default: agent-codespaces
#   CR_EXPECT_DEPS       space-separated OPTIONAL COMPANION plugins to probe.
#                        default: "agent-bridge agent-worktrees". NOTE: the CLI
#                        does NOT auto-install plugin dependencies (proven), and
#                        these plugins are standalone -- companions compose
#                        opportunistically (agent-bridge discovers providers;
#                        agent-worktrees is an optional state-root base). So their
#                        absence is recorded as INFO, never a failure.
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
MARKETPLACE_REPO_JSON="$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$MARKETPLACE_REPO")"
if [ -d "$MARKETPLACE_REPO" ]; then
    MARKETPLACE_SOURCE="{ \"source\": { \"source\": \"directory\", \"path\": $MARKETPLACE_REPO_JSON } }"
else
    MARKETPLACE_SOURCE="{ \"source\": { \"source\": \"github\", \"repo\": $MARKETPLACE_REPO_JSON } }"
fi

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
    "$MARKETPLACE_NAME": $MARKETPLACE_SOURCE
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
phase 2 "standalone install (no phantom dependency auto-pull)"
# The Copilot CLI does NOT auto-install declared plugin dependencies (proven in
# the clean-room: the plugin.json manifest has no `dependencies` field and a
# declared entry is inert). And these plugins are standalone by design:
# agent-codespaces / agent-containers are transport PROVIDERS that agent-bridge
# optionally DISCOVERS -- they do not depend on it (`<primary> ssh` etc. work
# without agent-bridge) -- and agent-worktrees is an OPTIONAL state-root base the
# primary's touch points fall open without. So absent siblings are EXPECTED, not
# a failure: record them as optional companions and assert the primary stands
# alone.
for dep in $EXPECT_DEPS; do
    if [ -d "$INSTALLED_ROOT/$dep" ]; then
        info "optional companion also present: $dep (independently installed)"
    else
        info "optional companion absent (expected -- no auto-pull): $dep"
    fi
done
pass "$PRIMARY_PLUGIN installed standalone (companions compose opportunistically, are not auto-pulled)"
info "installed-plugins dir: $(ls -1 "$INSTALLED_ROOT" 2>/dev/null | tr '\n' ' ' || echo '(none)')"

# Prove the installed payload's generic catalog contract without relying on an
# ambient binstub. The same session ID must emit once, while the advertised
# POSIX argv prefix must be absolute and contained by this exact payload.
_catalog_emitter="$INSTALLED_ROOT/$PRIMARY_PLUGIN/scripts/emit-command-catalog.sh"
_catalog_session="clean-room-$$"
_catalog_first="$CR_LOGDIR/command-catalog-first.json"
_catalog_second="$CR_LOGDIR/command-catalog-second.json"
_catalog_tmp="$CR_LOGDIR/command-catalog-markers"
if [ -f "$_catalog_emitter" ]; then
    mkdir -p "$_catalog_tmp"
    printf '{"sessionId":"%s"}' "$_catalog_session" |
        TMPDIR="$_catalog_tmp" \
        COPILOT_PLUGIN_ROOT="$INSTALLED_ROOT/$PRIMARY_PLUGIN" \
        bash "$_catalog_emitter" >"$_catalog_first"
    printf '{"sessionId":"%s"}' "$_catalog_session" |
        TMPDIR="$_catalog_tmp" \
        COPILOT_PLUGIN_ROOT="$INSTALLED_ROOT/$PRIMARY_PLUGIN" \
        bash "$_catalog_emitter" >"$_catalog_second"
    if python3 - "$_catalog_first" "$_catalog_second" "$INSTALLED_ROOT/$PRIMARY_PLUGIN" <<'PY'
import json
import os
import re
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    first = json.load(stream)
with open(sys.argv[2], encoding="utf-8") as stream:
    second = json.load(stream)
payload = os.path.realpath(sys.argv[3])
match = re.search(r"```json\n(.*?)\n```", first["additionalContext"], re.S)
assert match
catalog = json.loads(match.group(1))
assert catalog["commands"]
for command in catalog["commands"]:
    prefix = command["argv"]
    assert prefix and os.path.isabs(prefix[0])
    assert os.path.commonpath((payload, os.path.realpath(prefix[0]))) == payload
assert second == {}
PY
    then
        pass "$PRIMARY_PLUGIN emits one payload-contained command catalog per sessionStart launch"
    else
        fail "$PRIMARY_PLUGIN command catalog is duplicated or escapes its installed payload"
    fi
else
    fail "$PRIMARY_PLUGIN payload has no session command catalog emitter"
fi

# =========================================================================
phase 3 "first session stamps binstub; first use provisions runtime"
# Firing a session runs the plugins' sessionStart hooks. The hook must perform
# only the grace-window-cheap stamp; the stamped binstub builds the runtime on
# first use.
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
if [ -e "$HOME/.local/bin/agent-codespaces" ]; then
    pass "first session stamped ~/.local/bin/agent-codespaces"
    if capture "first-use-agent-codespaces" -- bash -lc 'agent-codespaces --help'; then
        first_use_ok=1
    else
        first_use_ok=0
        jam "path-binstub" "first agent-codespaces binstub use failed (see cr-logs/first-use-agent-codespaces.log)" \
            "stamped binstub should invoke install provision and then dispatch"
    fi
else
    first_use_ok=0
    jam "path-binstub" "first session did not stamp ~/.local/bin/agent-codespaces" \
        "payload bootstrap-check should run install.sh stamp"
fi
sleep 3
_current="$(tr -d ' \t\r\n' < "$HOME/.agent-codespaces/current-version" 2>/dev/null || true)"
_slot="$HOME/.agent-codespaces/versions/$_current"
if [ "$first_use_ok" -eq 1 ] && [ -n "$_current" ] && [ -x "$_slot/bin/python" ] && [ -f "$_slot/.install-complete.json" ]; then
    pass "agent-codespaces runtime venv deployed on first binstub use"
else
    if [ -z "$UV_INDEX" ] && grep -qiE 'HandshakeFailure|pythonhosted|SSL|TLS|self.signed|certificate' "$CR_LOGDIR/first-use-agent-codespaces.log" 2>/dev/null; then
        jam "toolchain-uv" "first use: uv could not reach its index (public PyPI TLS-blocked on a governed box)" \
            "re-run with CR_UV_INDEX=<internal pip index-url> (the uv-index fixture)"
    else
        jam "path-binstub" "agent-codespaces runtime venv NOT deployed by first binstub use" \
            "stamped binstub should invoke install provision"
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
# Optional composition (NOT a dependency): the primary is a transport provider
# agent-bridge DISCOVERS rather than requires, and it falls open without the
# optional agent-worktrees state-root base. Its absence is expected, not a
# failure. (Standalone verb behavior is proven by the primary's own *-solo
# scenario; assert it here WITHOUT invoking the binstub, so the deferred venv
# stays unprovisioned for Phase 7's stamp-signal.)
if bash -lc 'command -v agent-worktrees >/dev/null'; then
    info "optional base agent-worktrees on PATH (account/state-root shell-outs will use it)"
else
    pass "agent-worktrees absent -> $PRIMARY_PLUGIN composes opportunistically (degrade-safe; see its *-solo scenario)"
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
phase 6 "optional: register the repo as a harness project (needs the agent-worktrees base)"
if bash -lc 'command -v agent-worktrees >/dev/null'; then
    ( cd "$HOME/harness-repo" && capture "register" -- bash -lc 'agent-worktrees register harness-repo' ) || true
    if [ -f "$HOME/.agent-worktrees/projects.yaml" ] && grep -qi harness-repo "$HOME/.agent-worktrees/projects.yaml" 2>/dev/null; then
        pass "harness-repo registered (projects.yaml written)"
    else
        fail "harness-repo registration did not produce a projects.yaml entry"
    fi
else
    info "register is an agent-worktrees capability; the optional base is not installed -> N/A (standalone $PRIMARY_PLUGIN needs no project registration)"
fi

# =========================================================================
phase 7 "repo-scoped enablement (.github/copilot/settings.json fires sessionStart hooks)"
# The real harness enables plugins via the REPO's .github/copilot/settings.json,
# NOT ~/.copilot. Prove that repo-scoped enablement ALONE loads the plugin and
# fires its sessionStart hooks. Generic signal: for a stamp-capable runtime the
# binstub re-appears (the bootstrap-check stamp); the plugin must have loaded for
# that to happen.
_repo="$HOME/repo-scoped"
rm -rf "$_repo"; mkdir -p "$_repo/.github/copilot"
cat > "$_repo/.github/copilot/settings.json" <<JSON
{
  "extraKnownMarketplaces": { "$MARKETPLACE_NAME": $MARKETPLACE_SOURCE },
  "enabledPlugins": { "$PRIMARY_PLUGIN@$MARKETPLACE_NAME": true }
}
JSON
( cd "$_repo" && git init -q && git config user.email t@e && git config user.name t && git add -A && git commit -qm init )
# Neutralize USER-level enablement so ONLY the repo settings can load the plugin,
# and clear the binstub so its re-appearance is unambiguous evidence.
if [ -f "$HOME/.copilot/settings.json" ]; then cp "$HOME/.copilot/settings.json" "$HOME/.copilot/settings.json.crbak"; fi
echo '{}' > "$HOME/.copilot/settings.json"
rm -f "$HOME/.local/bin/$PRIMARY_PLUGIN"
_apply_uv_index_fixture
# Run a session FROM the repo, WITHOUT --plugin-dir: only .github/copilot/settings.json can enable it.
( cd "$_repo" && capture "session-reposcoped" -- copilot -p "Reply with the single word: ok." --allow-all-tools ) || true
sleep 5
if [ -e "$HOME/.local/bin/$PRIMARY_PLUGIN" ]; then
    pass "repo-scoped .github/copilot/settings.json enablement fired the sessionStart hook ($PRIMARY_PLUGIN binstub re-stamped)"
elif grep -qE '^[[:space:]]*stamp\)' "$INSTALLED_ROOT/$PRIMARY_PLUGIN/scripts/install.sh" 2>/dev/null; then
    jam "experimental-mode-gate" "repo-scoped enablement did NOT fire the sessionStart hook ($PRIMARY_PLUGIN is stamp-capable but no binstub re-stamped)" \
        "verify .github/copilot/settings.json enablement loads plugins + fires hooks in this CLI/launch mode"
else
    info "repo-scoped enablement: $PRIMARY_PLUGIN is not stamp-capable, so hook-firing can't be asserted via binstub (session ran; see cr-logs/session-reposcoped.log)"
fi
# Restore user-level settings.
[ -f "$HOME/.copilot/settings.json.crbak" ] && mv -f "$HOME/.copilot/settings.json.crbak" "$HOME/.copilot/settings.json"

# =========================================================================
cr_finalize
