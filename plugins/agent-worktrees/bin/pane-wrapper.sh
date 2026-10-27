#!/usr/bin/env bash
# worktree-manager:pane-boundary-v1
# pane-wrapper.sh -- wraps the tmux/psmux pane command to handle exit
# codes gracefully.
#
# Behavior:
#   exit 0, runtime >= threshold : exit 0 silently (normal session end)
#   exit 130 (SIGINT / Ctrl+C)  : exit 0 silently (intentional interrupt)
#   exit 0, runtime < threshold  : pause with diagnostic (startup crash)
#   any other non-zero exit      : pause with diagnostic (error/crash)
#
# Always exits 0 so tmux's remain-on-exit doesn't trap the pane.
# The pause gives the user time to read error output before the pane
# closes.  Auto-closes after a timeout to prevent abandoned tabs.

set +e

MIN_RUNTIME="${WORKTREE_PANE_MIN_RUNTIME:-3}"
WAIT_TIMEOUT="${WORKTREE_PANE_WAIT_TIMEOUT:-60}"
PROMPT_STARTUP_GRACE="${WORKTREE_PROMPT_STARTUP_GRACE:-3}"

# Optional leading `--aw-wt <id>`: the worktree id for the pane_exited activity
# mark. Consumed here so it is never forwarded to the wrapped command.
AW_WT=""
AW_PROJECT=""
AW_MUX_SESSION=""
AW_MANAGER_ROOT=""
AW_MUX_BIN=""
INITIAL_PROMPT_B64=""
INITIAL_PROMPT_RECEIPT_B64=""
AHP_TOKEN_FILE=""
while [[ $# -ge 2 ]]; do
    case "$1" in
        --aw-wt) AW_WT="$2" ;;
        --aw-project) AW_PROJECT="$2" ;;
        --aw-mux-session) AW_MUX_SESSION="$2" ;;
        --aw-manager-root) AW_MANAGER_ROOT="$2" ;;
        --aw-mux-bin) AW_MUX_BIN="$2" ;;
        --aw-prompt-b64) INITIAL_PROMPT_B64="$2" ;;
        --aw-prompt-receipt-b64) INITIAL_PROMPT_RECEIPT_B64="$2" ;;
        --aw-ahp-token-file) AHP_TOKEN_FILE="$2" ;;
        *) break ;;
    esac
    shift 2
done
[[ $# -gt 0 ]] || exit 0

# The callback job has its own process group and no terminal or credentials.
# Keep the deadline and cleanup in this copied OS shell, not in a runtime waiter.
aw_pane_boundary_process() (
    local ticks="$1" output helper="" result i
    shift
    output="$(mktemp "${BASH_SOURCE[0]%/*}/.pane-boundary.XXXXXX")" || return 1
    trap 'if [[ -n "$helper" ]]; then kill -KILL -- "-$helper" 2>/dev/null || true; fi;
          rm -f -- "$output" "$output.err"' EXIT
    set -m
    "$@" </dev/null >"$output" 2>"$output.err" &
    helper=$!
    set +m
    for ((i=0; i<ticks; i++)); do
        kill -0 "$helper" 2>/dev/null || break
        sleep 0.05
    done
    if kill -0 "$helper" 2>/dev/null; then
        kill -TERM -- "-$helper" 2>/dev/null || true
        sleep 0.05
        kill -KILL -- "-$helper" 2>/dev/null || true
        wait "$helper" 2>/dev/null || true
        helper=""
        return 1
    fi
    wait "$helper"
    result=$?
    helper=""
    [[ "$result" -eq 0 ]] || return 1
    cat -- "$output"
) 2>/dev/null

aw_pane_boundary() {
    local action="$1" version slot python bootstrap
    [[ "$AW_MANAGER_ROOT" == /* && -f "$AW_MANAGER_ROOT/current-version" ]] || return 1
    IFS= read -r version < "$AW_MANAGER_ROOT/current-version" || [[ -n "$version" ]] || return 1
    [[ "$version" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]{0,255}$ ]] || return 1
    slot="$AW_MANAGER_ROOT/versions/$version"
    python="$slot/bin/python"
    bootstrap="$slot/.runtime-bootstrap/runtime_launcher.py"
    [[ -x "$python" && -f "$bootstrap" && -f "$slot/.install-complete.json" ]] || return 1
    local -a arguments=(
        -I -S -B -X utf8 "$bootstrap" -m worktree_manager.pane_lifecycle "$action" --shell
        --contract-version 1
        --root "$AW_MANAGER_ROOT" --project "$AW_PROJECT" --worktree-id "$AW_WT"
        --mux-session "$AW_MUX_SESSION" --session-incarnation "$AW_SESSION_INCARNATION"
        --pane-id "$AW_NATIVE_PANE"
    )
    if [[ "$action" == start ]]; then
        arguments+=(--worktree-path "$PWD" --mux-bin "$AW_MUX_BIN")
    else
        arguments+=(--expected-installation "$AW_BOUNDARY_INSTALLATION")
        if [[ "$action" == end ]]; then
            arguments+=(--exit-code "$EXIT_CODE" --runtime "$RUNTIME")
            if [[ -n "${WORKTREE_LAUNCH_ID:-}" ]]; then
                arguments+=(--launch-id "$WORKTREE_LAUNCH_ID")
            fi
        fi
    fi
    aw_pane_boundary_process 100 env \
        -u PYTHONPATH -u PYTHONHOME -u VIRTUAL_ENV -u __PYVENV_LAUNCHER__ \
        -u GH_TOKEN -u GITHUB_TOKEN -u AGENT_WORKTREES_AHP_AUTH_TOKEN \
        WORKTREE_MANAGER_ROOT="$AW_MANAGER_ROOT" "$python" "${arguments[@]}"
}

AW_SESSION_INCARNATION=""
AW_NATIVE_PANE=""
AW_BOUNDARY_INSTALLATION=""
AW_CHILD_ATTEMPTED=0
aw_pane_failed_start() {
    local status=$?
    if [[ "$AW_CHILD_ATTEMPTED" -eq 0 && -n "$AW_BOUNDARY_INSTALLATION" ]]; then
        aw_pane_boundary cancel >/dev/null 2>&1 || true
    fi
    return "$status"
}
trap aw_pane_failed_start EXIT
if [[ -n "$AW_MANAGER_ROOT" && -n "$AW_PROJECT" && -n "$AW_WT" \
      && -n "$AW_MUX_SESSION" && -n "$AW_MUX_BIN" ]]; then
    if native="$(aw_pane_boundary_process 40 "$AW_MUX_BIN" display-message -p \
        '#{session_id}:#{session_created}|#{pane_id}')" \
        && [[ "$native" =~ ^(\$[0-9]+:[0-9]+)\|(%[0-9]+)$ ]]; then
        AW_SESSION_INCARNATION="${BASH_REMATCH[1]}"
        AW_NATIVE_PANE="${BASH_REMATCH[2]}"
        if reply="$(aw_pane_boundary start)" \
            && [[ "$reply" =~ ^worktree-pane-boundary-v1$'\t'start$'\t'([0-9a-f]{64})$'\t'1$ ]]; then
            AW_BOUNDARY_INSTALLATION="${BASH_REMATCH[1]}"
        fi
    fi
fi

if [[ -n "$AHP_TOKEN_FILE" ]]; then
    if [[ -L "$AHP_TOKEN_FILE" || ! -f "$AHP_TOKEN_FILE" ]]; then
        echo "[agent-worktrees] invalid AHP token handoff file" >&2
        exit 2
    fi
    AHP_TOKEN=$(cat -- "$AHP_TOKEN_FILE") || exit 2
    if ! rm -f -- "$AHP_TOKEN_FILE"; then
        unset AHP_TOKEN
        echo "[agent-worktrees] could not retire AHP token handoff file" >&2
        exit 2
    fi
    if [[ -z "$AHP_TOKEN" || "$AHP_TOKEN" == *$'\n'* ]]; then
        unset AHP_TOKEN
        echo "[agent-worktrees] invalid AHP token handoff payload" >&2
        exit 2
    fi
    AHP_CHILD_TOKEN="$AHP_TOKEN"
    unset AHP_TOKEN
    if [[ ",${COPILOT_CLI_ENABLED_FEATURE_FLAGS:-}," != *",AHP_CLIENT,"* ]]; then
        if [[ -n "${COPILOT_CLI_ENABLED_FEATURE_FLAGS:-}" ]]; then
            AHP_CHILD_FEATURES="${COPILOT_CLI_ENABLED_FEATURE_FLAGS},AHP_CLIENT"
        else
            AHP_CHILD_FEATURES="AHP_CLIENT"
        fi
    else
        AHP_CHILD_FEATURES="$COPILOT_CLI_ENABLED_FEATURE_FLAGS"
    fi
fi

# Native interactive handoff seed. UTF-8 base64 keeps every wrapper control
# argument space-free so psmux never sees a multi-word pane argument. Decode
# after mux argv handling, append a real Copilot argument, and write the receipt
# before exec.
if [[ -n "$INITIAL_PROMPT_B64" ]]; then
    # Command substitution strips trailing newlines. Append a non-newline
    # sentinel inside the substitution, then remove only that sentinel so the
    # original prompt (including any trailing newlines) survives byte-for-byte.
    if INITIAL_PROMPT_RAW="$(
        { printf '%s' "$INITIAL_PROMPT_B64" | base64 --decode; rc=$?;
          printf '\034'; exit "$rc"; } 2>/dev/null
    )"; then
        INITIAL_PROMPT="${INITIAL_PROMPT_RAW%$'\034'}"
        :
    elif INITIAL_PROMPT_RAW="$(
        { printf '%s' "$INITIAL_PROMPT_B64" | base64 -D; rc=$?;
          printf '\034'; exit "$rc"; } 2>/dev/null
    )"; then
        INITIAL_PROMPT="${INITIAL_PROMPT_RAW%$'\034'}"
        :
    else
        echo "[agent-worktrees] invalid initial-prompt transport" >&2
        exit 2
    fi
    if RECEIPT_PATH="$(printf '%s' "$INITIAL_PROMPT_RECEIPT_B64" | base64 --decode 2>/dev/null)"; then
        :
    elif RECEIPT_PATH="$(printf '%s' "$INITIAL_PROMPT_RECEIPT_B64" | base64 -D 2>/dev/null)"; then
        :
    else
        echo "[agent-worktrees] invalid initial-prompt receipt path" >&2
        exit 2
    fi
    [[ -n "$RECEIPT_PATH" ]] || exit 2
    set -- "$@" --interactive "$INITIAL_PROMPT"
    RECEIPT_DIR="$(dirname "$RECEIPT_PATH")"
    mkdir -p "$RECEIPT_DIR" || exit 2
    RECEIPT_TMP="$RECEIPT_PATH.$$.tmp"
    printf 'launching' > "$RECEIPT_TMP" || exit 2
    mv -f "$RECEIPT_TMP" "$RECEIPT_PATH" || exit 2
fi

START_TIME=$(date +%s)
AW_CHILD_ATTEMPTED=1
if [[ -n "${AHP_CHILD_TOKEN:-}" ]]; then
    GH_TOKEN="$AHP_CHILD_TOKEN" \
    COPILOT_CLI_ENABLED_FEATURE_FLAGS="$AHP_CHILD_FEATURES" \
        env -u GITHUB_TOKEN -u AGENT_WORKTREES_AHP_AUTH_TOKEN "$@"
else
    "$@"
fi
EXIT_CODE=$?
unset AHP_CHILD_TOKEN AHP_CHILD_FEATURES
END_TIME=$(date +%s)
RUNTIME=$((END_TIME - START_TIME))

# A prompt receipt is provisional until the child survives startup. If native
# --interactive is rejected or the launcher fails immediately, overwrite it so
# the parent keeps the predecessor and reaps this failed successor.
if [[ -n "${RECEIPT_PATH:-}" ]] \
    && { [[ $EXIT_CODE -ne 0 ]] || [[ $RUNTIME -lt $PROMPT_STARTUP_GRACE ]]; }; then
    RECEIPT_TMP="$RECEIPT_PATH.$$.tmp"
    if printf 'failed:%s' "$EXIT_CODE" > "$RECEIPT_TMP"; then
        mv -f "$RECEIPT_TMP" "$RECEIPT_PATH" || true
    fi
fi

# Durable pane-exit mark (Tier-A): the only place the mux pane's real exit code
# is observable (the launcher can't see it -- the child ran inside the pane).
# Best-effort, fully detached, fail-silent -- must never delay pane teardown.
# Correlates to the launch flow via WORKTREE_LAUNCH_ID (inherited from the mux
# server env). Fires on every exit path, before the interrupt/clean shortcuts.
if [[ -n "$AW_MANAGER_ROOT" ]]; then
    if [[ -n "$AW_BOUNDARY_INSTALLATION" ]]; then
        aw_pane_boundary end >/dev/null 2>&1 || true
    fi
else
# Legacy callers retain their prior callbacks until migrated to copied shells.
if command -v agent-worktrees >/dev/null 2>&1; then
    ( agent-worktrees activity-log pane_exited --source launcher \
        ${AW_WT:+--worktree-id "$AW_WT"} \
        ${WORKTREE_LAUNCH_ID:+--launch-id "$WORKTREE_LAUNCH_ID"} \
        --field "exit_code=$EXIT_CODE" --field "runtime=$RUNTIME" \
        >/dev/null 2>&1 & ) || true
fi
if [[ -n "$AW_PROJECT" && -n "$AW_WT" ]]; then
    WM_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P 2>/dev/null || true)"
    if [[ -n "$WM_ROOT" ]]; then
        source "$WM_ROOT/bin/manager-runtime.sh" || true
        # Derive the real mux session's own stable identity (Copilot review
        # finding on PR #3906): the display session name alone is NOT
        # unique per launch incarnation -- both launchers derive the same
        # deterministic `wt-<worktree_id>`-shaped name for the same
        # worktree, so an old pane wrapper and a newer live mapping compare
        # equal by name alone and a stale teardown can still tombstone the
        # replacement. tmux's own #{session_id}:#{session_created} IS
        # unique per real session object and stays IDENTICAL across
        # repeated joins of the same still-live session (unlike a
        # freshly-generated per-call token, which would falsely look
        # "stale" after every join re-registers a new one) -- query it live
        # from inside this same pane rather than threading a value from the
        # launcher.
        AW_SESSION_INCARNATION=""
        if command -v tmux >/dev/null 2>&1; then
            AW_SESSION_INCARNATION="$(tmux display-message -p '#{session_id}:#{session_created}' 2>/dev/null || true)"
        fi
        # Wait only for the canonical spawn handshake. The metadata worker
        # then survives this pane's cleanup without delaying teardown on its
        # potentially expensive CLI operation.
        # `--mux-session`, when known (#3838 fix): lets the daemon-side
        # registry reject this removal as a stale no-op if a newer
        # session already superseded this one for the same
        # (project, worktree_id) -- an old pane's delayed teardown must
        # never tombstone a newer, still-live session's mapping.
        aw_manager_callback "$WM_ROOT" remove \
            --project="$AW_PROJECT" \
            --worktree-id="$AW_WT" \
            ${AW_MUX_SESSION:+--mux-session="$AW_MUX_SESSION"} \
            ${AW_SESSION_INCARNATION:+--session-incarnation="$AW_SESSION_INCARNATION"} \
            >/dev/null 2>&1 || true
    fi
fi
fi

# Intentional interrupt -- exit silently so post-exit finalization runs
if [[ $EXIT_CODE -eq 130 ]]; then
    exit 0
fi

# Normal exit after running long enough -- nothing to report
if [[ $EXIT_CODE -eq 0 && $RUNTIME -ge $MIN_RUNTIME ]]; then
    exit 0
fi

# Something worth showing the user -- crash, error, or suspiciously fast exit
echo ""
echo "------------------------------------------------------------"
if [[ $EXIT_CODE -eq 0 ]]; then
    echo "  Session exited immediately (runtime: ${RUNTIME}s)"
    echo "  This usually means a startup error occurred."
elif [[ $EXIT_CODE -ge 128 ]]; then
    SIG=$((EXIT_CODE - 128))
    echo "  Session terminated by signal $SIG (exit code $EXIT_CODE)"
else
    echo "  Session exited with code $EXIT_CODE"
fi
echo ""
if [[ -n "${WORKTREE_SETUP_LOG:-}" && -f "$WORKTREE_SETUP_LOG" ]]; then
    echo "  Setup log: $WORKTREE_SETUP_LOG"
    echo ""
fi
echo "  Press any key to close, or wait ${WAIT_TIMEOUT}s..."
echo "------------------------------------------------------------"
read -rsn1 -t "$WAIT_TIMEOUT" </dev/tty 2>/dev/null || true
exit 0
