#!/usr/bin/env bash
# Self-provisioning check -- runs on session start via hooks.json (dotfiles #693).
#
# "Enabling a runtime plugin" should be the whole install: on any session start,
# bring each enabled plugin's runtime into existence and keep it version-matched
# to its payload -- not only when the session was launched through the
# agent-worktrees worktree launcher. This shim runs the same version-keyed
# `reconcile-plugins` logic universally.
#
# Non-blocking by construction: the foreground does only a cheap read-only
# `--peek` (no cache side effects). A cold first provision builds a venv (slow),
# so when there is work we spawn the `--apply` worker DETACHED and return
# immediately -- session start never waits on a runtime build.

set -uo pipefail

_LOG="${WORKTREE_SETUP_LOG:-/dev/null}"
_log() { printf '[%s] [%s] provision-check: %s\n' "$(date '+%H:%M:%S')" "$1" "$2" >> "$_LOG" 2>/dev/null || true; }

# Opt-out: honor the launcher's reconcile switch plus a provisioning-scoped one.
if [[ "${WORKTREE_NO_RECONCILE:-}" == "1" || "${WORKTREE_NO_PROVISION:-}" == "1" ]]; then
    exit 0
fi

_awresolve="$HOME/.agent-worktrees/bin/resolve-runtime.sh"
[ -f "$_awresolve" ] && . "$_awresolve"
PYTHON="${AW_PY:-}"
if [[ ! -x "$PYTHON" ]]; then exit 0; fi

# Read-only preview: does anything need provisioning? (No throttle side effects.)
peek="$(PYTHONPATH="" "$PYTHON" -m agent_worktrees reconcile-plugins --peek 2>/dev/null)" || exit 0
[[ -n "$peek" ]] || exit 0

# Decide + extract the services needing work in one python pass (no jq dep).
# Exits non-zero when there is nothing to do, so the shim returns immediately.
services="$(printf '%s' "$peek" | PYTHONPATH="" "$PYTHON" -c \
'import sys, json
d = json.load(sys.stdin)
if d.get("action") != "reconcile":
    sys.exit(1)
print(", ".join(sorted({u["service"] for u in d.get("updates", [])})))' 2>/dev/null)" || exit 0

echo "[agent-worktrees] Provisioning runtime(s) in background: $services"
_log INFO "provisioning in background: $services"

# Background apply: execute the plan detached so the slow build never blocks the
# session. Fully detach so the child outlives this hook. Move out of the plugin
# payload first for parity with Windows, where an inherited payload cwd prevents
# an in-place marketplace refresh.
log_dir="$HOME/.agent-worktrees/logs"
mkdir -p "$log_dir" 2>/dev/null || true
stamp="$(date -u '+%Y%m%d-%H%M%S')"
(
    cd "$HOME" || exit 0
    PYTHONPATH="" nohup "$PYTHON" -m agent_worktrees reconcile-plugins --apply \
        >> "$log_dir/provision-$stamp.log" 2>&1 &
) >/dev/null 2>&1

exit 0
