#!/usr/bin/env bash
# bind-nudge -- postToolUse hook (hooks.json). See bind-nudge.ps1 for the parity.
#
# Detects an unbound-but-active worktree (an agent is working here -- this hook is
# firing -- yet the worktree has no session bound on record) and emits an
# additionalContext nudge inviting the agent to run `agent-worktrees bind-session
# --worktree-dir=<dir>`. The bind itself is the agent's explicit act; this hook
# only detects and prompts. Fail-open: emits `{}` (a no-op) on any problem so a
# nudge never disturbs the tool result.
#
# Runs under the agent-worktrees venv python (needs the package for the
# liveness-aware head resolution). The postToolUse payload is piped on stdin; the
# command reads workingDirectory from it (--stdin), falling back to the hook cwd.

set -uo pipefail

emit_empty() { printf '{}'; exit 0; }

_awresolve="$HOME/.agent-worktrees/bin/resolve-runtime.sh"
[ -f "$_awresolve" ] && . "$_awresolve"
PY="${AW_PY:-}"
[[ -x "$PY" ]] || emit_empty

# Forward the CLI's stdin payload; the command parses workingDirectory from it.
PYTHONPATH="" "$PY" -m agent_worktrees bind-nudge --stdin 2>/dev/null || emit_empty
exit 0
