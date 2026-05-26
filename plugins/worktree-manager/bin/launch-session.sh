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

# Dual-layout resolution: prefer ~/.worktree-manager/, fall back to legacy
NEW_RUNTIME="$HOME/.worktree-manager"
LEGACY_RUNTIME="$HOME/.aperture-labs"

if [[ -x "$NEW_RUNTIME/.venv/bin/python" ]]; then
    RUNTIME_DIR="$NEW_RUNTIME"
    setup_log INFO "Venv resolved: $NEW_RUNTIME"
elif [[ -x "$LEGACY_RUNTIME/.venv/bin/python" ]]; then
    RUNTIME_DIR="$LEGACY_RUNTIME"
    setup_log INFO "Venv resolved (legacy): $LEGACY_RUNTIME"
else
    setup_log ERROR 'Venv not found — aborting'
    echo "ERROR: Venv not found. Run the installer first." >&2
    exit 1
fi

PYTHON="$RUNTIME_DIR/.venv/bin/python"
export PYTHONPATH="$RUNTIME_DIR/lib"
unset PYTHONHOME

# --recovery: skip vault credential loading (propagated via env var to setup.sh)
FILTERED_ARGS=()
for arg in "$@"; do
    if [[ "$arg" == "--recovery" || "$arg" == "recovery" ]]; then
        export WORKTREE_RECOVERY=1
        export APERTURE_RECOVERY=1  # backward compat
        setup_log INFO 'Recovery mode requested via CLI arg'
    else
        FILTERED_ARGS+=("$arg")
    fi
done
set -- "${FILTERED_ARGS[@]+"${FILTERED_ARGS[@]}"}"

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

# ── Pre-launch self-update (two-pass) ─────────────────────────────────────
# Checks bootstrap service staleness and runs updates if needed.
# Controlled by WORKTREE_NO_UPDATE env var (set by cmd_launch in Python).

_NO_UPDATE="${WORKTREE_NO_UPDATE:-${APERTURE_NO_UPDATE:-}}"
if [[ "$_NO_UPDATE" != "1" ]]; then
    setup_log INFO 'Running pre-launch staleness check'
    PRE_JSON=$("$PYTHON" -m worktree_manager pre-launch 2>/dev/null) || PRE_JSON='{"action":"continue","reason":"error"}'
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
        PRE_JSON=$("$PYTHON" -m worktree_manager pre-launch 2>/dev/null) || PRE_JSON='{"action":"continue"}'
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
# Subcommands that worktree_manager's main() handles directly — these
# must NOT fall through to the resolve→picker flow.  Keep in sync with
# COMMAND_MAP in __main__.py, plus "services" and "worktree-manager".
_DIRECT_COMMANDS="services worktree-manager resolve post-exit finalize mark-complete status list create cleanup validate install register uninstall update install-status deploy-instructions get pre-launch dev handoff"
if [[ $# -gt 0 ]]; then
    for _dc in $_DIRECT_COMMANDS; do
        if [[ "$1" == "$_dc" ]]; then
            setup_log INFO "Direct dispatch: $1 (bypassing resolve)"
            exec "$PYTHON" -m worktree_manager "$@"
        fi
    done
fi

# ── Resolve launch plan via Python ────────────────────────────────────────

setup_log INFO 'Calling worktree_manager resolve'
JSON=$("$PYTHON" -m worktree_manager resolve "$@")
RC=$?
if [[ $RC -ne 0 ]]; then
    setup_log ERROR "worktree_manager resolve failed (exit $RC)"
    exit $RC
fi

ACTION=$(echo "$JSON" | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('action','none'))")
setup_log INFO "Plan resolved: action=$ACTION"

if [[ "$ACTION" == "none" ]]; then
    EXIT_CODE=$(echo "$JSON" | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('exit_code',0))")
    exit "$EXIT_CODE"
fi

if [[ "$ACTION" == "wsl" || "$ACTION" == "exec" ]]; then
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

        # If a tmux session already exists for this worktree, join it.
        # The attacher gets the shared view; no post-exit responsibility.
        if tmux has-session -t "=$TMUX_SESS" 2>/dev/null; then
            echo "Joining existing session: $TMUX_SESS"
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
        if [[ -n "${WORKTREE_ID:-}" ]]; then
            TMUX_ENV_FLAGS+=(-e "WORKTREE_ID=$WORKTREE_ID")
            TMUX_ENV_FLAGS+=(-e "APERTURE_WORKTREE_ID=$WORKTREE_ID")
        fi
        if [[ -n "$ENV_EXPORTS" ]]; then
            while IFS= read -r line; do
                # Strip 'export ' prefix → KEY=VALUE
                local_kv="${line#export }"
                TMUX_ENV_FLAGS+=(-e "$local_kv")
            done <<< "$ENV_EXPORTS"
        fi

        if ! tmux new-session -d -s "$TMUX_SESS" -c "${WORK_DIR:-.}" \
            "${TMUX_ENV_FLAGS[@]+"${TMUX_ENV_FLAGS[@]}"}" \
            "${CMD_ARRAY[@]}"; then
            echo "WARNING: Failed to create tmux session. Falling back to direct launch." >&2
            set -e
        else
            # Brief pause — let the command initialize so we can detect
            # immediate crashes before trying to attach.
            sleep 0.1

            if ! tmux has-session -t "=$TMUX_SESS" 2>/dev/null; then
                # Session died before we could attach — command crashed on
                # startup.  Pause so the user can read the error before the
                # terminal tab closes (the binstub chain uses exec, so exit
                # here = tab closes).
                setup_log ERROR "tmux session $TMUX_SESS died immediately — command crashed"
                echo "" >&2
                echo "ERROR: Session crashed before startup completed." >&2
                echo "       tmux session '$TMUX_SESS' exited immediately." >&2
                echo "" >&2
                echo "Tip: check the setup log at $SETUP_LOG" >&2
                echo "     or run with --no-update to skip pre-flight checks." >&2
                echo "" >&2
                read -rp "Press Enter to close..." _ </dev/tty 2>/dev/null || sleep 5
                set -e

                if [[ "$POST_EXIT" == "1" && -n "$WORKTREE_ID" ]]; then
                    "$PYTHON" -m worktree_manager post-exit "$WORKTREE_ID" 2>/dev/null || true
                fi
                exit 1
            fi

            if [[ -n "${TMUX:-}" ]]; then
                tmux switch-client -t "=$TMUX_SESS"
            else
                tmux attach-session -t "=$TMUX_SESS"
            fi
            set -e

            # We're back — either the user detached or the session ended.
            # Only run post-exit if the session is truly gone (user exited
            # the shell, not just detached).
            if ! tmux has-session -t "=$TMUX_SESS" 2>/dev/null; then
                # Check for handoff-driven relaunch before post-exit
                if [[ -n "$WORKTREE_ID" ]]; then
                    HANDOFF_JSON=$("$PYTHON" -m worktree_manager handoff consume "$WORKTREE_ID" 2>/dev/null) || true
                    if [[ -n "$HANDOFF_JSON" ]]; then
                        HANDOFF_PATH=$(echo "$HANDOFF_JSON" | "$PYTHON" -c "import sys,json; print(json.load(sys.stdin).get('prompt_path',''))" 2>/dev/null) || HANDOFF_PATH=""
                        if [[ -n "$HANDOFF_PATH" && -f "$HANDOFF_PATH" ]]; then
                            setup_log INFO "Handoff relaunch (tmux): consuming $HANDOFF_PATH"
                            echo ""
                            echo "Handoff detected — relaunching with continuation prompt..."
                            echo ""
                            HANDOFF_PROMPT="Continuing from a previous session in this worktree. A handoff was prepared - read it for full context: cat \"$HANDOFF_PATH\""
                            set +e
                            tmux new-session -d -s "$TMUX_SESS" -c "$WORK_DIR" "${TMUX_ENV_FLAGS[@]+"${TMUX_ENV_FLAGS[@]}"}" "${CMD_ARRAY[@]}" -i "$HANDOFF_PROMPT"
                            if [[ $? -eq 0 ]]; then
                                # Smoke-test: let the process initialize so
                                # we can detect immediate crashes before attach.
                                sleep 0.3

                                if ! tmux has-session -t "=$TMUX_SESS" 2>/dev/null; then
                                    setup_log ERROR "tmux handoff session $TMUX_SESS died immediately — relaunch crashed"
                                    echo "" >&2
                                    echo "ERROR: Handoff relaunch crashed before startup completed." >&2
                                    echo "       tmux session '$TMUX_SESS' exited immediately." >&2
                                    echo "" >&2
                                    echo "Tip: check the setup log at $SETUP_LOG" >&2
                                    echo "     or relaunch manually." >&2
                                    echo "" >&2
                                    read -rp "Press Enter to close..." _ </dev/tty 2>/dev/null || sleep 5
                                    set -e
                                    exit 1
                                fi

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
                        "$PYTHON" -m worktree_manager post-exit "$WORKTREE_ID" || \
                            echo "WARNING: Post-exit finalization failed. Run 'worktree-manager finalize' to retry." >&2
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

    # Check for handoff-driven relaunch (max once per launcher invocation)
    if [[ -n "$WORKTREE_ID" ]]; then
        HANDOFF_JSON=$("$PYTHON" -m worktree_manager handoff consume "$WORKTREE_ID" 2>/dev/null) || true
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
        "$PYTHON" -m worktree_manager post-exit "$WORKTREE_ID" || \
            echo "WARNING: Post-exit finalization failed. Run 'worktree-manager finalize' to retry." >&2
    fi

    exit $COPILOT_EXIT
fi

echo "ERROR: Unknown action: $ACTION" >&2
exit 1
