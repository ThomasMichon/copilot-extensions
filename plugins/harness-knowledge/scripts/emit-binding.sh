#!/usr/bin/env bash
# emit-binding -- harness-knowledge sessionStart hook. See emit-binding.ps1 for
# the full rationale.
#
# Emits the machine-local knowledge-binding fragment (~/.<harness>/knowledge-binding.md)
# as {"additionalContext": "..."}, cwd-gated to the harness project (resolved via
# agent-worktrees); emits {} outside a managed project or when no binding exists.
# Declarative, launch-path-independent replacement for the per-project
# knowledge-binding.instructions.md loaded via COPILOT_CUSTOM_INSTRUCTIONS_DIRS
# (dotfiles#1057).

set -uo pipefail

emit_empty() { printf '{}'; exit 0; }

# Resolve the harness project via the agent-worktrees BINSTUB (its own marker),
# never by reaching into its runtime venv (#1106).
AW="$(command -v agent-worktrees || true)"
[[ -n "$AW" ]] || AW="$HOME/.local/bin/agent-worktrees"
[[ -x "$AW" ]] || emit_empty
project="$("$AW" get project 2>/dev/null || true)"
[[ -n "$project" ]] || emit_empty

frag="$HOME/.$project/knowledge-binding.md"
[[ -f "$frag" ]] || emit_empty

PYTHONPATH="" "$PY" - "$frag" <<'PYEOF'
import json, sys

with open(sys.argv[1], encoding="utf-8") as fh:
    text = fh.read()
lines = [ln for ln in text.splitlines()
         if ln.strip() != "<!-- managed by harness-knowledge -->"]
body = "\n".join(lines).strip()
print(json.dumps({"additionalContext": body}) if body else "{}")
PYEOF
exit 0
