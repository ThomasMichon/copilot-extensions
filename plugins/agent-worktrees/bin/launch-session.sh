#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Setup log — per-launch log file with PID disambiguation
# ---------------------------------------------------------------------------
_SETUP_LOG_DIR="${TMPDIR:-/tmp}/worktree-setup-logs"
mkdir -p "$_SETUP_LOG_DIR" 2>/dev/null || true
SETUP_LOG="${WORKTREE_SETUP_LOG:-${APERTURE_SETUP_LOG:-$_SETUP_LOG_DIR/setup-$$.log}}"
export WORKTREE_SETUP_LOG="$SETUP_LOG"
export APERTURE_SETUP_LOG="$SETUP_LOG"  # backward compat

setup_log() {
    local level="$1" msg="$2"
    printf '[%s] [%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$level" "$msg" >> "$SETUP_LOG" 2>/dev/null || true
}

# Write header
{
    echo "# Worktree Manager — session launch log"
    echo "# Started: $(date '+%Y-%m-%d %H:%M:%S %z')"
    echo "# PID: $$"
    echo "# Host: $(hostname)"
    echo ""
} > "$SETUP_LOG" 2>/dev/null || true
chmod 600 "$SETUP_LOG" 2>/dev/null || true

# Prune old logs (keep last 10)
# shellcheck disable=SC2012
ls -t "$_SETUP_LOG_DIR"/setup-*.log 2>/dev/null | tail -n +11 | xargs rm -f 2>/dev/null || true

setup_log INFO 'launch-session.sh starting'

# Runtime resolution
RUNTIME_DIR="$HOME/.agent-worktrees"

if [[ -x "$RUNTIME_DIR/.venv/bin/python" ]]; then
    setup_log INFO "Venv resolved: $RUNTIME_DIR"
else
    setup_log ERROR 'Venv not found - aborting'
    echo "ERROR: Venv not found. Run the installer first." >&2
    exit 1
fi

PYTHON="$RUNTIME_DIR/.venv/bin/python"
export PYTHONPATH="$RUNTIME_DIR/lib"
unset PYTHONHOME

# Append a high-level lifecycle event to the persistent activity log.
# Best-effort and fully detached -- never blocks or fails the launch.
#   activity_log EVENT WORKTREE_ID [key=value ...]
activity_log() {
    local event="$1" wt="${2:-}"; shift 2 2>/dev/null || shift $# 
    [[ -z "$event" || -z "$wt" ]] && return 0
    local fields=()
    local kv
    for kv in "$@"; do
        fields+=(--field "$kv")
    done
    ( "$PYTHON" -m agent_worktrees activity-log "$event" \
        --worktree-id "$wt" --source launcher \
        "${fields[@]+"${fields[@]}"}" >/dev/null 2>&1 & ) || true
}

# --recovery: skip vault credential loading (propagated via env var to setup.sh)
# --: everything after this separator is copilot passthrough args (e.g. --acp --stdio)
FILTERED_ARGS=()
COPILOT_PASSTHROUGH=()
_SEEN_SEPARATOR=0
for arg in "$@"; do
    if [[ $_SEEN_SEPARATOR -eq 1 ]]; then
        COPILOT_PASSTHROUGH+=("$arg")
    elif [[ "$arg" == "--" ]]; then
        _SEEN_SEPARATOR=1
    elif [[ "$arg" == "--recovery" || "$arg" == "recovery" ]]; then
        export WORKTREE_RECOVERY=1
        export APERTURE_RECOVERY=1  # backward compat
        setup_log INFO 'Recovery mode requested via CLI arg'
    else
        FILTERED_ARGS+=("$arg")
    fi
done
set -- "${FILTERED_ARGS[@]+"${FILTERED_ARGS[@]}"}"
if [[ ${#COPILOT_PASSTHROUGH[@]} -gt 0 ]]; then
    setup_log INFO "Copilot passthrough args: ${COPILOT_PASSTHROUGH[*]}"
fi

# Recovery escape hatch (broken venv)
if [[ "${WORKTREE_RECOVERY:-${APERTURE_RECOVERY:-}}" == "1" ]] && [[ ! -x "$PYTHON" ]]; then
    PROJECT="${WORKTREE_PROJECT:-}"
    if [[ -z "$PROJECT" ]]; then
        echo "ERROR: WORKTREE_PROJECT is not set. Set it or run from inside the anchor repo." >&2
        exit 1
    fi
    CONFIG="$HOME/.$PROJECT/config.yaml"
    if [[ -f "$CONFIG" ]]; then
        ANCHOR=$(awk '/^    anchor:/ {print $2}' "$CONFIG")
        if [[ -n "$ANCHOR" && -d "$ANCHOR" ]]; then
            cd "$ANCHOR"
            exec bash "$ANCHOR/tools/setup/setup.sh" --recovery "$@"
        fi
    fi
    echo "ERROR: Cannot determine anchor path for recovery." >&2
    exit 1
fi

# ── Plugin auto-update ─────────────────────────────────────────────────────
# If installed from the copilot-extensions marketplace plugin, check for
# updates.  When the plugin source changes: run the full installer (which
# deploys package, launch scripts, binstubs, terminal configs), then
# re-exec into the newly deployed launch-session so the rest of the boot
# uses updated code.
#
# Guard: WORKTREE_NO_UPDATE=1 skips this block entirely (set by --no-update
# and by the re-exec below to prevent infinite loops).

_NO_UPDATE="${WORKTREE_NO_UPDATE:-${APERTURE_NO_UPDATE:-}}"
_STAGE_PID=""
_UPDATE_APPLIED=""

# ── Background update: stage-then-join (#1430) ─────────────────────────────
# The Picker runs from the installed runtime venv, so the slow marketplace
# download is STAGED in the background while the Picker is open, then the apply
# (installer -> runtime, pre-launch, reconcile) runs at the JOIN, after the
# Picker closes and before the tmux/Copilot handoff. The launcher script is
# applied via the installer but NOT re-exec'd mid-flight: a launcher change
# takes effect on the NEXT launch (stage-next).

start_update_stage() {
    # Spawn the background stage (marketplace download + fingerprint + plan).
    # Output is discarded so it never writes to the Picker's terminal.
    [[ "$_NO_UPDATE" == "1" ]] && return 0
    setup_log INFO 'Starting background update stage (stage-update)'
    ( "$PYTHON" -m agent_worktrees stage-update >/dev/null 2>&1 ) &
    _STAGE_PID=$!
}

invoke_update_apply() {
    # $1 = "1" to also run plugin reconcile (Picker path); "0" otherwise.
    # Idempotent: runs its body at most once per launch.
    local with_reconcile="${1:-0}"
    [[ -n "$_UPDATE_APPLIED" ]] && return 0
    _UPDATE_APPLIED=1

    local status_file="$HOME/.agent-worktrees/updater-status.json"
    local stage_done="" plugin_changed="" skipped="" plugin_dir=""

    _parse_stage_status() {
        [[ -f "$status_file" ]] || return 0
        IFS=$'\t' read -r stage_done plugin_changed skipped plugin_dir < <(
            "$PYTHON" -c "
import sys, json
try:
    d = json.load(open(sys.argv[1], encoding='utf-8'))
except Exception:
    d = {}
print('\t'.join([
    str(d.get('stage_done', False)),
    str(d.get('plugin_changed', False)),
    str(d.get('skipped', '')),
    str(d.get('plugin_dir', '')),
]))
" "$status_file" 2>/dev/null
        )
    }

    if [[ "$_NO_UPDATE" != "1" ]]; then
        # Join the background stage.
        if [[ -n "$_STAGE_PID" ]]; then
            wait "$_STAGE_PID" 2>/dev/null || true
        fi
        _parse_stage_status
        # No usable staged result (stage failed, or a peer launch held the
        # lock): stage inline so the marketplace pull still happens.
        if [[ "$stage_done" != "True" || "$skipped" == "locked" ]]; then
            setup_log INFO 'No usable staged update result; staging inline'
            "$PYTHON" -m agent_worktrees stage-update >/dev/null 2>&1 || true
            _parse_stage_status
        fi

        # (1) Marketplace installer, iff the download changed the payload.
        #     NO re-exec: a launcher-script change applies on the next launch.
        if [[ "$plugin_changed" == "True" ]]; then
            setup_log INFO 'Staged update changed the plugin payload -- running installer'
            local _installer="$plugin_dir/scripts/install.sh"
            if [[ -n "$plugin_dir" && -f "$_installer" ]]; then
                local _inst_args=(update)
                if [[ -n "${WORKTREE_PROJECT:-}" ]]; then
                    _inst_args+=(--project-name "$WORKTREE_PROJECT")
                fi
                if bash "$_installer" "${_inst_args[@]}" 2>&1 | while IFS= read -r _line; do
                    setup_log INFO "installer: $_line"
                done; then
                    setup_log INFO 'Installer update succeeded (launcher change, if any, applies next launch)'
                else
                    setup_log WARN "Installer update failed -- continuing with existing version"
                fi
            else
                setup_log WARN "Plugin installer not found ($_installer) -- skipping"
            fi
        fi

        # (2) Pre-launch self-update (bootstrap-service staleness; two-pass).
        setup_log INFO 'Running pre-launch staleness check'
        PRE_JSON=$("$PYTHON" -m agent_worktrees pre-launch 2>/dev/null) || PRE_JSON='{"action":"continue","reason":"error"}'
        PRE_ACTION=$(echo "$PRE_JSON" | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('action','continue'))" 2>/dev/null) || PRE_ACTION="continue"
        if [[ "$PRE_ACTION" == "self-update" ]]; then
            setup_log INFO 'Self-update required -- running update commands'
            UPDATE_COUNT=$("$PYTHON" -c "import sys,json; print(len(json.load(sys.stdin).get('updates',[])))" <<< "$PRE_JSON" 2>/dev/null) || UPDATE_COUNT=0
            for (( i=0; i<UPDATE_COUNT; i++ )); do
                SVC_NAME=$("$PYTHON" -c "import sys,json; print(json.load(sys.stdin)['updates'][$i]['service'])" <<< "$PRE_JSON" 2>/dev/null) || SVC_NAME="unknown"
                mapfile -t UPDATE_ARGV < <("$PYTHON" -c "
import sys, json
for a in json.load(sys.stdin)['updates'][$i].get('argv', []):
    print(a)
" <<< "$PRE_JSON" 2>/dev/null)
                if [[ ${#UPDATE_ARGV[@]} -gt 0 ]]; then
                    setup_log INFO "Updating $SVC_NAME: ${UPDATE_ARGV[*]}"
                    "${UPDATE_ARGV[@]}" || setup_log WARN "Update failed for $SVC_NAME (exit $?)"
                fi
            done
            setup_log INFO 'Re-checking staleness after update'
            PRE_JSON=$("$PYTHON" -m agent_worktrees pre-launch 2>/dev/null) || PRE_JSON='{"action":"continue"}'
            PRE_ACTION=$(echo "$PRE_JSON" | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('action','continue'))" 2>/dev/null) || PRE_ACTION="continue"
            if [[ "$PRE_ACTION" == "self-update" ]]; then
                setup_log WARN 'Still stale after update -- proceeding anyway'
            fi
        fi
    else
        setup_log INFO 'Update apply skipped (WORKTREE_NO_UPDATE=1)'
    fi

    # (3) Plugin reconciliation (repo-configured payloads + gated runtimes).
    #     Independent of WORKTREE_NO_UPDATE; opt out with WORKTREE_NO_RECONCILE=1.
    #     Two passes: payload first, then runtime (readable only next pass).
    if [[ "$with_reconcile" == "1" && "${WORKTREE_NO_RECONCILE:-}" != "1" ]]; then
        for _rpass in 1 2; do
            REC_JSON=$("$PYTHON" -m agent_worktrees reconcile-plugins 2>/dev/null) \
                || REC_JSON='{"action":"continue"}'
            REC_ACTION=$(printf '%s' "$REC_JSON" \
                | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('action','continue'))" 2>/dev/null) \
                || REC_ACTION="continue"
            if [[ "$REC_ACTION" != "reconcile" ]]; then
                [[ "$_rpass" == "1" ]] && setup_log INFO 'Plugin reconcile: nothing to do'
                break
            fi
            REC_COUNT=$("$PYTHON" -c "import sys,json; print(len(json.load(sys.stdin).get('updates',[])))" <<< "$REC_JSON" 2>/dev/null) || REC_COUNT=0
            setup_log INFO "Plugin reconcile pass $_rpass: $REC_COUNT action(s)"
            for (( _ri=0; _ri<REC_COUNT; _ri++ )); do
                _RSVC=$("$PYTHON" -c "import sys,json; print(json.load(sys.stdin)['updates'][$_ri].get('service','?'))" <<< "$REC_JSON" 2>/dev/null) || _RSVC="?"
                mapfile -t _RARGV < <("$PYTHON" -c "
import sys, json
for a in json.load(sys.stdin)['updates'][$_ri].get('argv', []):
    print(a)
" <<< "$REC_JSON" 2>/dev/null)
                [[ ${#_RARGV[@]} -gt 0 ]] || continue
                if [[ "${_RARGV[0]}" == "copilot" ]] && ! command -v copilot &>/dev/null; then
                    setup_log WARN "Plugin reconcile: skipping $_RSVC (copilot not on PATH)"
                    continue
                fi
                setup_log INFO "Plugin reconcile: $_RSVC -> ${_RARGV[*]}"
                "${_RARGV[@]}" 2>&1 | while IFS= read -r _rl; do setup_log INFO "reconcile: $_rl"; done \
                    || setup_log WARN "Plugin reconcile: step failed for $_RSVC"
            done
        done
    fi
}

# ── Pre-launch self-update + reconcile now run via invoke_update_apply ────
# (moved into the stage-then-join functions defined above, #1430).


# ── Direct-dispatch commands (bypass resolve/picker) ─────────────────────
# Subcommands that agent_worktrees's main() handles directly — these
# must NOT fall through to the resolve→picker flow.  Keep in sync with
# COMMAND_MAP in __main__.py, plus "services" and "agent-worktrees".
_DIRECT_COMMANDS="services repos worktree agent-worktrees resolve post-exit finalize push-changes mark-complete status list create cleanup validate install register unregister uninstall update install-status deploy-instructions get pre-launch stage-update reconcile-plugins dev handoff-cutover register-session deregister-session backfill-sessions anchor-check activity activity-log"
_IS_DIRECT=""
if [[ $# -gt 0 ]]; then
    for _dc in $_DIRECT_COMMANDS; do
        if [[ "$1" == "$_dc" ]]; then _IS_DIRECT=1; break; fi
    done
fi
if [[ -n "$_IS_DIRECT" ]]; then
    setup_log INFO "Direct dispatch: $1 (bypassing resolve)"
    # No Picker window to hide behind: stage + apply synchronously (no
    # reconcile, matching historical direct-command behavior) before dispatch.
    start_update_stage
    invoke_update_apply 0
    exec "$PYTHON" -m agent_worktrees "$@"
fi

# ── Background update stage (#1430) ──────────────────────────────────────
# Spawn the marketplace download now so it runs WHILE the Picker is open. It is
# joined and applied (installer + pre-launch + reconcile) after resolve returns
# an exec plan, before the tmux handoff -- see invoke_update_apply below.
start_update_stage

# ── Resolve launch plan via Python ────────────────────────────────────────

setup_log INFO 'Calling agent_worktrees resolve'
JSON=$("$PYTHON" -m agent_worktrees resolve "$@")
RC=$?
if [[ $RC -ne 0 ]]; then
    setup_log ERROR "agent_worktrees resolve failed (exit $RC)"
    exit $RC
fi

# Non-interactive resolves (`resolve --json --worktree-id` / `--json --new`,
# used by agent-bridge ACP launches) emit the bridge's nested plan shape:
#   {"worktree": {...}, "launch": {"action": "exec", ...}}
# The launcher below consumes the *flat* plan ({"action": "exec", ...}); the
# nested `launch` object carries the identical keys, so unwrap it when present.
# A flat plan (no top-level `launch`) passes through unchanged.
JSON=$(printf '%s' "$JSON" | "$PYTHON" -c "import sys, json
d = json.load(sys.stdin)
print(json.dumps(d['launch'] if isinstance(d, dict) and 'launch' in d else d))")

ACTION=$(echo "$JSON" | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('action','none'))")
setup_log INFO "Plan resolved: action=$ACTION"

if [[ "$ACTION" == "none" ]]; then
    EXIT_CODE=$(echo "$JSON" | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('exit_code',0))")
    exit "$EXIT_CODE"
fi

# ── Remote machine handoff via SSH ───────────────────────────────────────
if [[ "$ACTION" == "remote" ]]; then
    SSH_ALIAS=$(echo "$JSON" | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('ssh_alias',''))")
    REMOTE_CMD=$(echo "$JSON" | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('remote_command',''))")
    DISPLAY_NAME=$(echo "$JSON" | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('display_name',''))")
    setup_log INFO "Handing off to remote machine: $DISPLAY_NAME via $SSH_ALIAS"
    echo "Connecting to $DISPLAY_NAME..."
    # exec ssh with TTY allocation; the remote binstub takes over
    exec ssh -t "$SSH_ALIAS" "$REMOTE_CMD"
fi

if [[ "$ACTION" == "refresh" ]]; then
    # ── Picker refresh: apply the staged update, then relaunch (#1430) ───────
    # The picker's refresh icon exits with action=refresh. It runs from the
    # runtime venv the update replaces, so apply here (venv now free), then
    # re-exec the (now-updated) launcher to reopen the picker on the new version.
    setup_log INFO 'Picker refresh -- applying staged update and relaunching'
    invoke_update_apply 1
    _RELAUNCH="$HOME/.agent-worktrees/bin/launch-session.sh"
    if [[ -x "$_RELAUNCH" ]]; then
        exec "$_RELAUNCH" "$@"
    fi
    setup_log WARN 'Relaunch launcher missing after refresh; exiting'
    exit 1
fi

if [[ "$ACTION" == "exec" ]]; then
    # ── Join the background update + apply, before the tmux handoff (#1430) ──
    # The Picker has closed, so it is now safe to swap the runtime venv. This
    # waits for the staged marketplace download, runs the installer if it
    # changed the payload (no re-exec -- a launcher change applies next launch),
    # then the pre-launch self-update and plugin reconcile.
    invoke_update_apply 1

    WORK_DIR=$(echo "$JSON" | "$PYTHON" -c "import sys,json; d=json.load(sys.stdin); print(d.get('work_dir',''))")
    POST_EXIT=$(echo "$JSON" | "$PYTHON" -c "import sys,json; d=json.load(sys.stdin); print('1' if d.get('post_exit') else '0')")
    WORKTREE_ID=$(echo "$JSON" | "$PYTHON" -c "import sys,json; d=json.load(sys.stdin); print(d.get('worktree_id') or '')")
    NO_MUX=$(echo "$JSON" | "$PYTHON" -c "import sys,json; d=json.load(sys.stdin); print('1' if d.get('no_mux') else '0')")

    # Env var override takes precedence
    _NO_MUX="${WORKTREE_NO_MUX:-${APERTURE_NO_MUX:-}}"
    if [[ "$_NO_MUX" == "1" ]]; then
        NO_MUX="1"
    fi

    # Worktree ID stays a LOCAL (non-exported) shell var: the launcher uses it
    # for the tmux session name, activity log, handoff, and post-exit, but it is
    # NOT exported into the child Copilot session. In-session tools resolve the
    # worktree from CWD (git-like), so no identity env var is leaked. See the
    # env -u prefix on the child launches below.

    # Export profile env vars (BYOK, offline mode, token limits, etc.)
    # Uses shlex.quote for safe shell quoting; keys are validated alphanumeric.
    ENV_EXPORTS=$(echo "$JSON" | "$PYTHON" -c "
import sys, json, shlex
d = json.load(sys.stdin)
for k, v in d.get('env', {}).items():
    print(f'export {k}={shlex.quote(str(v))}')
" 2>/dev/null) || true
    if [[ -n "$ENV_EXPORTS" ]]; then
        eval "$ENV_EXPORTS"
    fi

    # Build command array from JSON
    eval "CMD_ARRAY=( $( echo "$JSON" | "$PYTHON" -c "
import sys, json, shlex
d = json.load(sys.stdin)
print(' '.join(shlex.quote(a) for a in d.get('cmd', [])))
") )"

    # Append copilot passthrough args (from after -- separator)
    if [[ ${#COPILOT_PASSTHROUGH[@]} -gt 0 ]]; then
        CMD_ARRAY+=("${COPILOT_PASSTHROUGH[@]}")
    fi

    # Identity vars are stripped from the CHILD Copilot process so the session
    # env carries no ambient project/worktree identity -- in-session tools
    # resolve context from CWD (git-like). `env -u` runs inside the pane, so it
    # is robust to tmux-server-env inheritance. The launcher's own logic keeps
    # its local WORKTREE_ID / WORKTREE_PROJECT shell vars.
    CLEAN_ENV=(env -u WORKTREE_PROJECT -u WORKTREE_ID -u APERTURE_WORKTREE_ID)

    if [[ -n "$WORK_DIR" ]]; then
        cd "$WORK_DIR"
    fi

    if [[ "$NO_MUX" == "1" ]]; then
        setup_log INFO "Mux disabled; launching directly"
    fi

    # ── tmux session-per-worktree (exec actions only) ─────────────────
    # Each worktree gets a single shared tmux session. Multiple terminal
    # connections (local, SSH) all land in the same session. The tmux
    # session ends when the launched process exits.
    #
    # WSL delegation (action=wsl) skips tmux — handled on the Linux side.
    # --no-mux / WORKTREE_NO_MUX=1 bypasses tmux for debugging.
    if [[ "$NO_MUX" != "1" && "$ACTION" == "exec" ]] && command -v tmux &>/dev/null; then
        TMUX_SESS="wt-${WORKTREE_ID:-base}"
        setup_log INFO "tmux: looking for session $TMUX_SESS"

        # Per-session status bar + behaviors. agent-worktrees does NOT own your
        # global ~/.tmux.conf; instead we stamp these onto each session we
        # create/join, scoped to that session (`tmux set -t`, no -g), leaving
        # your personal config and any ad-hoc tmux sessions untouched.
        _AW_SESSION_OPTS="$HOME/.agent-worktrees/bin/session-options.sh"
        if [[ -r "$_AW_SESSION_OPTS" ]]; then
            # shellcheck source=/dev/null
            source "$_AW_SESSION_OPTS"
        fi
        _aw_apply_session_opts() {
            if declare -F aw_apply_tmux_session_options >/dev/null 2>&1; then
                aw_apply_tmux_session_options "$1" "${WORKTREE_ID:-}" || true
            fi
        }
        # Spawn the common, in-process Python status-updater (detached). It
        # keeps this session's @aw_ctx/@aw_seg vars fresh OFF the render path,
        # so the bar reads #{@aw_ctx}/#{@aw_seg} with zero spawn per repaint.
        # Safe to call on every create/join/handoff: an @aw_updater token
        # elects a single live updater and older ones self-retire. The updater
        # self-terminates within one interval of the session ending.
        _aw_spawn_status_updater() {
            local sess="$1"
            local aw; aw="$(command -v agent-worktrees 2>/dev/null || true)"
            [[ -x "$aw" ]] || aw="$HOME/.local/bin/agent-worktrees"
            [[ -x "$aw" ]] || return 0
            setsid "$aw" status-updater --session "$sess" --mux tmux \
                --path "${WORK_DIR:-$PWD}" >/dev/null 2>&1 < /dev/null &
            disown 2>/dev/null || true
        }

        # If a tmux session already exists for this worktree, join it.
        # The attacher gets the shared view; no post-exit responsibility.
        if tmux has-session -t "=$TMUX_SESS" 2>/dev/null; then
            echo "Joining existing session: $TMUX_SESS"
            activity_log mux_attached "$WORKTREE_ID" mux=join
            # Refresh per-session options on (re)connect so a long-lived
            # session picks up the current bar without us owning the global.
            _aw_apply_session_opts "$TMUX_SESS"
            _aw_spawn_status_updater "$TMUX_SESS"
            if [[ -n "${TMUX:-}" ]]; then
                exec tmux switch-client -t "=$TMUX_SESS"
            else
                exec tmux attach-session -t "=$TMUX_SESS"
            fi
        fi

        # Create a new tmux session for this worktree.
        # The command is passed directly to new-session so the pane
        # (and session) exits when the process finishes — no lingering
        # shell.
        #
        # Disable errexit for the entire tmux block — if any tmux
        # command fails the script must NOT die (the binstub uses exec,
        # so an unhandled exit kills the terminal).
        set +e
        setup_log INFO "tmux: creating session $TMUX_SESS"
        echo "Creating tmux session: $TMUX_SESS"
        echo ""

        # Propagate profile env vars into the tmux session.
        # The tmux server may predate this shell, so exported vars aren't
        # automatically inherited by new sessions. Identity vars
        # (WORKTREE_PROJECT/WORKTREE_ID) are deliberately NOT injected -- the
        # child resolves context from CWD, and CLEAN_ENV strips any inherited
        # copies inside the pane.
        TMUX_ENV_FLAGS=()
        if [[ -n "${SETUP_LOG:-}" ]]; then
            TMUX_ENV_FLAGS+=(-e "WORKTREE_SETUP_LOG=$SETUP_LOG")
            TMUX_ENV_FLAGS+=(-e "APERTURE_SETUP_LOG=$SETUP_LOG")
        fi
        if [[ -n "$ENV_EXPORTS" ]]; then
            while IFS= read -r line; do
                # Strip 'export ' prefix → KEY=VALUE
                local_kv="${line#export }"
                TMUX_ENV_FLAGS+=(-e "$local_kv")
            done <<< "$ENV_EXPORTS"
        fi

        # Pane wrapper — catches exit codes, shows diagnostics on crash,
        # and always exits 0 so remain-on-exit doesn't trap the pane.
        PANE_WRAPPER="$HOME/.agent-worktrees/bin/pane-wrapper.sh"
        if [[ -r "$PANE_WRAPPER" ]]; then
            PANE_CMD=("${CLEAN_ENV[@]}" bash "$PANE_WRAPPER" "${CMD_ARRAY[@]}")
        else
            setup_log WARN "pane wrapper missing at $PANE_WRAPPER; using direct command"
            PANE_CMD=("${CLEAN_ENV[@]}" "${CMD_ARRAY[@]}")
        fi

        if ! tmux new-session -d -s "$TMUX_SESS" -c "${WORK_DIR:-.}" \
            "${TMUX_ENV_FLAGS[@]+"${TMUX_ENV_FLAGS[@]}"}" \
            "${PANE_CMD[@]}"; then
            echo "WARNING: Failed to create tmux session. Falling back to direct launch." >&2
            set -e
        else

            activity_log mux_attached "$WORKTREE_ID" mux=create
            _aw_apply_session_opts "$TMUX_SESS"
            _aw_spawn_status_updater "$TMUX_SESS"
            if [[ -n "${TMUX:-}" ]]; then
                tmux switch-client -t "=$TMUX_SESS"
            else
                tmux attach-session -t "=$TMUX_SESS"
            fi
            set -e

            # We're back — either the user detached or the session ended.
            # Only run post-exit if the session is truly gone (user exited
            # the shell, not just detached).
            activity_log mux_detached "$WORKTREE_ID"
            if ! tmux has-session -t "=$TMUX_SESS" 2>/dev/null; then
                activity_log copilot_exited "$WORKTREE_ID" mux=tmux
                # Post-exit finalization
                if ! tmux has-session -t "=$TMUX_SESS" 2>/dev/null; then
                    if [[ "$POST_EXIT" == "1" && -n "$WORKTREE_ID" ]]; then
                        "$PYTHON" -m agent_worktrees post-exit "$WORKTREE_ID" || \
                            echo "WARNING: Post-exit finalization failed. Run 'agent-worktrees finalize' to retry." >&2
                    fi
                fi
            fi

            exit 0
        fi
        # (fallthrough from failed new-session → non-tmux launch below)
    fi

    # ── Non-tmux fallback (WSL, or tmux not installed) ────────────────

    setup_log INFO "Handing off to setup script"
    echo "Launching Copilot..."
    echo ""

    set +e
    "${CLEAN_ENV[@]}" "${CMD_ARRAY[@]}"
    COPILOT_EXIT=$?
    set -e
    activity_log copilot_exited "$WORKTREE_ID" mux=none "exit_code=$COPILOT_EXIT"

    # Post-exit finalization
    if [[ "$POST_EXIT" == "1" && -n "$WORKTREE_ID" ]]; then
        "$PYTHON" -m agent_worktrees post-exit "$WORKTREE_ID" || \
            echo "WARNING: Post-exit finalization failed. Run 'agent-worktrees finalize' to retry." >&2
    fi

    exit $COPILOT_EXIT
fi

echo "ERROR: Unknown action: $ACTION" >&2
exit 1
