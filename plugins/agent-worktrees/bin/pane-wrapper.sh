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

START_TIME=$(date +%s)
"$@"
EXIT_CODE=$?
END_TIME=$(date +%s)
RUNTIME=$((END_TIME - START_TIME))

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
