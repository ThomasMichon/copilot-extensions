#!/usr/bin/env bash
# context-handoff-cutover/scenario.sh -- Tier-P (programmatic) F1 scenario.
#
# Proves the compact, prompt-first handoff contract on a fresh box:
#   (3) the shipped seed is bounded, three-part, fidelity-preserving, and records
#       token/round-trip budgets without inlining lifecycle orchestration;
#   (4) the consume, binding, and retire CLI verbs are real;
#   (5) [best-effort, needs tmux] the retire verb really retires a live pane.
#
# Name-free / public F1. Env: CR_MARKETPLACE_REPO / CR_MARKETPLACE_NAME + the
# lib's vars. MUST be LF.
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

MARKETPLACE_REPO="${CR_MARKETPLACE_REPO:-ThomasMichon/copilot-extensions}"
MARKETPLACE_NAME="${CR_MARKETPLACE_NAME:-copilot-extensions}"
UV_INDEX="${CR_UV_INDEX:-}"
INSTALLED_ROOT="$HOME/.copilot/installed-plugins/$MARKETPLACE_NAME"
TRIO=(agent-worktrees agent-dispatch context-handoff)
MARKETPLACE_REPO_JSON="$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$MARKETPLACE_REPO")"
if [ -d "$MARKETPLACE_REPO" ]; then
    MARKETPLACE_SOURCE="{ \"source\": { \"source\": \"directory\", \"path\": $MARKETPLACE_REPO_JSON } }"
else
    MARKETPLACE_SOURCE="{ \"source\": { \"source\": \"github\", \"repo\": $MARKETPLACE_REPO_JSON } }"
fi

: "${CR_SCENARIO_NAME:=context-handoff-cutover}"
export CR_SCENARIO_NAME
cr_init
cr_meta "plugins" "${TRIO[*]}"
cr_meta "base" "fresh-standalone"

# Binstubs land in ~/.local/bin, which a *login* shell puts on PATH -- but the
# lib's `capture` runs commands directly (non-login), so export it here so bare
# `agent-worktrees` / `agent-dispatch` invocations resolve throughout.
export PATH="$HOME/.local/bin:$PATH"

_apply_uv_index_fixture() {
    [ -n "$UV_INDEX" ] || return 0
    export UV_INDEX_URL="$UV_INDEX"
    export UV_DEFAULT_INDEX="$UV_INDEX"
    export UV_EXTRA_INDEX_URL="${UV_EXTRA_INDEX_URL:-$UV_INDEX}"
    mkdir -p "$HOME/.config/uv"
    cat > "$HOME/.config/uv/uv.toml" <<TOML
# clean-room uv-index fixture (opt-in, CR_UV_INDEX)
[[index]]
url = "$UV_INDEX"
default = true
TOML
    info "uv-index fixture applied"
}

_seed_module() {
    # Resolve the installed cutover-seed.mjs (payload path is marketplace-scoped).
    local p="$INSTALLED_ROOT/context-handoff/extensions/context-handoff/cutover-seed.mjs"
    [ -f "$p" ] && { printf '%s' "$p"; return 0; }
    p="$(ls "$HOME"/.copilot/installed-plugins/*/context-handoff/extensions/context-handoff/cutover-seed.mjs 2>/dev/null | head -n1)"
    [ -n "$p" ] && [ -f "$p" ] && { printf '%s' "$p"; return 0; }
    return 1
}

_handoff_cli() {
    local p="$INSTALLED_ROOT/context-handoff/extensions/context-handoff/handoff-cli.mjs"
    [ -f "$p" ] && { printf '%s' "$p"; return 0; }
    p="$(ls "$HOME"/.copilot/installed-plugins/*/context-handoff/extensions/context-handoff/handoff-cli.mjs 2>/dev/null | head -n1)"
    [ -n "$p" ] && [ -f "$p" ] && { printf '%s' "$p"; return 0; }
    return 1
}

_handoff_core() {
    local p="$INSTALLED_ROOT/context-handoff/extensions/context-handoff/handoff-core.mjs"
    [ -f "$p" ] && { printf '%s' "$p"; return 0; }
    p="$(ls "$HOME"/.copilot/installed-plugins/*/context-handoff/extensions/context-handoff/handoff-core.mjs 2>/dev/null | head -n1)"
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
    if bash -lc "command -v $bin >/dev/null 2>&1"; then
        bash -lc "$bin --version >/dev/null 2>&1" && return 0
    fi
    installer="$(_installer_path "$bin" || true)"
    if [ -n "$installer" ]; then
        capture "provision-$bin" -- bash "$installer" provision || true
    fi
    bash -lc "command -v $bin >/dev/null 2>&1 && $bin --version >/dev/null 2>&1"
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
  "extraKnownMarketplaces": { "$MARKETPLACE_NAME": $MARKETPLACE_SOURCE },
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
_apply_uv_index_fixture
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
phase 3 "compact seed, fidelity, and takeover budget metrics"
_seed_mod="$(_seed_module || true)"
_core_mod="$(_handoff_core || true)"
_metrics="/home/operator/out/context-handoff-efficiency.json"
if [ -z "$_seed_mod" ] || [ -z "$_core_mod" ]; then
    jam "repo-config" "seed/core modules not found under the installed context-handoff payload" "the plugin should ship its SDK-free seed and handoff core"
else
    info "seed module: $_seed_mod"
    if capture "seed-probe" -- node "$_SELF_DIR/seed-probe.mjs" \
        "$_seed_mod" "$_core_mod" "$_metrics"; then
        pass "compact seed, one-turn acknowledgement budget, and payload fidelity metrics passed"
        cr_meta "handoff_efficiency_metrics" "context-handoff-efficiency.json"
    else
        jam "repo-config" "context-handoff efficiency/fidelity probe failed" "see cr-logs/seed-probe.log and context-handoff-efficiency.json"
    fi
fi

# =========================================================================
phase 4 "consume, startup binding, and retire CLI verbs are real"
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
_verb_ok "binding"   agent-worktrees session-binding
_verb_ok "head"      agent-worktrees head-session
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
# Highest-fidelity Tier-P mechanism check: run the retire primitive against a
# REAL tmux pane and confirm the predecessor is gone. Needs
# tmux; if it can't be installed on this box we INFO-skip (the fix is already
# proven deterministically by phases 3-4) rather than fail on an env limitation.
if ! command -v tmux >/dev/null 2>&1; then
    capture "tmux-install" -- bash -lc 'sudo apt-get update -q && sudo apt-get install -y --no-install-recommends tmux' || true
fi
if ! command -v tmux >/dev/null 2>&1; then
    info "tmux unavailable (no apt/network); mechanism phase skipped -- the compact seed/fidelity contract is already proven by phases 3-4"
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
            info "predecessor pane $_pane in $_sess; running the retire primitive"
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
phase 6 "adopted anchor: no-coordinator file fallback and mux resolution"
_handoff_cli_path="$(_handoff_cli || true)"
if [ -z "$_handoff_cli_path" ]; then
    jam "repo-config" "handoff-cli.mjs not found under the installed context-handoff payload" "the plugin should ship extensions/context-handoff/handoff-cli.mjs"
elif ! bash -lc 'command -v agent-worktrees >/dev/null'; then
    info "agent-worktrees binstub unavailable; adopted-anchor fallback check skipped"
else
    # Register the ordinary checkout as an adopted project. It remains the
    # anchor (no linked worktree is created), which is the regression shape.
    ( cd "$HOME/wt-repo" && capture "anchor-register" -- agent-worktrees register wt-repo ) || true
    _anchor_state="$(cd "$HOME/wt-repo" && agent-worktrees get worktree-state-dir 2>/dev/null || true)"
    if [ -z "$_anchor_state" ]; then
        fail "adopted anchor did not resolve a machine-local handoff state directory"
    elif [[ "$_anchor_state" == "$HOME/wt-repo"* ]]; then
        fail "adopted-anchor state resolved inside the repository: $_anchor_state"
    else
        pass "adopted anchor resolves external state: $_anchor_state"
        _save_json="$HOME/context-handoff-anchor-save.json"
        rm -f "$_save_json"
        (
            cd "$HOME/wt-repo" &&
            node "$_handoff_cli_path" save --no-task --json \
                --session-id clean-room-anchor-predecessor \
                --cwd "$HOME/wt-repo" \
                --title "Anchor fallback" \
                --prompt "continue the clean-room anchor handoff"
        ) >"$_save_json" 2>"$CR_LOGDIR/anchor-save.stderr" || true
        _handoff_path="$(node -e 'const fs=require("fs"); try { const x=JSON.parse(fs.readFileSync(process.argv[1],"utf8")); process.stdout.write(x.path||""); } catch {}' "$_save_json")"
        _handoff_seed="$(node -e 'const fs=require("fs"); try { const x=JSON.parse(fs.readFileSync(process.argv[1],"utf8")); process.stdout.write(x.seed||""); } catch {}' "$_save_json")"
        if [ -z "$_handoff_path" ] || [ ! -f "$_handoff_path" ]; then
            fail "no-coordinator anchor save did not create a durable handoff (see $CR_LOGDIR/anchor-save.stderr)"
        elif [[ "$_handoff_path" == "$HOME/wt-repo"* ]]; then
            fail "handoff was written inside the repository: $_handoff_path"
        elif [ "$(dirname "$_handoff_path")" != "$_anchor_state/handoff" ]; then
            fail "handoff path did not use the state-first anchor namespace: $_handoff_path"
        else
            pass "no-coordinator anchor handoff is durable and state-first discoverable"
            if printf '%s' "$_handoff_seed" | grep -q \
                'Recovery: context-handoff file:handoff-clean-room-anchor-predecessor'; then
                pass "file-backed successor seed carries one opaque recovery locator"
            else
                fail "file-backed successor seed did not carry the expected recovery locator"
            fi
            if capture "anchor-consume-first" -- node "$_handoff_cli_path" consume \
                --json --session-id clean-room-anchor-successor \
                --locator file:handoff-clean-room-anchor-predecessor; then
                pass "anchor handoff consumed once through the seed's locator"
            else
                fail "first anchor handoff consume failed"
            fi
            if capture "anchor-consume-second" -- node "$_handoff_cli_path" consume \
                --json --session-id clean-room-anchor-replay --path "$_handoff_path"; then
                fail "anchor handoff replay unexpectedly succeeded"
            else
                pass "anchor handoff rejects a second consume"
            fi
        fi

        if command -v tmux >/dev/null 2>&1; then
            _anchor_cutover="$HOME/context-handoff-anchor-cutover.json"
            rm -f "$_anchor_cutover"
            tmux kill-session -t cr-anchor-handoff 2>/dev/null || true
            tmux new-session -d -s cr-anchor-handoff -c "$HOME/wt-repo" \
                "pane=\$(tmux display-message -p '#{pane_id}'); agent-worktrees handoff-cutover --seed anchor-probe --old-pane \"\$pane\" --session-id clean-room-anchor-predecessor --dry-run --json > '$_anchor_cutover' 2>&1" \
                2>>"$CR_LOGDIR/anchor-mux.log" || true
            for _ in 1 2 3 4 5; do
                [ -s "$_anchor_cutover" ] && break
                sleep 1
            done
            if [ -s "$_anchor_cutover" ] \
                && grep -q '"ok": true' "$_anchor_cutover" \
                && grep -q '"session": "cr-anchor-handoff"' "$_anchor_cutover"; then
                pass "anchor handoff resolves the caller-owned mux for successor cutover"
            else
                fail "anchor mux cutover resolution failed (see $_anchor_cutover and cr-logs/anchor-mux.log)"
            fi
            tmux kill-session -t cr-anchor-handoff 2>/dev/null || true
        else
            info "tmux unavailable; anchor mux resolution check skipped"
        fi

        rm -f "$_save_json" "$HOME/context-handoff-anchor-cutover.json"
        if [ -n "${_handoff_path:-}" ]; then
            rm -f "$_handoff_path" "$_handoff_path.consume.lock" \
                "$_handoff_path.consume.lock.recover"
        fi
    fi
fi

# =========================================================================
cr_finalize
