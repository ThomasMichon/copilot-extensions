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
if [[ "$_NO_UPDATE" != "1" ]]; then
    # Discover the active plugin directory (marketplace or _direct layout)
    _PLUGIN_DIR=""
    _PLUGIN_LAYOUT=""
    _MKT_DIR="$HOME/.copilot/installed-plugins/copilot-extensions/agent-worktrees"
    _DIRECT_ROOT="$HOME/.copilot/installed-plugins/_direct"

    if [[ -d "$_MKT_DIR" ]]; then
        _PLUGIN_DIR="$_MKT_DIR"
        _PLUGIN_LAYOUT="marketplace"
    elif [[ -d "$_DIRECT_ROOT" ]]; then
        for _dir in "$_DIRECT_ROOT"/*/; do
            _manifest="${_dir}plugin.json"
            if [[ -f "$_manifest" ]]; then
                _name=$(grep -o '"name"[[:space:]]*:[[:space:]]*"[^"]*"' "$_manifest" 2>/dev/null \
                       | head -1 | sed 's/.*"name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/')
                if [[ "$_name" == "agent-worktrees" ]]; then
                    _PLUGIN_DIR="${_dir%/}"
                    _PLUGIN_LAYOUT="direct"
                    break
                fi
            fi
        done
    fi

    if [[ -n "$_PLUGIN_DIR" ]]; then
        setup_log INFO "Plugin auto-update: layout=$_PLUGIN_LAYOUT dir=$_PLUGIN_DIR"

        # Snapshot key plugin files to detect changes after update
        _HASH_FILES="pyproject.toml plugin.json bin/launch-session.ps1 bin/launch-session.sh bin/pane-wrapper.sh scripts/install.ps1 scripts/install.sh"
        _OLD_FP=""
        for _f in $_HASH_FILES; do
            _fp="$_PLUGIN_DIR/$_f"
            if [[ -f "$_fp" ]]; then
                _OLD_FP+=$(sha256sum "$_fp" 2>/dev/null | cut -d' ' -f1)
            fi
        done

        # Try to update the plugin from the marketplace
        if [[ "$_PLUGIN_LAYOUT" == "marketplace" ]]; then
            if command -v copilot &>/dev/null; then
                setup_log INFO 'Running: copilot plugin update agent-worktrees@copilot-extensions'
                _UPDATE_OUT=$(copilot plugin update agent-worktrees@copilot-extensions 2>&1) || true
                setup_log INFO "Plugin update result: $_UPDATE_OUT"
            fi
        else
            setup_log INFO 'Direct-install layout -- skipping marketplace update'
        fi

        # Check if any tracked files changed
        _NEW_FP=""
        for _f in $_HASH_FILES; do
            _fp="$_PLUGIN_DIR/$_f"
            if [[ -f "$_fp" ]]; then
                _NEW_FP+=$(sha256sum "$_fp" 2>/dev/null | cut -d' ' -f1)
            fi
        done

        if [[ -n "$_NEW_FP" && "$_NEW_FP" != "$_OLD_FP" ]]; then
            setup_log INFO 'Plugin source changed -- running full installer update'

            _PLUGIN_INSTALLER="$_PLUGIN_DIR/scripts/install.sh"
            if [[ -f "$_PLUGIN_INSTALLER" ]]; then
                _INST_ARGS=(update)
                if [[ -n "${WORKTREE_PROJECT:-}" ]]; then
                    _INST_ARGS+=(--project-name "$WORKTREE_PROJECT")
                fi

                if bash "$_PLUGIN_INSTALLER" "${_INST_ARGS[@]}" 2>&1 | while IFS= read -r _line; do
                    setup_log INFO "installer: $_line"
                done; then
                    setup_log INFO 'Installer update succeeded -- re-execing into new launch-session'

                    _NEW_LAUNCHER="$HOME/.agent-worktrees/bin/launch-session.sh"
                    if [[ -x "$_NEW_LAUNCHER" ]]; then
                        export WORKTREE_NO_UPDATE=1
                        export APERTURE_NO_UPDATE=1
                        exec "$_NEW_LAUNCHER" "$@"
                    else
                        setup_log WARN 'Updated but deployed launcher missing; continuing current process'
                    fi
                else
                    setup_log WARN "Installer update failed (exit $?) -- continuing with existing version"
                fi
            else
                setup_log WARN "Plugin installer not found at $_PLUGIN_INSTALLER -- skipping"
            fi
        else
            setup_log INFO 'Plugin source unchanged -- no update needed'
        fi
    fi
fi

# ── Pre-launch self-update (two-pass) ─────────────────────────────────────
# Checks bootstrap service staleness and runs updates if needed.
# Controlled by WORKTREE_NO_UPDATE env var (set by cmd_launch in Python).

_NO_UPDATE="${WORKTREE_NO_UPDATE:-${APERTURE_NO_UPDATE:-}}"
if [[ "$_NO_UPDATE" != "1" ]]; then
    setup_log INFO 'Running pre-launch staleness check'
    PRE_JSON=$("$PYTHON" -m agent_worktrees pre-launch 2>/dev/null) || PRE_JSON='{"action":"continue","reason":"error"}'
    PRE_ACTION=$(echo "$PRE_JSON" | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('action','continue'))" 2>/dev/null) || PRE_ACTION="continue"

    if [[ "$PRE_ACTION" == "self-update" ]]; then
        setup_log INFO 'Self-update required — running update commands'
        _VERBOSE="${WORKTREE_VERBOSE:-${APERTURE_PRE_FLIGHT_VERBOSE:-}}"
        if [[ "$_VERBOSE" == "1" ]]; then
            echo "Pre-flight: updating stale bootstrap services..."
        fi

        # Extract and run each update command from argv arrays (safe, no eval)
        UPDATE_COUNT=$("$PYTHON" -c "import sys,json; print(len(json.load(sys.stdin).get('updates',[])))" <<< "$PRE_JSON" 2>/dev/null) || UPDATE_COUNT=0
        for (( i=0; i<UPDATE_COUNT; i++ )); do
            # Read argv array as newline-delimited elements
            SVC_NAME=$("$PYTHON" -c "import sys,json; print(json.load(sys.stdin)['updates'][$i]['service'])" <<< "$PRE_JSON" 2>/dev/null) || SVC_NAME="unknown"
            mapfile -t UPDATE_ARGV < <("$PYTHON" -c "
import sys, json
for a in json.load(sys.stdin)['updates'][$i].get('argv', []):
    print(a)
" <<< "$PRE_JSON" 2>/dev/null)

            if [[ ${#UPDATE_ARGV[@]} -gt 0 ]]; then
                setup_log INFO "Updating $SVC_NAME: ${UPDATE_ARGV[*]}"
                [[ "$_VERBOSE" == "1" ]] && echo "  Updating $SVC_NAME..."
                "${UPDATE_ARGV[@]}" || setup_log WARN "Update failed for $SVC_NAME (exit $?)"
            fi
        done

        # Re-check after update (one retry max)
        setup_log INFO 'Re-checking staleness after update'
        PRE_JSON=$("$PYTHON" -m agent_worktrees pre-launch 2>/dev/null) || PRE_JSON='{"action":"continue"}'
        PRE_ACTION=$(echo "$PRE_JSON" | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('action','continue'))" 2>/dev/null) || PRE_ACTION="continue"

        if [[ "$PRE_ACTION" == "self-update" ]]; then
            setup_log WARN 'Still stale after update — proceeding anyway'
            [[ "$_VERBOSE" == "1" ]] && echo "  Warning: bootstrap services still stale after update. Proceeding."
        fi
    fi
else
    setup_log INFO 'Pre-launch update skipped (WORKTREE_NO_UPDATE=1)'
fi

# ── Direct-dispatch commands (bypass resolve/picker) ─────────────────────
# Subcommands that agent_worktrees's main() handles directly — these
# must NOT fall through to the resolve→picker flow.  Keep in sync with
# COMMAND_MAP in __main__.py, plus "services" and "agent-worktrees".
_DIRECT_COMMANDS="services repos worktree agent-worktrees resolve post-exit finalize push-changes mark-complete status list create cleanup validate install register unregister uninstall update install-status deploy-instructions get pre-launch reconcile-plugins dev handoff register-session deregister-session backfill-sessions anchor-check activity activity-log"
if [[ $# -gt 0 ]]; then
    for _dc in $_DIRECT_COMMANDS; do
        if [[ "$1" == "$_dc" ]]; then
            setup_log INFO "Direct dispatch: $1 (bypassing resolve)"
            exec "$PYTHON" -m agent_worktrees "$@"
        fi
    done
fi

# ── Plugin reconciliation (repo-configured payloads + gated runtimes) ──────
# Reconcile the anchor repo's .github/copilot/settings.json enabledPlugins:
# for each copilot-extensions plugin ensure its payload is installed, and its
# runtime is deployed per the plugin's runtimeScope + facility machine gate.
#
# Placed AFTER direct-dispatch so plain `agent-worktrees <subcommand>` calls
# never trigger it -- only the real launch path reaches here. Deliberately NOT
# guarded by WORKTREE_NO_UPDATE: the agent-worktrees self-update above re-execs
# with that flag set, and reconcile must still run on that pass. Opt out with
# WORKTREE_NO_RECONCILE=1.
#
# Two passes: payload first, then runtime -- a freshly installed payload's
# runtime manifest is only readable on the second pass.
if [[ "${WORKTREE_NO_RECONCILE:-}" != "1" ]]; then
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

if [[ "$ACTION" == "exec" ]]; then
    WORK_DIR=$(echo "$JSON" | "$PYTHON" -c "import sys,json; d=json.load(sys.stdin); print(d.get('work_dir',''))")
    POST_EXIT=$(echo "$JSON" | "$PYTHON" -c "import sys,json; d=json.load(sys.stdin); print('1' if d.get('post_exit') else '0')")
    WORKTREE_ID=$(echo "$JSON" | "$PYTHON" -c "import sys,json; d=json.load(sys.stdin); print(d.get('worktree_id') or '')")
    NO_MUX=$(echo "$JSON" | "$PYTHON" -c "import sys,json; d=json.load(sys.stdin); print('1' if d.get('no_mux') else '0')")

    # Env var override takes precedence
    _NO_MUX="${WORKTREE_NO_MUX:-${APERTURE_NO_MUX:-}}"
    if [[ "$_NO_MUX" == "1" ]]; then
        NO_MUX="1"
    fi

    # Publish worktree ID so tools (finalize, mark-complete) can auto-detect
    if [[ -n "$WORKTREE_ID" ]]; then
        export WORKTREE_ID="$WORKTREE_ID"
        export APERTURE_WORKTREE_ID="$WORKTREE_ID"  # backward compat
    fi

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
        # automatically inherited by new sessions.
        TMUX_ENV_FLAGS=()
        if [[ -n "${WORKTREE_PROJECT:-}" ]]; then
            TMUX_ENV_FLAGS+=(-e "WORKTREE_PROJECT=$WORKTREE_PROJECT")
        fi
        if [[ -n "${WORKTREE_ID:-}" ]]; then
            TMUX_ENV_FLAGS+=(-e "WORKTREE_ID=$WORKTREE_ID")
            TMUX_ENV_FLAGS+=(-e "APERTURE_WORKTREE_ID=$WORKTREE_ID")
        fi
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
            PANE_CMD=(bash "$PANE_WRAPPER" "${CMD_ARRAY[@]}")
        else
            setup_log WARN "pane wrapper missing at $PANE_WRAPPER; using direct command"
            PANE_CMD=("${CMD_ARRAY[@]}")
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
                # Check for handoff-driven relaunch before post-exit
                if [[ -n "$WORKTREE_ID" ]]; then
                    HANDOFF_JSON=$("$PYTHON" -m agent_worktrees handoff consume "$WORKTREE_ID" 2>/dev/null) || true
                    if [[ -n "$HANDOFF_JSON" ]]; then
                        HANDOFF_PATH=$(echo "$HANDOFF_JSON" | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('prompt_path',''))" 2>/dev/null) || HANDOFF_PATH=""
                        if [[ -n "$HANDOFF_PATH" && -f "$HANDOFF_PATH" ]]; then
                            setup_log INFO "Handoff relaunch (tmux): consuming $HANDOFF_PATH"
                            echo ""
                            echo "Handoff detected — relaunching with continuation prompt..."
                            echo ""
                            HANDOFF_PROMPT="Continuing from a previous session in this worktree. A handoff was prepared - read it for full context: cat \"$HANDOFF_PATH\""
                            HANDOFF_CMD=("${CMD_ARRAY[@]}" -i "$HANDOFF_PROMPT")
                            if [[ -r "$PANE_WRAPPER" ]]; then
                                HANDOFF_PANE_CMD=(bash "$PANE_WRAPPER" "${HANDOFF_CMD[@]}")
                            else
                                HANDOFF_PANE_CMD=("${HANDOFF_CMD[@]}")
                            fi
                            set +e
                            tmux new-session -d -s "$TMUX_SESS" -c "$WORK_DIR" "${TMUX_ENV_FLAGS[@]+"${TMUX_ENV_FLAGS[@]}"}" "${HANDOFF_PANE_CMD[@]}"
                            if [[ $? -eq 0 ]]; then
                                _aw_apply_session_opts "$TMUX_SESS"
                                _aw_spawn_status_updater "$TMUX_SESS"
                                if [[ -n "${TMUX:-}" ]]; then
                                    tmux switch-client -t "=$TMUX_SESS"
                                else
                                    tmux attach-session -t "=$TMUX_SESS"
                                fi
                            fi
                            set -e
                        fi
                    fi
                fi

                # Post-exit finalization (after both original and relaunched sessions)
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
    "${CMD_ARRAY[@]}"
    COPILOT_EXIT=$?
    set -e
    activity_log copilot_exited "$WORKTREE_ID" mux=none "exit_code=$COPILOT_EXIT"

    # Check for handoff-driven relaunch (max once per launcher invocation)
    if [[ -n "$WORKTREE_ID" ]]; then
        HANDOFF_JSON=$("$PYTHON" -m agent_worktrees handoff consume "$WORKTREE_ID" 2>/dev/null) || true
        if [[ -n "$HANDOFF_JSON" ]]; then
            HANDOFF_PATH=$(echo "$HANDOFF_JSON" | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('prompt_path',''))" 2>/dev/null) || HANDOFF_PATH=""
            if [[ -n "$HANDOFF_PATH" && -f "$HANDOFF_PATH" ]]; then
                setup_log INFO "Handoff relaunch: consuming $HANDOFF_PATH"
                echo ""
                echo "Handoff detected — relaunching with continuation prompt..."
                echo ""
                HANDOFF_PROMPT="Continuing from a previous session in this worktree. A handoff was prepared - read it for full context: cat \"$HANDOFF_PATH\""
                set +e
                "${CMD_ARRAY[@]}" -i "$HANDOFF_PROMPT"
                COPILOT_EXIT=$?
                set -e
            fi
        fi
    fi

    # Post-exit finalization
    if [[ "$POST_EXIT" == "1" && -n "$WORKTREE_ID" ]]; then
        "$PYTHON" -m agent_worktrees post-exit "$WORKTREE_ID" || \
            echo "WARNING: Post-exit finalization failed. Run 'agent-worktrees finalize' to retry." >&2
    fi

    exit $COPILOT_EXIT
fi

echo "ERROR: Unknown action: $ACTION" >&2
exit 1
