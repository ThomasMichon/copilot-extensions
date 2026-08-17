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
# Prerequisites are auto-provisioned: uv is installed user-local (no admin) when
# missing; git is installed best-effort where a package manager exists, otherwise
# the payload is fetched as a GitHub tarball so a bare machine still bootstraps.
# The git source (repo + ref) is taken from the user-level source config
# ($ROOT/config.toml [source]) when present — set it with `worktree-manager
# source set` to track a fork / canary branch — otherwise the canonical defaults
# below. Relocate the install root with $WORKTREE_MANAGER_ROOT.

set -euo pipefail

ROOT="${WORKTREE_MANAGER_ROOT:-$HOME/.worktree-manager}"
STAGING="$ROOT/staging"
CONFIG="$ROOT/config.toml"

# Source (repo + ref): user-level config file [source] overrides, else defaults.
REPO='https://github.com/ThomasMichon/copilot-extensions.git'
REF='main'
if [ -f "$CONFIG" ]; then
    # Read a key only from within the [source] table (ignore other tables).
    _wm_src_cfg() {
        awk -v k="$1" '
            /^[[:space:]]*\[/ { insrc = ($0 ~ /^[[:space:]]*\[source\][[:space:]]*$/); next }
            insrc && match($0, "^[[:space:]]*" k "[[:space:]]*=[[:space:]]*\"") {
                s = substr($0, RSTART + RLENGTH); sub(/".*/, "", s); print s; exit
            }' "$CONFIG"
    }
    cfg_repo=$(_wm_src_cfg repo)
    cfg_ref=$(_wm_src_cfg ref)
    [ -n "${cfg_repo:-}" ] && REPO="$cfg_repo"
    [ -n "${cfg_ref:-}" ] && REF="$cfg_ref"
fi

echo 'copilot-extensions Worktree Manager - bootstrap'

# -- Prerequisites (auto-provision; restart-aware; user-local first) ----------
# The one-liner must take a *bare* machine into the Manager (vision installer
# one-line-bootstrap). uv is auto-installed (user-local, no admin). git is
# installed best-effort where a package manager exists, else we fetch the payload
# as a GitHub tarball so the bootstrap never dead-ends without git.

LOCAL_BIN="$HOME/.local/bin"

if ! command -v uv >/dev/null 2>&1; then
    echo '  uv not found - installing (user-local, no admin)...'
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # uv installs into ~/.local/bin (or its env file); amend THIS session's PATH
    # so we can continue without a restart (the installer persists it for future
    # shells).
    [ -f "$LOCAL_BIN/env" ] && . "$LOCAL_BIN/env" || true
    export PATH="$LOCAL_BIN:$PATH"
    if ! command -v uv >/dev/null 2>&1; then
        echo 'ERROR: uv was installed but is not on PATH yet. Restart your shell and re-run.' >&2
        exit 1
    fi
fi

have_git=0
command -v git >/dev/null 2>&1 && have_git=1
if [ "$have_git" -eq 0 ]; then
    os="$(uname -s)"
    if [ "$os" = "Darwin" ] && command -v brew >/dev/null 2>&1; then
        echo '  git not found - installing via Homebrew...'
        brew install git || true
    elif [ "$os" = "Linux" ] && [ "$(id -u)" = "0" ]; then
        echo '  git not found - installing via the system package manager...'
        if command -v apt-get >/dev/null 2>&1; then apt-get update -qq && apt-get install -y git || true
        elif command -v dnf >/dev/null 2>&1; then dnf install -y git || true
        elif command -v pacman >/dev/null 2>&1; then pacman -Sy --noconfirm git || true
        fi
    fi
    command -v git >/dev/null 2>&1 && have_git=1
    [ "$have_git" -eq 0 ] && echo '  git unavailable - will fetch the payload as a tarball (no git).'
fi

# -- Fetch the payload (git clone/fetch when available, else GitHub tarball) ---
if [ "$have_git" -eq 1 ]; then
    mkdir -p "$STAGING"
    if [ -d "$STAGING/.git" ]; then
        git -C "$STAGING" fetch --depth 1 "$REPO" "$REF" >/dev/null 2>&1
        git -C "$STAGING" checkout -q FETCH_HEAD
    else
        rm -rf "$STAGING"; mkdir -p "$STAGING"
        git clone --depth 1 --branch "$REF" "$REPO" "$STAGING" >/dev/null 2>&1
    fi
    payload_parent="$STAGING"
else
    # Derive the codeload tarball URL from the (GitHub) source; a non-GitHub
    # source genuinely needs git. POSIX parameter expansion (no lazy regex).
    norm="${REPO%/}"; norm="${norm%.git}"
    tar_url=""
    case "$norm" in
        *github.com*)
            rest="${norm#*github.com}"; rest="${rest#[:/]}"   # owner/name
            owner="${rest%%/*}"; name="${rest##*/}"
            tar_url="https://codeload.github.com/$owner/$name/tar.gz/$REF"
            ;;
    esac
    if [ -z "$tar_url" ]; then
        echo "ERROR: git is required for a non-GitHub source ($REPO). Install git and re-run." >&2
        exit 1
    fi
    echo '  fetching payload tarball (no git)...'
    mkdir -p "$STAGING"
    extract="$STAGING/extract"; rm -rf "$extract"; mkdir -p "$extract"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$tar_url" -o "$STAGING/payload.tar.gz"
    else
        wget -qO "$STAGING/payload.tar.gz" "$tar_url"
    fi
    tar -xzf "$STAGING/payload.tar.gz" -C "$extract"
    payload_parent="$(find "$extract" -mindepth 1 -maxdepth 1 -type d | head -n1)"
    rm -f "$STAGING/payload.tar.gz"
fi

cd "$payload_parent/worktree-manager"
# Version-install the fetched payload (idempotent, version-gated): publishes the
# versions/<ver> slot + current-version marker + ~/.local/bin binstub.
uv run --quiet python -m worktree_manager self-install --apply

case ":$PATH:" in
    *":$LOCAL_BIN:"*) : ;;
    *) echo "NOTE: add '$LOCAL_BIN' to your PATH (uv and the worktree-manager binstub live there), then restart your shell." >&2 ;;
esac

# Then run whatever the caller asked for through the freshly-installed app.
exec uv run --quiet python -m worktree_manager "$@"
