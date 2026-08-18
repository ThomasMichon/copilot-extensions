#!/usr/bin/env bash
# worktree-manager-bootstrap/scenario.sh -- clean-room proof of the out-of-plugin
# Worktree Manager bootstrap one-liner on a PRISTINE box (no uv).
#
# Falsifies: "the bootstrap dead-ends on a bare machine without a pre-installed
# toolchain." On the pristine image (Copilot + git + a system python3, but NO
# uv / pip / venv / ~/.local/bin) the bootstrap one-liner must SELF-PROVISION uv
# (user-local), fetch the payload, and publish the versioned slot +
# current-version marker + the ~/.local/bin/worktree-manager binstub -- then the
# binstub runs a read-only verb from a stock login shell.
#
# Asserts on FILESYSTEM/CLI OUTCOMES (marker + slot + binstub + a clean verb
# exit), not exact CLI syntax, so it stays robust across versions and records the
# surface it saw. Name-free / public.
#
# Config (env):
#   CR_MANAGER_REPO   owner/name of the source repo (raw bootstrap + payload)
#                     default: ThomasMichon/copilot-extensions
#   CR_MANAGER_REF    branch/ref to bootstrap from    default: main
#
# CR_LIB: absolute path to clean-room-lib.sh (the runner sets this; falls back to
# a path relative to this script for a hand-run).
set -uo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/clean-room-lib.sh
source "${CR_LIB:-$_SELF_DIR/../../lib/clean-room-lib.sh}"

MANAGER_REPO="${CR_MANAGER_REPO:-ThomasMichon/copilot-extensions}"
MANAGER_REF="${CR_MANAGER_REF:-main}"
BOOTSTRAP_URL="https://raw.githubusercontent.com/$MANAGER_REPO/$MANAGER_REF/worktree-manager/bootstrap.sh"
WM_ROOT="$HOME/.worktree-manager"

: "${CR_SCENARIO_NAME:=worktree-manager-bootstrap}"
export CR_SCENARIO_NAME
cr_init
cr_meta "manager_repo" "$MANAGER_REPO"
cr_meta "manager_ref"  "$MANAGER_REF"
cr_meta "bootstrap_url" "$BOOTSTRAP_URL"

# =========================================================================
phase 0 "environment (pristine: no uv, no ~/.local/bin, no ~/.worktree-manager)"
envdump
info "whoami=$(whoami) HOME=$HOME"
info "git: $(command -v git || echo MISSING)  curl: $(command -v curl || echo MISSING)  python3: $(command -v python3 || echo MISSING)"
info "uv: $(command -v uv || echo MISSING)  (pristine box: expected MISSING -- the bootstrap must provision it)"
info "login-shell PATH: $(bash -lc 'echo $PATH')"
_clean=1
[ -d "$WM_ROOT" ] && { _clean=0; info "pre-existing $WM_ROOT"; }
[ -e "$HOME/.local/bin/worktree-manager" ] && { _clean=0; info "pre-existing worktree-manager binstub"; }
if [ "$_clean" -eq 1 ]; then
    pass "clean slate: no ~/.worktree-manager, no worktree-manager binstub"
else
    fail "environment is NOT clean (see info lines above)"
fi
if command -v uv >/dev/null 2>&1; then
    info "NOTE: uv is already present -- the uv self-provision path won't be exercised (use the pristine image to falsify it)"
fi

# =========================================================================
phase 1 "run the bootstrap one-liner (self-provisions uv, fetches payload, installs)"
# Pipe the published one-liner exactly as a user would. Pass a non-interactive,
# zero-on-success verb (--version) through so the post-install passthrough
# neither hangs (no bare interactive default) nor conflates a read-only "fully
# set up?" verdict (doctor exits non-zero on a fresh box by design) with whether
# the bootstrap itself succeeded.
capture "bootstrap" -- bash -c "curl -fsSL '$BOOTSTRAP_URL' | bash -s -- --version"
_rc=$?
if [ "$_rc" -eq 0 ]; then
    pass "bootstrap one-liner exited 0"
elif grep -qiE 'astral|install\.sh|uv' "$CR_LOGDIR/bootstrap.log" 2>/dev/null && ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
    jam "toolchain-uv" "bootstrap: uv self-provision failed (exit $_rc; see cr-logs/bootstrap.log)" \
        "confirm astral.sh is reachable; uv installs user-local into ~/.local/bin"
else
    jam "path-binstub" "bootstrap: one-liner exited $_rc (see cr-logs/bootstrap.log)" \
        "inspect the fetch/self-install step in the log"
fi

# =========================================================================
phase 2 "uv self-provisioned + versioned install published"
if command -v uv >/dev/null 2>&1 || [ -x "$HOME/.local/bin/uv" ]; then
    pass "uv is now present (auto-provisioned by the bootstrap)"
else
    jam "toolchain-uv" "uv still absent after bootstrap" \
        "the bootstrap must install uv user-local when it is missing"
fi
if [ -f "$WM_ROOT/current-version" ]; then
    _ver="$(cat "$WM_ROOT/current-version" 2>/dev/null)"
    if [ -n "$_ver" ] && [ -d "$WM_ROOT/versions/$_ver" ]; then
        pass "versioned install published: current-version=$_ver + versions/$_ver slot"
    else
        fail "current-version marker present ('$_ver') but its slot is missing ($WM_ROOT/versions/$_ver)"
    fi
else
    fail "no $WM_ROOT/current-version marker (self-install did not publish)"
fi

# =========================================================================
phase 3 "binstub deployed + reachable on a stock login-shell PATH, runs a verb"
if [ -e "$HOME/.local/bin/worktree-manager" ]; then
    pass "binstub deployed: ~/.local/bin/worktree-manager"
else
    jam "path-binstub" "binstub missing: ~/.local/bin/worktree-manager" \
        "self-install deploys the binstub to ~/.local/bin"
fi
if bash -lc 'command -v worktree-manager >/dev/null'; then
    pass "worktree-manager resolves on a fresh login-shell PATH"
else
    jam "path-binstub" "worktree-manager NOT on login-shell PATH (~/.local/bin not exported at login)" \
        "ensure ~/.local/bin is on the login PATH (the uv installer adds it)"
fi
capture "wm-version" -- bash -lc 'worktree-manager --version'
if [ $? -eq 0 ]; then
    pass "worktree-manager --version exits 0 via the binstub"
else
    fail "worktree-manager --version failed via the binstub (see cr-logs/wm-version.log)"
fi

# =========================================================================
phase 4 "git-optional note (tarball fallback) + read-only doctor"
# doctor is READ-ONLY and legitimately exits non-zero on a fresh box (the
# agent-worktrees core is not installed by the Manager bootstrap) -- capture it
# for visibility WITHOUT asserting on its exit code.
capture "doctor" -- bash -lc 'worktree-manager doctor' || true
info "worktree-manager doctor captured (read-only; a non-zero 'not fully set up' verdict on a fresh box is expected, not a bootstrap failure)"
info "git is present on this image ($(command -v git || echo MISSING)); the git-clone fetch path was exercised."
info "The git-ABSENT tarball fallback (bootstrap codeload path + manager_tarball_url / _fetch_via_tarball) is covered by unit tests; a no-git image variant is a follow-up scenario."

cr_finalize
