#!/usr/bin/env bash
# Bootstrap check — read-only, runs on session start
# Checks whether the worktree-manager runtime is installed and on PATH.
# If missing, prints a hint. Never installs anything automatically.

if ! command -v worktree-manager &>/dev/null; then
    echo ""
    echo -e "\033[33m[worktree-manager] Runtime not installed.\033[0m"
    echo -e "\033[90m  Run the 'worktree-setup' skill to bootstrap: ask Copilot to 'set up worktree-manager'\033[0m"
    echo ""
fi
exit 0
