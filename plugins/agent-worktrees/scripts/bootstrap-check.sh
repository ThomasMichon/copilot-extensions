#!/usr/bin/env bash
# Bootstrap check — read-only, runs on session start
# Checks whether the agent-worktrees runtime is installed and on PATH.
# If missing, prints a hint. Never installs anything automatically.

if ! command -v agent-worktrees &>/dev/null; then
    echo ""
    echo -e "\033[33m[agent-worktrees] Runtime not installed.\033[0m"
    echo -e "\033[90m  Run the 'worktree-setup' skill to bootstrap: ask Copilot to 'set up agent-worktrees'\033[0m"
    echo ""
fi
exit 0
