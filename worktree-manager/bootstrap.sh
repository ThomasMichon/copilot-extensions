#!/usr/bin/env bash
# bootstrap.sh — fetch, version-install, and launch the copilot-extensions Worktree Manager.
#
#   curl -fsSL https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/worktree-manager/bootstrap.sh | bash
#
# This is delivered OUTSIDE the plugin pipe: it does not require any Copilot
# plugin to be installed, and does not go through `copilot plugin`. It fetches
# the Worktree Manager payload, installs it under the SAME versioning convention as
# the harness's other installers — an immutable ~/.worktree-manager/versions/<ver>
# slot, a plain-text ~/.worktree-manager/current-version marker, and a
# ~/.local/bin/worktree-manager binstub — then launches it. Re-running is
# version-gated (a no-op when already current).
#
# Phase 0 assumes git + uv are already present; automatic prerequisite
# provisioning lands in Phase 2 (issue #355). Override the fetched ref with
# $WORKTREE_MANAGER_REF, the git source (mirror/fork) with $WORKTREE_MANAGER_REPO,
# and the install root with $WORKTREE_MANAGER_ROOT.

set -euo pipefail

REPO="${WORKTREE_MANAGER_REPO:-https://github.com/ThomasMichon/copilot-extensions.git}"
REF="${WORKTREE_MANAGER_REF:-main}"
ROOT="${WORKTREE_MANAGER_ROOT:-$HOME/.worktree-manager}"
STAGING="$ROOT/staging"

echo 'copilot-extensions Worktree Manager - bootstrap'

for tool in git uv; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "ERROR: $tool is required for the Phase 0 bootstrap (automatic prerequisite install lands in Phase 2 / issue #355). Install $tool and re-run." >&2
        exit 1
    fi
done

mkdir -p "$STAGING"
if [ -d "$STAGING/.git" ]; then
    git -C "$STAGING" fetch --depth 1 origin "$REF" >/dev/null 2>&1
    git -C "$STAGING" checkout -q FETCH_HEAD
else
    git clone --depth 1 --branch "$REF" "$REPO" "$STAGING" >/dev/null 2>&1
fi

cd "$STAGING/worktree-manager"
# Version-install the fetched payload (idempotent, version-gated): publishes the
# versions/<ver> slot + current-version marker + ~/.local/bin binstub.
uv run --quiet python -m worktree_manager self-install --apply
# Then run whatever the caller asked for through the freshly-installed app.
exec uv run --quiet python -m worktree_manager "$@"
