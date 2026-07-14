#!/usr/bin/env bash
# Wire this checkout's git to the tracked hooks under tools/hooks so the
# pre-commit / pre-push guards run. Safe to re-run; will not overwrite a
# non-standard core.hooksPath. Git does not auto-enable a committed hooks dir,
# so run this once per clone (and per worktree host that lacks it).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

current="$(git config --local core.hooksPath 2>/dev/null || true)"
if [[ "$current" == "tools/hooks" ]]; then
    echo "core.hooksPath already = tools/hooks"
    exit 0
fi
if [[ -n "$current" ]]; then
    echo "core.hooksPath already set to '$current' -- not overwriting." >&2
    echo "  To force: git config --local core.hooksPath tools/hooks" >&2
    exit 0
fi
git config --local core.hooksPath tools/hooks
echo "Set core.hooksPath = tools/hooks (pre-commit + pre-push guards active)."
