#!/usr/bin/env bash
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
INITIAL_PROMPT_B64=""
INITIAL_PROMPT_RECEIPT_B64=""
AHP_TOKEN_FILE=""
while [[ $# -ge 2 ]]; do
    case "$1" in
        --aw-wt) AW_WT="$2" ;;
        --aw-prompt-b64) INITIAL_PROMPT_B64="$2" ;;
        --aw-prompt-receipt-b64) INITIAL_PROMPT_RECEIPT_B64="$2" ;;
        --aw-ahp-token-file) AHP_TOKEN_FILE="$2" ;;
        *) break ;;
    esac
    shift 2
done

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
if command -v agent-worktrees >/dev/null 2>&1; then
    ( agent-worktrees activity-log pane_exited --source launcher \
        ${AW_WT:+--worktree-id "$AW_WT"} \
        ${WORKTREE_LAUNCH_ID:+--launch-id "$WORKTREE_LAUNCH_ID"} \
        --field "exit_code=$EXIT_CODE" --field "runtime=$RUNTIME" \
        >/dev/null 2>&1 & ) || true
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
