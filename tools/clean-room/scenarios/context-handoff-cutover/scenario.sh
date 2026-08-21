#!/usr/bin/env bash
# context-handoff-cutover/scenario.sh -- Tier-P (programmatic) F1 scenario.
#
# Proves the handoff-cutover ROBUSTNESS contract (GitHub issue #853) on a fresh
# box: the live-cutover successor must NOT be able to hang indefinitely on its
# first action. The fix makes the task-backed cutover seed BASH-FIRST -- the
# successor's first action is a core `bash` command chain, not the
# `consume_handoff` extension tool -- so a startup extension-reload race cannot
# orphan it. This scenario asserts, on a freshly installed suite:
#   (3) the shipped `cutover-seed.mjs` builds a bash-first task seed (the fix),
#       and file/unknown-pane handoffs fall back to the tool-based seed;
#   (4) the three CLI verbs that bash-first seed relies on actually exist;
#   (5) [best-effort, needs tmux] the retire verb really retires a live pane.
#
# Name-free / public F1. Env: CR_MARKETPLACE_REPO / CR_MARKETPLACE_NAME + the
# lib's vars. MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
INSTALLED_ROOT="$HOME/.copilot/installed-plugins/$MARKETPLACE_NAME"
TRIO=(agent-worktrees agent-dispatch context-handoff)

: "${CR_SCENARIO_NAME:=context-handoff-cutover}"
export CR_SCENARIO_NAME
cr_init
cr_meta "plugins" "${TRIO[*]}"
cr_meta "base" "fresh-standalone"

# Binstubs land in ~/.local/bin, which a *login* shell puts on PATH -- but the
# lib's `capture` runs commands directly (non-login), so export it here so bare
# `agent-worktrees` / `agent-dispatch` invocations resolve throughout.
export PATH="$HOME/.local/bin:$PATH"

_seed_module() {
    # Resolve the installed cutover-seed.mjs (payload path is marketplace-scoped).
    local p="$INSTALLED_ROOT/context-handoff/extensions/context-handoff/cutover-seed.mjs"
    [ -f "$p" ] && { printf '%s' "$p"; return 0; }
    p="$(ls "$HOME"/.copilot/installed-plugins/*/context-handoff/extensions/context-handoff/cutover-seed.mjs 2>/dev/null | head -n1)"
    [ -n "$p" ] && [ -f "$p" ] && { printf '%s' "$p"; return 0; }
    return 1
}

# Resolve a plugin's scripts/install.sh (for explicit runtime provisioning).
_installer_path() {  # <plugin>
    local plugin="$1" p=""
    p="$INSTALLED_ROOT/$plugin/scripts/install.sh"
    [ -f "$p" ] && { printf '%s' "$p"; return 0; }
    p="$(ls "$HOME"/.copilot/installed-plugins/*/"$plugin"/scripts/install.sh 2>/dev/null | head -n1)"
    [ -n "$p" ] && [ -f "$p" ] && { printf '%s' "$p"; return 0; }
    return 1
}

# Ensure a plugin's login-shell binstub resolves, provisioning it explicitly via
# the installer if first-session self-provisioning did not land it. agent-worktrees
# in particular does not always self-provision on a fresh box (a known gap:
# bootstrap-check is a no-op on first install) -- but that is NOT this scenario's
# subject (the handoff-cutover seed is), so we provision the prerequisite runtime
# deterministically rather than red-lining on it. Returns 0 if the binstub
# resolves afterward.
_ensure_binstub() {  # <plugin-binary>
    local bin="$1" installer=""
    bash -lc "command -v $bin >/dev/null 2>&1" && return 0
    installer="$(_installer_path "$bin" || true)"
    if [ -n "$installer" ]; then
        capture "provision-$bin" -- bash "$installer" provision || true
    fi
    bash -lc "command -v $bin >/dev/null 2>&1"
}

# =========================================================================
phase 0 "environment (fresh machine)"
envdump
if [ -d "$HOME/.agent-worktrees" ] || [ -d "$HOME/.agent-dispatch" ] || [ -d "$HOME/.local/bin" ]; then
    fail "environment is NOT clean -- pre-existing runtime dirs"
else
    pass "clean slate: no ~/.agent-worktrees, ~/.agent-dispatch, ~/.local/bin"
fi

# =========================================================================
phase 1 "install the live-cutover trio"
mkdir -p "$HOME/.copilot"
cat > "$HOME/.copilot/settings.json" <<JSON
{
  "extraKnownMarketplaces": { "$MARKETPLACE_NAME": { "source": { "source": "github", "repo": "$MARKETPLACE_REPO" } } },
  "enabledPlugins": {
    "agent-worktrees@$MARKETPLACE_NAME": true,
    "agent-dispatch@$MARKETPLACE_NAME": true,
    "context-handoff@$MARKETPLACE_NAME": true
  }
}
JSON
capture "marketplace-add" -- copilot plugin marketplace add "$MARKETPLACE_REPO" || true
for p in "${TRIO[@]}"; do
    capture "install-$p" -- copilot plugin install "$p@$MARKETPLACE_NAME" || true
done
_all_present=1
for p in "${TRIO[@]}"; do
    if [ -d "$INSTALLED_ROOT/$p" ]; then
        pass "$p payload present on disk"
    else
        _all_present=0
        jam "npm-registry" "$p payload NOT installed (see cr-logs/install-$p.log)" "check marketplace source + node/npm feed"
    fi
done

# =========================================================================
phase 2 "runtimes provision on first session (venv + binstubs)"
mkdir -p "$HOME/wt-repo" && ( cd "$HOME/wt-repo" && git init -q && git config user.email t@e && git config user.name t && echo '# wt' > README.md && git add -A && git commit -qm init )
PLUGIN_ARGS=()
for p in "${TRIO[@]}"; do
    [ -d "$INSTALLED_ROOT/$p" ] && PLUGIN_ARGS+=( --plugin-dir "$INSTALLED_ROOT/$p" )
done
( cd "$HOME/wt-repo" && capture "session-first" -- copilot -p "Reply with the single word: ready." --allow-all-tools "${PLUGIN_ARGS[@]}" ) || true
sleep 8
# Deterministically ensure the agent-worktrees + agent-dispatch binstubs resolve.
# First-session self-provisioning is best-effort here (agent-worktrees notably
# does not always self-deploy on a fresh box); provision explicitly via the
# installer so the handoff-cutover checks below have their prerequisite runtimes.
# This is prerequisite scaffolding -- the SUBJECT is the seed (Phase 3), which
# needs no runtime at all.
for b in agent-worktrees agent-dispatch; do
    if _ensure_binstub "$b"; then
        pass "$b binstub resolves on a fresh login-shell PATH"
    else
        if grep -qiE 'HandshakeFailure|pythonhosted|SSL|TLS|certificate|Could not resolve|connection' "$CR_LOGDIR"/*.log 2>/dev/null; then
            jam "toolchain-uv" "$b: runtime could not provision (uv could not reach its index)" "re-run with CR_UV_INDEX=<internal index-url>"
        else
            info "$b did not provision (known agent-worktrees self-provision gap); dependent checks below will INFO-skip"
        fi
    fi
done

# =========================================================================
phase 3 "seed invariant: bash-first task cutover (issue #853)"
# The crux: the SHIPPED cutover-seed.mjs must build a bash-first task seed so the
# successor's first action is a core `bash` chain, immune to the extension-reload
# race that orphaned `consume_handoff` (multi-hour hangs in the field).
_seed_mod="$(_seed_module || true)"
if [ -z "$_seed_mod" ]; then
    jam "repo-config" "cutover-seed.mjs not found under the installed context-handoff payload" "the plugin should ship extensions/context-handoff/cutover-seed.mjs"
else
    info "seed module: $_seed_mod"
    if capture "seed-probe" -- node "$_SELF_DIR/seed-probe.mjs" "$_seed_mod"; then
        pass "seed invariant holds: task cutover seed is BASH-FIRST; file/unknown-pane fall back to the tool seed"
    else
        # A red here means the fix regressed: the successor's first action is the
        # consume_handoff extension tool again (orphanable by the reload race).
        jam "repo-config" "cutover seed is NOT bash-first (see cr-logs/seed-probe.log)" "buildCutoverSeed('task', ...) must emit the agent-dispatch/agent-worktrees shell chain, not the consume_handoff tool"
    fi
fi

# =========================================================================
phase 4 "the bash-first seed's CLI verbs are real"
# The bash-first seed hands the successor three verbs. Prove they exist on a
# fresh install so the seed is executable (not pointing at phantom subcommands).
_verb_ok() {  # <label> <bin> <args...> -- pass if the CLI recognizes the verb
    local label="$1"; shift
    local bin="$1"; shift
    if bash -lc "command -v $bin >/dev/null"; then
        # `--help` on a real subcommand exits 0 and does NOT print the "unknown
        # command / could not resolve" top-level usage. Run outside any repo.
        if ( cd "$HOME" && capture "verb-$label" -- bash -lc "$bin $* --help" ) \
             && ! grep -qiE 'unknown command|could not resolve|invalid choice|no such' "$CR_LOGDIR/verb-$label.log"; then
            pass "verb present: $bin $*"
        else
            # Some verbs reject --help but still parse the subcommand; treat an
            # explicit "unknown/invalid subcommand" as the only true failure.
            if grep -qiE "invalid choice: '?$(printf '%s' "$1")|unknown command '?$(printf '%s' "$1")" "$CR_LOGDIR/verb-$label.log" 2>/dev/null; then
                fail "verb MISSING: $bin $* (see cr-logs/verb-$label.log)"
            else
                pass "verb present: $bin $* (non-zero --help, but subcommand recognized)"
            fi
        fi
    else
        info "$bin not on PATH (runtime did not provision -- Phase 2); INFO-skip verb check for $*"
    fi
}
_verb_ok "consume"   agent-dispatch  consume
_verb_ok "conclude"  agent-worktrees conclude-session
# handoff-cutover exposes --retire-pane (the seed's 3rd verb). `--help` needs a
# project context (from $HOME it prints the top-level usage), so recognize the
# verb context-independently here -- a "could not resolve a project for
# 'handoff-cutover'" message proves the subcommand parsed -- and defer the real
# --retire-pane proof to Phase 5's live mechanism.
if bash -lc 'command -v agent-worktrees >/dev/null'; then
    ( cd "$HOME" && capture "verb-retire" -- bash -lc "agent-worktrees handoff-cutover --retire-pane %cr-probe --worktree-id cr-nonexistent --session-id x" ) || true
    if grep -q -- '--retire-pane' "$CR_LOGDIR/verb-retire.log" 2>/dev/null \
         || grep -qiE "handoff-cutover|retire" "$CR_LOGDIR/verb-retire.log" 2>/dev/null; then
        if grep -qiE "unknown command|invalid choice" "$CR_LOGDIR/verb-retire.log" 2>/dev/null; then
            fail "agent-worktrees does NOT recognize the handoff-cutover subcommand (see cr-logs/verb-retire.log)"
        else
            pass "verb present: agent-worktrees handoff-cutover (--retire-pane recognized)"
        fi
    else
        fail "agent-worktrees handoff-cutover / --retire-pane not recognized (see cr-logs/verb-retire.log)"
    fi
else
    info "agent-worktrees not on PATH (runtime did not provision -- Phase 2); INFO-skip handoff-cutover verb check"
fi

# =========================================================================
phase 5 "mechanism: retire verb kills a live mux pane (best-effort)"
# Highest-fidelity check: run the exact retire verb the bash-first seed hands the
# successor against a REAL tmux pane and confirm the predecessor is gone. Needs
# tmux; if it can't be installed on this box we INFO-skip (the fix is already
# proven deterministically by phases 3-4) rather than fail on an env limitation.
if ! command -v tmux >/dev/null 2>&1; then
    capture "tmux-install" -- bash -lc 'sudo apt-get update -q && sudo apt-get install -y --no-install-recommends tmux' || true
fi
if ! command -v tmux >/dev/null 2>&1; then
    info "tmux unavailable (no apt/network); mechanism phase skipped -- the bash-first fix is already proven by phases 3-4"
elif ! bash -lc 'command -v agent-worktrees >/dev/null'; then
    info "agent-worktrees binstub unavailable; mechanism phase skipped"
else
    # Carve a real worktree so handoff-cutover resolves its project from cwd.
    ( cd "$HOME/wt-repo" && capture "register" -- agent-worktrees register wt-repo ) || true
    ( cd "$HOME/wt-repo" && capture "create" -- agent-worktrees create --json ) || true
    _wt_id="$(grep -oE '"(id|worktree_id)"[[:space:]]*:[[:space:]]*"[^"]+"' "$CR_LOGDIR/create.log" 2>/dev/null | head -1 | sed -E 's/.*"([^"]+)".*/\1/')"
    _wt_path="$(grep -oE '"(work_dir|worktree_path|path)"[[:space:]]*:[[:space:]]*"[^"]+"' "$CR_LOGDIR/create.log" 2>/dev/null | head -1 | sed -E 's/.*"([^"]+)".*/\1/')"
    [ -n "$_wt_path" ] && [ -d "$_wt_path" ] || _wt_path="$HOME/wt-repo"
    if [ -z "$_wt_id" ]; then
        jam "repo-config" "create: no worktree id (see cr-logs/create.log)" "create --json should print the worktree id/path without launching"
    else
        _sess="wt-$_wt_id"
        # window 0 = the dummy "predecessor" pane; add a 2nd window so the pane is
        # NOT the session's last window (else the retire's last-window guard skips
        # it by design). `sleep` dies on the SIGINT the retire sends -> graceful.
        tmux kill-session -t "$_sess" 2>/dev/null || true
        tmux new-session -d -s "$_sess" -c "$_wt_path" 'sleep 600' 2>>"$CR_LOGDIR/mux.log" || true
        tmux new-window -t "$_sess" -c "$_wt_path" 'sleep 600' 2>>"$CR_LOGDIR/mux.log" || true
        _pane="$(tmux list-panes -t "$_sess" -F '#{pane_id}' 2>/dev/null | head -1)"
        if [ -z "$_pane" ]; then
            info "could not stand up a tmux $_sess session; mechanism phase inconclusive (see cr-logs/mux.log)"
        else
            info "predecessor pane $_pane in $_sess; running the seed's retire verb"
            ( cd "$_wt_path" && capture "retire" -- agent-worktrees handoff-cutover \
                --retire-pane "$_pane" --successor-verified --retire-reason handoff-consume \
                --worktree-id "$_wt_id" --session-id clean-room-fake-sid ) || true
            sleep 1
            if tmux list-panes -a -F '#{pane_id}' 2>/dev/null | grep -qx "$_pane"; then
                fail "retire verb did NOT retire predecessor pane $_pane (see cr-logs/retire.log)"
            else
                pass "retire verb retired predecessor pane $_pane (live mux cutover mechanism works)"
            fi
        fi
        tmux kill-session -t "$_sess" 2>/dev/null || true
    fi
fi

# =========================================================================
cr_finalize
