#!/usr/bin/env bash
# register-nudge -- sessionStart additionalContext hook (hooks.json). See
# register-nudge.ps1 for the parity.
#
# First-run ONBOARDING nudge: when a session is in a git repo that is NOT a
# registered agent-worktrees project, emit a ONE-TIME (per repo) additionalContext
# nudge inviting `agent-worktrees register <name>`. Nudge ONLY -- it NEVER
# registers/adopts anything (install-vs-adopt boundary): the register is the
# operator's explicit act.
#
# Grace-window-cheap + resolver-free: pure shell + a heuristic read of
# projects.yaml, so it works on a tools-half box (the self-provisioned runtime,
# no full-launcher resolver). Fail-open: emits `{}` (a no-op) on ANY uncertainty
# so it never nags wrongly, and it writes only machine-local runtime state
# (~/.agent-worktrees/.register-nudged/), never the repo.
set -uo pipefail

emit_empty() { printf '{}'; exit 0; }

# Only nudge when agent-worktrees is actually available to register with (the
# self-provisioning tool binstub is on PATH or deployed).
if ! command -v agent-worktrees >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/agent-worktrees" ]; then
    emit_empty
fi

# Must be inside a git work tree.
top="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$top" ] || emit_empty

# Derive the repo name: strip a `<repo>.worktrees/<id>` worktree suffix, then
# take the basename.
base="$top"
case "$base" in
    *.worktrees/*) base="${base%%.worktrees/*}" ;;
esac
name="$(basename "$base" 2>/dev/null || true)"
[ -n "$name" ] || emit_empty

# Already registered? (heuristic: the repo name is a project key in
# projects.yaml.) If so -- or if we cannot be sure it is NOT registered -- stay
# silent.
projects="$HOME/.agent-worktrees/projects.yaml"
if [ -f "$projects" ] && grep -qE "^[[:space:]]+${name}:[[:space:]]*$" "$projects" 2>/dev/null; then
    emit_empty
fi

# Once-per-repo gating: skip if we have already nudged for this repo path.
marker_dir="$HOME/.agent-worktrees/.register-nudged"
key="$(printf '%s' "$top" | cksum 2>/dev/null | cut -d' ' -f1)"
[ -n "$key" ] || key="$name"
marker="$marker_dir/$key"
[ -f "$marker" ] && emit_empty
mkdir -p "$marker_dir" 2>/dev/null || true
: > "$marker" 2>/dev/null || true

msg="This repo ($name) is not a registered agent-worktrees project. To enable isolated, concurrent worktree sessions (create/finalize + the PR flow), register it once from the repo root: agent-worktrees register $name . This is an onboarding nudge only -- nothing has been registered, and agent-worktrees never auto-adopts a repo."

# JSON-encode the message (backslashes then quotes) and emit the object.
esc=${msg//\\/\\\\}
esc=${esc//\"/\\\"}
printf '{"additionalContext": "%s"}' "$esc"
exit 0
