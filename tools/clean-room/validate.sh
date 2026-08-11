#!/usr/bin/env bash
# Clean-room validation driver -- runs INSIDE the disposable container as the
# unprivileged `operator`, from a stock login shell. It reproduces a naive
# operator's fresh-machine experience and turns every "I believe / mixed
# reports" question about the copilot-extensions install flow into a hard
# PASS/FAIL/INFO line.
#
# It asserts on FILESYSTEM OUTCOMES (venv present, binstub on PATH, plugins on
# disk, project registered) rather than on exact CLI syntax, so it stays robust
# if a `copilot plugin ...` subcommand spelling changes -- it records the CLI
# surface it saw and captures all output for triage.
#
# Configurable via env:
#   CR_MARKETPLACE_REPO   owner/name of the marketplace repo (GitHub)
#                         default: ThomasMichon/copilot-extensions
#   CR_MARKETPLACE_NAME   marketplace id used in <plugin>@<name> sources
#                         default: copilot-extensions
#   CR_PRIMARY_PLUGIN     the single plugin a naive user installs first
#                         default: agent-codespaces
#   CR_EXPECT_DEPS        space-separated plugins that SHOULD arrive with it
#                         default: "agent-bridge agent-worktrees"
#   CR_REPORT             path for the JSON report (default ~/cr-report.json)
set -uo pipefail

MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
PRIMARY_PLUGIN="${CR_PRIMARY_PLUGIN:-agent-codespaces}"
EXPECT_DEPS="${CR_EXPECT_DEPS:-agent-bridge agent-worktrees}"
REPORT="${CR_REPORT:-$HOME/cr-report.json}"

INSTALLED_ROOT="$HOME/.copilot/installed-plugins/$MARKETPLACE_NAME"
LOGDIR="$HOME/cr-logs"
mkdir -p "$LOGDIR"

# ---- result accounting ---------------------------------------------------
PASS=0 FAIL=0
declare -a RESULTS
_rec() {  # kind message
    local kind="$1"; shift
    local msg="$*"
    case "$kind" in
        PASS) PASS=$((PASS+1)); printf '  \033[32m[PASS]\033[0m %s\n' "$msg" ;;
        FAIL) FAIL=$((FAIL+1)); printf '  \033[31m[FAIL]\033[0m %s\n' "$msg" ;;
        INFO) printf '  \033[36m[INFO]\033[0m %s\n' "$msg" ;;
    esac
    # JSON-escape the message crudely (quotes + backslashes).
    local esc=${msg//\\/\\\\}; esc=${esc//\"/\\\"}
    RESULTS+=("{\"kind\":\"$kind\",\"msg\":\"$esc\"}")
}
_phase() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }
_run() {  # label -- command...   (captures output to a log, echoes tail)
    local label="$1"; shift
    local log="$LOGDIR/${label}.log"
    printf '  $ %s\n' "$*" | tee -a "$log" >/dev/null
    "$@" >"$log" 2>&1
    local rc=$?
    printf '  (%s exit=%s, log=%s)\n' "$label" "$rc" "$log"
    return $rc
}

# =========================================================================
_phase "Phase 0 -- environment (must look like a fresh machine)"
_rec INFO "whoami=$(whoami) HOME=$HOME"
_rec INFO "copilot: $(command -v copilot || echo MISSING) ($(copilot --version 2>/dev/null | head -1))"
_rec INFO "uv: $(command -v uv || echo MISSING)  git: $(command -v git || echo MISSING)  node: $(node --version 2>/dev/null)"
_rec INFO "login-shell PATH: $(bash -lc 'echo $PATH')"
if [ -d "$HOME/.agent-codespaces" ] || [ -d "$HOME/.agent-worktrees" ] || [ -d "$HOME/.local/bin" ]; then
    _rec FAIL "environment is NOT clean -- pre-existing ~/.agent-* or ~/.local/bin"
else
    _rec PASS "clean slate: no ~/.agent-*, no ~/.local/bin"
fi
# Record the CLI surface so we know what subcommands exist on this version.
copilot --help              >"$LOGDIR/copilot-help.log"        2>&1 || true
copilot plugin --help       >"$LOGDIR/copilot-plugin-help.log" 2>&1 || true

# =========================================================================
_phase "Phase 1 -- register marketplace + install ONE plugin ($PRIMARY_PLUGIN)"
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
_rec INFO "wrote ~/.copilot/settings.json (marketplace=$MARKETPLACE_NAME repo=$MARKETPLACE_REPO)"

# Attempt the install a couple of plausible ways; success is judged by disk.
_run "marketplace-add" copilot plugin marketplace add "$MARKETPLACE_REPO" || true
_run "install-primary" copilot plugin install "$PRIMARY_PLUGIN@$MARKETPLACE_NAME" || true

if [ -d "$INSTALLED_ROOT/$PRIMARY_PLUGIN" ]; then
    _rec PASS "$PRIMARY_PLUGIN payload present on disk ($INSTALLED_ROOT/$PRIMARY_PLUGIN)"
else
    _rec FAIL "$PRIMARY_PLUGIN payload NOT installed (see $LOGDIR/install-primary.log)"
fi

# =========================================================================
_phase "Phase 2 -- dependency chain (does one install pull the rest?)"
for dep in $EXPECT_DEPS; do
    if [ -d "$INSTALLED_ROOT/$dep" ]; then
        _rec PASS "dependency present: $dep (auto-pulled)"
    else
        _rec FAIL "dependency ABSENT: $dep (installing $PRIMARY_PLUGIN did not bring it)"
    fi
done
_rec INFO "installed-plugins dir: $(ls -1 "$INSTALLED_ROOT" 2>/dev/null | tr '\n' ' ' || echo '(none)')"

# =========================================================================
_phase "Phase 3 -- runtime bootstrap on first session (venv + binstub)"
# Firing a session runs the plugins' sessionStart hooks. On a clean machine the
# bootstrap-check hook currently EXITS if there is no deploy-manifest yet -- this
# phase measures whether a first session actually deploys the runtime.
mkdir -p "$HOME/harness-repo" && ( cd "$HOME/harness-repo" && git init -q && git config user.email t@e && git config user.name t && echo '# harness' > README.md && git add -A && git commit -qm init )
_plugin_dir_args() {
    local args=()
    for p in "$PRIMARY_PLUGIN" $EXPECT_DEPS; do
        [ -d "$INSTALLED_ROOT/$p" ] && args+=( --plugin-dir "$INSTALLED_ROOT/$p" )
    done
    printf '%s\n' "${args[@]}"
}
mapfile -t PLUGIN_ARGS < <(_plugin_dir_args)
( cd "$HOME/harness-repo" && _run "session-first" \
    copilot -p "Reply with the single word: ready." --allow-all-tools "${PLUGIN_ARGS[@]}" ) || true
# Give any backgrounded installer a moment.
sleep 8
if [ -x "$HOME/.agent-codespaces/.venv/bin/python" ] || [ -d "$HOME/.agent-codespaces/versions" ]; then
    _rec PASS "agent-codespaces runtime venv deployed after first session"
else
    _rec FAIL "agent-codespaces runtime venv NOT deployed by first session (bootstrap-check no-op'd on fresh machine)"
fi

# =========================================================================
_phase "Phase 4 -- binstub reachability from a stock login shell"
if [ -e "$HOME/.local/bin/agent-codespaces" ]; then
    _rec PASS "binstub deployed: ~/.local/bin/agent-codespaces"
else
    _rec FAIL "binstub missing: ~/.local/bin/agent-codespaces"
fi
if bash -lc 'command -v agent-codespaces >/dev/null'; then
    _rec PASS "agent-codespaces resolves on a fresh login-shell PATH"
else
    _rec FAIL "agent-codespaces NOT on login-shell PATH (~/.local/bin not exported at login)"
fi
# Cross-plugin shell-out that agent-codespaces relies on:
if bash -lc 'command -v agent-worktrees >/dev/null'; then
    _rec PASS "agent-worktrees resolves on PATH (cross-plugin shell-outs will work)"
else
    _rec FAIL "agent-worktrees NOT on PATH (agent-codespaces account/state-root shell-outs fail open)"
fi

# =========================================================================
_phase "Phase 5 -- plugin loading in headless copilot -p (enabledPlugins vs --plugin-dir)"
# (a) rely on enabledPlugins in settings.json (NO --plugin-dir)
( cd "$HOME/harness-repo" && _run "load-enabledonly" \
    copilot -p "List your available skills that mention 'codespace'. If none, say NONE." --allow-all-tools ) || true
# (b) explicit --plugin-dir staging
( cd "$HOME/harness-repo" && _run "load-plugindir" \
    copilot -p "List your available skills that mention 'codespace'. If none, say NONE." --allow-all-tools "${PLUGIN_ARGS[@]}" ) || true
if grep -qiE 'codespace' "$LOGDIR/load-enabledonly.log" 2>/dev/null; then
    _rec INFO "headless -p appears to honor enabledPlugins (codespace skill surfaced WITHOUT --plugin-dir)"
else
    _rec INFO "headless -p did NOT surface a codespace skill from enabledPlugins alone (expected: needs --plugin-dir)"
fi
if grep -qiE 'codespace' "$LOGDIR/load-plugindir.log" 2>/dev/null; then
    _rec PASS "headless -p surfaces codespace skills WITH --plugin-dir staging"
else
    _rec FAIL "headless -p did not surface codespace skills even WITH --plugin-dir (see log)"
fi

# =========================================================================
_phase "Phase 6 -- register the current repo as a harness project"
if command -v agent-worktrees >/dev/null 2>&1; then
    ( cd "$HOME/harness-repo" && _run "register" agent-worktrees register harness-repo ) || true
    if [ -f "$HOME/.agent-worktrees/projects.yaml" ] && grep -qi harness-repo "$HOME/.agent-worktrees/projects.yaml" 2>/dev/null; then
        _rec PASS "harness-repo registered (projects.yaml written)"
    else
        _rec FAIL "harness-repo registration did not produce a projects.yaml entry"
    fi
else
    _rec FAIL "cannot register: agent-worktrees binstub unavailable (see Phase 2/4)"
fi

# =========================================================================
_phase "Summary"
printf '  \033[1m%d passed, %d failed\033[0m\n' "$PASS" "$FAIL"
{
    printf '{\n  "marketplace_repo": "%s",\n  "primary_plugin": "%s",\n' "$MARKETPLACE_REPO" "$PRIMARY_PLUGIN"
    printf '  "copilot_version": "%s",\n' "$(copilot --version 2>/dev/null | head -1)"
    printf '  "passed": %d, "failed": %d,\n  "results": [\n' "$PASS" "$FAIL"
    local_first=1
    for r in "${RESULTS[@]}"; do
        [ $local_first -eq 1 ] && local_first=0 || printf ',\n'
        printf '    %s' "$r"
    done
    printf '\n  ]\n}\n'
} > "$REPORT"
printf '  report: %s\n  logs:   %s\n' "$REPORT" "$LOGDIR"

[ "$FAIL" -eq 0 ]
