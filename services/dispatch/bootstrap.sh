#!/usr/bin/env bash
# bootstrap.sh — fetch and install the copilot-extensions agent-dispatch
# coordinator, with NO Copilot CLI and NO marketplace involved.
#
#   curl -fsSL https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/services/dispatch/bootstrap.sh | bash
#
# This is delivered OUTSIDE the plugin pipe, mirroring
# worktree-manager/bootstrap.sh's exact shape: it does not require any
# Copilot plugin to be installed, and does not go through `copilot plugin`.
# It fetches the repository payload, then hands off to the EXISTING,
# already-mature `plugins/agent-dispatch/scripts/install.sh` -- the same
# versioned-venv + binstub + systemd-user-unit coordinator installer every
# plugin-delivered agent-dispatch host already uses. Nothing about that
# installer is reimplemented here; this script only supplies the
# Copilot-independent fetch step install.sh otherwise gets from the
# marketplace payload.
#
# Usage:
#   bash bootstrap.sh install        # venv + binstub + coordinator service
#   bash bootstrap.sh update         # idempotent refresh (downgrade-guarded)
#   bash bootstrap.sh status
#   bash bootstrap.sh start | stop
#   bash bootstrap.sh uninstall [--purge]
#
# Every install.sh flag (--no-service, --no-supervisor, --purge, --force,
# --install-dir DIR) passes straight through.
#
# Prerequisites are auto-provisioned: git is installed best-effort where a
# package manager exists, otherwise the payload is fetched as a GitHub
# tarball so a bare machine still bootstraps (install.sh itself
# auto-provisions uv the same way its plugin-delivered path already does).
# The git source (repo + ref) is taken from $AGENT_DISPATCH_SERVICE_REPO /
# $AGENT_DISPATCH_SERVICE_REF when set, otherwise the canonical defaults
# below. Relocate the staging root with $AGENT_DISPATCH_SERVICE_ROOT.

set -euo pipefail

ROOT="${AGENT_DISPATCH_SERVICE_ROOT:-$HOME/.agent-dispatch-service}"
STAGING="$ROOT/staging"

REPO="${AGENT_DISPATCH_SERVICE_REPO:-https://github.com/ThomasMichon/copilot-extensions.git}"
REF="${AGENT_DISPATCH_SERVICE_REF:-main}"

echo 'copilot-extensions agent-dispatch - standalone bootstrap'

# -- Prerequisites (auto-provision; restart-aware; user-local first) --------
# git is installed best-effort where a package manager exists, else we fetch
# the payload as a GitHub tarball so the bootstrap never dead-ends without
# git. (uv provisioning is install.sh's own job, same as every plugin-
# delivered host -- not duplicated here.)

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

# -- Fetch the payload (git clone/fetch when available, else GitHub tarball) -
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

# Hand off to the existing, already-mature coordinator installer. install.sh
# resolves its own payload (src/, libs/, pyproject.toml) relative to itself,
# so running it from this freshly fetched checkout works exactly like running
# it from an installed marketplace payload.
exec bash "$payload_parent/plugins/agent-dispatch/scripts/install.sh" "$@"
